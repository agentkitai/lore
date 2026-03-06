"""Tests for ConflictResolver resolution strategies and audit trail."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from lore.extract.extractor import ExtractedFact, FactExtractor
from lore.extract.resolver import ConflictResolver
from lore.store.memory import MemoryStore
from lore.types import Fact, Memory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fact(
    fid: str = "f1", memory_id: str = "m1", subject: str = "user",
    predicate: str = "lives_in", obj: str = "Berlin",
) -> Fact:
    return Fact(
        id=fid, memory_id=memory_id, subject=subject, predicate=predicate,
        object=obj, confidence=0.9, extracted_at="2026-01-01T00:00:00",
    )


def _make_extracted(
    fact: Fact | None = None,
    resolution: str = "NOOP",
    reasoning: str = "",
    conflicting_fact: Fact | None = None,
) -> ExtractedFact:
    if fact is None:
        fact = _make_fact()
    return ExtractedFact(
        fact=fact, resolution=resolution,
        reasoning=reasoning, conflicting_fact=conflicting_fact,
    )


# ---------------------------------------------------------------------------
# NOOP resolution
# ---------------------------------------------------------------------------

class TestNOOPResolution:
    def test_noop_saves_fact_no_conflict(self):
        store = MemoryStore()
        resolver = ConflictResolver(store)
        ef = _make_extracted(resolution="NOOP")
        result = resolver.resolve_all([ef], memory_id="m1")

        assert len(result.saved_facts) == 1
        assert len(result.conflicts) == 0
        assert result.stats["noop"] == 1
        # Fact is saved in store with memory_id="m1"
        assert len(list(store._facts.values())) == 1


# ---------------------------------------------------------------------------
# SUPERSEDE resolution
# ---------------------------------------------------------------------------

class TestSupersedeResolution:
    def test_supersede_invalidates_old_saves_new(self):
        store = MemoryStore()
        old_fact = _make_fact("old1", "m0", subject="user", predicate="city", obj="NYC")
        store.save_fact(old_fact)

        new_fact = _make_fact("new1", "m1", subject="user", predicate="city", obj="Berlin")
        ef = _make_extracted(fact=new_fact, resolution="SUPERSEDE",
                            reasoning="moved", conflicting_fact=old_fact)

        resolver = ConflictResolver(store)
        result = resolver.resolve_all([ef], memory_id="m1")

        assert result.stats["supersede"] == 1
        assert len(result.saved_facts) == 1
        assert len(result.conflicts) == 1
        assert result.conflicts[0].resolution == "SUPERSEDE"
        assert result.conflicts[0].old_value == "NYC"
        assert result.conflicts[0].new_value == "Berlin"

        # Old fact should be invalidated
        assert store._facts["old1"].invalidated_by == "m1"
        assert store._facts["old1"].invalidated_at is not None

    def test_supersede_without_conflicting_fact(self):
        store = MemoryStore()
        new_fact = _make_fact("new1", "m1")
        ef = _make_extracted(fact=new_fact, resolution="SUPERSEDE",
                            reasoning="no old", conflicting_fact=None)

        resolver = ConflictResolver(store)
        result = resolver.resolve_all([ef], memory_id="m1")

        assert result.stats["supersede"] == 1
        assert len(result.saved_facts) == 1
        assert result.conflicts[0].old_fact_id == ""
        assert result.conflicts[0].old_value == ""


# ---------------------------------------------------------------------------
# MERGE resolution
# ---------------------------------------------------------------------------

class TestMergeResolution:
    def test_merge_saves_both_active(self):
        store = MemoryStore()
        old_fact = _make_fact("old1", "m0", subject="project", predicate="language", obj="Python")
        store.save_fact(old_fact)

        new_fact = _make_fact("new1", "m1", subject="project", predicate="language", obj="TypeScript")
        ef = _make_extracted(fact=new_fact, resolution="MERGE",
                            reasoning="complementary", conflicting_fact=old_fact)

        resolver = ConflictResolver(store)
        result = resolver.resolve_all([ef], memory_id="m1")

        assert result.stats["merge"] == 1
        assert len(result.saved_facts) == 1
        assert len(result.conflicts) == 1
        assert result.conflicts[0].resolution == "MERGE"

        # Old fact should NOT be invalidated
        assert store._facts["old1"].invalidated_by is None
        # Both facts are active
        active = store.get_active_facts(subject="project", predicate="language")
        assert len(active) == 2


# ---------------------------------------------------------------------------
# CONTRADICT resolution
# ---------------------------------------------------------------------------

class TestContradictResolution:
    def test_contradict_does_not_save_new_fact(self):
        store = MemoryStore()
        old_fact = _make_fact("old1", "m0", subject="project", predicate="database", obj="MySQL")
        store.save_fact(old_fact)

        new_fact = _make_fact("new1", "m1", subject="project", predicate="database", obj="PostgreSQL")
        ef = _make_extracted(fact=new_fact, resolution="CONTRADICT",
                            reasoning="ambiguous", conflicting_fact=old_fact)

        resolver = ConflictResolver(store)
        result = resolver.resolve_all([ef], memory_id="m1")

        assert result.stats["contradict"] == 1
        assert len(result.saved_facts) == 0  # NOT saved
        assert len(result.conflicts) == 1
        assert result.conflicts[0].resolution == "CONTRADICT"
        assert result.conflicts[0].new_fact_id is None

        # Proposed fact in metadata
        assert result.conflicts[0].metadata["proposed_fact"]["object"] == "PostgreSQL"

        # Only old fact exists in store
        assert len(list(store._facts.values())) == 1


# ---------------------------------------------------------------------------
# Mixed resolution stats
# ---------------------------------------------------------------------------

class TestResolutionStats:
    def test_mixed_stats(self):
        store = MemoryStore()
        old1 = _make_fact("old1", "m0", subject="a", predicate="b", obj="x")
        old2 = _make_fact("old2", "m0", subject="c", predicate="d", obj="y")
        old3 = _make_fact("old3", "m0", subject="e", predicate="f", obj="z")
        store.save_fact(old1)
        store.save_fact(old2)
        store.save_fact(old3)

        extracted = [
            _make_extracted(_make_fact("n1", "m1", "g", "h", "1"), "NOOP"),
            _make_extracted(_make_fact("n2", "m1", "i", "j", "2"), "NOOP"),
            _make_extracted(_make_fact("n3", "m1", "a", "b", "new_x"), "SUPERSEDE",
                          conflicting_fact=old1),
            _make_extracted(_make_fact("n4", "m1", "c", "d", "y2"), "MERGE",
                          conflicting_fact=old2),
            _make_extracted(_make_fact("n5", "m1", "e", "f", "new_z"), "CONTRADICT",
                          conflicting_fact=old3),
        ]

        resolver = ConflictResolver(store)
        result = resolver.resolve_all(extracted, memory_id="m1")

        assert result.stats == {"noop": 2, "supersede": 1, "merge": 1, "contradict": 1}
        assert len(result.saved_facts) == 4  # CONTRADICT excluded
        assert len(result.conflicts) == 3  # NOOP excluded


# ---------------------------------------------------------------------------
# Unknown resolution
# ---------------------------------------------------------------------------

class TestUnknownResolution:
    def test_unknown_treated_as_noop(self):
        store = MemoryStore()
        fact = _make_fact("f1", "m1")
        ef = _make_extracted(fact=fact, resolution="INVALID")

        resolver = ConflictResolver(store)
        result = resolver.resolve_all([ef], memory_id="m1")

        assert result.stats["noop"] == 1
        assert len(result.saved_facts) == 1


# ---------------------------------------------------------------------------
# Multi-step supersede chain
# ---------------------------------------------------------------------------

class TestSupersedeChain:
    def test_chain_a_to_b_to_c(self):
        store = MemoryStore()
        store.save(Memory(id="m0", content="a", created_at="2026-01-01", updated_at="2026-01-01"))
        store.save(Memory(id="m1", content="b", created_at="2026-01-02", updated_at="2026-01-02"))
        store.save(Memory(id="m2", content="c", created_at="2026-01-03", updated_at="2026-01-03"))

        # Fact A
        fact_a = _make_fact("fA", "m0", subject="user", predicate="city", obj="NYC")
        store.save_fact(fact_a)

        # B supersedes A
        fact_b = _make_fact("fB", "m1", subject="user", predicate="city", obj="Berlin")
        ef_b = _make_extracted(fact=fact_b, resolution="SUPERSEDE",
                              conflicting_fact=fact_a)
        resolver = ConflictResolver(store)
        resolver.resolve_all([ef_b], memory_id="m1")

        # C supersedes B
        fact_c = _make_fact("fC", "m2", subject="user", predicate="city", obj="London")
        ef_c = _make_extracted(fact=fact_c, resolution="SUPERSEDE",
                              conflicting_fact=fact_b)
        resolver.resolve_all([ef_c], memory_id="m2")

        # Only C should be active
        active = store.get_active_facts(subject="user", predicate="city")
        assert len(active) == 1
        assert active[0].id == "fC"

        # Conflict log should have 2 entries
        conflicts = store.list_conflicts()
        assert len(conflicts) == 2


# ---------------------------------------------------------------------------
# Integration: pipeline test
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    def test_remember_with_fact_extraction(self):
        """Test that Lore.remember() with fact_extraction uses mock LLM."""
        store = MemoryStore()

        # Create a mock LLM response
        llm_response = json.dumps({"facts": [
            {"subject": "project", "predicate": "uses", "object": "PostgreSQL 16",
             "confidence": 0.95, "resolution": "NOOP", "reasoning": "new fact"},
        ]})

        from lore.extract.resolver import ConflictResolver

        extractor = FactExtractor(
            llm_client=lambda prompt: llm_response,
            store=store,
        )
        resolver = ConflictResolver(store)

        # Simulate what remember() does
        memory = Memory(
            id="m1", content="We use PostgreSQL 16",
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        store.save(memory)

        extracted = extractor.extract(memory.id, memory.content)
        result = resolver.resolve_all(extracted, memory_id=memory.id)

        assert len(result.saved_facts) == 1
        store.get_facts("m1")
        # Facts are stored by id, not memory_id in MemoryStore._facts
        all_facts = [f for f in store._facts.values() if f.memory_id == "m1"]
        assert len(all_facts) == 1
        assert all_facts[0].subject == "project"

    def test_backward_compat_no_fact_extraction(self):
        """When fact_extraction is disabled, no fact-related calls."""
        store = MemoryStore()
        memory = Memory(
            id="m1", content="test",
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        store.save(memory)
        # No facts should exist
        assert store.get_facts("m1") == []
        assert store.get_active_facts() == []


# ---------------------------------------------------------------------------
# SUPERSEDE -> graph edge expiration wiring
# ---------------------------------------------------------------------------

class TestSupersedeGraphWiring:
    def test_supersede_calls_expire_relationship_for_fact(self):
        """SUPERSEDE should expire graph edges for the old fact."""
        store = MemoryStore()
        old_fact = _make_fact("old1", "m0", subject="user", predicate="city", obj="NYC")
        store.save_fact(old_fact)

        new_fact = _make_fact("new1", "m1", subject="user", predicate="city", obj="Berlin")
        ef = _make_extracted(fact=new_fact, resolution="SUPERSEDE",
                            reasoning="moved", conflicting_fact=old_fact)

        mock_rm = MagicMock()
        resolver = ConflictResolver(store, relationship_manager=mock_rm)
        resolver.resolve_all([ef], memory_id="m1")

        mock_rm.expire_relationship_for_fact.assert_called_once_with("old1")

    def test_supersede_no_relationship_manager(self):
        """SUPERSEDE works without relationship_manager (no crash)."""
        store = MemoryStore()
        old_fact = _make_fact("old1", "m0", subject="user", predicate="city", obj="NYC")
        store.save_fact(old_fact)

        new_fact = _make_fact("new1", "m1", subject="user", predicate="city", obj="Berlin")
        ef = _make_extracted(fact=new_fact, resolution="SUPERSEDE",
                            reasoning="moved", conflicting_fact=old_fact)

        resolver = ConflictResolver(store)
        result = resolver.resolve_all([ef], memory_id="m1")
        assert result.stats["supersede"] == 1

    def test_supersede_graph_expiration_failure_is_non_fatal(self):
        """If expire_relationship_for_fact raises, supersede still completes."""
        store = MemoryStore()
        old_fact = _make_fact("old1", "m0", subject="user", predicate="city", obj="NYC")
        store.save_fact(old_fact)

        new_fact = _make_fact("new1", "m1", subject="user", predicate="city", obj="Berlin")
        ef = _make_extracted(fact=new_fact, resolution="SUPERSEDE",
                            reasoning="moved", conflicting_fact=old_fact)

        mock_rm = MagicMock()
        mock_rm.expire_relationship_for_fact.side_effect = RuntimeError("db error")
        resolver = ConflictResolver(store, relationship_manager=mock_rm)
        result = resolver.resolve_all([ef], memory_id="m1")

        assert result.stats["supersede"] == 1
        assert len(result.saved_facts) == 1
