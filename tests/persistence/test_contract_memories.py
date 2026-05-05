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
