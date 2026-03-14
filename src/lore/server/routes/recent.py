"""Recent activity endpoint — GET /v1/recent."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["recent"])

VALID_FORMATS = {"brief", "detailed", "structured"}


# ── Response Models ────────────────────────────────────────────────


class RecentMemoryItem(BaseModel):
    id: str
    content: str
    type: str
    tier: str
    created_at: str
    tags: List[str] = []
    importance_score: float = 1.0


class RecentProjectGroup(BaseModel):
    project: str
    memories: List[RecentMemoryItem]
    count: int
    summary: Optional[str] = None


class RecentActivityResponse(BaseModel):
    groups: Optional[List[RecentProjectGroup]] = None
    formatted: Optional[str] = None
    total_count: int
    hours: int
    generated_at: str
    has_llm_summary: bool = False
    query_time_ms: float


# ── Endpoint ──────────────────────────────────────────────────────


@router.get("/recent", response_model=RecentActivityResponse)
async def recent_activity(
    hours: int = Query(24, ge=1, le=168, description="Lookback window in hours"),
    project: Optional[str] = Query(None, description="Filter by project"),
    format: str = Query("brief", description="Output format: brief, detailed, structured"),
    max_memories: int = Query(50, ge=1, le=200, description="Max memories to return"),
    auth: AuthContext = Depends(get_auth_context),
) -> RecentActivityResponse:
    """Get recent memory activity grouped by project."""
    start = time.monotonic()

    if format not in VALID_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid format '{format}'. Must be one of: {', '.join(sorted(VALID_FORMATS))}",
        )

    # Compute cutoff
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # Resolve project scoping
    effective_project = project
    if auth.project is not None:
        effective_project = auth.project

    where_parts = ["org_id = $1", "created_at >= $2", "(expires_at IS NULL OR expires_at > now())"]
    params: list = [auth.org_id, cutoff]

    if effective_project is not None:
        params.append(effective_project)
        where_parts.append(f"project = ${len(params)}")

    params.append(max_memories)
    limit_idx = len(params)

    sql = f"""
        SELECT id, content,
               COALESCE(meta->>'type', 'general') AS type,
               COALESCE(meta->>'tier', 'long') AS tier,
               source, project, tags, created_at, importance_score
        FROM memories
        WHERE {' AND '.join(where_parts)}
        ORDER BY created_at DESC
        LIMIT ${limit_idx}
    """

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    # Group by project
    groups_dict: dict[str, list] = {}
    for r in rows:
        rd = dict(r)
        proj = rd.get("project") or "default"
        tags = rd.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags)
        created_at = rd.get("created_at")
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()

        item = RecentMemoryItem(
            id=rd["id"],
            content=rd["content"],
            type=rd.get("type", "general"),
            tier=rd.get("tier", "long"),
            created_at=str(created_at or ""),
            tags=tags,
            importance_score=rd.get("importance_score", 1.0) or 1.0,
        )
        groups_dict.setdefault(proj, []).append(item)

    groups = [
        RecentProjectGroup(project=p, memories=mems, count=len(mems))
        for p, mems in groups_dict.items()
    ]
    if groups:
        groups.sort(key=lambda g: g.memories[0].created_at, reverse=True)

    total_count = sum(g.count for g in groups)
    elapsed_ms = round((time.monotonic() - start) * 1000, 2)
    generated_at = datetime.now(timezone.utc).isoformat()

    if format == "structured":
        return RecentActivityResponse(
            groups=groups,
            total_count=total_count,
            hours=hours,
            generated_at=generated_at,
            query_time_ms=elapsed_ms,
        )

    # Format as text
    formatted = _format_text(groups, hours, format)
    return RecentActivityResponse(
        formatted=formatted,
        total_count=total_count,
        hours=hours,
        generated_at=generated_at,
        query_time_ms=elapsed_ms,
    )


def _format_text(groups: List[RecentProjectGroup], hours: int, fmt: str) -> str:
    """Format groups as brief or detailed text."""
    if not groups:
        return f"No recent activity in the last {hours}h."

    lines = [f"## Recent Activity (last {hours}h)\n"]
    for group in groups:
        lines.append(f"### {group.project} ({group.count})")
        for m in group.memories:
            ts = m.created_at[11:16] if len(m.created_at) >= 16 else "??:??"
            if fmt == "detailed":
                lines.append(f"**[{ts}] {m.type}** (tier: {m.tier}, importance: {m.importance_score:.2f})")
                lines.append(m.content)
                if m.tags:
                    lines.append(f"Tags: {', '.join(m.tags)}")
                lines.append("")
            else:
                content = m.content[:100]
                if len(m.content) > 100:
                    content += "..."
                lines.append(f"- [{ts}] {m.type}: {content}")
        lines.append("")
    return "\n".join(lines)
