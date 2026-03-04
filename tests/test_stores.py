"""Tests for MemoryStore and SqliteStore."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Generator, List

import pytest

from lore import Lore, Memory
from lore.store.base import Store
from lore.store.memory import MemoryStore
from lore.store.sqlite import SqliteStore


def _stub_embed(text: str) -> List[float]:
    """Trivial embedding function for tests that don't need real embeddings."""
    return [0.0] * 384


@pytest.fixture
def memory_store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def sqlite_store() -> Generator[SqliteStore, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SqliteStore(os.path.join(tmpdir, "test.db"))
        yield store
        store.close()


TS = "2026-01-01T00:00:00+00:00"


@pytest.fixture(params=["memory", "sqlite"])
def store(
    request: pytest.FixtureRequest,
    memory_store: MemoryStore,
    sqlite_store: SqliteStore,
) -> Store:
    if request.param == "memory":
        return memory_store
    return sqlite_store


def _make_memory(id: str = "01", **kwargs) -> Memory:
    defaults = dict(
        id=id,
        content="some knowledge",
        created_at=TS,
        updated_at=TS,
    )
    defaults.update(kwargs)
    return Memory(**defaults)


class TestStore:
    """Tests that run against both MemoryStore and SqliteStore."""

    def test_save_and_get(self, store: Store) -> None:
        memory = _make_memory()
        store.save(memory)
        got = store.get("01")
        assert got is not None
        assert got.content == "some knowledge"

    def test_get_nonexistent(self, store: Store) -> None:
        assert store.get("nonexistent") is None

    def test_list_empty(self, store: Store) -> None:
        assert store.list() == []

    def test_list_returns_all(self, store: Store) -> None:
        store.save(_make_memory("a", created_at=TS))
        store.save(_make_memory(
            "b", created_at="2026-01-02T00:00:00+00:00",
        ))
        results = store.list()
        assert len(results) == 2
        assert results[0].id == "b"

    def test_list_filter_by_project(self, store: Store) -> None:
        store.save(_make_memory("a", project="foo", created_at=TS))
        store.save(_make_memory("b", project="bar", created_at=TS))
        results = store.list(project="foo")
        assert len(results) == 1
        assert results[0].id == "a"

    def test_list_filter_by_type(self, store: Store) -> None:
        store.save(_make_memory("a", type="lesson", created_at=TS))
        store.save(_make_memory("b", type="fact", created_at=TS))
        results = store.list(type="lesson")
        assert len(results) == 1
        assert results[0].id == "a"

    def test_list_with_limit(self, store: Store) -> None:
        for i in range(5):
            ts = f"2026-01-0{i + 1}T00:00:00+00:00"
            store.save(_make_memory(str(i), created_at=ts))
        results = store.list(limit=2)
        assert len(results) == 2

    def test_delete(self, store: Store) -> None:
        store.save(_make_memory())
        assert store.delete("01") is True
        assert store.get("01") is None

    def test_delete_nonexistent(self, store: Store) -> None:
        assert store.delete("nonexistent") is False

    def test_tags_roundtrip(self, store: Store) -> None:
        store.save(_make_memory(tags=["a", "b"]))
        got = store.get("01")
        assert got is not None
        assert got.tags == ["a", "b"]

    def test_update_existing(self, store: Store) -> None:
        memory = _make_memory()
        store.save(memory)
        memory.upvotes = 5
        memory.downvotes = 2
        assert store.update(memory) is True
        got = store.get("01")
        assert got is not None
        assert got.upvotes == 5
        assert got.downvotes == 2

    def test_update_nonexistent(self, store: Store) -> None:
        memory = _make_memory(id="nope")
        assert store.update(memory) is False

    def test_metadata_roundtrip(self, store: Store) -> None:
        store.save(_make_memory(metadata={"key": "val"}))
        got = store.get("01")
        assert got is not None
        assert got.metadata == {"key": "val"}

    def test_type_roundtrip(self, store: Store) -> None:
        store.save(_make_memory(type="preference"))
        got = store.get("01")
        assert got is not None
        assert got.type == "preference"

    def test_count_all(self, store: Store) -> None:
        store.save(_make_memory("a"))
        store.save(_make_memory("b"))
        assert store.count() == 2

    def test_count_by_project(self, store: Store) -> None:
        store.save(_make_memory("a", project="foo"))
        store.save(_make_memory("b", project="bar"))
        assert store.count(project="foo") == 1

    def test_count_by_type(self, store: Store) -> None:
        store.save(_make_memory("a", type="lesson"))
        store.save(_make_memory("b", type="fact"))
        store.save(_make_memory("c", type="lesson"))
        assert store.count(type="lesson") == 2

    def test_count_empty(self, store: Store) -> None:
        assert store.count() == 0

    def test_ttl_roundtrip(self, store: Store) -> None:
        store.save(_make_memory(ttl=3600, expires_at="2026-01-01T01:00:00+00:00"))
        got = store.get("01")
        assert got is not None
        assert got.ttl == 3600
        assert got.expires_at == "2026-01-01T01:00:00+00:00"


class TestSqliteMigration:
    """Test auto-migration from lessons table to memories table."""

    def test_migrate_lessons_to_memories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE lessons (
                    id TEXT PRIMARY KEY, problem TEXT NOT NULL,
                    resolution TEXT NOT NULL, context TEXT, tags TEXT,
                    confidence REAL DEFAULT 0.5, source TEXT, project TEXT,
                    embedding BLOB, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, expires_at TEXT,
                    upvotes INTEGER DEFAULT 0, downvotes INTEGER DEFAULT 0,
                    meta TEXT
                );
            """)
            conn.execute(
                """INSERT INTO lessons (id, problem, resolution, tags, confidence,
                   created_at, updated_at, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("test-1", "rate limiting", "use backoff", '["api"]', 0.8,
                 "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
                 '{"key": "val"}'),
            )
            conn.commit()
            conn.close()

            store = SqliteStore(db_path)
            memory = store.get("test-1")
            assert memory is not None
            assert "rate limiting" in memory.content
            assert "use backoff" in memory.content
            assert memory.type == "lesson"
            assert memory.tags == ["api"]
            assert memory.metadata == {"key": "val"}
            assert memory.confidence == 0.8
            store.close()


class TestLore:
    """Tests for the Lore class."""

    def test_remember_and_get(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        mid = lore.remember("Always use backoff for rate limits")
        assert len(mid) == 26
        memory = lore.get(mid)
        assert memory is not None
        assert memory.content == "Always use backoff for rate limits"
        assert memory.created_at != ""

    def test_remember_with_project_default(self) -> None:
        lore = Lore(project="myproj", store=MemoryStore(), embedding_fn=_stub_embed)
        mid = lore.remember("test")
        memory = lore.get(mid)
        assert memory is not None
        assert memory.project == "myproj"

    def test_remember_project_override(self) -> None:
        lore = Lore(project="default", store=MemoryStore(), embedding_fn=_stub_embed)
        mid = lore.remember("test", project="override")
        memory = lore.get(mid)
        assert memory is not None
        assert memory.project == "override"

    def test_list_and_forget(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        mid = lore.remember("test")
        assert len(lore.list_memories()) == 1
        lore.forget(mid)
        assert len(lore.list_memories()) == 0
        assert lore.get(mid) is None

    def test_list_filter_project(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore.remember("a", project="a")
        lore.remember("b", project="b")
        assert len(lore.list_memories(project="a")) == 1

    def test_list_limit(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        for _ in range(5):
            lore.remember("test")
        assert len(lore.list_memories(limit=3)) == 3

    def test_confidence_validation(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        with pytest.raises(ValueError, match="confidence"):
            lore.remember("test", confidence=1.5)
        with pytest.raises(ValueError, match="confidence"):
            lore.remember("test", confidence=-0.1)

    def test_context_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            with Lore(db_path=db, embedding_fn=_stub_embed) as lore:
                mid = lore.remember("test")
                assert lore.get(mid) is not None

    def test_sqlite_default_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            lore = Lore(db_path=db, embedding_fn=_stub_embed)
            mid = lore.remember("test")
            assert lore.get(mid) is not None

    def test_remember_with_type(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        mid = lore.remember("user prefers dark mode", type="preference")
        memory = lore.get(mid)
        assert memory is not None
        assert memory.type == "preference"

    def test_remember_with_metadata(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        mid = lore.remember("lesson", metadata={"problem": "p", "resolution": "r"})
        memory = lore.get(mid)
        assert memory is not None
        assert memory.metadata == {"problem": "p", "resolution": "r"}

    def test_remember_with_ttl(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        mid = lore.remember("ephemeral", ttl=3600)
        memory = lore.get(mid)
        assert memory is not None
        assert memory.ttl == 3600
        assert memory.expires_at is not None

    def test_stats_empty(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        s = lore.stats()
        assert s.total == 0
        assert s.by_type == {}

    def test_stats_with_memories(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore.remember("a", type="lesson")
        lore.remember("b", type="fact")
        lore.remember("c", type="lesson")
        s = lore.stats()
        assert s.total == 3
        assert s.by_type["lesson"] == 2
        assert s.by_type["fact"] == 1
        assert s.oldest is not None
        assert s.newest is not None

    def test_list_filter_by_type(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore.remember("a", type="lesson")
        lore.remember("b", type="fact")
        assert len(lore.list_memories(type="lesson")) == 1

    def test_store_invalid_string(self) -> None:
        with pytest.raises(ValueError, match="must be a Store instance"):
            Lore(store="invalid")  # type: ignore
