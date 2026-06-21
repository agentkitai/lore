"""Memory CRUD + search service functions.

Pure async functions: take a Store and typed params, return dataclasses.
Routes and AsyncLore both call into here.
"""

from __future__ import annotations

import logging
import os
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
from lore.persistence.exceptions import StoreNotFoundError
from lore.redact.write import get_write_redactor, redact_for_write

logger = logging.getLogger(__name__)


# Phase 6G: types whose default scope is 'global' — universal lessons that
# apply regardless of which repo you're in. Everything else (notes, facts,
# observations, etc.) defaults to 'project' so it stays scoped to the repo
# it was captured in. Manual ``remember()`` callers can override via the
# explicit ``scope=`` parameter.
GLOBAL_TYPES = frozenset({"lesson", "preference", "pattern", "convention"})


def default_scope_for_type(t: Optional[str]) -> str:
    """Return the default scope ('global' or 'project') for a memory type."""
    return "global" if (t or "") in GLOBAL_TYPES else "project"


async def create_memory(
    store: Store,
    *,
    org_id: str,
    content: str,
    embedding: Sequence[float],
    context: Optional[str] = None,
    tags: Sequence[str] = (),
    source: Optional[str] = None,
    project: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    meta: Optional[Mapping[str, Any]] = None,
    scope: Optional[str] = None,
    user_id: Optional[str] = None,
) -> StoredMemory:
    """Insert a memory. Tag normalization and meta defaulting happen here.

    Phase 6G: ``scope`` is the project-vs-global discriminator. When the
    caller passes ``None`` (the default), the scope is derived from
    ``meta.get('type')`` via ``default_scope_for_type`` — universal types
    (lesson/preference/pattern/convention) become 'global', everything else
    stays 'project'. Pass ``scope='project'`` or ``scope='global'`` to
    override the type-based default.
    """
    # Write-side redaction (LORE_REDACT_* config): the single chokepoint every
    # server/AsyncLore/internal write funnels through. Masks secrets/PII/
    # denylisted terms + tags meta.redacted; raises SecretBlockedError only in
    # block mode. ponytail: the caller-supplied embedding may reflect
    # pre-redaction text — the secret value itself is never stored; re-embed in
    # the caller if exact vector/text parity matters.
    content, context, redaction_meta = redact_for_write(
        get_write_redactor(), content, context
    )
    meta_dict = dict(meta or {})
    meta_dict.update(redaction_meta)
    normalized_tags = tuple(t.strip() for t in tags if t and t.strip())
    effective_scope = (
        scope
        if scope is not None
        else default_scope_for_type(meta_dict.get("type"))
    )
    return await store.insert_memory(
        NewMemory(
            org_id=org_id,
            content=content,
            embedding=embedding,
            context=context,
            tags=normalized_tags,
            source=source,
            project=project,
            expires_at=expires_at,
            meta=meta_dict,
            scope=effective_scope,
            user_id=user_id,
        )
    )


async def get_memory(
    store: Store,
    org_id: str,
    memory_id: str,
    *,
    requesting_user_id: Optional[str] = None,
) -> Optional[StoredMemory]:
    return await store.get_memory(
        org_id, memory_id, requesting_user_id=requesting_user_id
    )


async def update_memory(
    store: Store,
    *,
    org_id: str,
    memory_id: str,
    content: Optional[str] = None,
    context: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    source: Optional[str] = None,
    project: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    meta: Optional[Mapping[str, Any]] = None,
) -> StoredMemory:
    # Redact updated content/context too (no-op when neither is being patched).
    content, context, _redaction_meta = redact_for_write(
        get_write_redactor(), content, context
    )
    patch = MemoryPatch(
        content=content,
        context=context,
        tags=tuple(tags) if tags is not None else None,
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
    requesting_user_id: Optional[str] = None,
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
            requesting_user_id=requesting_user_id,
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
    scope_mode: str = "default",
    requesting_user_id: Optional[str] = None,
) -> Sequence[ScoredMemory]:
    return await store.recall_by_embedding(
        RecallParams(
            org_id=org_id,
            query_vec=query_vec,
            limit=limit,
            min_score=min_score,
            project=project,
            half_life_days=half_life_days,
            scope_mode=scope_mode,
            requesting_user_id=requesting_user_id,
        )
    )


async def vote_memory(
    store: Store, *, org_id: str, memory_id: str, direction: str
) -> StoredMemory:
    return await store.vote_memory(org_id, memory_id, direction=direction)


async def enrich_memory_async(
    store: Store,
    *,
    memory_id: str,
    content: str,
    context: Optional[str],
) -> None:
    """Run the LLM enrichment pipeline on a memory and persist the result. Fire-and-forget.

    Lifted from the pre-1E `_enrich_memory` helper in routes/memories.py.
    Errors are logged and swallowed.
    """
    try:
        from lore.enrichment.llm import LLMClient
        from lore.enrichment.pipeline import EnrichmentPipeline

        model = os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")
        client = LLMClient(model=model)
        pipeline = EnrichmentPipeline(client)

        result = pipeline.enrich(content, context=context)
        if result is None:
            return

        await store.enrich_memory_meta(memory_id, result)
    except Exception:
        logger.warning("Failed to enrich memory %s", memory_id, exc_info=True)


async def record_memory_access(
    store: Store,
    org_id: str,
    memory_id: str,
) -> StoredMemory:
    """Record an access event on a memory and return the updated row.

    Raises StoreNotFoundError if the memory doesn't exist or doesn't
    belong to the requested org.
    """
    updated = await store.record_memory_access(org_id, memory_id)
    if updated is None:
        raise StoreNotFoundError("memories", memory_id)
    return updated
