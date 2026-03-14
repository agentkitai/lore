"""Tests for E2 S2/S3: Store layer since parameter."""

from __future__ import annotations

import pytest

from lore.store.memory import MemoryStore
from lore.types import Memory


def _make_memory(
    id: str,
    created_at: str,
    project: str | None = "lore",
) -> Memory:
    return Memory(
        id=id,
        content=f"Memory {id}",
        project=project,
        created_at=created_at,
        updated_at=created_at,
    )


class TestSqliteListSince:
    @pytest.fixture
    def store(self, tmp_path):
        str(tmp_path / "test.db")
        s = MemoryStore()
        # Insert memories at different times
        s.save(_make_memory("m1", "2026-03-14T08:00:00+00:00"))
        s.save(_make_memory("m2", "2026-03-14T12:00:00+00:00"))
        s.save(_make_memory("m3", "2026-03-14T16:00:00+00:00"))
        return s

    def test_since_filters_old(self, store):
        results = store.list(since="2026-03-14T10:00:00+00:00")
        ids = {m.id for m in results}
        assert "m1" not in ids
        assert "m2" in ids
        assert "m3" in ids

    def test_since_none_returns_all(self, store):
        results = store.list(since=None)
        assert len(results) == 3

    def test_since_with_project(self, store):
        store.save(_make_memory("m4", "2026-03-14T14:00:00+00:00", project="other"))
        results = store.list(since="2026-03-14T10:00:00+00:00", project="lore")
        ids = {m.id for m in results}
        assert ids == {"m2", "m3"}

    def test_since_with_limit(self, store):
        results = store.list(since="2026-03-14T08:00:00+00:00", limit=2)
        assert len(results) == 2
        # Should be newest first
        assert results[0].id == "m3"

    def test_since_inclusive(self, store):
        results = store.list(since="2026-03-14T12:00:00+00:00")
        ids = {m.id for m in results}
        assert "m2" in ids  # Exact match included


class TestMemoryStoreListSince:
    def test_since_filters(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "2026-03-14T08:00:00+00:00"))
        store.save(_make_memory("m2", "2026-03-14T12:00:00+00:00"))
        store.save(_make_memory("m3", "2026-03-14T16:00:00+00:00"))

        results = store.list(since="2026-03-14T10:00:00+00:00")
        ids = {m.id for m in results}
        assert "m1" not in ids
        assert "m2" in ids
        assert "m3" in ids

    def test_since_none_returns_all(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "2026-03-14T08:00:00+00:00"))
        store.save(_make_memory("m2", "2026-03-14T12:00:00+00:00"))
        assert len(store.list(since=None)) == 2

    def test_since_with_project(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "2026-03-14T12:00:00+00:00", project="lore"))
        store.save(_make_memory("m2", "2026-03-14T12:00:00+00:00", project="other"))
        results = store.list(since="2026-03-14T10:00:00+00:00", project="lore")
        assert len(results) == 1
        assert results[0].id == "m1"
