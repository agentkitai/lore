"""Contract tests for the Phase 6C hybrid-retrieval Store slice.

Covers:
* ``recall_by_text`` (PG ts_rank + SQLite bm25 / FTS5 virtual table)
* ``recall_by_entities`` (overlap counting)
* ``retrieval_profiles.fts_weight`` round-trip via NewProfile / ProfilePatch.

Runs against every Store implementation via the parametrized ``store`` fixture
in ``tests/persistence/conftest.py``.
"""

from __future__ import annotations

from typing import Sequence

import pytest

from lore.persistence import (
    NewEntity,
    NewMemory,
    NewMention,
    NewProfile,
    ProfilePatch,
    Store,
)


def _vec(seed: int) -> Sequence[float]:
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


# ── recall_by_text ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_by_text_round_trip(store: Store):
    """FTS picks up matching memories and excludes non-matches."""
    await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="kubernetes ingress troubleshooting",
            embedding=_vec(1),
        )
    )
    await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="postgres backup restoration",
            embedding=_vec(2),
        )
    )
    await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="kubernetes pods crashlooping fix",
            context="kubernetes is a container orchestrator",
            embedding=_vec(3),
        )
    )
    rows = await store.recall_by_text("solo", "kubernetes", limit=10)
    contents = [m.content for m, _ in rows]
    assert any("kubernetes ingress" in c for c in contents)
    assert any("kubernetes pods" in c for c in contents)
    assert not any("postgres" in c for c in contents)
    assert all(score > 0 for _, score in rows)


@pytest.mark.asyncio
async def test_recall_by_text_empty_query_returns_empty(store: Store):
    """Empty / whitespace queries short-circuit to []."""
    rows = await store.recall_by_text("solo", "", limit=10)
    assert list(rows) == []
    rows = await store.recall_by_text("solo", "   ", limit=10)
    assert list(rows) == []


@pytest.mark.asyncio
async def test_recall_by_text_respects_org_isolation(store: Store):
    await store.insert_memory(
        NewMemory(org_id="org_a", content="alpha-only docs", embedding=_vec(10))
    )
    rows = await store.recall_by_text("org_b", "alpha", limit=10)
    assert list(rows) == []


@pytest.mark.asyncio
async def test_recall_by_text_respects_project_filter(store: Store):
    await store.insert_memory(
        NewMemory(
            org_id="solo", content="foo backend service", project="backend",
            embedding=_vec(40),
        )
    )
    await store.insert_memory(
        NewMemory(
            org_id="solo", content="foo frontend ui", project="frontend",
            embedding=_vec(41),
        )
    )
    backend_rows = await store.recall_by_text("solo", "foo", limit=10, project="backend")
    contents = [m.content for m, _ in backend_rows]
    assert any("backend" in c for c in contents)
    assert not any("frontend" in c for c in contents)


# ── recall_by_entities ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_by_entities_counts_overlap(store: Store):
    """Memory hit by 2 entities outranks one hit by a single entity."""
    m1 = await store.insert_memory(
        NewMemory(org_id="solo", content="touched by both entities", embedding=_vec(20))
    )
    m2 = await store.insert_memory(
        NewMemory(org_id="solo", content="touched by one entity", embedding=_vec(21))
    )
    e1 = await store.upsert_entity(NewEntity(name="kubernetes", entity_type="tech"))
    e2 = await store.upsert_entity(NewEntity(name="postgres", entity_type="tech"))
    await store.save_mention(NewMention(entity_id=e1.id, memory_id=m1.id))
    await store.save_mention(NewMention(entity_id=e2.id, memory_id=m1.id))
    await store.save_mention(NewMention(entity_id=e1.id, memory_id=m2.id))

    rows = await store.recall_by_entities("solo", [e1.id, e2.id], limit=10)
    by_id = {m.id: cnt for m, cnt in rows}
    assert by_id[m1.id] == 2
    assert by_id[m2.id] == 1
    assert rows[0][0].id == m1.id


@pytest.mark.asyncio
async def test_recall_by_entities_empty_input(store: Store):
    assert list(await store.recall_by_entities("solo", [], limit=10)) == []


@pytest.mark.asyncio
async def test_recall_by_entities_respects_org_isolation(store: Store):
    """Entities are global but the memory's org_id must match the requested org."""
    m_other = await store.insert_memory(
        NewMemory(org_id="org_a", content="other-org memory", embedding=_vec(30))
    )
    e = await store.upsert_entity(NewEntity(name="iso-entity", entity_type="tech"))
    await store.save_mention(NewMention(entity_id=e.id, memory_id=m_other.id))

    # Calling for "solo" should not see the org_a memory.
    rows = await store.recall_by_entities("solo", [e.id], limit=10)
    assert all(m.id != m_other.id for m, _ in rows)


# ── fts_weight round-trip ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_fts_weight_round_trip(store: Store):
    """Phase 6C added ``fts_weight`` — round-trip via create + get + update."""
    created = await store.create_profile(
        NewProfile(
            org_id="org_a",
            name="hybrid-test",
            fts_weight=2.5,
            semantic_weight=1.5,
        )
    )
    assert created.fts_weight == pytest.approx(2.5)

    fetched = await store.get_profile(created.id)
    assert fetched is not None
    assert fetched.fts_weight == pytest.approx(2.5)

    updated = await store.update_profile(created.id, ProfilePatch(fts_weight=0.25))
    assert updated is not None
    assert updated.fts_weight == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_profile_fts_weight_default_is_one(store: Store):
    """Profiles created without fts_weight default to 1.0 (NOT NULL DB default)."""
    created = await store.create_profile(
        NewProfile(org_id="org_a", name="no-fts-weight-set")
    )
    assert created.fts_weight == pytest.approx(1.0)
