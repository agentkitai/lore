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

from datetime import datetime, timezone
from typing import Optional, Sequence

from lore.persistence import Store, StoredMemory
from lore.persistence.types import StoredSupersession


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


async def memories_at_time(
    store: Store,
    org_id: str,
    *,
    at: datetime,
    entity_name: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
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
    )
