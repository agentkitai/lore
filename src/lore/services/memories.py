"""Memory CRUD + search service functions.

Pure async functions: take a Store and typed params, return dataclasses.
Routes and AsyncLore both call into here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from lore.persistence import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    ScoredMemory,
    Store,
    StoredMemory,
)


async def create_memory(
    store: Store,
    *,
    org_id: str,
    content: str,
    embedding: Sequence[float],
    context: Optional[str] = None,
    tags: Sequence[str] = (),
    confidence: float = 0.5,
    source: Optional[str] = None,
    project: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    meta: Optional[Mapping[str, Any]] = None,
) -> StoredMemory:
    """Insert a memory. Tag normalization and meta defaulting happen here."""
    normalized_tags = tuple(t.strip() for t in tags if t and t.strip())
    return await store.insert_memory(
        NewMemory(
            org_id=org_id,
            content=content,
            embedding=embedding,
            context=context,
            tags=normalized_tags,
            confidence=confidence,
            source=source,
            project=project,
            expires_at=expires_at,
            meta=dict(meta or {}),
        )
    )


async def get_memory(
    store: Store, org_id: str, memory_id: str
) -> Optional[StoredMemory]:
    return await store.get_memory(org_id, memory_id)


async def update_memory(
    store: Store,
    *,
    org_id: str,
    memory_id: str,
    content: Optional[str] = None,
    context: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    confidence: Optional[float] = None,
    source: Optional[str] = None,
    project: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    meta: Optional[Mapping[str, Any]] = None,
) -> StoredMemory:
    patch = MemoryPatch(
        content=content,
        context=context,
        tags=tuple(tags) if tags is not None else None,
        confidence=confidence,
        source=source,
        project=project,
        expires_at=expires_at,
        meta=dict(meta) if meta is not None else None,
    )
    return await store.update_memory(org_id, memory_id, patch)


async def delete_memory(
    store: Store, *, org_id: str, memory_id: str
) -> bool:
    return await store.delete_memory(org_id, memory_id)


async def list_memories(
    store: Store,
    *,
    org_id: str,
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    include_expired: bool = False,
) -> Sequence[StoredMemory]:
    return await store.list_memories(
        MemoryFilter(
            org_id=org_id,
            project=project,
            type=type,
            tier=tier,
            tags=tuple(tags) if tags is not None else None,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
            include_expired=include_expired,
        )
    )


async def search_memories(
    store: Store,
    *,
    org_id: str,
    query_vec: Sequence[float],
    limit: int = 5,
    min_score: float = 0.3,
    project: Optional[str] = None,
    half_life_days: int = 30,
) -> Sequence[ScoredMemory]:
    return await store.recall_by_embedding(
        RecallParams(
            org_id=org_id,
            query_vec=query_vec,
            limit=limit,
            min_score=min_score,
            project=project,
            half_life_days=half_life_days,
        )
    )


async def vote_memory(
    store: Store, *, org_id: str, memory_id: str, direction: str
) -> StoredMemory:
    return await store.vote_memory(org_id, memory_id, direction=direction)
