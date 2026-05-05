"""Contract tests for the MemoryOps slice of Store.

These tests run against every Store implementation (Phase 1A: Postgres only).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

import pytest

from lore.persistence import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    Store,
    StoredMemory,
)
from lore.persistence.exceptions import StoreNotFound


def _vec(seed: int) -> Sequence[float]:
    """Deterministic 384-dim vector seeded by an int."""
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


@pytest.mark.asyncio
async def test_insert_and_get_round_trip(store: Store):
    nm = NewMemory(
        org_id="solo",
        content="how to use pgvector with asyncpg",
        embedding=_vec(1),
        tags=("postgres", "vectors"),
        project="lore",
        confidence=0.9,
        meta={"type": "lesson"},
    )
    inserted = await store.insert_memory(nm)
    assert isinstance(inserted, StoredMemory)
    assert inserted.id
    assert inserted.content == nm.content
    assert tuple(inserted.tags) == ("postgres", "vectors")
    assert inserted.confidence == pytest.approx(0.9)

    fetched = await store.get_memory("solo", inserted.id)
    assert fetched is not None
    assert fetched.id == inserted.id
    assert fetched.content == nm.content


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(store: Store):
    assert await store.get_memory("solo", "mem_does_not_exist") is None


@pytest.mark.asyncio
async def test_get_respects_org_isolation(store: Store):
    nm = NewMemory(org_id="org_a", content="alpha", embedding=_vec(2))
    inserted = await store.insert_memory(nm)
    # Fetching with a different org returns None
    assert await store.get_memory("org_b", inserted.id) is None
    # Fetching with the right org returns the row
    assert (await store.get_memory("org_a", inserted.id)) is not None


@pytest.mark.asyncio
async def test_update_memory_partial(store: Store):
    inserted = await store.insert_memory(
        NewMemory(org_id="solo", content="original", embedding=_vec(3))
    )
    updated = await store.update_memory(
        "solo",
        inserted.id,
        MemoryPatch(content="rewritten", tags=("edited",)),
    )
    assert updated.content == "rewritten"
    assert tuple(updated.tags) == ("edited",)
    # Confidence not in patch → preserved
    assert updated.confidence == inserted.confidence


@pytest.mark.asyncio
async def test_update_memory_raises_when_missing(store: Store):
    with pytest.raises(StoreNotFound):
        await store.update_memory("solo", "mem_missing", MemoryPatch(content="x"))


@pytest.mark.asyncio
async def test_delete_memory(store: Store):
    inserted = await store.insert_memory(
        NewMemory(org_id="solo", content="to delete", embedding=_vec(4))
    )
    assert (await store.get_memory("solo", inserted.id)) is not None

    deleted = await store.delete_memory("solo", inserted.id)
    assert deleted is True

    assert (await store.get_memory("solo", inserted.id)) is None


@pytest.mark.asyncio
async def test_delete_returns_false_when_missing(store: Store):
    assert (await store.delete_memory("solo", "mem_missing")) is False


@pytest.mark.asyncio
async def test_list_memories_filters_by_project(store: Store):
    await store.insert_memory(
        NewMemory(org_id="solo", content="a", project="x", embedding=_vec(5))
    )
    await store.insert_memory(
        NewMemory(org_id="solo", content="b", project="y", embedding=_vec(6))
    )
    only_x = await store.list_memories(MemoryFilter(org_id="solo", project="x"))
    assert {m.content for m in only_x} == {"a"}


@pytest.mark.asyncio
async def test_list_memories_respects_limit_and_order(store: Store):
    import asyncio
    for i in range(3):
        await store.insert_memory(
            NewMemory(org_id="solo", content=f"item-{i}", embedding=_vec(10 + i))
        )
        await asyncio.sleep(0.01)
    rows = await store.list_memories(MemoryFilter(org_id="solo", limit=2))
    assert len(rows) == 2
    # ordered by created_at DESC
    assert rows[0].created_at >= rows[1].created_at


@pytest.mark.asyncio
async def test_list_memories_excludes_expired_by_default(store: Store):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = await store.insert_memory(
        NewMemory(org_id="solo", content="expired", embedding=_vec(20), expires_at=past)
    )
    fresh = await store.insert_memory(
        NewMemory(org_id="solo", content="fresh", embedding=_vec(21))
    )
    visible = await store.list_memories(MemoryFilter(org_id="solo"))
    ids = {m.id for m in visible}
    assert fresh.id in ids
    assert expired.id not in ids

    with_expired = await store.list_memories(
        MemoryFilter(org_id="solo", include_expired=True)
    )
    assert {m.id for m in with_expired} >= {fresh.id, expired.id}
