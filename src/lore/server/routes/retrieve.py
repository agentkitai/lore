"""Retrieve endpoint — GET /v1/retrieve for memory-augmented responses."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.persistence import StoredMemory
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services import retrieve as retrieve_service
from lore.services.retrieve import (
    HybridResult,  # noqa: F401  (re-exported for tests / external consumers)
)
from lore.services.retrieve import (
    hybrid_retrieve as _hybrid_retrieve_service,  # noqa: F401  (kept as named export)
)
from lore.services.retrieve import (
    hybrid_retrieve_with_report as _hybrid_retrieve_with_report,
)

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
    # Phase 6C: per-signal breakdown so consumers can debug ranking.
    # Optional / absent for legacy session-context entries that bypass hybrid scoring.
    signals: Optional[dict] = None


class RetrieveResponse(BaseModel):
    memories: List[RetrieveMemory]
    formatted: str
    count: int
    query_time_ms: float
    # Diagnostic fields so a caller getting an empty ``memories`` list can
    # tell "best match was 0.27 (just below threshold)" from "nothing
    # matched at all", and which retrievers actually contributed.
    # ``best_score`` is the pre-min_score top score across vector / fts /
    # graph candidates. ``attempted`` is keyed by retriever name with
    # values "ok" | "empty" | "error".
    best_score: float = 0.0
    attempted: dict = {}


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


# ── Conversion helpers ─────────────────────────────────────────────


def _stored_to_retrieve_memory(sm: StoredMemory) -> RetrieveMemory:
    """Convert a persistence-layer StoredMemory to the route-layer RetrieveMemory."""
    tags = list(sm.tags or [])
    created_at = sm.created_at.isoformat() if hasattr(sm.created_at, "isoformat") else str(sm.created_at or "")
    return RetrieveMemory(
        id=sm.id,
        content=f"[Session Context] {sm.content}",
        type=(sm.meta or {}).get("type", "session_snapshot"),
        tier=(sm.meta or {}).get("tier", "long"),
        score=0.0,
        created_at=created_at,
        source=sm.source,
        project=sm.project,
        tags=tags,
    )


# ── Route ──────────────────────────────────────────────────────────


@router.get("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    query: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(5, ge=1, le=50, description="Max results"),
    min_score: float = Query(0.3, ge=0.0, le=1.0, description="Minimum relevance score"),
    format: str = Query("xml", description="Output format: xml, markdown, raw"),
    project: Optional[str] = Query(None, description="Filter by project"),
    profile: Optional[str] = Query(
        None, description="Retrieval profile name (e.g. precise, broad, balanced)",
    ),
    include_session_context: bool = Query(
        True, description="Append recent session_snapshot memories (last 24h)",
    ),
    scope: str = Query(
        "default",
        description=(
            "Scope mode (Phase 6G). 'default' applies "
            "(scope='global') OR (scope='project' AND project=:current); "
            "'all' skips the predicate (cross-project search opt-in)."
        ),
    ),
    auth: AuthContext = Depends(get_auth_context),
) -> RetrieveResponse:
    """Retrieve relevant memories for a query.

    Returns semantically similar memories with formatted output
    suitable for injection into LLM prompts.

    When a --profile is specified, its settings (k, threshold, etc.)
    override the default limit/min_score values.
    """
    start = time.monotonic()

    store = await get_store()

    # Resolve profile if specified — override limit/min_score from profile
    # settings, and pass the resolved profile through to the hybrid path so
    # its semantic / fts / graph weights drive RRF fusion.
    resolved_profile = None
    if profile:
        from lore.services import profiles as profiles_service

        resolved_profile = await profiles_service.resolve_profile(
            store, auth.org_id, profile
        )
        if resolved_profile:
            # Profile k overrides limit; otherwise use max_results.
            limit = (
                resolved_profile.k
                if resolved_profile.k is not None
                else resolved_profile.max_results
            )
            # Profile threshold overrides min_score; otherwise use min_score.
            min_score = (
                resolved_profile.threshold
                if resolved_profile.threshold is not None
                else resolved_profile.min_score
            )
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile}' not found. Use GET /v1/profiles to list available profiles.",
            )

    # Validate format
    if format not in VALID_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid format '{format}'. Must be one of: {', '.join(sorted(VALID_FORMATS))}",
        )

    # Phase 6G: validate scope mode.
    if scope not in ("default", "all"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid scope '{scope}'. Must be 'default' or 'all'.",
        )

    # Embed the query
    embedder = _get_embedder()
    query_vec = embedder.embed(query)

    # Project scoping: auth key scope overrides query param
    effective_project = project
    if auth.project is not None:
        effective_project = auth.project

    # Phase 6C hybrid path. ``hybrid_retrieve_with_report`` falls back to
    # a default profile when ``resolved_profile`` is None and degrades
    # each signal independently if the migration / extension isn't
    # available — and surfaces ``best_score`` + per-retriever ``attempted``
    # status so the caller can disambiguate "near miss" from "empty index".
    report = await _hybrid_retrieve_with_report(
        store,
        org_id=auth.org_id,
        query_text=query,
        query_vec=query_vec,
        limit=limit,
        project=effective_project,
        profile=resolved_profile,
        min_score_override=min_score,
        scope_mode=scope,
        requesting_user_id=auth.principal_id,
    )
    hybrid_results = report.results

    # Convert HybridResult dataclasses to RetrieveMemory pydantic models.
    memories: List[RetrieveMemory] = [
        RetrieveMemory(
            id=r.memory.id,
            content=r.memory.content,
            type=(r.memory.meta or {}).get("type", "unknown"),
            tier=(r.memory.meta or {}).get("tier", "long"),
            score=round(float(r.score), 4),
            created_at=r.memory.created_at.isoformat() if hasattr(r.memory.created_at, "isoformat") else str(r.memory.created_at),
            source=r.memory.source,
            project=r.memory.project,
            tags=list(r.memory.tags),
            signals={k: round(float(v), 4) for k, v in r.signals.items()},
        )
        for r in hybrid_results
    ]

    # Auto-inject recent session snapshots (last 24h)
    session_memories: List[RetrieveMemory] = []
    if include_session_context:
        existing_ids = {m.id for m in memories}
        session_stored = await retrieve_service.recent_session_snapshots(
            store, org_id=auth.org_id, project=effective_project,
            exclude_ids=tuple(existing_ids), limit=3,
            requesting_user_id=auth.principal_id,
        )
        session_memories = [_stored_to_retrieve_memory(sm) for sm in session_stored]
        memories.extend(session_memories)

    # Phase 6C: render formatted output directly from the (possibly augmented)
    # memory list — the hybrid path no longer returns a pre-formatted blob.
    formatter = _FORMATTERS[format]
    formatted = formatter(memories, query)

    elapsed_ms = round((time.monotonic() - start) * 1000, 2)

    # Fire-and-forget: record analytics event and update Prometheus metrics
    asyncio.create_task(retrieve_service.record_retrieval_event(
        store,
        org_id=auth.org_id,
        query_text=query,
        memory_ids=[m.id for m in memories],
        scores=[m.score for m in memories],
        min_score=min_score,
        elapsed_ms=elapsed_ms,
        fmt=format,
        project=effective_project,
    ))

    # Fire-and-forget: bump access_count + recalculate importance for returned memories
    if memories:
        asyncio.create_task(retrieve_service.bump_access_counts(
            store, auth.org_id, [m.id for m in memories],
        ))

    return RetrieveResponse(
        memories=memories,
        formatted=formatted,
        count=len(memories),
        query_time_ms=elapsed_ms,
        best_score=round(float(report.best_score), 4),
        attempted=dict(report.attempted),
    )
