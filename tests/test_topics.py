"""Tests for E4 — Topic Notes / Auto-Summaries."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from lore import Lore
from lore.graph.cache import TopicSummaryCache
from lore.graph.entities import EntityManager
from lore.store.memory import MemoryStore
from lore.types import (
    Entity,
    EntityMention,
    Memory,
    RelatedEntity,
    Relationship,
    TopicDetail,
    TopicSummary,
)


def _stub_embed(text: str):
    return [0.0] * 384


def _make_lore(**kwargs) -> Lore:
    return Lore(
        store=MemoryStore(),
        embedding_fn=_stub_embed,
        knowledge_graph=True,
        **kwargs,
    )


def _populate_entities(store: MemoryStore, count: int = 5) -> list:
    """Create entities with varying mention counts and linked memories."""
    from datetime import datetime, timezone

    from ulid import ULID

    now = datetime.now(timezone.utc).isoformat()
    entities = []
    for i in range(count):
        entity = Entity(
            id=str(ULID()),
            name=f"entity_{i}",
            entity_type="concept" if i % 2 == 0 else "project",
            mention_count=i + 1,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        store.save_entity(entity)
        entities.append(entity)

        # Create linked memories and mentions
        for j in range(i + 1):
            mem = Memory(
                id=str(ULID()),
                content=f"Memory about {entity.name} item {j}",
                type="general",
                created_at=now,
                updated_at=now,
            )
            store.save(mem)
            mention = EntityMention(
                id=str(ULID()),
                entity_id=entity.id,
                memory_id=mem.id,
                mention_type="explicit",
                confidence=1.0,
                created_at=now,
            )
            store.save_entity_mention(mention)

    return entities


# ── E4-S1: Topic data types ──────────────────────────────────────


class TestTopicDataTypes:
    def test_topic_summary_creation(self):
        ts = TopicSummary(
            entity_id="id1",
            name="auth",
            entity_type="concept",
            mention_count=5,
            first_seen_at="2026-01-01",
            last_seen_at="2026-03-14",
        )
        assert ts.name == "auth"
        assert ts.related_entity_count == 0  # default

    def test_topic_detail_creation(self):
        entity = Entity(
            id="e1", name="test", entity_type="concept",
            created_at="", updated_at="",
        )
        td = TopicDetail(
            entity=entity,
            related_entities=[],
            memories=[],
        )
        assert td.summary is None
        assert td.summary_method is None
        assert td.memory_count == 0

    def test_related_entity_creation(self):
        re = RelatedEntity(
            name="postgres",
            entity_type="tool",
            relationship="uses",
            direction="outgoing",
        )
        assert re.direction == "outgoing"

    def test_types_importable(self):
        from lore.types import RelatedEntity, TopicDetail, TopicSummary
        assert TopicSummary is not None
        assert TopicDetail is not None
        assert RelatedEntity is not None


# ── E4-S2: TopicSummaryCache ─────────────────────────────────────


class TestTopicSummaryCache:
    def test_set_then_get(self):
        cache = TopicSummaryCache(ttl_seconds=3600)
        cache.set("e1", "Summary text", "llm")
        result = cache.get("e1")
        assert result == ("Summary text", "llm")

    def test_get_missing_key(self):
        cache = TopicSummaryCache()
        assert cache.get("missing") is None

    def test_ttl_expiry(self):
        cache = TopicSummaryCache(ttl_seconds=0)  # immediate expiry
        cache.set("e1", "old", "llm")
        time.sleep(0.01)
        assert cache.get("e1") is None

    def test_invalidate(self):
        cache = TopicSummaryCache()
        cache.set("e1", "cached", "llm")
        cache.invalidate("e1")
        assert cache.get("e1") is None

    def test_independent_entries(self):
        cache = TopicSummaryCache()
        cache.set("e1", "summary1", "llm")
        cache.set("e2", "summary2", "structured")
        assert cache.get("e1") == ("summary1", "llm")
        assert cache.get("e2") == ("summary2", "structured")
        cache.invalidate("e1")
        assert cache.get("e1") is None
        assert cache.get("e2") is not None


# ── E4-S3: Cache invalidation on entity mention ──────────────────


class TestCacheInvalidation:
    def test_ingest_enrichment_invalidates_cache(self):
        store = MemoryStore()
        cache = TopicSummaryCache()
        mgr = EntityManager(store, topic_summary_cache=cache)

        # Create entity and cache a summary
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()
        entity = Entity(
            id=str(ULID()), name="test", entity_type="concept",
            first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
        )
        store.save_entity(entity)
        cache.set(entity.id, "old summary", "llm")

        # Ingest enrichment mentioning this entity
        mem = Memory(id=str(ULID()), content="about test", created_at=now, updated_at=now)
        store.save(mem)
        mgr.ingest_from_enrichment(mem.id, [{"name": "test", "type": "concept"}])

        # Cache should be invalidated
        assert cache.get(entity.id) is None

    def test_ingest_fact_invalidates_cache(self):
        store = MemoryStore()
        cache = TopicSummaryCache()
        mgr = EntityManager(store, topic_summary_cache=cache)

        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()

        # Pre-create entities
        e1 = Entity(id=str(ULID()), name="python", entity_type="language",
                     first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now)
        e2 = Entity(id=str(ULID()), name="flask", entity_type="framework",
                     first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now)
        store.save_entity(e1)
        store.save_entity(e2)
        cache.set(e1.id, "python summary", "llm")
        cache.set(e2.id, "flask summary", "llm")

        # Create a fact
        class FakeFact:
            subject = "python"
            object = "flask"
            confidence = 0.9

        mem = Memory(id=str(ULID()), content="python uses flask", created_at=now, updated_at=now)
        store.save(mem)
        mgr.ingest_from_fact(mem.id, FakeFact())

        # Both caches should be invalidated
        assert cache.get(e1.id) is None
        assert cache.get(e2.id) is None

    def test_entity_manager_without_cache(self):
        store = MemoryStore()
        mgr = EntityManager(store)  # No cache
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()
        mem = Memory(id=str(ULID()), content="test", created_at=now, updated_at=now)
        store.save(mem)
        # Should not raise
        mgr.ingest_from_enrichment(mem.id, [{"name": "foo", "type": "concept"}])


# ── E4-S4: Lore.list_topics() ────────────────────────────────────


class TestListTopics:
    def test_default_threshold(self):
        lore = _make_lore()
        _populate_entities(lore._store, count=5)
        # Entities have mention_count 1..5
        # Default threshold is 3, so entities with 3, 4, 5 mentions qualify
        results = lore.list_topics()
        assert len(results) == 3

    def test_custom_threshold(self):
        lore = _make_lore()
        _populate_entities(lore._store, count=5)
        results = lore.list_topics(min_mentions=5)
        assert len(results) == 1
        assert results[0].mention_count == 5

    def test_sorted_by_mention_count_desc(self):
        lore = _make_lore()
        _populate_entities(lore._store, count=5)
        results = lore.list_topics(min_mentions=1)
        counts = [r.mention_count for r in results]
        assert counts == sorted(counts, reverse=True)

    def test_filter_by_entity_type(self):
        lore = _make_lore()
        _populate_entities(lore._store, count=5)
        results = lore.list_topics(entity_type="project", min_mentions=1)
        for r in results:
            assert r.entity_type == "project"

    def test_limit(self):
        lore = _make_lore()
        _populate_entities(lore._store, count=10)
        results = lore.list_topics(min_mentions=1, limit=2)
        assert len(results) == 2

    def test_empty_when_no_entities_meet_threshold(self):
        lore = _make_lore()
        results = lore.list_topics()
        assert results == []

    def test_empty_when_knowledge_graph_disabled(self):
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed, knowledge_graph=False)
        results = lore.list_topics()
        assert results == []

    def test_related_entity_count(self):
        lore = _make_lore()
        entities = _populate_entities(lore._store, count=4)
        # Add a relationship between entity_2 and entity_3
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()
        rel = Relationship(
            id=str(ULID()),
            source_entity_id=entities[2].id,
            target_entity_id=entities[3].id,
            rel_type="uses",
            created_at=now, updated_at=now,
        )
        lore._store.save_relationship(rel)
        results = lore.list_topics(min_mentions=3)
        # entity_2 (mention_count=3) or entity_3 (mention_count=4) should have related_entity_count > 0
        has_related = any(r.related_entity_count > 0 for r in results)
        assert has_related


# ── E4-S5: Lore.topic_detail() structured path ───────────────────


class TestTopicDetailStructured:
    def _setup_entity(self, lore):
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()

        entity = Entity(
            id=str(ULID()), name="postgres", entity_type="tool",
            aliases=["pg", "postgresql"],
            mention_count=3, first_seen_at=now, last_seen_at=now,
            created_at=now, updated_at=now,
        )
        lore._store.save_entity(entity)

        # Create linked memories
        mems = []
        for i in range(3):
            mem = Memory(
                id=str(ULID()), content=f"Memory about postgres #{i}",
                type="general", created_at=now, updated_at=now,
            )
            lore._store.save(mem)
            lore._store.save_entity_mention(EntityMention(
                id=str(ULID()), entity_id=entity.id,
                memory_id=mem.id, mention_type="explicit",
                confidence=1.0, created_at=now,
            ))
            mems.append(mem)

        return entity, mems

    def test_resolve_by_name(self):
        lore = _make_lore()
        entity, _ = self._setup_entity(lore)
        detail = lore.topic_detail("postgres")
        assert detail is not None
        assert detail.entity.name == "postgres"

    def test_resolve_by_alias(self):
        lore = _make_lore()
        entity, _ = self._setup_entity(lore)
        detail = lore.topic_detail("pg")
        assert detail is not None
        assert detail.entity.name == "postgres"

    def test_returns_none_for_nonexistent(self):
        lore = _make_lore()
        assert lore.topic_detail("nonexistent") is None

    def test_memories_sorted_desc(self):
        lore = _make_lore()
        self._setup_entity(lore)
        detail = lore.topic_detail("postgres")
        assert detail is not None
        dates = [m.created_at for m in detail.memories]
        assert dates == sorted(dates, reverse=True)

    def test_max_memories_cap(self):
        lore = _make_lore()
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()
        entity = Entity(
            id=str(ULID()), name="big", entity_type="concept",
            mention_count=10, first_seen_at=now, last_seen_at=now,
            created_at=now, updated_at=now,
        )
        lore._store.save_entity(entity)
        for i in range(10):
            mem = Memory(id=str(ULID()), content=f"mem {i}",
                         created_at=now, updated_at=now)
            lore._store.save(mem)
            lore._store.save_entity_mention(EntityMention(
                id=str(ULID()), entity_id=entity.id,
                memory_id=mem.id, created_at=now,
            ))
        detail = lore.topic_detail("big", max_memories=3)
        assert len(detail.memories) == 3
        assert detail.memory_count == 10

    def test_related_entities(self):
        lore = _make_lore()
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()

        e1 = Entity(id=str(ULID()), name="source", entity_type="concept",
                     first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now)
        e2 = Entity(id=str(ULID()), name="target", entity_type="tool",
                     first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now)
        lore._store.save_entity(e1)
        lore._store.save_entity(e2)

        rel = Relationship(
            id=str(ULID()), source_entity_id=e1.id, target_entity_id=e2.id,
            rel_type="uses", created_at=now, updated_at=now,
        )
        lore._store.save_relationship(rel)

        detail = lore.topic_detail("source")
        assert detail is not None
        assert len(detail.related_entities) == 1
        assert detail.related_entities[0].name == "target"
        assert detail.related_entities[0].direction == "outgoing"

    def test_no_relationships(self):
        lore = _make_lore()
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()
        entity = Entity(id=str(ULID()), name="lonely", entity_type="concept",
                        first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now)
        lore._store.save_entity(entity)
        detail = lore.topic_detail("lonely")
        assert detail is not None
        assert detail.related_entities == []

    def test_structured_method_without_llm(self):
        lore = _make_lore()
        self._setup_entity(lore)
        detail = lore.topic_detail("postgres")
        assert detail.summary_method == "structured"
        assert detail.summary is None

    def test_knowledge_graph_disabled(self):
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed, knowledge_graph=False)
        assert lore.topic_detail("anything") is None


# ── E4-S6: LLM topic summary generation ──────────────────────────


class TestTopicLLMSummary:
    def test_llm_summary_generated(self):
        lore = _make_lore()
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()
        entity = Entity(
            id=str(ULID()), name="llmtopic", entity_type="concept",
            mention_count=3, first_seen_at=now, last_seen_at=now,
            created_at=now, updated_at=now,
        )
        lore._store.save_entity(entity)
        mem = Memory(id=str(ULID()), content="about llmtopic",
                     created_at=now, updated_at=now)
        lore._store.save(mem)
        lore._store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=entity.id,
            memory_id=mem.id, created_at=now,
        ))

        mock_pipeline = MagicMock()
        mock_pipeline._llm.complete.return_value = "LLMTopic is a core concept used for testing."
        lore._enrichment_pipeline = mock_pipeline

        detail = lore.topic_detail("llmtopic")
        assert detail.summary == "LLMTopic is a core concept used for testing."
        assert detail.summary_method == "llm"
        assert detail.summary_generated_at is not None

    def test_llm_summary_cached(self):
        lore = _make_lore()
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()
        entity = Entity(
            id=str(ULID()), name="cached", entity_type="concept",
            first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
        )
        lore._store.save_entity(entity)

        # Pre-populate cache
        lore._topic_summary_cache.set(entity.id, "Cached summary", "llm")

        detail = lore.topic_detail("cached")
        assert detail.summary == "Cached summary"
        assert detail.summary_method == "llm"

    def test_llm_failure_falls_back(self):
        lore = _make_lore()
        from datetime import datetime, timezone

        from ulid import ULID
        now = datetime.now(timezone.utc).isoformat()
        entity = Entity(
            id=str(ULID()), name="failsafe", entity_type="concept",
            first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
        )
        lore._store.save_entity(entity)
        mem = Memory(id=str(ULID()), content="about failsafe",
                     created_at=now, updated_at=now)
        lore._store.save(mem)
        lore._store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=entity.id,
            memory_id=mem.id, created_at=now,
        ))

        mock_pipeline = MagicMock()
        mock_pipeline._llm.complete.side_effect = RuntimeError("boom")
        lore._enrichment_pipeline = mock_pipeline

        detail = lore.topic_detail("failsafe")
        assert detail.summary is None
        assert detail.summary_method == "structured"


# ── E4-S7: MCP tools ─────────────────────────────────────────────


class TestTopicsMCP:
    @pytest.fixture
    def mock_lore(self):
        lore = _make_lore()
        _populate_entities(lore._store, count=5)
        with patch("lore.mcp.server._get_lore", return_value=lore):
            yield lore

    @pytest.fixture
    def mock_lore_no_graph(self):
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed, knowledge_graph=False)
        with patch("lore.mcp.server._get_lore", return_value=lore):
            yield lore

    def test_topics_tool(self, mock_lore):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import topics
        result = topics(min_mentions=1)
        assert "Topics" in result
        assert "found" in result

    def test_topics_tool_no_graph(self, mock_lore_no_graph):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import topics
        result = topics()
        assert "knowledge graph" in result.lower()

    def test_topics_tool_empty(self, mock_lore):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import topics
        result = topics(min_mentions=100)
        assert "No topics found" in result

    def test_topic_detail_tool(self, mock_lore):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import topic_detail
        result = topic_detail("entity_4")
        assert "Topic:" in result
        assert "entity_4" in result

    def test_topic_detail_not_found(self, mock_lore):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import topic_detail
        result = topic_detail("nonexistent_xyz")
        assert "No topic found" in result

    def test_topic_detail_brief_vs_detailed(self, mock_lore):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import topic_detail
        brief = topic_detail("entity_4", format="brief")
        detailed = topic_detail("entity_4", format="detailed")
        # Detailed should have full content, brief may have truncated
        assert "Topic:" in brief
        assert "Topic:" in detailed


# ── E4-S9: CLI topics command ─────────────────────────────────────


class TestTopicsCLI:
    def test_cli_topics_list(self, capsys):
        lore = _make_lore()
        _populate_entities(lore._store, count=5)
        with patch("lore.cli._helpers._get_lore", return_value=lore):
            from lore.cli import main
            main(["topics", "--min-mentions", "1"])
        captured = capsys.readouterr()
        assert "Topics" in captured.out

    def test_cli_topics_detail(self, capsys):
        lore = _make_lore()
        _populate_entities(lore._store, count=5)
        with patch("lore.cli._helpers._get_lore", return_value=lore):
            from lore.cli import main
            main(["topics", "entity_4"])
        captured = capsys.readouterr()
        assert "Topic:" in captured.out
        assert "entity_4" in captured.out

    def test_cli_topics_no_graph(self, capsys):
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed, knowledge_graph=False)
        with patch("lore.cli._helpers._get_lore", return_value=lore):
            from lore.cli import main
            main(["topics"])
        captured = capsys.readouterr()
        assert "knowledge graph" in captured.out.lower()
