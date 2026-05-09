"""Lessons service — wire-shape preservation over MemoryOps.

Lessons are memories. Migration 009 made the `lessons` table a view backed
by `memories` with column aliases (problem→content, resolution→context).
This service wraps MemoryOps with field translation at the boundary.

Notable behaviors:
- The wire-level `context` field on LessonCreateRequest is preserved for
  backward compatibility but never stored (the underlying memories.context
  receives `body.resolution`, not `body.context`). The pre-1H route had
  this same no-op semantics.
- Vote updates ("+1"/"-1" strings or absolute ints) are NOT atomic across
  the two-call pattern (update_memory + vote_memory or fetch-then-set for
  absolute). Concurrency relaxation vs. the pre-1H single-UPDATE.
- Phase 1H scope cut: only "+1" string votes are supported via atomic
  `vote_memory`. "-1" and absolute-int modes raise ValueError.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence, Tuple, Union

from ulid import ULID

from lore.persistence import (
    ExportedMemory,
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    Store,
    StoredMemory,
)
from lore.persistence.exceptions import StoreNotFoundError

logger = logging.getLogger(__name__)


# Type-specific decay half-lives (days), matching DECAY_HALF_LIVES in lore.types.
_HALF_LIVES = {
    "code": 14,
    "note": 21,
    "lesson": 30,
    "convention": 60,
}
_HALF_LIFE_DEFAULT = 30


def _half_life_for(meta: Mapping[str, Any]) -> int:
    return _HALF_LIVES.get(str(meta.get("type", "")), _HALF_LIFE_DEFAULT)


def _project_match(stored: StoredMemory, project: Optional[str]) -> bool:
    """Project-scope check: when project is None, always matches; else require equality."""
    if project is None:
        return True
    return stored.project == project


async def create(
    store: Store,
    *,
    org_id: str,
    problem: str,
    resolution: Optional[str],
    context: Optional[str],  # intentionally unused — wire-compat only
    tags: Optional[Sequence[str]],
    source: Optional[str],
    project: Optional[str],
    embedding: Optional[Sequence[float]],
    expires_at: Optional[datetime],
    meta: Optional[Mapping[str, Any]],
    scope: Optional[str] = None,
) -> str:
    """Insert a lesson (memory) with field translation.

    `problem` maps to content, `resolution` maps to context.
    The `context` arg is intentionally ignored — matches pre-1H no-op behavior.
    Returns the new memory id.

    Phase 6G: ``scope`` is the project-vs-global discriminator. When ``None``
    (the default), the service derives it from ``meta.type`` via
    ``default_scope_for_type`` — universal types (lesson/preference/pattern/
    convention) become 'global', everything else stays 'project'.
    """
    # Local import to avoid a circular import between services.lessons and
    # services.memories at module-load time.
    from lore.services.memories import default_scope_for_type

    effective_scope = (
        scope
        if scope is not None
        else default_scope_for_type((meta or {}).get("type") if meta else None)
    )
    nm = NewMemory(
        org_id=org_id,
        content=problem,
        context=resolution if resolution is not None else "",
        tags=tuple(tags or ()),
        source=source,
        project=project,
        embedding=embedding if embedding else [0.0] * 384,
        expires_at=expires_at,
        meta=dict(meta or {}),
        scope=effective_scope,
    )
    stored = await store.insert_memory(nm)
    return stored.id


async def search(
    store: Store,
    *,
    org_id: str,
    embedding: Sequence[float],
    project: Optional[str],
    tags: Optional[Sequence[str]],
    limit: int,
    min_score: float,
    scope_mode: str = "default",
) -> list[dict]:
    """Vector recall with time-decay re-ranking.

    Fetches a wider candidate pool (max(50, limit*5)) to give decay re-ranking
    room to work, then scores, filters, sorts, and trims to `limit`.

    The route layer translates content→problem and context→resolution for the
    response wire shape.

    Phase 6G: ``scope_mode`` is forwarded to ``recall_by_embedding`` so the
    project-vs-global predicate runs at the SQL layer alongside the
    per-row post-filter.
    """
    wider_limit = max(50, limit * 5)
    params = RecallParams(
        org_id=org_id,
        query_vec=embedding,
        limit=wider_limit,
        project=project,
        scope_mode=scope_mode,
    )
    candidates = await store.recall_by_embedding(params)

    # Post-filter by project and tags (RecallParams doesn't expose these fields)
    tag_set = set(tags) if tags else None
    results = []
    now = datetime.now(timezone.utc)

    for m in candidates:
        # Project filter
        if project is not None and m.project != project:
            continue
        # Tags filter (all requested tags must be present)
        if tag_set and not tag_set.issubset(set(m.tags)):
            continue

        age_created_days = max(0.0, (now - m.created_at).total_seconds() / 86400.0)
        last_acc = m.last_accessed_at or m.created_at
        age_accessed_days = max(0.0, (now - last_acc).total_seconds() / 86400.0)
        effective_age = min(age_created_days, age_accessed_days)
        half_life = _half_life_for(m.meta)
        time_decay = 0.5 ** (effective_age / half_life)
        final_score = m.score * time_decay

        if final_score < min_score:
            continue

        results.append({
            "id": m.id,
            "content": m.content,
            "context": m.context,
            "tags": list(m.tags),
            "source": m.source,
            "project": m.project,
            "created_at": m.created_at,
            "updated_at": m.updated_at,
            "expires_at": m.expires_at,
            "upvotes": m.upvotes,
            "downvotes": m.downvotes,
            "meta": dict(m.meta),
            "score": round(max(final_score, 0.0), 6),
        })

    # Sort by score descending, then take top `limit`
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


async def record_access(
    store: Store,
    *,
    org_id: str,
    lesson_id: str,
    project: Optional[str],
) -> dict:
    """Record an access event on a lesson. Returns a summary dict.

    Raises StoreNotFoundError on missing lesson or project mismatch.
    """
    existing = await store.get_memory(org_id, lesson_id)
    if existing is None or not _project_match(existing, project):
        raise StoreNotFoundError("memories", lesson_id)

    updated = await store.record_memory_access(org_id, lesson_id)
    if updated is None:
        raise StoreNotFoundError("memories", lesson_id)

    return {
        "id": updated.id,
        "access_count": updated.access_count,
        "last_accessed_at": updated.last_accessed_at,
    }


async def get(
    store: Store,
    *,
    org_id: str,
    lesson_id: str,
    project: Optional[str],
) -> StoredMemory:
    """Fetch a lesson by id with project-scope check.

    Raises StoreNotFoundError on missing or project mismatch.
    """
    existing = await store.get_memory(org_id, lesson_id)
    if existing is None or not _project_match(existing, project):
        raise StoreNotFoundError("memories", lesson_id)
    return existing


async def update(
    store: Store,
    *,
    org_id: str,
    lesson_id: str,
    project: Optional[str],
    tags: Optional[Sequence[str]],
    meta: Optional[Mapping[str, Any]],
    upvotes: Optional[Union[str, int]],
    downvotes: Optional[Union[str, int]],
) -> StoredMemory:
    """Patch a lesson's fields and/or record vote increments.

    Phase 1H scope cut:
    - "+1" upvotes/downvotes → atomic increment via vote_memory.
    - "-1" and absolute-int modes → ValueError (behavioral regression vs. pre-1H).
    - At least one field must be provided or a ValueError is raised.

    Returns the final StoredMemory (last operation's result).
    """
    existing = await store.get_memory(org_id, lesson_id)
    if existing is None or not _project_match(existing, project):
        raise StoreNotFoundError("memories", lesson_id)

    patch = MemoryPatch(
        tags=tuple(tags) if tags is not None else None,
        meta=dict(meta) if meta is not None else None,
    )

    has_non_vote_patch = any(
        getattr(patch, f) is not None for f in ("tags", "meta")
    )

    # Validate vote modes early, before any writes
    def _check_vote(value: Optional[Union[str, int]], field_name: str) -> None:
        if value is None:
            return
        if value == "+1":
            return  # supported
        raise ValueError(
            f"Vote update mode {value!r} for {field_name!r} is not supported in "
            f"this version. Use '+1' string for atomic increment."
        )

    _check_vote(upvotes, "upvotes")
    _check_vote(downvotes, "downvotes")

    has_upvote = upvotes == "+1"
    has_downvote = downvotes == "+1"

    if not has_non_vote_patch and not has_upvote and not has_downvote:
        raise ValueError("No fields to update")

    result: StoredMemory = existing

    if has_non_vote_patch:
        result = await store.update_memory(org_id, lesson_id, patch)

    if has_upvote:
        result = await store.vote_memory(org_id, lesson_id, direction="up")

    if has_downvote:
        result = await store.vote_memory(org_id, lesson_id, direction="down")

    return result


async def delete(
    store: Store,
    *,
    org_id: str,
    lesson_id: str,
    project: Optional[str],
) -> None:
    """Delete a lesson by id with project-scope check.

    Raises StoreNotFoundError on missing or project mismatch.
    """
    existing = await store.get_memory(org_id, lesson_id)
    if existing is None or not _project_match(existing, project):
        raise StoreNotFoundError("memories", lesson_id)
    await store.delete_memory(org_id, lesson_id)


async def list_lessons(
    store: Store,
    *,
    org_id: str,
    project: Optional[str],
    query: Optional[str],
    category: Optional[str],
    since: Optional[datetime],
    min_reputation: Optional[int],
    limit: int,
    offset: int,
) -> Tuple[int, Sequence[StoredMemory]]:
    """List lessons with pagination.

    Returns (total_count, page_of_rows).
    `category` maps to a single tag filter.
    """
    f = MemoryFilter(
        org_id=org_id,
        project=project,
        since=since,
        text_query=query,
        min_reputation=min_reputation,
        tags=tuple([category]) if category else None,
    )
    return await store.list_memories_paginated(f, limit=limit, offset=offset)


async def export(
    store: Store,
    *,
    org_id: str,
    project: Optional[str],
) -> Sequence[ExportedMemory]:
    """Export all lessons (including embeddings) for the given org/project scope."""
    f = MemoryFilter(org_id=org_id, project=project)
    return await store.list_memories_with_embeddings(f)


async def import_lessons(
    store: Store,
    *,
    org_id: str,
    lessons: Sequence[Any],
    project_override: Optional[str],
) -> int:
    """Upsert a batch of lesson records.

    Each lesson must have: id, problem, resolution, tags, source,
    project, embedding, expires_at, upvotes, downvotes, meta.

    Returns the count of items processed.
    """
    count = 0
    for lesson in lessons:
        memory_id = lesson.id or str(ULID())
        project = project_override if project_override is not None else lesson.project
        await store.upsert_memory_with_embedding(
            memory_id=memory_id,
            org_id=org_id,
            content=lesson.problem,
            context=lesson.resolution if lesson.resolution else "",
            tags=tuple(lesson.tags or ()),
            source=lesson.source,
            project=project,
            embedding=list(lesson.embedding) if lesson.embedding else None,
            expires_at=lesson.expires_at,
            upvotes=lesson.upvotes or 0,
            downvotes=lesson.downvotes or 0,
            meta=dict(lesson.meta or {}),
        )
        count += 1
    return count
