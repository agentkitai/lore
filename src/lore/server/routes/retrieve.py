"""Retrieve endpoint — GET /v1/retrieve for memory-augmented responses."""

from __future__ import annotations

import json
import logging
import time
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["retrieve"])

# Valid output formats
VALID_FORMATS = {"xml", "markdown", "raw"}

# Half-life default for time-adjusted importance scoring (days)
_HALF_LIFE_DEFAULT = 30


# ── Response Models ────────────────────────────────────────────────


class RetrieveMemory(BaseModel):
    id: str
    content: str
    type: str
    tier: str
    score: float
    created_at: str
    source: Optional[str] = None
    project: Optional[str] = None
    tags: List[str] = []


class RetrieveResponse(BaseModel):
    memories: List[RetrieveMemory]
    formatted: str
    count: int
    query_time_ms: float


# ── Embedder singleton ─────────────────────────────────────────────

_embedder = None


def _get_embedder():
    """Lazy-load the local embedder (ONNX MiniLM-L6-v2)."""
    global _embedder
    if _embedder is None:
        from lore.embed.local import LocalEmbedder
        _embedder = LocalEmbedder()
    return _embedder


# ── Formatting ─────────────────────────────────────────────────────


def _format_xml(memories: List[RetrieveMemory], query: str) -> str:
    """Format memories as XML block."""
    if not memories:
        return ""
    lines = [f'<memories query="{query}">']
    for m in memories:
        lines.append(f"  <memory id=\"{m.id}\" score=\"{m.score:.2f}\" type=\"{m.type}\">")
        lines.append(f"    {m.content}")
        lines.append("  </memory>")
    lines.append("</memories>")
    return "\n".join(lines)


def _format_markdown(memories: List[RetrieveMemory], query: str) -> str:
    """Format memories as Markdown list."""
    if not memories:
        return ""
    lines = [f"## Relevant Memories ({len(memories)})\n"]
    for m in memories:
        lines.append(f"- **[{m.score:.2f}]** {m.content}")
    return "\n".join(lines)


def _format_raw(memories: List[RetrieveMemory], query: str) -> str:
    """Format memories as plain newline-separated text."""
    if not memories:
        return ""
    return "\n".join(m.content for m in memories)


_FORMATTERS = {
    "xml": _format_xml,
    "markdown": _format_markdown,
    "raw": _format_raw,
}


# ── Route ──────────────────────────────────────────────────────────


@router.get("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    query: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(5, ge=1, le=50, description="Max results"),
    min_score: float = Query(0.3, ge=0.0, le=1.0, description="Minimum relevance score"),
    format: str = Query("xml", description="Output format: xml, markdown, raw"),
    project: Optional[str] = Query(None, description="Filter by project"),
    auth: AuthContext = Depends(get_auth_context),
) -> RetrieveResponse:
    """Retrieve relevant memories for a query.

    Returns semantically similar memories with formatted output
    suitable for injection into LLM prompts.
    """
    start = time.monotonic()

    # Validate format
    if format not in VALID_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid format '{format}'. Must be one of: {', '.join(sorted(VALID_FORMATS))}",
        )

    # Embed the query
    embedder = _get_embedder()
    query_vec = embedder.embed(query)

    # Build SQL query
    where_parts: list[str] = ["org_id = $1"]
    params: list = [auth.org_id]

    # Project scoping: auth key scope overrides query param
    effective_project = project
    if auth.project is not None:
        effective_project = auth.project
    if effective_project is not None:
        params.append(effective_project)
        where_parts.append(f"project = ${len(params)}")

    # Exclude expired
    where_parts.append("(expires_at IS NULL OR expires_at > now())")

    # Embedding must exist
    where_parts.append("embedding IS NOT NULL")

    where_sql = " AND ".join(where_parts)

    # Embedding parameter
    params.append(json.dumps(query_vec))
    emb_idx = len(params)

    # Min score parameter
    params.append(min_score)
    score_idx = len(params)

    # Limit parameter
    params.append(limit)
    limit_idx = len(params)

    # SQL with time-adjusted importance scoring (same formula as lessons/search)
    sql = f"""
        SELECT id, content, type, tier, source, project, tags,
               created_at, importance_score,
               (1 - (embedding <=> ${emb_idx}::vector)) *
               COALESCE(importance_score, 1.0) *
               power(0.5,
                   LEAST(
                       EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0,
                       COALESCE(
                           EXTRACT(EPOCH FROM (now() - last_accessed_at)) / 86400.0,
                           EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0
                       )
                   )
                   / {_HALF_LIFE_DEFAULT}
               )
               AS score
        FROM lessons
        WHERE {where_sql}
          AND (1 - (embedding <=> ${emb_idx}::vector)) >= ${score_idx}
        ORDER BY score DESC
        LIMIT ${limit_idx}
    """

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    # Build response
    memories: List[RetrieveMemory] = []
    for r in rows:
        rd = dict(r)
        tags = rd.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags)
        created_at = rd.get("created_at")
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()

        memories.append(RetrieveMemory(
            id=rd["id"],
            content=rd["content"],
            type=rd.get("type", "general"),
            tier=rd.get("tier", "long"),
            score=round(float(rd.get("score", 0.0)), 4),
            created_at=str(created_at or ""),
            source=rd.get("source"),
            project=rd.get("project"),
            tags=tags,
        ))

    # Format output
    formatter = _FORMATTERS[format]
    formatted = formatter(memories, query)

    elapsed_ms = round((time.monotonic() - start) * 1000, 2)

    return RetrieveResponse(
        memories=memories,
        formatted=formatted,
        count=len(memories),
        query_time_ms=elapsed_ms,
    )
