"""Memory CRUD, search, and stats REST endpoints for Open Brain."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from ulid import ULID

from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_pool
from openbrain.server.embed import ServerEmbedder
from openbrain.server.models import (
    BulkDeleteResponse,
    MemoryCreateRequest,
    MemoryCreateResponse,
    MemoryListResponse,
    MemoryResponse,
    MemorySearchResponse,
    MemorySearchResult,
    StatsResponse,
)
from openbrain.server.store import ServerStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/memories", tags=["memories"])
stats_router = APIRouter(tags=["stats"])


def _parse_tags(tags_param: Optional[str]) -> Optional[List[str]]:
    """Parse comma-separated tags query param."""
    if not tags_param:
        return None
    return [t.strip() for t in tags_param.split(",") if t.strip()]


async def _get_store() -> ServerStore:
    """Get a ServerStore instance from the connection pool."""
    pool = await get_pool()
    return ServerStore(pool)


def _row_to_response(d: dict) -> MemoryResponse:
    """Convert a store row dict to a MemoryResponse."""
    return MemoryResponse(
        id=d["id"],
        content=d["content"],
        type=d["type"],
        source=d.get("source"),
        project=d.get("project"),
        tags=d.get("tags", []),
        metadata=d.get("metadata", {}),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        expires_at=d.get("expires_at"),
    )


# ── Create ─────────────────────────────────────────────────────────


@router.post("", response_model=MemoryCreateResponse, status_code=201)
async def create_memory(
    body: MemoryCreateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> MemoryCreateResponse:
    """Create a new memory with server-side embedding."""
    project = body.project
    if auth.project is not None:
        project = auth.project

    memory_id = str(ULID())
    store = await _get_store()

    # Server-side embedding
    embedder = ServerEmbedder.get_instance()
    embedding = embedder.embed(body.content)

    await store.save(
        org_id=auth.org_id,
        memory_id=memory_id,
        content=body.content,
        embedding=embedding,
        type=body.type,
        source=body.source,
        project=project,
        tags=body.tags,
        metadata=body.metadata,
        expires_at=body.expires_at,
    )

    return MemoryCreateResponse(id=memory_id)


# ── Search ─────────────────────────────────────────────────────────


@router.get("/search", response_model=MemorySearchResponse)
async def search_memories(
    q: str = Query(..., min_length=1, description="Search query text"),
    type: Optional[str] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    project: Optional[str] = Query(None),
    limit: int = Query(5, ge=1, le=20),
    auth: AuthContext = Depends(get_auth_context),
) -> MemorySearchResponse:
    """Semantic search — server embeds the query text."""
    effective_project = project
    if auth.project is not None:
        effective_project = auth.project

    # Embed query server-side
    embedder = ServerEmbedder.get_instance()
    embedding = embedder.embed(q)

    if embedding is None:
        return MemorySearchResponse(memories=[])

    store = await _get_store()
    rows = await store.search(
        org_id=auth.org_id,
        embedding=embedding,
        type=type,
        tags=_parse_tags(tags),
        project=effective_project,
        limit=limit,
    )

    results = []
    for d in rows:
        score = d.pop("score", 0.0)
        resp = _row_to_response(d)
        results.append(MemorySearchResult(**resp.model_dump(), score=score))

    return MemorySearchResponse(memories=results)


# ── Read ───────────────────────────────────────────────────────────


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> MemoryResponse:
    """Get a single memory by ID."""
    store = await _get_store()
    d = await store.get(auth.org_id, memory_id)

    if d is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    return _row_to_response(d)


# ── List ───────────────────────────────────────────────────────────


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    type: Optional[str] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    project: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_auth_context),
) -> MemoryListResponse:
    """List memories with pagination."""
    effective_project = project
    if auth.project is not None:
        effective_project = auth.project

    store = await _get_store()
    rows, total = await store.list(
        org_id=auth.org_id,
        type=type,
        tags=_parse_tags(tags),
        project=effective_project,
        limit=limit,
        offset=offset,
    )

    return MemoryListResponse(
        memories=[_row_to_response(d) for d in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── Delete ─────────────────────────────────────────────────────────


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> None:
    """Delete a single memory."""
    store = await _get_store()
    deleted = await store.delete(auth.org_id, memory_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")


@router.delete("", response_model=BulkDeleteResponse)
async def bulk_delete_memories(
    type: Optional[str] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    project: Optional[str] = Query(None),
    confirm: bool = Query(False, description="Required for bulk delete"),
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> BulkDeleteResponse:
    """Bulk delete memories by filter. Requires confirm=true."""
    has_filter = any([type, tags, project])
    if not has_filter and not confirm:
        raise HTTPException(
            status_code=400,
            detail="Bulk delete without filters requires confirm=true",
        )

    effective_project = project
    if auth.project is not None:
        effective_project = auth.project

    store = await _get_store()
    count = await store.delete_by_filter(
        org_id=auth.org_id,
        type=type,
        tags=_parse_tags(tags),
        project=effective_project,
    )

    return BulkDeleteResponse(deleted=count)


# ── Stats ──────────────────────────────────────────────────────────


@stats_router.get("/v1/stats", response_model=StatsResponse)
async def get_stats(
    auth: AuthContext = Depends(get_auth_context),
) -> StatsResponse:
    """Get memory store statistics."""
    store = await _get_store()
    data = await store.stats(auth.org_id)

    return StatsResponse(
        total_count=data["total_count"],
        count_by_type=data["count_by_type"],
        count_by_project=data["count_by_project"],
        oldest_memory=data.get("oldest_memory"),
        newest_memory=data.get("newest_memory"),
    )
