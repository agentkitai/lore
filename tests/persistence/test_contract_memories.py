"""Contract tests for the MemoryOps slice of Store.

These tests run against every Store implementation (Phase 1A: Postgres only).
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timedelta, timezone
from pathlib import Path as _Path
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
from lore.persistence.exceptions import StoreNotFoundError


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
    with pytest.raises(StoreNotFoundError):
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


_FIXTURES = _json.loads(
    (_Path(__file__).parent / "fixtures" / "embeddings.json").read_text()
)


@pytest.mark.asyncio
async def test_recall_by_embedding_returns_ranked_results(store: Store):
    # Insert 5 fixture memories
    inserted = []
    for i, item in enumerate(_FIXTURES[:5]):
        m = await store.insert_memory(
            NewMemory(
                org_id="solo",
                content=item["text"],
                embedding=item["embedding"],
            )
        )
        inserted.append((m, item))

    # Query with the embedding of the first item — it should rank #1
    target = inserted[0][1]
    results = await store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target["embedding"],
            limit=5,
            min_score=0.0,
        )
    )
    assert len(results) >= 1
    assert results[0].content == target["text"]
    # Score is in [0, 1] for cosine-similarity-derived score
    assert 0.0 <= results[0].score <= 1.0


@pytest.mark.asyncio
async def test_recall_respects_min_score(store: Store):
    target = _FIXTURES[0]
    other = _FIXTURES[5] if len(_FIXTURES) > 5 else _FIXTURES[1]
    await store.insert_memory(
        NewMemory(org_id="solo", content=target["text"], embedding=target["embedding"])
    )
    await store.insert_memory(
        NewMemory(org_id="solo", content=other["text"], embedding=other["embedding"])
    )
    # min_score=0.999 should exclude the unrelated entry
    results = await store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target["embedding"],
            limit=10,
            min_score=0.999,
        )
    )
    assert all(r.score >= 0.999 for r in results)


@pytest.mark.asyncio
async def test_expire_memories_deletes_past_expiry(store: Store):
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = await store.insert_memory(
        NewMemory(org_id="solo", content="expired", embedding=_vec(30), expires_at=past)
    )
    keep = await store.insert_memory(
        NewMemory(org_id="solo", content="alive", embedding=_vec(31))
    )
    n = await store.expire_memories()
    assert n >= 1
    assert (await store.get_memory("solo", expired.id)) is None
    assert (await store.get_memory("solo", keep.id)) is not None


@pytest.mark.asyncio
async def test_bump_access_counts_increments(store: Store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="popular", embedding=_vec(40))
    )
    assert m.access_count == 0
    await store.bump_access_counts("solo", [m.id])
    after = await store.get_memory("solo", m.id)
    assert after is not None
    assert after.access_count == 1
    assert after.last_accessed_at is not None


@pytest.mark.asyncio
async def test_bump_access_counts_cross_org_isolation(store: Store):
    """bump_access_counts must not touch rows belonging to a different org."""
    m = await store.insert_memory(
        NewMemory(org_id="org_a", content="org_a memory", embedding=_vec(41))
    )
    assert m.access_count == 0
    # Attempt to bump from org_b — should silently affect 0 rows
    await store.bump_access_counts("org_b", [m.id])
    after = await store.get_memory("org_a", m.id)
    assert after is not None
    assert after.access_count == 0


@pytest.mark.asyncio
async def test_update_memory_raises_when_expired(store: Store):
    """Updating an expired memory must raise StoreNotFoundError."""
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="stale", embedding=_vec(42), expires_at=past)
    )
    with pytest.raises(StoreNotFoundError):
        await store.update_memory("solo", m.id, MemoryPatch(content="too late"))


@pytest.mark.asyncio
async def test_vote_memory_up_and_down(store: Store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="rate me", embedding=_vec(50))
    )
    after_up = await store.vote_memory("solo", m.id, direction="up")
    assert after_up.upvotes == 1

    after_down = await store.vote_memory("solo", m.id, direction="down")
    assert after_down.downvotes == 1


@pytest.mark.asyncio
async def test_vote_memory_invalid_direction(store: Store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="x", embedding=_vec(51))
    )
    with pytest.raises(ValueError):
        await store.vote_memory("solo", m.id, direction="sideways")


@pytest.mark.asyncio
async def test_vote_memory_raises_when_missing(store: Store):
    with pytest.raises(StoreNotFoundError):
        await store.vote_memory("solo", "mem_missing", direction="up")
