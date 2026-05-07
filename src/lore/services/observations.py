"""Observation service — Phase 6B.

Thin wrapper around ``services.memories.create_memory`` that produces a
memory with a ``type='observation'`` discriminator and a structured
``{title, facts, narrative}`` payload in ``meta``. The narrative is
written to the ``content`` column so existing semantic recall surfaces
observations on the same axis as polished memories. The title is stored
in the ``context`` column for convenience.

Observations are the bulk-write tier consumed by the Phase 6A
auto-capture subagent. They share the ``memories`` table with polished
memories — there is no separate observations table.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Sequence

from lore.persistence import NewObservation, Store, StoredMemory
from lore.services.memories import create_memory

EmbedFn = Callable[[str], Awaitable[Sequence[float]]]


async def create_observation(
    store: Store,
    obs: NewObservation,
    embed: EmbedFn,
) -> StoredMemory:
    """Persist an observation. Returns the StoredMemory row.

    The embedding is computed from ``f"{title}\\n{narrative}"`` so search
    can match either the headline label or the prose context. The
    ``content`` column receives the narrative; the ``context`` column
    receives the title. The structured payload is duplicated in
    ``meta`` for downstream readers (the Phase 6C FTS layer indexes
    ``meta.title`` and ``meta.facts`` directly).

    Discriminator: ``meta.type = "observation"``. This matches the
    existing in-database convention where Lore stores its memory-type
    discriminator at ``meta.type`` (NOT ``meta.memory_type``).
    """
    embedding = await embed(f"{obs.title}\n{obs.narrative}")
    meta = {
        "type": "observation",
        "title": obs.title,
        "facts": list(obs.facts),
        "narrative": obs.narrative,
        "captured_by": obs.captured_by,
    }
    if obs.session_id is not None:
        meta["session_id"] = obs.session_id

    return await create_memory(
        store,
        org_id=obs.org_id,
        content=obs.narrative,
        context=obs.title,
        embedding=embedding,
        tags=tuple(obs.tags),
        confidence=0.5,
        source=obs.source or "observation",
        project=obs.project,
        meta=meta,
    )
