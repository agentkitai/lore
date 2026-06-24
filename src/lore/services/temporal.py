"""Temporal service — Phase 6F memory supersession + at-time queries.

Thin orchestration layer over the SupersessionOps slice of the Store. The
HTTP route, MCP tools, and the dream / capture subagents all funnel
through these helpers so the audit-log shape and at-time semantics stay
consistent.

Concept summary:

  * A memory is "superseded" iff its LATEST row in ``memory_supersessions``
    has ``superseded_by IS NOT NULL``. A row with ``superseded_by IS NULL``
    explicitly *un*-supersedes (keeps the audit trail; flips current state).
  * Re-supersession appends a new row instead of mutating the previous one,
    so the chain is monotonic from an audit-trail standpoint.
  * ``at_time`` queries treat ``ts > at`` events as not-yet-known: a memory
    that is currently superseded but wasn't at ``at`` shows up.

See docs/superpowers/specs/2026-05-07-lore-temporal-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from lore.persistence import Store, StoredMemory
from lore.persistence.types import (
    StoredRelationshipSupersession,
    StoredSupersession,
)


async def supersede_memory(
    store: Store,
    memory_id: str,
    *,
    superseded_by: Optional[str],
    reason: Optional[str] = None,
    agent: str = "auto",
) -> None:
    """Append a supersession event to the audit log.

    ``superseded_by=None`` is allowed and represents an explicit
    un-supersession — the chain still records the event, but the memory
    is no longer considered superseded by ``is_superseded`` /
    ``are_superseded`` queries.
    """
    await store.record_supersession(
        memory_id,
        superseded_by=superseded_by,
        reason=reason,
        agent=agent,
    )


async def is_memory_superseded(
    store: Store,
    memory_id: str,
    *,
    at: Optional[datetime] = None,
) -> bool:
    """Convenience wrapper around ``Store.is_superseded``."""
    return await store.is_superseded(memory_id, at=at)


async def supersession_chain(
    store: Store,
    memory_id: str,
) -> Sequence[StoredSupersession]:
    """Full audit trail for a memory, oldest first."""
    return await store.get_supersession_chain(memory_id)


async def supersession_sources(
    store: Store,
    memory_id: str,
) -> Sequence[StoredSupersession]:
    """Inverse of ``supersession_chain``: events where ``memory_id``
    appears as ``superseded_by``. Used by the provenance endpoint to
    answer "what memories did this one consolidate from?".
    """
    return await store.list_supersession_sources(memory_id)


async def consolidate_memories(
    store: Store,
    *,
    org_id: str,
    source_ids: Sequence[str],
    new_memory_id: str,
    reason: Optional[str] = None,
    agent: str = "auto",
) -> int:
    """Record supersession events for every ``source_ids`` entry pointing
    at ``new_memory_id``. Returns the count of supersessions recorded.

    Caller must already have inserted the new memory and validated that
    every source belongs to ``org_id`` — this helper exists only to keep
    the audit-trail write path in one place so dream / classic-engine /
    HTTP all funnel through identical semantics.
    """
    count = 0
    for src_id in source_ids:
        if not src_id:
            continue
        await store.record_supersession(
            src_id,
            superseded_by=new_memory_id,
            reason=reason,
            agent=agent,
        )
        count += 1
    return count


async def memories_at_time(
    store: Store,
    org_id: str,
    *,
    at: datetime,
    entity_name: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
    requesting_user_id: Optional[str] = None,
) -> Sequence[StoredMemory]:
    """Memories that existed and were not superseded at the given timestamp.

    The store-level method does the heavy lifting (latest-row-per-memory
    subquery + optional entity_mentions JOIN); we normalize the timestamp
    and forward the call.
    """
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return await store.list_memories_at_time(
        org_id,
        at=at,
        entity_name=entity_name,
        type_filter=type_filter,
        limit=limit,
        requesting_user_id=requesting_user_id,
    )


# ── Bi-temporal facts (#67) ─────────────────────────────────────────────
#
# Lore stores "facts" (subject–predicate–object assertions) as graph
# relationships: entity[subject] --predicate--> entity[object]. Edges already
# carry a validity window (valid_from / valid_until), so as-of-date fact
# queries are a thin SPO-shaped view over query_relationships(at_time=...).
# supersede_relationship adds supersede-not-delete + an auditable correction
# chain, mirroring the memory supersession helpers above.


@dataclass(frozen=True, slots=True)
class FactAtTime:
    """A subject–predicate–object fact (a relationship edge) as it stood at a
    given timestamp. ``subject`` / ``object`` are resolved entity names."""

    relationship_id: str
    subject: str
    predicate: str
    object: str
    valid_from: Optional[datetime]
    valid_until: Optional[datetime]
    superseded_by: Optional[str]
    weight: float
    source_memory_id: Optional[str]


async def supersede_relationship(
    store: Store,
    relationship_id: str,
    *,
    superseded_by: str,
    reason: Optional[str] = None,
    agent: str = "auto",
) -> None:
    """Supersede-not-delete a fact edge: close its validity window, point it at
    the newer edge that replaced it, and append the correction to the
    ``relationship_supersessions`` audit log. See ``Store.supersede_relationship``.
    """
    await store.supersede_relationship(
        relationship_id,
        superseded_by=superseded_by,
        reason=reason,
        agent=agent,
    )


async def relationship_supersession_chain(
    store: Store,
    relationship_id: str,
) -> Sequence[StoredRelationshipSupersession]:
    """Full correction trail for a fact edge, oldest first."""
    return await store.get_relationship_supersession_chain(relationship_id)


async def facts_at_time(
    store: Store,
    *,
    entity: str,
    at: datetime,
    predicate: Optional[str] = None,
    direction: str = "both",
    limit: int = 50,
) -> list[FactAtTime]:
    """Subject–predicate–object facts involving ``entity`` that were valid at
    ``at`` — the relationship edges whose validity window contains ``at``
    (``valid_from <= at`` and ``valid_until`` is null or ``> at``), excluding
    ones superseded by then.

    A thin SPO-shaped view over ``query_relationships(at_time=...)`` with entity
    -name resolution. Returns ``[]`` if ``entity`` is unknown.
    """
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)

    ent = await store.get_entity_by_name(entity)
    if ent is None:
        normalized = entity.strip().lower()
        if normalized != entity:
            ent = await store.get_entity_by_name(normalized)
    if ent is None:
        return []

    rels = await store.query_relationships(
        [ent.id],
        direction=direction,
        at_time=at,
        rel_types=[predicate] if predicate else None,
    )

    names: dict[str, str] = {ent.id: ent.name}

    async def _name(entity_id: str) -> str:
        if entity_id not in names:
            other = await store.get_entity(entity_id)
            names[entity_id] = other.name if other is not None else entity_id
        return names[entity_id]

    facts: list[FactAtTime] = []
    for rel in rels[:limit]:
        facts.append(
            FactAtTime(
                relationship_id=rel.id,
                subject=await _name(rel.source_entity_id),
                predicate=rel.rel_type,
                object=await _name(rel.target_entity_id),
                valid_from=rel.valid_from,
                valid_until=rel.valid_until,
                superseded_by=rel.superseded_by,
                weight=rel.weight,
                source_memory_id=rel.source_memory_id,
            )
        )
    return facts
