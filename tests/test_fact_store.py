"""Tests for fact/conflict CRUD on both MemoryStore and MemoryStore."""

from __future__ import annotations

import pytest

from lore.store.memory import MemoryStore
from lore.types import ConflictEntry, Fact, Memory

# ---------------------------------------------------------------------------
# Fixtures: parametrize store backends
# ---------------------------------------------------------------------------

@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    else:
        str(tmp_path / "test.db")
        s = MemoryStore()
        return s


def _make_memory(mid: str = "m1") -> Memory:
    return Memory(
        id=mid, content="test content", created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


def _make_fact(
    fid: str = "f1", memory_id: str = "m1", subject: str = "user",
    predicate: str = "lives_in", obj: str = "Berlin", confidence: float = 0.9,
) -> Fact:
    return Fact(
        id=fid, memory_id=memory_id, subject=subject, predicate=predicate,
        object=obj, confidence=confidence, extracted_at="2026-01-01T00:00:00",
    )


def _make_conflict(
    cid: str = "c1", resolution: str = "SUPERSEDE",
    new_memory_id: str = "m2",
) -> ConflictEntry:
    return ConflictEntry(
        id=cid, new_memory_id=new_memory_id, old_fact_id="f1", new_fact_id="f2",
        subject="user", predicate="lives_in", old_value="NYC", new_value="Berlin",
        resolution=resolution, resolved_at="2026-01-01T00:00:00",
    )


# ---------------------------------------------------------------------------
# save_fact + get_facts
# ---------------------------------------------------------------------------

class TestSaveAndGetFacts:
    def test_save_and_get_roundtrip(self, store):
        store.save(_make_memory("m1"))
        fact = _make_fact("f1", "m1")
        store.save_fact(fact)
        facts = store.get_facts("m1")
        assert len(facts) == 1
        assert facts[0].id == "f1"
        assert facts[0].subject == "user"

    def test_get_facts_for_specific_memory(self, store):
        store.save(_make_memory("m1"))
        store.save(_make_memory("m2"))
        store.save_fact(_make_fact("f1", "m1"))
        store.save_fact(_make_fact("f2", "m1"))
        store.save_fact(_make_fact("f3", "m2"))
        assert len(store.get_facts("m1")) == 2
        assert len(store.get_facts("m2")) == 1

    def test_get_facts_empty(self, store):
        assert store.get_facts("nonexistent") == []

    def test_metadata_serialization(self, store):
        store.save(_make_memory("m1"))
        fact = _make_fact("f1", "m1")
        fact.metadata = {"model": "gpt-4", "reasoning": "test"}
        store.save_fact(fact)
        loaded = store.get_facts("m1")[0]
        assert loaded.metadata == {"model": "gpt-4", "reasoning": "test"}


# ---------------------------------------------------------------------------
# get_active_facts
# ---------------------------------------------------------------------------

class TestGetActiveFacts:
    def test_excludes_invalidated(self, store):
        store.save(_make_memory("m1"))
        store.save_fact(_make_fact("f1", "m1", subject="user"))
        f2 = _make_fact("f2", "m1", subject="project")
        f2.invalidated_by = "m2"
        f2.invalidated_at = "2026-02-01T00:00:00"
        store.save_fact(f2)
        active = store.get_active_facts()
        assert len(active) == 1
        assert active[0].id == "f1"

    def test_filter_by_subject(self, store):
        store.save(_make_memory("m1"))
        store.save_fact(_make_fact("f1", "m1", subject="user"))
        store.save_fact(_make_fact("f2", "m1", subject="project"))
        active = store.get_active_facts(subject="user")
        assert len(active) == 1
        assert active[0].subject == "user"

    def test_filter_by_subject_and_predicate(self, store):
        store.save(_make_memory("m1"))
        store.save_fact(_make_fact("f1", "m1", subject="project", predicate="uses_database"))
        store.save_fact(_make_fact("f2", "m1", subject="project", predicate="language"))
        active = store.get_active_facts(subject="project", predicate="uses_database")
        assert len(active) == 1
        assert active[0].predicate == "uses_database"

    def test_normalizes_input(self, store):
        store.save(_make_memory("m1"))
        store.save_fact(_make_fact("f1", "m1", subject="user"))
        active = store.get_active_facts(subject="  User  ")
        assert len(active) == 1

    def test_limit(self, store):
        store.save(_make_memory("m1"))
        for i in range(10):
            store.save_fact(_make_fact(f"f{i}", "m1", subject=f"s{i}"))
        active = store.get_active_facts(limit=3)
        assert len(active) == 3


# ---------------------------------------------------------------------------
# invalidate_fact
# ---------------------------------------------------------------------------

class TestInvalidateFact:
    def test_marks_as_invalidated(self, store):
        store.save(_make_memory("m1"))
        store.save_fact(_make_fact("f1", "m1"))
        store.invalidate_fact("f1", invalidated_by="m2")
        facts = store.get_facts("m1")
        assert facts[0].invalidated_by == "m2"
        assert facts[0].invalidated_at is not None

    def test_idempotent_for_already_invalidated(self, store):
        store.save(_make_memory("m1"))
        store.save_fact(_make_fact("f1", "m1"))
        store.invalidate_fact("f1", invalidated_by="m2")
        store.invalidate_fact("f1", invalidated_by="m3")
        facts = store.get_facts("m1")
        # Original invalidated_by preserved
        assert facts[0].invalidated_by == "m2"

    def test_invalidated_excluded_from_active(self, store):
        store.save(_make_memory("m1"))
        store.save_fact(_make_fact("f1", "m1"))
        store.invalidate_fact("f1", invalidated_by="m2")
        assert len(store.get_active_facts()) == 0


# ---------------------------------------------------------------------------
# save_conflict + list_conflicts
# ---------------------------------------------------------------------------

class TestConflictLog:
    def test_save_and_list_roundtrip(self, store):
        conflict = _make_conflict("c1")
        store.save_conflict(conflict)
        entries = store.list_conflicts()
        assert len(entries) == 1
        assert entries[0].id == "c1"

    def test_list_ordered_by_resolved_at_desc(self, store):
        store.save_conflict(ConflictEntry(
            id="c1", new_memory_id="m1", old_fact_id="f1", new_fact_id="f2",
            subject="a", predicate="b", old_value="x", new_value="y",
            resolution="SUPERSEDE", resolved_at="2026-01-01T00:00:00",
        ))
        store.save_conflict(ConflictEntry(
            id="c2", new_memory_id="m2", old_fact_id="f3", new_fact_id="f4",
            subject="a", predicate="b", old_value="x", new_value="z",
            resolution="MERGE", resolved_at="2026-02-01T00:00:00",
        ))
        entries = store.list_conflicts()
        assert entries[0].id == "c2"  # Most recent first
        assert entries[1].id == "c1"

    def test_filter_by_resolution(self, store):
        store.save_conflict(_make_conflict("c1", resolution="SUPERSEDE"))
        store.save_conflict(_make_conflict("c2", resolution="MERGE"))
        store.save_conflict(_make_conflict("c3", resolution="CONTRADICT"))
        entries = store.list_conflicts(resolution="CONTRADICT")
        assert len(entries) == 1
        assert entries[0].resolution == "CONTRADICT"

    def test_limit(self, store):
        for i in range(5):
            store.save_conflict(_make_conflict(f"c{i}"))
        entries = store.list_conflicts(limit=3)
        assert len(entries) == 3

    def test_conflict_metadata(self, store):
        conflict = _make_conflict("c1")
        conflict.metadata = {"reasoning": "temporal update"}
        store.save_conflict(conflict)
        loaded = store.list_conflicts()[0]
        assert loaded.metadata["reasoning"] == "temporal update"


# ---------------------------------------------------------------------------
# Cascade deletion
# ---------------------------------------------------------------------------

class TestCascadeDeletion:
    def test_memory_deletion_cascades_facts(self, store):
        store.save(_make_memory("m1"))
        store.save_fact(_make_fact("f1", "m1"))
        store.save_fact(_make_fact("f2", "m1"))
        store.delete("m1")
        assert store.get_facts("m1") == []

    def test_conflict_log_preserved_after_cascade(self, store):
        store.save(_make_memory("m1"))
        store.save_fact(_make_fact("f1", "m1"))
        conflict = _make_conflict("c1", new_memory_id="m1")
        store.save_conflict(conflict)
        store.delete("m1")
        # Conflict log should still exist
        entries = store.list_conflicts()
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# Schema idempotency (SQLite specific)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Store ABC no-op defaults
# ---------------------------------------------------------------------------

class TestStoreBaseDefaults:
    def test_base_store_noop(self):
        from lore.store.base import Store

        # Create a minimal concrete subclass
        class MinimalStore(Store):
            def save(self, memory): pass
            def get(self, memory_id): return None
            def list(self, **kw): return []
            def update(self, memory): return False
            def delete(self, memory_id): return False
            def count(self, **kw): return 0
            def cleanup_expired(self): return 0

        s = MinimalStore()
        # All fact methods should work without error
        s.save_fact(_make_fact())
        assert s.get_facts("m1") == []
        assert s.get_active_facts() == []
        s.invalidate_fact("f1", "m2")
        s.save_conflict(_make_conflict())
        assert s.list_conflicts() == []
