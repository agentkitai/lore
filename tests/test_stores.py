"""Tests for MemoryStore and SqliteStore."""

from __future__ import annotations

import os
import tempfile
from typing import Generator

import pytest

from typing import List

from lore import Lesson, Lore
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


def _make_lesson(id: str = "01", **kwargs) -> Lesson:  # noqa: N802
    defaults = dict(
        id=id,
        problem="problem",
        resolution="resolution",
        created_at=TS,
        updated_at=TS,
    )
    defaults.update(kwargs)
    return Lesson(**defaults)


class TestStore:
    """Tests that run against both MemoryStore and SqliteStore."""

    def test_save_and_get(self, store: Store) -> None:
        lesson = _make_lesson()
        store.save(lesson)
        got = store.get("01")
        assert got is not None
        assert got.problem == "problem"

    def test_get_nonexistent(self, store: Store) -> None:
        assert store.get("nonexistent") is None

    def test_list_empty(self, store: Store) -> None:
        assert store.list() == []

    def test_list_returns_all(self, store: Store) -> None:
        store.save(_make_lesson("a", created_at=TS))
        store.save(_make_lesson(
            "b", created_at="2026-01-02T00:00:00+00:00",
        ))
        results = store.list()
        assert len(results) == 2
        assert results[0].id == "b"

    def test_list_filter_by_project(self, store: Store) -> None:
        store.save(_make_lesson(
            "a", project="foo", created_at=TS,
        ))
        store.save(_make_lesson(
            "b", project="bar", created_at=TS,
        ))
        results = store.list(project="foo")
        assert len(results) == 1
        assert results[0].id == "a"

    def test_list_with_limit(self, store: Store) -> None:
        for i in range(5):
            ts = f"2026-01-0{i + 1}T00:00:00+00:00"
            store.save(_make_lesson(str(i), created_at=ts))
        results = store.list(limit=2)
        assert len(results) == 2

    def test_delete(self, store: Store) -> None:
        store.save(_make_lesson())
        assert store.delete("01") is True
        assert store.get("01") is None

    def test_delete_nonexistent(self, store: Store) -> None:
        assert store.delete("nonexistent") is False

    def test_tags_roundtrip(self, store: Store) -> None:
        store.save(_make_lesson(tags=["a", "b"]))
        got = store.get("01")
        assert got is not None
        assert got.tags == ["a", "b"]

    def test_update_existing(self, store: Store) -> None:
        lesson = _make_lesson()
        store.save(lesson)
        lesson.upvotes = 5
        lesson.downvotes = 2
        assert store.update(lesson) is True
        got = store.get("01")
        assert got is not None
        assert got.upvotes == 5
        assert got.downvotes == 2

    def test_update_nonexistent(self, store: Store) -> None:
        lesson = _make_lesson(id="nope")
        assert store.update(lesson) is False

    def test_meta_roundtrip(self, store: Store) -> None:
        store.save(_make_lesson(meta={"key": "val"}))
        got = store.get("01")
        assert got is not None
        assert got.meta == {"key": "val"}


class TestLore:
    """Tests for the Lore class."""

    def test_publish_and_get(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lid = lore.publish(problem="p", resolution="r")
        assert len(lid) == 26  # ULID length
        lesson = lore.get(lid)
        assert lesson is not None
        assert lesson.problem == "p"
        assert lesson.created_at != ""

    def test_publish_with_project_default(self) -> None:
        lore = Lore(project="myproj", store=MemoryStore(), embedding_fn=_stub_embed)
        lid = lore.publish(problem="p", resolution="r")
        lesson = lore.get(lid)
        assert lesson is not None
        assert lesson.project == "myproj"

    def test_publish_project_override(self) -> None:
        lore = Lore(project="default", store=MemoryStore(), embedding_fn=_stub_embed)
        lid = lore.publish(
            problem="p", resolution="r", project="override",
        )
        lesson = lore.get(lid)
        assert lesson is not None
        assert lesson.project == "override"

    def test_list_and_delete(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lid = lore.publish(problem="p", resolution="r")
        assert len(lore.list()) == 1
        lore.delete(lid)
        assert len(lore.list()) == 0
        assert lore.get(lid) is None

    def test_list_filter_project(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore.publish(problem="p", resolution="r", project="a")
        lore.publish(problem="p", resolution="r", project="b")
        assert len(lore.list(project="a")) == 1

    def test_list_limit(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        for _ in range(5):
            lore.publish(problem="p", resolution="r")
        assert len(lore.list(limit=3)) == 3

    def test_confidence_validation(self) -> None:
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        with pytest.raises(ValueError, match="confidence"):
            lore.publish(problem="p", resolution="r", confidence=1.5)
        with pytest.raises(ValueError, match="confidence"):
            lore.publish(problem="p", resolution="r", confidence=-0.1)

    def test_context_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            with Lore(db_path=db, embedding_fn=_stub_embed) as lore:
                lid = lore.publish(problem="p", resolution="r")
                assert lore.get(lid) is not None

    def test_sqlite_default_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            lore = Lore(db_path=db, embedding_fn=_stub_embed)
            lid = lore.publish(problem="p", resolution="r")
            assert lore.get(lid) is not None
