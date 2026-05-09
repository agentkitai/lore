"""Memory CRUD endpoints for Lore Cloud Server (v0.9.0+).

Uses the new `memories` table with `content` and `context` columns.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence.exceptions import StoreNotFoundError
from lore.persistence.protocol import Store
from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.server.models import (
    MemoryCreateRequest,
    MemoryCreateResponse,
    MemoryListResponse,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResult,
    MemoryUpdateRequest,
)
from lore.server.routes._parsers import _parse_meta, _parse_tags
from lore.services import memories as memories_service
from lore.services.memories import (
    create_memory as _create_memory,
)
from lore.services.memories import (
    delete_memory as _delete_memory,
)
from lore.services.memories import (
    get_memory as _get_memory,
)
from lore.services.memories import (
    list_memories as _list_memories,
)
from lore.services.memories import (
    search_memories as _search_memories,
)
from lore.services.memories import (
    update_memory as _update_memory,
)
from lore.services.memories import (
    vote_memory as _vote_memory,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/memories", tags=["memories"])

# Type-specific decay half-lives (days)
_HALF_LIFE_DEFAULT = 30


def _row_to_response(row: dict) -> MemoryResponse:
    """Convert a DB row to a MemoryResponse (no embedding)."""
    tags = _parse_tags(row.get("tags"))
    meta = _parse_meta(row.get("meta"))
    return MemoryResponse(
        id=row["id"],
        content=row["content"],
        context=row.get("context"),
        tags=tags,
        source=row.get("source"),
        project=row.get("project"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row.get("expires_at"),
        upvotes=row.get("upvotes", 0),
        downvotes=row.get("downvotes", 0),
        meta=meta,
        scope=row.get("scope") or "project",
    )



# ── Create ─────────────────────────────────────────────────────────


@router.post("", response_model=MemoryCreateResponse, status_code=201)
async def create_memory(
    body: MemoryCreateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> MemoryCreateResponse:
    """Create a memory. Routes layer: parse → call service → serialize."""
    store = await get_store()

    # Embedding stays at this layer for now — Phase 1B will factor it out.
    from lore.server.routes.retrieve import _get_embedder
    embedder = _get_embedder()
    embedding = body.embedding if body.embedding else await asyncio.to_thread(embedder.embed, body.content)

    stored = await _create_memory(
        store,
        org_id=auth.org_id,
        content=body.content,
        context=body.context,
        embedding=embedding,
        tags=body.tags or [],
        source=body.source,
        project=auth.project or body.project,
        expires_at=body.expires_at,
        meta=body.meta or {},
        scope=body.scope,
    )

    # Fire-and-forget enrichment unchanged from before
    enrich = body.enrich
    if enrich is None:
        enrich = os.environ.get("LORE_ENRICHMENT_ENABLED", "").lower() in ("true", "1", "yes")
    if enrich:
        asyncio.create_task(memories_service.enrich_memory_async(
            store, memory_id=stored.id, content=stored.content, context=stored.context,
        ))

    # Fire-and-forget graph extraction. Auto-on iff `claude` is on PATH;
    # explicit override via LORE_GRAPH_EXTRACTION_ENABLED. The semaphore
    # inside the service caps concurrency so a burst of creates doesn't
    # spawn unbounded subprocesses.
    from lore.services import graph_extraction as graph_svc

    if graph_svc.is_enabled():
        asyncio.create_task(graph_svc.extract_and_persist(
            store, org_id=auth.org_id, memory_id=stored.id,
            content=stored.content, context=stored.context,
        ))

    return MemoryCreateResponse(id=stored.id)


# ── Search ─────────────────────────────────────────────────────────


@router.post("/search", response_model=MemorySearchResponse)
async def search_memories(
    body: MemorySearchRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> MemorySearchResponse:
    """Semantic search with multiplicative scoring."""
    store = await get_store()
    # body.embedding is the pre-computed query vector (384-dim)
    results = await _search_memories(
        store,
        org_id=auth.org_id,
        query_vec=body.embedding,
        limit=body.limit,
        min_score=body.min_score,
        project=auth.project or body.project,
        scope_mode=body.scope,
    )
    return MemorySearchResponse(
        memories=[
            MemorySearchResult(
                id=r.id,
                content=r.content,
                context=r.context,
                tags=list(r.tags),
                source=r.source,
                project=r.project,
                created_at=r.created_at,
                updated_at=r.updated_at,
                expires_at=r.expires_at,
                upvotes=r.upvotes,
                downvotes=r.downvotes,
                meta=dict(r.meta),
                scope=getattr(r, "scope", "project") or "project",
                score=round(max(r.score, 0.0), 6),
            )
            for r in results
        ]
    )


# ── Access tracking ────────────────────────────────────────────────


@router.post("/{memory_id}/access", status_code=200)
async def record_access(
    memory_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> dict:
    """Record an access event."""
    # Enforce project scoping: a project-scoped key must not access memories
    # outside its project.
    if auth.project:
        existing = await store.get_memory(auth.org_id, memory_id)
        if existing is None or existing.project != auth.project:
            raise HTTPException(status_code=404, detail="Memory not found")

    try:
        updated = await memories_service.record_memory_access(store, auth.org_id, memory_id)
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {
        "id": updated.id,
        "access_count": updated.access_count,
        "last_accessed_at": updated.last_accessed_at.isoformat() if updated.last_accessed_at else None,
    }


# ── Bulk read (Phase 6D progressive disclosure) ─────────────────────


class MemoryDetailsResponse(MemoryListResponse):
    """Response for /v1/memories/details: full payloads for a CSV id list.

    Reuses ``MemoryListResponse``'s ``memories``/``total``/``limit``/``offset``
    shape so existing list-consuming clients can reuse decoders, with one
    addition: ``errors`` lists any IDs that were missing or unauthorized so
    the caller can distinguish "we returned everything you asked for" from
    "some IDs silently dropped". A 404 is raised only when *every* requested
    ID failed to resolve (don't leak existence one-by-one, but also don't
    hand back an empty array on a typo).
    """

    errors: List[str] = []


_MAX_DETAIL_IDS = 10


@router.get("/details", response_model=MemoryDetailsResponse)
async def get_memory_details(
    ids: str = Query(
        ..., min_length=1, description="Comma-separated memory IDs (max 10)",
    ),
    auth: AuthContext = Depends(get_auth_context),
) -> MemoryDetailsResponse:
    """Fetch full ``StoredMemory`` payloads for one or more IDs.

    Phase 6D progressive-disclosure: an agent surveys ``/v1/search`` (compact
    index) and calls this endpoint with the IDs it wants to drill into.

    404 policy:
        * If at least one ID resolves to a memory the caller can read, return
          200 with the resolved memories plus an ``errors`` array listing the
          unresolved ones.
        * If every ID is missing or scoped out, return 404 — don't leak
          existence by returning a 200 with an empty array.
    """
    requested = [s for s in (chunk.strip() for chunk in ids.split(",")) if s]
    if not requested:
        raise HTTPException(status_code=422, detail="At least one id is required")
    # De-dupe while preserving caller order so the response is deterministic.
    seen: set = set()
    unique_ids: list = []
    for mid in requested:
        if mid not in seen:
            seen.add(mid)
            unique_ids.append(mid)
    if len(unique_ids) > _MAX_DETAIL_IDS:
        raise HTTPException(
            status_code=422,
            detail=f"Too many ids; max {_MAX_DETAIL_IDS} per call",
        )

    store = await get_store()
    resolved: list = []
    errors: List[str] = []
    for mid in unique_ids:
        m = await _get_memory(store, auth.org_id, mid)
        if m is None:
            errors.append(mid)
            continue
        # Project-scoped key: refuse to disclose memories outside its project.
        if auth.project is not None and m.project != auth.project:
            errors.append(mid)
            continue
        resolved.append(_stored_to_memory_response(m))

    if not resolved:
        raise HTTPException(status_code=404, detail="No memories found for given ids")

    return MemoryDetailsResponse(
        memories=resolved,
        total=len(resolved),
        limit=len(unique_ids),
        offset=0,
        errors=errors,
    )


# ── Read ───────────────────────────────────────────────────────────


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> MemoryResponse:
    """Get a single memory by ID."""
    store = await get_store()
    m = await _get_memory(store, auth.org_id, memory_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return MemoryResponse(
        id=m.id,
        content=m.content,
        context=m.context,
        tags=list(m.tags),
        source=m.source,
        project=m.project,
        created_at=m.created_at,
        updated_at=m.updated_at,
        expires_at=m.expires_at,
        upvotes=m.upvotes,
        downvotes=m.downvotes,
        meta=dict(m.meta),
        scope=getattr(m, "scope", "project") or "project",
    )


# ── Update ─────────────────────────────────────────────────────────


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: str,
    body: MemoryUpdateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> MemoryResponse:
    """Update a memory."""
    if (
        body.tags is None
        and body.meta is None
        and body.upvotes is None
        and body.downvotes is None
    ):
        raise HTTPException(status_code=422, detail="No fields to update")

    store = await get_store()
    try:
        updated = await _update_memory(
            store,
            org_id=auth.org_id,
            memory_id=memory_id,
            tags=body.tags,
            meta=body.meta,
        )
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _stored_to_memory_response(updated)


# ── Delete ─────────────────────────────────────────────────────────


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> None:
    """Delete a memory."""
    store = await get_store()
    deleted = await _delete_memory(store, org_id=auth.org_id, memory_id=memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")


# ── List ───────────────────────────────────────────────────────────


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    project: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_expired: bool = Query(False),
    auth: AuthContext = Depends(get_auth_context),
) -> MemoryListResponse:
    """List memories with pagination."""
    store = await get_store()
    rows = await _list_memories(
        store,
        org_id=auth.org_id,
        project=auth.project or project,
        type=type,
        tier=tier,
        limit=limit,
        offset=offset,
        include_expired=include_expired,
    )
    return MemoryListResponse(
        memories=[_stored_to_memory_response(m) for m in rows],
        total=len(rows),
        limit=limit,
        offset=offset,
    )


def _stored_to_memory_response(m) -> MemoryResponse:
    return MemoryResponse(
        id=m.id, content=m.content, context=m.context, tags=list(m.tags),
        source=m.source, project=m.project,
        created_at=m.created_at, updated_at=m.updated_at, expires_at=m.expires_at,
        upvotes=m.upvotes, downvotes=m.downvotes, meta=dict(m.meta),
        scope=getattr(m, "scope", "project") or "project",
    )


# ── Vote endpoints ─────────────────────────────────────────────────


@router.post("/{memory_id}/upvote")
async def upvote_memory(
    memory_id: str,
    auth: AuthContext = Depends(require_role("writer", "admin")),
):
    """Increment the upvote counter for a memory."""
    store = await get_store()
    try:
        updated = await _vote_memory(
            store, org_id=auth.org_id, memory_id=memory_id, direction="up"
        )
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"id": updated.id, "upvotes": updated.upvotes, "downvotes": updated.downvotes}


@router.post("/{memory_id}/downvote")
async def downvote_memory(
    memory_id: str,
    auth: AuthContext = Depends(require_role("writer", "admin")),
):
    """Increment the downvote counter for a memory."""
    store = await get_store()
    try:
        updated = await _vote_memory(
            store, org_id=auth.org_id, memory_id=memory_id, direction="down"
        )
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"id": updated.id, "upvotes": updated.upvotes, "downvotes": updated.downvotes}
