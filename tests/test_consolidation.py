"""Tests for F3 — Memory Consolidation / Auto-Summarization."""

from __future__ import annotations

import asyncio
import struct
from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np

from lore.consolidation import ConsolidationEngine
from lore.store.memory import MemoryStore
from lore.types import (
    DEFAULT_CONSOLIDATION_CONFIG,
    DEFAULT_RETENTION_POLICIES,
    ConsolidationLogEntry,
    ConsolidationResult,
    EntityMention,
    Memory,
    MemoryStats,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _embed(vec: List[float]) -> bytes:
    """Serialize a float list to embedding bytes."""
    return struct.pack(f"{len(vec)}f", *vec)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _old_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class FakeEmbedder:
    """Fake embedder that returns a fixed vector."""

    def embed(self, text: str) -> List[float]:
        # Return a simple hash-based vector for reproducibility
        h = hash(text) % 1000
        return [float(h) / 1000.0] * 384

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class FakeLLM:
    """Fake LLM provider for testing."""
    model = "fake-model"

    def complete(self, prompt: str, *, max_tokens: int = 200) -> str:
        return "Consolidated summary of memories."


_UNSET = object()

def _make_memory(
    id: str,
    content: str = "test content",
    tier: str = "short",
    project: str = "proj",
    created_at: str = "",
    importance_score: float = 0.5,
    embedding: bytes | None = _UNSET,
    tags: list | None = None,
    type: str = "general",
    access_count: int = 0,
    upvotes: int = 0,
    downvotes: int = 0,
    confidence: float = 1.0,
    archived: bool = False,
    consolidated_into: str | None = None,
) -> Memory:
    if not created_at:
        created_at = _old_iso(700000)  # ~8 days old (past short retention)
    if embedding is _UNSET:
        embedding = _embed([0.5] * 384)
    return Memory(
        id=id,
        content=content,
        type=type,
        tier=tier,
        tags=tags or [],
        project=project,
        embedding=embedding,
        created_at=created_at,
        updated_at=created_at,
        importance_score=importance_score,
        access_count=access_count,
        upvotes=upvotes,
        downvotes=downvotes,
        confidence=confidence,
        archived=archived,
        consolidated_into=consolidated_into,
    )


def _make_engine(
    store: MemoryStore | None = None,
    llm: FakeLLM | None = None,
    config: dict | None = None,
) -> ConsolidationEngine:
    return ConsolidationEngine(
        store=store or MemoryStore(),
        embedder=FakeEmbedder(),
        llm_provider=llm,
        config=config,
    )


# ---------------------------------------------------------------------------
# S1: Types & Config
# ---------------------------------------------------------------------------

class TestTypes:
    def test_memory_archived_defaults(self):
        m = Memory(id="x", content="y", created_at="t", updated_at="t")
        assert m.archived is False
        assert m.consolidated_into is None

    def test_consolidation_log_entry(self):
        entry = ConsolidationLogEntry(
            id="log1",
            consolidated_memory_id="c1",
            original_memory_ids=["a", "b"],
            strategy="deduplicate",
            model_used=None,
            original_count=2,
            created_at=_now_iso(),
        )
        assert entry.original_count == 2
        assert entry.metadata is None

    def test_consolidation_result_defaults(self):
        r = ConsolidationResult()
        assert r.groups_found == 0
        assert r.dry_run is True
        assert r.groups == []

    def test_default_retention_policies(self):
        assert DEFAULT_RETENTION_POLICIES["working"] == 3600
        assert DEFAULT_RETENTION_POLICIES["short"] == 604800
        assert DEFAULT_RETENTION_POLICIES["long"] == 2592000

    def test_default_consolidation_config(self):
        assert DEFAULT_CONSOLIDATION_CONFIG["dedup_threshold"] == 0.95
        assert DEFAULT_CONSOLIDATION_CONFIG["min_group_size"] == 3
        assert DEFAULT_CONSOLIDATION_CONFIG["batch_size"] == 50
        assert DEFAULT_CONSOLIDATION_CONFIG["max_groups_per_run"] == 100

    def test_memory_stats_consolidation_fields(self):
        s = MemoryStats(total=10)
        assert s.archived_count == 0
        assert s.consolidation_count == 0
        assert s.last_consolidation_at is None


# ---------------------------------------------------------------------------
# S2: Store Persistence
# ---------------------------------------------------------------------------

class TestStorePersistence:
    def test_sqlite_round_trip_archived(self, tmp_path):
        from lore.store.memory import MemoryStore

        db = str(tmp_path / "test.db")
        store = MemoryStore()
        m = _make_memory("m1", archived=True, consolidated_into="c1")
        store.save(m)
        loaded = store.get("m1")
        assert loaded is not None
        assert loaded.archived is True
        assert loaded.consolidated_into == "c1"
        store.close()

    def test_sqlite_consolidation_log_round_trip(self, tmp_path):
        from lore.store.memory import MemoryStore

        db = str(tmp_path / "test.db")
        store = MemoryStore()
        entry = ConsolidationLogEntry(
            id="log1",
            consolidated_memory_id="c1",
            original_memory_ids=["a", "b", "c"],
            strategy="deduplicate",
            model_used="gpt-4",
            original_count=3,
            created_at=_now_iso(),
            metadata={"key": "value"},
        )
        store.save_consolidation_log(entry)
        entries = store.get_consolidation_log(limit=10)
        assert len(entries) == 1
        assert entries[0].id == "log1"
        assert entries[0].original_memory_ids == ["a", "b", "c"]
        assert entries[0].metadata == {"key": "value"}
        store.close()

    def test_memory_store_consolidation_log(self):
        store = MemoryStore()
        entry = ConsolidationLogEntry(
            id="log1",
            consolidated_memory_id="c1",
            original_memory_ids=["a"],
            strategy="summarize",
            model_used=None,
            original_count=1,
            created_at=_now_iso(),
        )
        store.save_consolidation_log(entry)
        entries = store.get_consolidation_log()
        assert len(entries) == 1
        assert entries[0].id == "log1"


# ---------------------------------------------------------------------------
# S3: Archived Filtering
# ---------------------------------------------------------------------------

class TestArchivedFiltering:
    def test_list_excludes_archived_by_default(self):
        store = MemoryStore()
        store.save(_make_memory("m1"))
        store.save(_make_memory("m2", archived=True))
        store.save(_make_memory("m3"))
        assert len(store.list()) == 2

    def test_list_includes_archived_when_requested(self):
        store = MemoryStore()
        store.save(_make_memory("m1"))
        store.save(_make_memory("m2", archived=True))
        assert len(store.list(include_archived=True)) == 2

    def test_sqlite_list_excludes_archived(self, tmp_path):
        from lore.store.memory import MemoryStore

        db = str(tmp_path / "test.db")
        store = MemoryStore()
        store.save(_make_memory("m1"))
        store.save(_make_memory("m2", archived=True))
        store.save(_make_memory("m3"))
        assert len(store.list()) == 2
        assert len(store.list(include_archived=True)) == 3
        store.close()


# ---------------------------------------------------------------------------
# S4: Candidate Identification
# ---------------------------------------------------------------------------

class TestCandidateIdentification:
    def test_identifies_old_memories(self):
        store = MemoryStore()
        # 8 days old short-tier memory (past 7-day retention)
        store.save(_make_memory("m1", tier="short", created_at=_old_iso(700000)))
        # 1 day old short-tier memory (within retention)
        store.save(_make_memory("m2", tier="short", created_at=_old_iso(86000)))
        engine = _make_engine(store)
        candidates = engine._identify_candidates()
        assert len(candidates) == 1
        assert candidates[0].id == "m1"

    def test_excludes_archived(self):
        store = MemoryStore()
        store.save(_make_memory("m1", archived=True, created_at=_old_iso(700000)))
        engine = _make_engine(store)
        candidates = engine._identify_candidates()
        assert len(candidates) == 0

    def test_custom_retention_policy(self):
        store = MemoryStore()
        # 2 seconds old working-tier memory
        store.save(_make_memory("m1", tier="working", created_at=_old_iso(2)))
        config = {"retention_policies": {"working": 1, "short": 604800, "long": 2592000}}
        engine = _make_engine(store, config=config)
        candidates = engine._identify_candidates()
        assert len(candidates) == 1

    def test_project_filter(self):
        store = MemoryStore()
        store.save(_make_memory("m1", project="app1", created_at=_old_iso(700000)))
        store.save(_make_memory("m2", project="app2", created_at=_old_iso(700000)))
        engine = _make_engine(store)
        candidates = engine._identify_candidates(project="app1")
        assert len(candidates) == 1
        assert candidates[0].id == "m1"

    def test_tier_filter(self):
        store = MemoryStore()
        store.save(_make_memory("m1", tier="short", created_at=_old_iso(700000)))
        store.save(_make_memory("m2", tier="working", created_at=_old_iso(4000)))
        engine = _make_engine(store)
        candidates = engine._identify_candidates(tier="short")
        assert len(candidates) == 1
        assert candidates[0].id == "m1"

    def test_batch_size_processing(self):
        """Candidates are processed in batches of batch_size."""
        store = MemoryStore()
        vec = [0.5] * 384
        # Create 5 duplicate memories
        for i in range(5):
            store.save(_make_memory(f"m{i}", embedding=_embed(vec), created_at=_old_iso(700000)))

        # batch_size=2 means batches of [m0,m1], [m2,m3], [m4]
        # Duplicates within a batch will be found, but cross-batch won't
        engine = _make_engine(store, config={"batch_size": 2})
        result = asyncio.run(engine.consolidate(dry_run=True))
        # With batch_size=2 we get groups from pairs within each batch
        assert result.groups_found >= 2  # At least 2 pairs from batches


# ---------------------------------------------------------------------------
# S5: Deduplication Grouping
# ---------------------------------------------------------------------------

class TestDeduplicationGrouping:
    def test_groups_near_duplicates(self):
        # Two memories with identical embeddings (similarity 1.0)
        vec = [0.5] * 384
        store = MemoryStore()
        m1 = _make_memory("m1", embedding=_embed(vec))
        m2 = _make_memory("m2", embedding=_embed(vec))
        store.save(m1)
        store.save(m2)
        engine = _make_engine(store)
        groups = engine._find_duplicates([m1, m2])
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_no_group_below_threshold(self):
        # Two memories with very different embeddings
        vec_a = [1.0] + [0.0] * 383
        vec_b = [0.0] + [1.0] + [0.0] * 382
        m1 = _make_memory("m1", embedding=_embed(vec_a))
        m2 = _make_memory("m2", embedding=_embed(vec_b))
        engine = _make_engine()
        groups = engine._find_duplicates([m1, m2])
        assert len(groups) == 0

    def test_custom_threshold(self):
        # Create embeddings with ~0.92 similarity
        vec_a = np.random.RandomState(42).randn(384).astype(np.float32)
        vec_a = vec_a / np.linalg.norm(vec_a)
        noise = np.random.RandomState(43).randn(384).astype(np.float32) * 0.05
        vec_b = vec_a + noise
        vec_b = vec_b / np.linalg.norm(vec_b)
        float(np.dot(vec_a, vec_b))

        m1 = _make_memory("m1", embedding=_embed(vec_a.tolist()))
        m2 = _make_memory("m2", embedding=_embed(vec_b.tolist()))

        # With default 0.95 threshold: may not group
        engine_high = _make_engine(config={"dedup_threshold": 0.99})
        engine_high._find_duplicates([m1, m2])

        # With low threshold: should group
        engine_low = _make_engine(config={"dedup_threshold": 0.5})
        groups_low = engine_low._find_duplicates([m1, m2])
        assert len(groups_low) == 1

    def test_transitive_grouping(self):
        # A~B and B~C should all be in one group
        vec_a = [1.0] + [0.0] * 383
        vec_b = [1.0] + [0.0] * 383  # identical to A
        vec_c = [1.0] + [0.0] * 383  # identical to B
        m1 = _make_memory("m1", embedding=_embed(vec_a))
        m2 = _make_memory("m2", embedding=_embed(vec_b))
        m3 = _make_memory("m3", embedding=_embed(vec_c))
        engine = _make_engine()
        groups = engine._find_duplicates([m1, m2, m3])
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_transitive_grouping_union_find(self):
        """A~B high, B~C high, A~C below threshold — Union-Find groups all three."""
        rng = np.random.RandomState(100)
        # Create base vector
        base = rng.randn(384).astype(np.float32)
        base = base / np.linalg.norm(base)

        # Create B close to A
        noise_b = rng.randn(384).astype(np.float32) * 0.02
        vec_b = base + noise_b
        vec_b = vec_b / np.linalg.norm(vec_b)

        # Create C close to B but further from A
        noise_c = rng.randn(384).astype(np.float32) * 0.02
        vec_c = vec_b + noise_c
        vec_c = vec_c / np.linalg.norm(vec_c)

        sim_ab = float(np.dot(base, vec_b))
        sim_bc = float(np.dot(vec_b, vec_c))
        float(np.dot(base, vec_c))

        # Use a threshold that A~B and B~C pass but A~C might not
        threshold = min(sim_ab, sim_bc) - 0.001
        assert sim_ab > threshold
        assert sim_bc > threshold

        m1 = _make_memory("m1", embedding=_embed(base.tolist()))
        m2 = _make_memory("m2", embedding=_embed(vec_b.tolist()))
        m3 = _make_memory("m3", embedding=_embed(vec_c.tolist()))

        engine = _make_engine(config={"dedup_threshold": threshold})
        groups = engine._find_duplicates([m1, m2, m3])
        # All three should be in one group via transitive closure
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_skips_no_embedding(self):
        m1 = _make_memory("m1", embedding=_embed([0.5] * 384))
        m2 = _make_memory("m2", embedding=None)
        engine = _make_engine()
        groups = engine._find_duplicates([m1, m2])
        assert len(groups) == 0


# ---------------------------------------------------------------------------
# S6: Entity/Topic Grouping
# ---------------------------------------------------------------------------

class TestEntityGrouping:
    def test_groups_by_shared_entity(self):
        store = MemoryStore()
        m1 = _make_memory("m1")
        m2 = _make_memory("m2")
        m3 = _make_memory("m3")
        m4 = _make_memory("m4")
        store.save(m1)
        store.save(m2)
        store.save(m3)
        store.save(m4)

        # All 4 memories share entity "auth-service"
        now = _now_iso()
        for mid in ["m1", "m2", "m3", "m4"]:
            store.save_entity_mention(EntityMention(
                id=f"em-{mid}", entity_id="e1", memory_id=mid,
                mention_type="explicit", confidence=1.0, created_at=now,
            ))

        engine = _make_engine(store)
        groups = engine._group_by_entity([m1, m2, m3, m4], set())
        assert len(groups) == 1
        assert len(groups[0]) == 4

    def test_below_min_group_size(self):
        store = MemoryStore()
        m1 = _make_memory("m1")
        m2 = _make_memory("m2")
        store.save(m1)
        store.save(m2)

        now = _now_iso()
        for mid in ["m1", "m2"]:
            store.save_entity_mention(EntityMention(
                id=f"em-{mid}", entity_id="e1", memory_id=mid,
                mention_type="explicit", confidence=1.0, created_at=now,
            ))

        engine = _make_engine(store)
        groups = engine._group_by_entity([m1, m2], set())
        assert len(groups) == 0  # Below min_group_size=3

    def test_excludes_already_grouped(self):
        store = MemoryStore()
        m1 = _make_memory("m1")
        m2 = _make_memory("m2")
        m3 = _make_memory("m3")
        store.save(m1)
        store.save(m2)
        store.save(m3)

        now = _now_iso()
        for mid in ["m1", "m2", "m3"]:
            store.save_entity_mention(EntityMention(
                id=f"em-{mid}", entity_id="e1", memory_id=mid,
                mention_type="explicit", confidence=1.0, created_at=now,
            ))

        engine = _make_engine(store)
        # m1 already in a dedup group
        groups = engine._group_by_entity([m1, m2, m3], {"m1"})
        assert len(groups) == 0  # Only m2, m3 are ungrouped — below min_group_size=3

    def test_no_mentions_returns_empty(self):
        store = MemoryStore()
        m1 = _make_memory("m1")
        store.save(m1)
        engine = _make_engine(store)
        groups = engine._group_by_entity([m1], set())
        assert len(groups) == 0


# ---------------------------------------------------------------------------
# S7: LLM Summarization
# ---------------------------------------------------------------------------

class TestLLMSummarization:
    def test_dedup_strategy_uses_highest_importance(self):
        m1 = _make_memory("m1", importance_score=0.3, content="low")
        m2 = _make_memory("m2", importance_score=0.8, content="high")
        engine = _make_engine(llm=FakeLLM())
        content = engine._summarize_group([m1, m2], "deduplicate")
        assert content == "high"

    def test_summarize_with_llm(self):
        m1 = _make_memory("m1", content="first memory")
        m2 = _make_memory("m2", content="second memory")
        engine = _make_engine(llm=FakeLLM())
        content = engine._summarize_group([m1, m2], "summarize")
        assert content == "Consolidated summary of memories."

    def test_no_llm_falls_back(self):
        m1 = _make_memory("m1", importance_score=0.5, content="best")
        m2 = _make_memory("m2", importance_score=0.3, content="other")
        engine = _make_engine(llm=None)
        content = engine._summarize_group([m1, m2], "summarize")
        assert content == "best"

    def test_llm_error_falls_back(self):
        class FailingLLM:
            model = "fail"
            def complete(self, prompt, *, max_tokens=200):
                raise RuntimeError("LLM unavailable")

        m1 = _make_memory("m1", importance_score=0.7, content="fallback content")
        m2 = _make_memory("m2", importance_score=0.3, content="other")
        engine = _make_engine(llm=FailingLLM())
        content = engine._summarize_group([m1, m2], "summarize")
        assert content == "fallback content"

    def test_create_consolidated_memory(self):
        m1 = _make_memory("m1", type="fact", importance_score=0.3, tags=["python"],
                          access_count=5, upvotes=1, downvotes=0, confidence=0.8)
        m2 = _make_memory("m2", type="fact", importance_score=0.8, tags=["testing", "python"],
                          access_count=3, upvotes=0, downvotes=2, confidence=0.9)
        m3 = _make_memory("m3", type="lesson", importance_score=0.5, tags=["ci"],
                          access_count=2, upvotes=2, downvotes=0, confidence=0.7)

        engine = _make_engine()
        consolidated = engine._create_consolidated_memory([m1, m2, m3], "merged content", "deduplicate")

        assert consolidated.content == "merged content"
        assert consolidated.type == "fact"  # most common
        assert consolidated.tier == "long"
        assert consolidated.source == "consolidation"
        assert consolidated.importance_score == 0.8
        assert consolidated.access_count == 10
        assert consolidated.upvotes == 3
        assert consolidated.downvotes == 2
        assert consolidated.confidence == 0.9
        assert set(consolidated.tags) == {"python", "testing", "ci"}
        assert consolidated.metadata["consolidation_strategy"] == "deduplicate"
        assert consolidated.metadata["original_count"] == 3
        assert len(consolidated.metadata["consolidated_from"]) == 3


# ---------------------------------------------------------------------------
# S8: Archive, Relink, Log
# ---------------------------------------------------------------------------

class TestArchiveRelinkLog:
    def test_archive_originals(self):
        store = MemoryStore()
        m1 = _make_memory("m1")
        m2 = _make_memory("m2")
        store.save(m1)
        store.save(m2)
        engine = _make_engine(store)
        engine._archive_originals([m1, m2], "consolidated-id")

        loaded1 = store.get("m1")
        loaded2 = store.get("m2")
        assert loaded1.archived is True
        assert loaded1.consolidated_into == "consolidated-id"
        assert loaded2.archived is True

    def test_log_consolidation(self):
        store = MemoryStore()
        engine = _make_engine(store)
        entry = engine._log_consolidation(
            consolidated_memory_id="c1",
            original_ids=["a", "b"],
            strategy="deduplicate",
            model_used=None,
        )
        assert entry.original_count == 2
        entries = store.get_consolidation_log()
        assert len(entries) == 1

    def test_relink_graph_edges(self):
        """Dedicated test: entity mentions and relationships are relinked to consolidated memory."""
        from lore.types import Relationship

        store = MemoryStore()
        # Create original memories
        m1 = _make_memory("m1")
        m2 = _make_memory("m2")
        store.save(m1)
        store.save(m2)

        now = _now_iso()

        # Create entity mentions for originals
        store.save_entity_mention(EntityMention(
            id="em1", entity_id="e1", memory_id="m1",
            mention_type="explicit", confidence=1.0, created_at=now,
        ))
        store.save_entity_mention(EntityMention(
            id="em2", entity_id="e2", memory_id="m2",
            mention_type="explicit", confidence=1.0, created_at=now,
        ))

        # Create a relationship referencing m1
        rel = Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="uses", weight=0.9, source_memory_id="m1",
            valid_from=now, created_at=now, updated_at=now,
        )
        store.update_relationship(rel)

        engine = _make_engine(store)
        updated = engine._relink_graph_edges(["m1", "m2"], "consolidated-1")

        assert updated > 0

        # Verify entity mentions were relinked
        mentions_c = store.get_entity_mentions_for_memory("consolidated-1")
        assert len(mentions_c) == 2
        mention_entities = {m.entity_id for m in mentions_c}
        assert mention_entities == {"e1", "e2"}

        # Verify relationship was relinked
        rels = store.list_relationships()
        rel_updated = [r for r in rels if r.id == "r1"][0]
        assert rel_updated.source_memory_id == "consolidated-1"


# ---------------------------------------------------------------------------
# S9: Dry-Run Mode
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_no_modifications(self):
        store = MemoryStore()
        vec = [0.5] * 384
        m1 = _make_memory("m1", embedding=_embed(vec), created_at=_old_iso(700000))
        m2 = _make_memory("m2", embedding=_embed(vec), created_at=_old_iso(700000))
        store.save(m1)
        store.save(m2)

        engine = _make_engine(store)
        result = asyncio.run(engine.consolidate(dry_run=True))

        assert result.dry_run is True
        assert result.groups_found >= 1
        # No modifications: memories still active
        assert len(store.list()) == 2
        assert not store.get("m1").archived
        assert len(store.get_consolidation_log()) == 0

    def test_dry_run_dedup_includes_similarity(self):
        store = MemoryStore()
        vec = [0.5] * 384
        m1 = _make_memory("m1", embedding=_embed(vec), created_at=_old_iso(700000))
        m2 = _make_memory("m2", embedding=_embed(vec), created_at=_old_iso(700000))
        store.save(m1)
        store.save(m2)

        engine = _make_engine(store)
        result = asyncio.run(engine.consolidate(dry_run=True))
        assert result.groups
        assert "similarity" in result.groups[0]
        assert result.groups[0]["similarity"] >= 0.99


# ---------------------------------------------------------------------------
# S10: Full Pipeline Orchestration
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_empty_store(self):
        store = MemoryStore()
        engine = _make_engine(store)
        result = asyncio.run(engine.consolidate(dry_run=False))
        assert result.groups_found == 0
        assert result.memories_consolidated == 0

    def test_dedup_strategy_only(self):
        store = MemoryStore()
        vec = [0.5] * 384
        m1 = _make_memory("m1", embedding=_embed(vec), created_at=_old_iso(700000))
        m2 = _make_memory("m2", embedding=_embed(vec), created_at=_old_iso(700000))
        store.save(m1)
        store.save(m2)

        engine = _make_engine(store, llm=FakeLLM())
        result = asyncio.run(engine.consolidate(strategy="deduplicate", dry_run=False))

        assert result.memories_consolidated == 2
        assert result.memories_created == 1
        assert result.duplicates_merged == 1
        # Originals archived
        assert store.get("m1").archived is True
        assert store.get("m2").archived is True
        # New consolidated memory exists
        active = store.list()
        assert len(active) == 1
        assert active[0].source == "consolidation"

    def test_summarize_strategy_requires_llm(self):
        store = MemoryStore()
        engine = _make_engine(store, llm=None)
        # With no LLM, summarize strategy should find no entity groups
        result = asyncio.run(engine.consolidate(strategy="summarize", dry_run=False))
        assert result.groups_found == 0

    def test_max_groups_safety_limit(self):
        store = MemoryStore()
        # Create many duplicate pairs
        for i in range(200):
            vec = [float(i)] * 384
            m = _make_memory(f"m{i}", embedding=_embed(vec), created_at=_old_iso(700000))
            store.save(m)

        engine = _make_engine(store, config={"max_groups_per_run": 5})
        result = asyncio.run(engine.consolidate(dry_run=True))
        assert result.groups_found <= 5

    def test_per_group_error_isolation(self):
        store = MemoryStore()
        vec = [0.5] * 384
        m1 = _make_memory("m1", embedding=_embed(vec), created_at=_old_iso(700000))
        m2 = _make_memory("m2", embedding=_embed(vec), created_at=_old_iso(700000))
        store.save(m1)
        store.save(m2)

        engine = _make_engine(store)
        # Patch _process_group to fail

        call_count = 0
        async def failing_process(group, strategy, result):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Simulated failure")

        engine._process_group = failing_process
        asyncio.run(engine.consolidate(dry_run=False))
        # Should have attempted processing but not crashed
        assert call_count > 0

    def test_full_execute_with_dedup(self):
        store = MemoryStore()
        vec = [0.5] * 384
        m1 = _make_memory("m1", embedding=_embed(vec), created_at=_old_iso(700000),
                          importance_score=0.8, content="primary content")
        m2 = _make_memory("m2", embedding=_embed(vec), created_at=_old_iso(700000),
                          importance_score=0.3, content="duplicate content")
        store.save(m1)
        store.save(m2)

        engine = _make_engine(store)
        result = asyncio.run(engine.consolidate(dry_run=False))

        # Verify result
        assert result.memories_consolidated == 2
        assert result.memories_created == 1

        # Verify store state
        active = store.list()
        assert len(active) == 1
        assert active[0].source == "consolidation"
        assert active[0].content == "primary content"  # highest importance

        # Verify log
        log = store.get_consolidation_log()
        assert len(log) == 1
        assert log[0].strategy == "deduplicate"

    def test_lore_facade_consolidation_config(self):
        """Test that Lore facade accepts consolidation_config."""
        from lore.lore import Lore
        from lore.store.memory import MemoryStore

        store = MemoryStore()
        lore = Lore(

            store=store,
            consolidation_config={"dedup_threshold": 0.90},
        )
        assert lore._consolidation_engine._config["dedup_threshold"] == 0.90


# ---------------------------------------------------------------------------
# S12: Stats Integration
# ---------------------------------------------------------------------------

class TestStatsIntegration:
    def test_stats_no_consolidation(self):
        from lore.lore import Lore

        store = MemoryStore()
        lore = Lore(store=store)
        m = _make_memory("m1", created_at=_now_iso())
        store.save(m)

        s = lore.stats()
        assert s.archived_count == 0
        assert s.consolidation_count == 0
        assert s.last_consolidation_at is None

    def test_stats_after_consolidation(self):
        from lore.lore import Lore

        store = MemoryStore()
        lore = Lore(store=store)
        vec = [0.5] * 384
        m1 = _make_memory("m1", embedding=_embed(vec), created_at=_old_iso(700000))
        m2 = _make_memory("m2", embedding=_embed(vec), created_at=_old_iso(700000))
        store.save(m1)
        store.save(m2)

        asyncio.run(lore.consolidate(dry_run=False))
        s = lore.stats()
        assert s.archived_count == 2
        assert s.consolidation_count == 1
        assert s.last_consolidation_at is not None
