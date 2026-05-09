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

from typing import Awaitable, Callable, Optional, Sequence

from lore.persistence import NewObservation, Store, StoredMemory
from lore.services.memories import create_memory

EmbedFn = Callable[[str], Awaitable[Sequence[float]]]


def _classify_kind(tags: Sequence[str], captured_by: str) -> Optional[str]:
    """Phase 6G — derive ``meta.kind`` from tags + capture provenance.

    Returns the value to set at ``meta['kind']``, or ``None`` if no kind
    should be recorded. The mapping:

    * ``tags`` contains ``"intent"``         → ``"intent"``
    * ``tags`` contains ``"session-summary"`` → ``"summary"``
    * else, ``captured_by == "auto"``        → ``"tool"``
    * else (manual)                           → ``None`` (don't auto-classify)

    Tag matches win over the auto/manual default — a manual observation
    explicitly tagged ``intent`` still gets ``meta.kind='intent'``.
    """
    tag_set = {t for t in tags if isinstance(t, str)}
    if "intent" in tag_set:
        return "intent"
    if "session-summary" in tag_set:
        return "summary"
    if captured_by == "auto":
        return "tool"
    return None


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
    meta: dict = {
        "type": "observation",
        "title": obs.title,
        "facts": list(obs.facts),
        "narrative": obs.narrative,
        "captured_by": obs.captured_by,
    }
    if obs.session_id is not None:
        meta["session_id"] = obs.session_id

    # Phase 6G — classify the observation's intent/summary/tool kind from
    # the tags the (sub)agent supplied. Manual observations without a
    # special tag stay unclassified.
    kind = _classify_kind(obs.tags, obs.captured_by)
    if kind is not None:
        meta["kind"] = kind

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
        scope=obs.scope,
        importance_score=0.5,
    )
