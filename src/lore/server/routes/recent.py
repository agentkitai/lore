"""Recent activity endpoint — GET /v1/recent."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.persistence import Store, StoredMemory
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services import recent as recent_service

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


def _to_item(m: StoredMemory) -> RecentMemoryItem:
    created_at = m.created_at.isoformat() if m.created_at else ""
    return RecentMemoryItem(
        id=m.id,
        content=m.content or "",
        type=(m.meta or {}).get("type", "general"),
        tier=(m.meta or {}).get("tier", "long"),
        created_at=created_at,
        tags=list(m.tags),
    )


# ── Endpoint ──────────────────────────────────────────────────────


@router.get("/recent", response_model=RecentActivityResponse)
async def recent_activity(
    hours: int = Query(24, ge=1, le=168, description="Lookback window in hours"),
    project: Optional[str] = Query(None, description="Filter by project"),
    format: str = Query("brief", description="Output format: brief, detailed, structured"),
    max_memories: int = Query(50, ge=1, le=200, description="Max memories to return"),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> RecentActivityResponse:
    """Get recent memory activity grouped by project."""
    start = time.monotonic()

    if format not in VALID_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid format '{format}'. Must be one of: {', '.join(sorted(VALID_FORMATS))}",
        )

    # Resolve project scoping — auth.project takes precedence
    effective_project = auth.project if auth.project is not None else project

    memories = await recent_service.get_recent_activity(
        store,
        org_id=auth.org_id,
        project=effective_project,
        hours=hours,
        max_memories=max_memories,
        requesting_user_id=auth.principal_id,
    )

    # Group by project
    groups_dict: dict[str, list[RecentMemoryItem]] = {}
    for m in memories:
        proj = m.project or "default"
        groups_dict.setdefault(proj, []).append(_to_item(m))

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
                lines.append(f"**[{ts}] {m.type}** (tier: {m.tier})")
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
