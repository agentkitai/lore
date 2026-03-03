"""Tests for Lore SqliteStore."""

from __future__ import annotations

import os
import struct
import tempfile
from typing import Generator

import pytest

from lore.memory_store.sqlite import SqliteStore
from lore.types import Memory

TS = "2026-01-01T00:00:00+00:00"


def _embed(dim: int = 384) -> bytes:
    """Create a trivial embedding (all 0.1) as bytes."""
    vec = [0.1] * dim
    return struct.pack(f"{dim}f", *vec)


def _make_memory(
    id: str = "01",
    content: str = "test content",
    **kwargs,
) -> Memory:
    defaults = dict(
        id=id,
        content=content,
        type="note",
        tags=[],
        metadata={},
        embedding=_embed(),
        created_at=TS,
        updated_at=TS,
    )
    defaults.update(kwargs)
    return Memory(**defaults)


@pytest.fixture
def store() -> Generator[SqliteStore, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        s = SqliteStore(os.path.join(tmpdir, "test.db"))
        yield s
        s.close()


class TestSqliteStoreBasic:
    def test_save_and_get(self, store: SqliteStore) -> None:
        m = _make_memory()
        store.save(m)
        got = store.get("01")
        assert got is not None
        assert got.content == "test content"
        assert got.type == "note"

    def test_get_nonexistent(self, store: SqliteStore) -> None:
        assert store.get("nope") is None

    def test_delete(self, store: SqliteStore) -> None:
        store.save(_make_memory())
        assert store.delete("01") is True
        assert store.get("01") is None

    def test_delete_nonexistent(self, store: SqliteStore) -> None:
        assert store.delete("nope") is False

    def test_tags_roundtrip(self, store: SqliteStore) -> None:
        store.save(_make_memory(tags=["python", "testing"]))
        got = store.get("01")
        assert got is not None
        assert got.tags == ["python", "testing"]

    def test_metadata_roundtrip(self, store: SqliteStore) -> None:
        store.save(_make_memory(metadata={"confidence": 0.9, "source": "test"}))
        got = store.get("01")
        assert got is not None
        assert got.metadata["confidence"] == 0.9


class TestSqliteStoreList:
    def test_list_empty(self, store: SqliteStore) -> None:
        memories, total = store.list()
        assert memories == []
        assert total == 0

    def test_list_returns_all(self, store: SqliteStore) -> None:
        store.save(_make_memory("a", created_at=TS))
        store.save(_make_memory("b", created_at="2026-01-02T00:00:00+00:00"))
        memories, total = store.list()
        assert total == 2
        assert len(memories) == 2
        # Newest first
        assert memories[0].id == "b"

    def test_list_filter_by_project(self, store: SqliteStore) -> None:
        store.save(_make_memory("a", project="foo"))
        store.save(_make_memory("b", project="bar"))
        memories, total = store.list(project="foo")
        assert len(memories) == 1
        assert memories[0].id == "a"

    def test_list_filter_by_type(self, store: SqliteStore) -> None:
        store.save(_make_memory("a", type="lesson"))
        store.save(_make_memory("b", type="note"))
        memories, _ = store.list(type="lesson")
        assert len(memories) == 1
        assert memories[0].id == "a"

    def test_list_with_limit(self, store: SqliteStore) -> None:
        for i in range(5):
            ts = f"2026-01-0{i + 1}T00:00:00+00:00"
            store.save(_make_memory(str(i), created_at=ts))
        memories, total = store.list(limit=2)
        assert len(memories) == 2
        assert total == 5

    def test_list_with_offset(self, store: SqliteStore) -> None:
        for i in range(5):
            ts = f"2026-01-0{i + 1}T00:00:00+00:00"
            store.save(_make_memory(str(i), created_at=ts))
        memories, total = store.list(limit=2, offset=2)
        assert len(memories) == 2
        assert total == 5


class TestSqliteStoreSearch:
    def test_search_returns_results(self, store: SqliteStore) -> None:
        store.save(_make_memory("a", content="Python error handling"))
        store.save(_make_memory("b", content="JavaScript async patterns"))
        # Use the same embedding for query
        query_vec = [0.1] * 384
        results = store.search(embedding=query_vec, limit=5)
        assert len(results) == 2
        # Scores should be positive
        assert all(r.score >= 0 for r in results)

    def test_search_respects_limit(self, store: SqliteStore) -> None:
        for i in range(5):
            store.save(_make_memory(str(i), content=f"content {i}"))
        results = store.search(embedding=[0.1] * 384, limit=2)
        assert len(results) == 2

    def test_search_filter_by_project(self, store: SqliteStore) -> None:
        store.save(_make_memory("a", content="hello", project="p1"))
        store.save(_make_memory("b", content="world", project="p2"))
        results = store.search(embedding=[0.1] * 384, project="p1")
        assert len(results) == 1
        assert results[0].memory.id == "a"

    def test_search_skips_no_embedding(self, store: SqliteStore) -> None:
        store.save(_make_memory("a", embedding=None))
        results = store.search(embedding=[0.1] * 384)
        assert len(results) == 0


class TestSqliteStoreBulkDelete:
    def test_delete_by_type(self, store: SqliteStore) -> None:
        store.save(_make_memory("a", type="lesson"))
        store.save(_make_memory("b", type="note"))
        deleted = store.delete_by_filter(type="lesson")
        assert deleted == 1
        assert store.get("a") is None
        assert store.get("b") is not None

    def test_delete_by_project(self, store: SqliteStore) -> None:
        store.save(_make_memory("a", project="old"))
        store.save(_make_memory("b", project="new"))
        deleted = store.delete_by_filter(project="old")
        assert deleted == 1


class TestSqliteStoreStats:
    def test_empty_stats(self, store: SqliteStore) -> None:
        s = store.stats()
        assert s.total_count == 0
        assert s.count_by_type == {}
        assert s.count_by_project == {}

    def test_populated_stats(self, store: SqliteStore) -> None:
        store.save(_make_memory("a", type="note", project="p1"))
        store.save(_make_memory("b", type="lesson", project="p1"))
        store.save(_make_memory("c", type="note", project="p2"))
        s = store.stats()
        assert s.total_count == 3
        assert s.count_by_type["note"] == 2
        assert s.count_by_type["lesson"] == 1
        assert s.count_by_project["p1"] == 2
        assert s.oldest_memory is not None
        assert s.newest_memory is not None


class TestSqliteStoreContextManager:
    def test_context_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with SqliteStore(os.path.join(tmpdir, "test.db")) as store:
                store.save(_make_memory())
                assert store.get("01") is not None
