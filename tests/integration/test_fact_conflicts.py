"""Scenario 3 — Fact storage and conflict log infrastructure."""

from __future__ import annotations

from datetime import datetime, timezone

from lore.store.memory import MemoryStore
from lore.types import ConflictEntry, Fact


class TestFactConflicts:
    """Test fact and conflict CRUD directly on the store (no LLM needed)."""

    def test_store_fact_and_retrieve(self, memory_store: MemoryStore) -> None:
        """save_fact + get_facts round-trips correctly."""
        fact = Fact(
            id="fact-001",
            memory_id="mem-001",
            subject="python",
            predicate="has_version",
            object="3.12",
            confidence=0.95,
            extracted_at=datetime.now(timezone.utc).isoformat(),
        )
        memory_store.save_fact(fact)

        facts = memory_store.get_facts("mem-001")
        assert len(facts) == 1
        assert facts[0].subject == "python"
        assert facts[0].predicate == "has_version"
        assert facts[0].object == "3.12"
        assert facts[0].confidence == 0.95

    def test_active_facts_filtering(self, memory_store: MemoryStore) -> None:
        """Invalidated facts are excluded from get_active_facts."""
        now = datetime.now(timezone.utc).isoformat()
        f1 = Fact(
            id="f1", memory_id="m1", subject="redis",
            predicate="default_port", object="6379",
            confidence=1.0, extracted_at=now,
        )
        f2 = Fact(
            id="f2", memory_id="m2", subject="redis",
            predicate="default_port", object="6380",
            confidence=0.8, extracted_at=now,
            invalidated_by="f1",
            invalidated_at=now,
        )
        memory_store.save_fact(f1)
        memory_store.save_fact(f2)

        active = memory_store.get_active_facts(subject="redis")
        assert len(active) == 1
        assert active[0].id == "f1"

    def test_active_facts_no_filter(self, memory_store: MemoryStore) -> None:
        """get_active_facts without subject returns all active facts."""
        now = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            memory_store.save_fact(Fact(
                id=f"f{i}", memory_id=f"m{i}",
                subject=f"subj{i}", predicate="pred", object="obj",
                confidence=1.0, extracted_at=now,
            ))

        active = memory_store.get_active_facts()
        assert len(active) == 3

    def test_conflict_log(self, memory_store: MemoryStore) -> None:
        """save_conflict + list_conflicts round-trips correctly."""
        now = datetime.now(timezone.utc).isoformat()
        entry = ConflictEntry(
            id="conflict-001",
            new_memory_id="mem-002",
            old_fact_id="fact-001",
            new_fact_id="fact-002",
            subject="python",
            predicate="has_version",
            old_value="3.11",
            new_value="3.12",
            resolution="SUPERSEDE",
            resolved_at=now,
        )
        memory_store.save_conflict(entry)

        conflicts = memory_store.list_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0].resolution == "SUPERSEDE"
        assert conflicts[0].old_value == "3.11"
        assert conflicts[0].new_value == "3.12"

    def test_conflict_log_filter_by_resolution(self, memory_store: MemoryStore) -> None:
        """list_conflicts(resolution=...) filters by resolution type."""
        now = datetime.now(timezone.utc).isoformat()
        for i, res in enumerate(["SUPERSEDE", "MERGE", "SUPERSEDE"]):
            memory_store.save_conflict(ConflictEntry(
                id=f"c{i}", new_memory_id=f"m{i}",
                old_fact_id=f"of{i}", new_fact_id=f"nf{i}",
                subject="s", predicate="p",
                old_value="old", new_value="new",
                resolution=res, resolved_at=now,
            ))

        supersedes = memory_store.list_conflicts(resolution="SUPERSEDE")
        assert len(supersedes) == 2

        merges = memory_store.list_conflicts(resolution="MERGE")
        assert len(merges) == 1

    def test_invalidate_fact(self, memory_store: MemoryStore) -> None:
        """invalidate_fact marks a fact as invalidated."""
        now = datetime.now(timezone.utc).isoformat()
        fact = Fact(
            id="f-inv", memory_id="m-inv",
            subject="node", predicate="lts_version", object="18",
            confidence=1.0, extracted_at=now,
        )
        memory_store.save_fact(fact)

        memory_store.invalidate_fact("f-inv", invalidated_by="f-new")

        active = memory_store.get_active_facts(subject="node")
        assert len(active) == 0
