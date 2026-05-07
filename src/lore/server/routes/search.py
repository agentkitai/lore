"""Compact search endpoint — GET /v1/search (Phase 6D).

Returns the same hybrid-scoring index ``/v1/retrieve`` produces, but strips
the heavy fields (``content``, ``tags``, ``meta``, ``created_at``) so an
agent can survey 20 candidates for ~50 tokens each before drilling in via
``GET /v1/memories/details?ids=...``.

The full-payload ``/v1/retrieve`` endpoint is left untouched for callers
that want a one-shot round-trip with formatted output.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.server._titles import memory_title
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.server.routes.retrieve import _get_embedder
from lore.services.retrieve import (
    HybridResult,
)
from lore.services.retrieve import (
    hybrid_retrieve as _hybrid_retrieve_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["search"])


# ── Response models ────────────────────────────────────────────────


class SearchHit(BaseModel):
    """One row of the compact search index."""

    id: str
    title: str
    score: float
    signals: dict


class SearchResponse(BaseModel):
    hits: List[SearchHit]
    count: int


# ── Route ──────────────────────────────────────────────────────────


@router.get("/search", response_model=SearchResponse)
async def search(
    query: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=50, description="Max index entries to return"),
    min_score: float = Query(0.3, ge=0.0, le=1.0, description="Minimum relevance score"),
    project: Optional[str] = Query(None, description="Filter by project"),
    profile: Optional[str] = Query(
        None, description="Retrieval profile name (e.g. precise, broad, balanced)",
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
) -> SearchResponse:
    """Return a compact ``[{id, title, score, signals}]`` index of relevant memories.

    The agent surveys the index and calls ``/v1/memories/details?ids=...`` to
    fetch full payloads for the rows worth drilling into. Same hybrid scoring
    as ``/v1/retrieve``; only the response shape differs.
    """
    store = await get_store()

    resolved_profile = None
    if profile:
        from lore.services import profiles as profiles_service

        resolved_profile = await profiles_service.resolve_profile(
            store, auth.org_id, profile
        )
        if resolved_profile is None:
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile}' not found. Use GET /v1/profiles to list available profiles.",
            )
        # Profile k/threshold override the query-string limit / min_score, mirroring /v1/retrieve.
        limit = (
            resolved_profile.k
            if resolved_profile.k is not None
            else resolved_profile.max_results
        )
        min_score = (
            resolved_profile.threshold
            if resolved_profile.threshold is not None
            else resolved_profile.min_score
        )

    # Phase 6G: validate scope mode.
    if scope not in ("default", "all"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid scope '{scope}'. Must be 'default' or 'all'.",
        )

    embedder = _get_embedder()
    query_vec = embedder.embed(query)

    # Project scoping: auth-key project always wins over a query-string override.
    effective_project = auth.project if auth.project is not None else project

    hybrid_results: Sequence[HybridResult] = await _hybrid_retrieve_service(
        store,
        org_id=auth.org_id,
        query_text=query,
        query_vec=query_vec,
        limit=limit,
        project=effective_project,
        profile=resolved_profile,
        min_score_override=min_score,
        scope_mode=scope,
    )

    hits = [
        SearchHit(
            id=r.memory.id,
            title=memory_title(r.memory),
            score=round(float(r.score), 4),
            signals={k: round(float(v), 4) for k, v in r.signals.items()},
        )
        for r in hybrid_results
    ]

    return SearchResponse(hits=hits, count=len(hits))
