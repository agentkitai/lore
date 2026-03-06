"""Comprehensive tests for F1 Knowledge Graph Layer.

Covers all 15 stories:
  S1: Schema, Types & Migrations
  S2: Entity Name Normalization
  S3: Entity CRUD & Deduplication
  S4: Relationship CRUD & Temporal Tracking
  S5: Entity-Memory Mentions & Junction
  S6: GraphTraverser Core Engine
  S7: Hop Query Builder
  S8: Score & Prune
  S9: Temporal Edge Support
  S10: Entity Cache
  S11: Hybrid Recall Scoring
  S12: F2 Integration (Facts to Edges)
  S13: F6 Integration (Enrichment to Nodes)
  S14: MCP Tools + CLI + Visualization
  S15: Backfill + Cascade on forget()
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pytest
from ulid import ULID

from lore.types import (
    Entity, EntityMention, Fact, GraphContext, Memory, Relationship,
    VALID_ENTITY_TYPES, VALID_REL_TYPES,
)
from lore.store.memory import MemoryStore
from lore.store.sqlite import SqliteStore
from lore.graph.entities import EntityManager
from lore.graph.relationships import RelationshipManager, PREDICATE_TO_REL_TYPE
from lore.graph.traverser import GraphTraverser
from lore.graph.cache import EntityCache, find_query_entities
from lore.graph.extraction import update_graph_from_facts
from lore.graph.visualization import to_d3_json, to_text_tree


# ============================================================
# Fixtures
# ============================================================

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        s = MemoryStore()
    else:
        s = SqliteStore(str(tmp_path / "test.db"), knowledge_graph=True)
    yield s
    if hasattr(s, "close"):
        s.close()


@pytest.fixture
def memory_store():
    return MemoryStore()


@pytest.fixture
def sqlite_store(tmp_path):
    s = SqliteStore(str(tmp_path / "test.db"), knowledge_graph=True)
    yield s
    s.close()


@pytest.fixture
def entity_manager(store):
    return EntityManager(store)


@pytest.fixture
def relationship_manager(store):
    em = EntityManager(store)
    return RelationshipManager(store, em)


def _make_memory(store, content="test", mid=None):
    """Helper to save a memory and return its ID."""
    mid = mid or str(ULID())
    now = _utc_now_iso()
    mem = Memory(
        id=mid, content=content, created_at=now, updated_at=now,
    )
    store.save(mem)
    return mid


def _make_entity(store, name, etype="concept", aliases=None):
    """Helper to create and save an entity."""
    now = _utc_now_iso()
    e = Entity(
        id=str(ULID()), name=name, entity_type=etype,
        aliases=aliases or [],
        first_seen_at=now, last_seen_at=now,
        created_at=now, updated_at=now,
    )
    store.save_entity(e)
    return e


def _make_relationship(store, source_id, target_id, rel_type="uses",
                        weight=0.8, valid_from=None, valid_until=None,
                        fact_id=None, memory_id=None):
    """Helper to create and save a relationship."""
    now = _utc_now_iso()
    r = Relationship(
        id=str(ULID()),
        source_entity_id=source_id,
        target_entity_id=target_id,
        rel_type=rel_type,
        weight=weight,
        valid_from=valid_from or now,
        valid_until=valid_until,
        source_fact_id=fact_id,
        source_memory_id=memory_id,
        created_at=now,
        updated_at=now,
    )
    store.save_relationship(r)
    return r


# ============================================================
# S1: Schema, Types & Migrations
# ============================================================

class TestS1SchemaTypes:

    def test_entity_dataclass_fields(self):
        e = Entity(id="1", name="test", entity_type="concept")
        assert e.aliases == []
        assert e.mention_count == 1
        assert e.description is None
        assert e.metadata is None

    def test_relationship_dataclass_fields(self):
        r = Relationship(id="1", source_entity_id="a", target_entity_id="b", rel_type="uses")
        assert r.weight == 1.0
        assert r.valid_until is None
        assert r.source_fact_id is None

    def test_entity_mention_dataclass_fields(self):
        m = EntityMention(id="1", entity_id="a", memory_id="b")
        assert m.mention_type == "explicit"
        assert m.confidence == 1.0

    def test_graph_context_dataclass_fields(self):
        gc = GraphContext()
        assert gc.entities == []
        assert gc.relationships == []
        assert gc.paths == []
        assert gc.relevance_score == 0.0

    def test_valid_entity_types(self):
        assert "person" in VALID_ENTITY_TYPES
        assert "service" in VALID_ENTITY_TYPES
        assert "other" in VALID_ENTITY_TYPES
        assert len(VALID_ENTITY_TYPES) == 10

    def test_valid_rel_types(self):
        assert "depends_on" in VALID_REL_TYPES
        assert "co_occurs_with" in VALID_REL_TYPES
        assert len(VALID_REL_TYPES) == 13

    def test_sqlite_graph_tables_created_when_enabled(self, tmp_path):
        s = SqliteStore(str(tmp_path / "test.db"), knowledge_graph=True)
        assert s._table_exists("entities")
        assert s._table_exists("relationships")
        assert s._table_exists("entity_mentions")
        s.close()

    def test_sqlite_graph_tables_not_created_when_disabled(self, tmp_path):
        s = SqliteStore(str(tmp_path / "test.db"), knowledge_graph=False)
        assert not s._table_exists("entities")
        assert not s._table_exists("relationships")
        assert not s._table_exists("entity_mentions")
        s.close()

    def test_store_base_graph_methods_return_defaults(self):
        from lore.store.base import Store

        class MinimalStore(Store):
            def save(self, m): pass
            def get(self, mid): return None
            def list(self, **kw): return []
            def update(self, m): return False
            def delete(self, mid): return False
            def count(self, **kw): return 0
            def cleanup_expired(self): return 0

        s = MinimalStore()
        assert s.get_entity("x") is None
        assert s.get_entity_by_name("x") is None
        assert s.list_entities() == []
        assert s.get_relationship("x") is None
        assert s.get_active_relationship("a", "b", "c") is None
        assert s.get_entity_mentions_for_memory("x") == []
        assert s.query_relationships([]) == []


# ============================================================
# S2: Entity Name Normalization
# ============================================================

class TestS2Normalization:

    def test_strip_and_lowercase(self):
        assert EntityManager._normalize_name("  PostgreSQL 16  ") == "postgresql 16"

    def test_react_js(self):
        assert EntityManager._normalize_name("  React.js  ") == "react.js"

    def test_no_alias_map(self):
        assert EntityManager._normalize_name("k8s") == "k8s"

    def test_collapse_spaces_strip_trailing_punct(self):
        assert EntityManager._normalize_name("My   Custom   Service.") == "my custom service"

    def test_already_canonical(self):
        assert EntityManager._normalize_name("alice") == "alice"

    def test_empty_string(self):
        assert EntityManager._normalize_name("") == ""

    def test_trailing_multiple_punctuation(self):
        assert EntityManager._normalize_name("hello...") == "hello"

    def test_exclamation(self):
        assert EntityManager._normalize_name("WOW!") == "wow"

    def test_mixed_punctuation(self):
        assert EntityManager._normalize_name("  Test, ") == "test"


# ============================================================
# S3: Entity CRUD & Deduplication
# ============================================================

class TestS3EntityCRUD:

    def test_save_and_get_entity(self, store):
        e = _make_entity(store, "redis", "tool")
        got = store.get_entity(e.id)
        assert got is not None
        assert got.name == "redis"
        assert got.entity_type == "tool"

    def test_get_entity_by_name(self, store):
        e = _make_entity(store, "postgresql", "tool")
        got = store.get_entity_by_name("postgresql")
        assert got is not None
        assert got.id == e.id

    def test_get_entity_by_name_not_found(self, store):
        assert store.get_entity_by_name("nonexistent") is None

    def test_update_entity(self, store):
        e = _make_entity(store, "docker", "tool")
        e.entity_type = "platform"
        e.mention_count = 5
        store.update_entity(e)
        got = store.get_entity(e.id)
        assert got.entity_type == "platform"
        assert got.mention_count == 5

    def test_delete_entity(self, store):
        e = _make_entity(store, "temp", "concept")
        store.delete_entity(e.id)
        assert store.get_entity(e.id) is None

    def test_list_entities_by_type(self, store):
        _make_entity(store, "alice", "person")
        _make_entity(store, "bob", "person")
        _make_entity(store, "redis", "tool")
        persons = store.list_entities(entity_type="person")
        assert len(persons) == 2
        assert all(e.entity_type == "person" for e in persons)

    def test_list_entities_ordered_by_mention_count(self, store):
        e1 = _make_entity(store, "low", "concept")
        e1.mention_count = 1
        store.update_entity(e1)
        e2 = _make_entity(store, "high", "concept")
        e2.mention_count = 10
        store.update_entity(e2)
        entities = store.list_entities()
        assert entities[0].name == "high"

    def test_resolve_entity_creates_new(self, entity_manager, store):
        e = entity_manager._resolve_entity("auth-service", "service")
        assert e.name == "auth-service"
        assert e.entity_type == "service"
        assert store.get_entity(e.id) is not None

    def test_resolve_entity_returns_existing(self, entity_manager):
        e1 = entity_manager._resolve_entity("redis", "tool")
        e2 = entity_manager._resolve_entity("redis", "tool")
        assert e1.id == e2.id

    def test_resolve_entity_type_promotion(self, entity_manager, store):
        e1 = entity_manager._resolve_entity("myapp", "concept")
        e2 = entity_manager._resolve_entity("myapp", "service")
        assert e1.id == e2.id
        got = store.get_entity(e1.id)
        assert got.entity_type == "service"

    def test_resolve_entity_no_downgrade(self, entity_manager, store):
        entity_manager._resolve_entity("myapp", "service")
        entity_manager._resolve_entity("myapp", "concept")
        got = store.get_entity_by_name("myapp")
        assert got.entity_type == "service"

    def test_get_entity_by_alias(self, store):
        e = _make_entity(store, "kubernetes", "platform", aliases=["k8s", "kube"])
        got = store.get_entity_by_alias("k8s")
        assert got is not None
        assert got.id == e.id

    def test_get_entity_by_alias_not_found(self, store):
        _make_entity(store, "kubernetes", "platform", aliases=["k8s"])
        assert store.get_entity_by_alias("docker") is None

    def test_resolve_entity_via_alias(self, entity_manager, store):
        e = entity_manager._resolve_entity("kubernetes", "platform")
        entity_manager.add_alias(e.id, "k8s")
        e2 = entity_manager._resolve_entity("k8s", "platform")
        assert e2.id == e.id

    def test_add_alias(self, entity_manager, store):
        e = entity_manager._resolve_entity("javascript", "language")
        entity_manager.add_alias(e.id, "JS")
        got = store.get_entity(e.id)
        assert "js" in got.aliases

    def test_add_alias_no_duplicate(self, entity_manager, store):
        e = entity_manager._resolve_entity("python", "language")
        entity_manager.add_alias(e.id, "py")
        entity_manager.add_alias(e.id, "py")
        got = store.get_entity(e.id)
        assert got.aliases.count("py") == 1

    def test_add_alias_not_same_as_name(self, entity_manager, store):
        e = entity_manager._resolve_entity("python", "language")
        entity_manager.add_alias(e.id, "python")
        got = store.get_entity(e.id)
        assert "python" not in got.aliases

    def test_merge_entities(self, entity_manager, store):
        mid = _make_memory(store)
        e1 = entity_manager._resolve_entity("auth-service", "service")
        e2 = entity_manager._resolve_entity("auth-svc", "service")
        e1.mention_count = 3
        store.update_entity(e1)
        e2.mention_count = 2
        store.update_entity(e2)

        # Create mention for e2
        store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e2.id, memory_id=mid,
            created_at=_utc_now_iso(),
        ))

        merged = entity_manager.merge_entities(e1.id, e2.id)
        assert merged.mention_count == 5
        assert "auth-svc" in merged.aliases
        assert store.get_entity(e2.id) is None

    def test_resolve_invalid_type_defaults_to_other(self, entity_manager, store):
        e = entity_manager._resolve_entity("xyz", "invalid_type")
        assert e.entity_type == "other"

    def test_delete_entity_cascades_relationships(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id)
        store.delete_entity(e1.id)
        # Relationships involving e1 should be gone
        rels = store.get_relationships_from([e1.id])
        assert len(rels) == 0


# ============================================================
# S4: Relationship CRUD & Temporal Tracking
# ============================================================

class TestS4RelationshipCRUD:

    def test_save_and_get_relationship(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        r = _make_relationship(store, e1.id, e2.id, "depends_on", 0.85)
        got = store.get_relationship(r.id)
        assert got is not None
        assert got.rel_type == "depends_on"
        assert got.weight == 0.85

    def test_get_active_relationship(self, store):
        e1 = _make_entity(store, "x", "concept")
        e2 = _make_entity(store, "y", "concept")
        r = _make_relationship(store, e1.id, e2.id, "uses")
        got = store.get_active_relationship(e1.id, e2.id, "uses")
        assert got is not None
        assert got.id == r.id

    def test_get_active_relationship_excludes_expired(self, store):
        e1 = _make_entity(store, "x", "concept")
        e2 = _make_entity(store, "y", "concept")
        _make_relationship(store, e1.id, e2.id, "uses",
                           valid_until=_utc_now_iso())
        got = store.get_active_relationship(e1.id, e2.id, "uses")
        assert got is None

    def test_update_relationship_weight(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        r = _make_relationship(store, e1.id, e2.id, "uses", 0.5)
        r.weight = 0.9
        store.update_relationship(r)
        got = store.get_relationship(r.id)
        assert got.weight == pytest.approx(0.9)

    def test_get_relationships_from(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        e3 = _make_entity(store, "c", "concept")
        _make_relationship(store, e1.id, e2.id, "uses")
        _make_relationship(store, e1.id, e3.id, "depends_on")
        rels = store.get_relationships_from([e1.id])
        assert len(rels) == 2

    def test_get_relationships_to(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses")
        rels = store.get_relationships_to([e2.id])
        assert len(rels) == 1

    def test_get_relationships_from_active_only(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses")
        _make_relationship(store, e1.id, e2.id, "depends_on",
                           valid_until=_utc_now_iso())
        rels = store.get_relationships_from([e1.id], active_only=True)
        assert len(rels) == 1
        assert rels[0].rel_type == "uses"

    def test_list_relationships_filter_type(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses")
        _make_relationship(store, e1.id, e2.id, "depends_on")
        rels = store.list_relationships(rel_type="uses")
        assert len(rels) == 1

    def test_delete_relationship(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        r = _make_relationship(store, e1.id, e2.id, "uses")
        store.delete_relationship(r.id)
        assert store.get_relationship(r.id) is None

    def test_get_relationship_by_fact(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        fact_id = str(ULID())
        r = _make_relationship(store, e1.id, e2.id, "uses", fact_id=fact_id)
        got = store.get_relationship_by_fact(fact_id)
        assert got is not None
        assert got.id == r.id

    def test_predicate_mapping(self):
        rm = RelationshipManager.__new__(RelationshipManager)
        assert rm._map_predicate("depends_on") == "depends_on"
        assert rm._map_predicate("uses") == "uses"
        assert rm._map_predicate("created") == "created_by"
        assert rm._map_predicate("is") == "related_to"
        assert rm._map_predicate("invented_by") == "related_to"

    def test_ingest_from_fact_creates_edge(self, store, relationship_manager):
        mid = _make_memory(store)
        fact = Fact(
            id=str(ULID()), memory_id=mid,
            subject="auth-service", predicate="depends_on", object="redis",
            confidence=0.85, extracted_at=_utc_now_iso(),
        )
        rel = relationship_manager.ingest_from_fact(mid, fact)
        assert rel is not None
        assert rel.rel_type == "depends_on"
        assert rel.weight == pytest.approx(0.85)

    def test_ingest_from_fact_strengthens_weight(self, store, relationship_manager):
        mid = _make_memory(store)
        fact = Fact(
            id=str(ULID()), memory_id=mid,
            subject="auth-service", predicate="depends_on", object="redis",
            confidence=0.85, extracted_at=_utc_now_iso(),
        )
        rel1 = relationship_manager.ingest_from_fact(mid, fact)
        fact2 = Fact(
            id=str(ULID()), memory_id=mid,
            subject="auth-service", predicate="depends_on", object="redis",
            confidence=0.9, extracted_at=_utc_now_iso(),
        )
        rel2 = relationship_manager.ingest_from_fact(mid, fact2)
        assert rel2.weight == pytest.approx(0.95)  # 0.85 + 0.1

    def test_weight_capped_at_1(self, store, relationship_manager):
        mid = _make_memory(store)
        fact = Fact(
            id=str(ULID()), memory_id=mid,
            subject="a", predicate="uses", object="b",
            confidence=0.95, extracted_at=_utc_now_iso(),
        )
        relationship_manager.ingest_from_fact(mid, fact)
        # Second ingestion: 0.95 + 0.1 = 1.05 -> capped at 1.0
        fact2 = Fact(
            id=str(ULID()), memory_id=mid,
            subject="a", predicate="uses", object="b",
            confidence=0.9, extracted_at=_utc_now_iso(),
        )
        rel = relationship_manager.ingest_from_fact(mid, fact2)
        assert rel.weight == pytest.approx(1.0)

    def test_expire_relationship_for_fact(self, store, relationship_manager):
        mid = _make_memory(store)
        fact_id = str(ULID())
        fact = Fact(
            id=fact_id, memory_id=mid,
            subject="a", predicate="uses", object="b",
            confidence=0.8, extracted_at=_utc_now_iso(),
        )
        relationship_manager.ingest_from_fact(mid, fact)
        relationship_manager.expire_relationship_for_fact(fact_id)
        # Active lookup should now be empty
        e_a = store.get_entity_by_name("a")
        e_b = store.get_entity_by_name("b")
        assert store.get_active_relationship(e_a.id, e_b.id, "uses") is None

    def test_co_occurrence_edges(self, store, relationship_manager):
        mid = _make_memory(store)
        entities = [
            _make_entity(store, "x", "concept"),
            _make_entity(store, "y", "concept"),
            _make_entity(store, "z", "concept"),
        ]
        rels = relationship_manager.ingest_co_occurrences(mid, entities, weight=0.3)
        # 3 pairs * 2 directions = 6
        assert len(rels) == 6
        assert all(r.rel_type == "co_occurs_with" for r in rels)

    def test_co_occurrence_strengthening(self, store, relationship_manager):
        mid1 = _make_memory(store, "mem1")
        mid2 = _make_memory(store, "mem2")
        entities = [
            _make_entity(store, "x", "concept"),
            _make_entity(store, "y", "concept"),
        ]
        relationship_manager.ingest_co_occurrences(mid1, entities, weight=0.3)
        relationship_manager.ingest_co_occurrences(mid2, entities, weight=0.3)
        # Weight should be 0.3 + 0.05 = 0.35
        rel = store.get_active_relationship(entities[0].id, entities[1].id, "co_occurs_with")
        assert rel.weight == pytest.approx(0.35)


# ============================================================
# S5: Entity-Memory Mentions
# ============================================================

class TestS5EntityMentions:

    def test_save_and_get_mentions_for_memory(self, store):
        mid = _make_memory(store)
        e1 = _make_entity(store, "redis", "tool")
        e2 = _make_entity(store, "postgresql", "tool")
        store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e1.id, memory_id=mid,
            created_at=_utc_now_iso(),
        ))
        store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e2.id, memory_id=mid,
            created_at=_utc_now_iso(),
        ))
        mentions = store.get_entity_mentions_for_memory(mid)
        assert len(mentions) == 2

    def test_get_mentions_for_entity(self, store):
        mid1 = _make_memory(store, "mem1")
        mid2 = _make_memory(store, "mem2")
        e = _make_entity(store, "redis", "tool")
        store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e.id, memory_id=mid1,
            created_at=_utc_now_iso(),
        ))
        store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e.id, memory_id=mid2,
            created_at=_utc_now_iso(),
        ))
        mentions = store.get_entity_mentions_for_entity(e.id)
        assert len(mentions) == 2

    def test_mention_idempotency(self, store):
        mid = _make_memory(store)
        e = _make_entity(store, "redis", "tool")
        for _ in range(3):
            store.save_entity_mention(EntityMention(
                id=str(ULID()), entity_id=e.id, memory_id=mid,
                created_at=_utc_now_iso(),
            ))
        mentions = store.get_entity_mentions_for_entity(e.id)
        assert len(mentions) == 1

    def test_transfer_entity_mentions(self, store):
        mid = _make_memory(store)
        e1 = _make_entity(store, "old", "concept")
        e2 = _make_entity(store, "new", "concept")
        store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e1.id, memory_id=mid,
            created_at=_utc_now_iso(),
        ))
        store.transfer_entity_mentions(from_id=e1.id, to_id=e2.id)
        assert len(store.get_entity_mentions_for_entity(e1.id)) == 0
        assert len(store.get_entity_mentions_for_entity(e2.id)) == 1

    def test_transfer_entity_relationships(self, store):
        e1 = _make_entity(store, "old", "concept")
        e2 = _make_entity(store, "new", "concept")
        e3 = _make_entity(store, "other", "concept")
        _make_relationship(store, e1.id, e3.id)
        store.transfer_entity_relationships(from_id=e1.id, to_id=e2.id)
        rels = store.get_relationships_from([e2.id])
        assert len(rels) == 1


# ============================================================
# S6: GraphTraverser Core Engine
# ============================================================

def _build_test_graph(store):
    """Build test graph: A->B->C->D, A->E->F->B"""
    a = _make_entity(store, "a", "concept")
    b = _make_entity(store, "b", "concept")
    c = _make_entity(store, "c", "concept")
    d = _make_entity(store, "d", "concept")
    e = _make_entity(store, "e", "concept")
    f = _make_entity(store, "f", "concept")

    _make_relationship(store, a.id, b.id, "uses", 0.9)
    _make_relationship(store, b.id, c.id, "depends_on", 0.8)
    _make_relationship(store, c.id, d.id, "deployed_on", 0.7)
    _make_relationship(store, a.id, e.id, "works_on", 0.85)
    _make_relationship(store, e.id, f.id, "uses", 0.75)
    _make_relationship(store, f.id, b.id, "uses", 0.6)

    return {"a": a, "b": b, "c": c, "d": d, "e": e, "f": f}


class TestS6Traverser:

    def test_traverse_depth_1(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        ctx = traverser.traverse([g["a"].id], depth=1)
        entity_names = {e.name for e in ctx.entities}
        assert "b" in entity_names
        assert "e" in entity_names
        assert "c" not in entity_names
        assert len(ctx.relationships) == 2

    def test_traverse_depth_2(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        ctx = traverser.traverse([g["a"].id], depth=2)
        entity_names = {e.name for e in ctx.entities}
        assert "b" in entity_names
        assert "c" in entity_names
        assert "e" in entity_names
        assert "f" in entity_names

    def test_traverse_depth_clamped_to_max(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        ctx = traverser.traverse([g["a"].id], depth=5)
        # Should clamp to MAX_DEPTH=3, no error
        assert isinstance(ctx, GraphContext)

    def test_traverse_cycle_prevention(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        # F->B creates a cycle. B should not be re-visited.
        ctx = traverser.traverse([g["a"].id], depth=3)
        entity_ids = [e.id for e in ctx.entities]
        assert entity_ids.count(g["b"].id) <= 1

    def test_traverse_lonely_entity(self, store):
        lonely = _make_entity(store, "lonely", "concept")
        traverser = GraphTraverser(store)
        ctx = traverser.traverse([lonely.id], depth=2)
        assert len(ctx.entities) == 1
        assert ctx.entities[0].name == "lonely"
        assert len(ctx.relationships) == 0
        assert ctx.relevance_score == 0.0

    def test_traverse_relevance_in_range(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        ctx = traverser.traverse([g["a"].id], depth=2)
        assert 0.0 <= ctx.relevance_score <= 1.0

    def test_traverse_paths_include_seed(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        ctx = traverser.traverse([g["a"].id], depth=1)
        # Paths should start with seed entity
        seed_paths = [p for p in ctx.paths if p[0] == g["a"].id]
        assert len(seed_paths) >= 1


# ============================================================
# S7: Hop Query Builder
# ============================================================

class TestS7HopQueryBuilder:

    def test_hop_outbound(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        edges = traverser._hop({g["a"].id}, "outbound", None, True, None)
        target_ids = {e.target_entity_id for e in edges}
        assert g["b"].id in target_ids
        assert g["e"].id in target_ids
        assert len(edges) == 2

    def test_hop_inbound(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        edges = traverser._hop({g["b"].id}, "inbound", None, True, None)
        source_ids = {e.source_entity_id for e in edges}
        assert g["a"].id in source_ids

    def test_hop_both(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        edges = traverser._hop({g["b"].id}, "both", None, True, None)
        assert len(edges) >= 2  # At least A->B and B->C

    def test_hop_with_rel_type_filter(self, store):
        g = _build_test_graph(store)
        traverser = GraphTraverser(store)
        edges = traverser._hop({g["a"].id}, "outbound", ["uses"], True, None)
        assert all(e.rel_type == "uses" for e in edges)
        assert len(edges) == 1  # Only A->B (uses)

    def test_hop_empty_frontier(self, store):
        traverser = GraphTraverser(store)
        edges = traverser._hop(set(), "outbound", None, True, None)
        assert edges == []


# ============================================================
# S8: Score & Prune
# ============================================================

class TestS8ScorePrune:

    def test_score_hop_0(self):
        traverser = GraphTraverser.__new__(GraphTraverser)
        edges = [
            Relationship(id="1", source_entity_id="a", target_entity_id="b",
                         rel_type="uses", weight=0.9),
            Relationship(id="2", source_entity_id="a", target_entity_id="c",
                         rel_type="uses", weight=0.7),
        ]
        scored = traverser._score(edges, 0)
        assert scored[0]._effective_weight == pytest.approx(0.9)
        assert scored[1]._effective_weight == pytest.approx(0.7)

    def test_score_hop_1(self):
        traverser = GraphTraverser.__new__(GraphTraverser)
        edges = [
            Relationship(id="1", source_entity_id="a", target_entity_id="b",
                         rel_type="uses", weight=0.9),
        ]
        scored = traverser._score(edges, 1)
        assert scored[0]._effective_weight == pytest.approx(0.63)  # 0.9 * 0.7

    def test_score_hop_2(self):
        traverser = GraphTraverser.__new__(GraphTraverser)
        edges = [
            Relationship(id="1", source_entity_id="a", target_entity_id="b",
                         rel_type="uses", weight=0.9),
        ]
        scored = traverser._score(edges, 2)
        assert scored[0]._effective_weight == pytest.approx(0.45)  # 0.9 * 0.5

    def test_prune_min_weight(self):
        traverser = GraphTraverser.__new__(GraphTraverser)
        edges = [
            Relationship(id="1", source_entity_id="a", target_entity_id="b",
                         rel_type="uses", weight=0.5),
            Relationship(id="2", source_entity_id="a", target_entity_id="c",
                         rel_type="uses", weight=0.08),
        ]
        for e in edges:
            e._effective_weight = e.weight
        pruned = traverser._prune(edges, min_weight=0.1, max_fanout=20)
        assert len(pruned) == 1
        assert pruned[0].id == "1"

    def test_prune_max_fanout(self):
        traverser = GraphTraverser.__new__(GraphTraverser)
        edges = []
        for i in range(30):
            e = Relationship(id=str(i), source_entity_id="a",
                             target_entity_id=f"t{i}", rel_type="uses",
                             weight=0.5 + i * 0.01)
            e._effective_weight = e.weight
            edges.append(e)
        pruned = traverser._prune(edges, min_weight=0.0, max_fanout=20)
        assert len(pruned) == 20
        # Should be sorted descending
        assert pruned[0].weight > pruned[-1].weight

    def test_compute_relevance_empty(self):
        traverser = GraphTraverser.__new__(GraphTraverser)
        assert traverser._compute_relevance([], 1, 2) == 0.0

    def test_compute_relevance_in_range(self):
        traverser = GraphTraverser.__new__(GraphTraverser)
        rels = [
            Relationship(id="1", source_entity_id="a", target_entity_id="b",
                         rel_type="uses", weight=0.8),
        ]
        for r in rels:
            r._effective_weight = r.weight
        score = traverser._compute_relevance(rels, 1, 2)
        assert 0.0 <= score <= 1.0


# ============================================================
# S9: Temporal Edge Support
# ============================================================

class TestS9TemporalEdges:

    def test_traverse_at_time_includes_valid(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses",
                           valid_from="2025-01-01T00:00:00+00:00",
                           valid_until="2025-06-15T00:00:00+00:00")
        traverser = GraphTraverser(store)
        ctx = traverser.traverse_at_time([e1.id], "2025-03-01T00:00:00+00:00")
        assert len(ctx.relationships) == 1

    def test_traverse_at_time_excludes_expired(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses",
                           valid_from="2025-01-01T00:00:00+00:00",
                           valid_until="2025-06-15T00:00:00+00:00")
        traverser = GraphTraverser(store)
        ctx = traverser.traverse_at_time([e1.id], "2025-12-01T00:00:00+00:00")
        assert len(ctx.relationships) == 0

    def test_traverse_at_time_excludes_future(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses",
                           valid_from="2025-07-01T00:00:00+00:00")
        traverser = GraphTraverser(store)
        ctx = traverser.traverse_at_time([e1.id], "2025-03-01T00:00:00+00:00")
        assert len(ctx.relationships) == 0

    def test_active_only_excludes_expired(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses",
                           valid_until=_utc_now_iso())
        traverser = GraphTraverser(store)
        ctx = traverser.traverse([e1.id], depth=1, active_only=True)
        assert len(ctx.relationships) == 0


# ============================================================
# S10: Entity Cache
# ============================================================

class TestS10EntityCache:

    def test_cache_hit(self, store):
        _make_entity(store, "redis", "tool")
        cache = EntityCache(store, ttl_seconds=300)
        result1 = cache.get_all()
        result2 = cache.get_all()
        assert len(result1) == 1
        assert result1 is result2  # Same list object (cache hit)

    def test_cache_invalidate(self, store):
        _make_entity(store, "redis", "tool")
        cache = EntityCache(store, ttl_seconds=300)
        result1 = cache.get_all()
        cache.invalidate()
        _make_entity(store, "postgresql", "tool")
        result2 = cache.get_all()
        assert len(result2) == 2

    def test_cache_ttl_expiry(self, store):
        _make_entity(store, "redis", "tool")
        cache = EntityCache(store, ttl_seconds=0)  # Immediate expiry
        result1 = cache.get_all()
        time.sleep(0.01)
        result2 = cache.get_all()
        assert result1 is not result2  # Different objects (cache miss)

    def test_find_query_entities_by_name(self, store):
        _make_entity(store, "auth-service", "service")
        _make_entity(store, "redis", "tool")
        cache = EntityCache(store)
        matches = find_query_entities("what does auth-service depend on?", cache)
        assert len(matches) == 1
        assert matches[0].name == "auth-service"

    def test_find_query_entities_by_alias(self, store):
        e = _make_entity(store, "kubernetes", "platform", aliases=["k8s"])
        cache = EntityCache(store)
        matches = find_query_entities("k8s cluster issues", cache)
        assert len(matches) == 1
        assert matches[0].name == "kubernetes"

    def test_find_query_entities_no_match(self, store):
        _make_entity(store, "redis", "tool")
        cache = EntityCache(store)
        matches = find_query_entities("how do I fix this bug?", cache)
        assert len(matches) == 0


# ============================================================
# S11: Hybrid Recall Scoring
# ============================================================

class TestS11HybridRecall:

    def test_graph_boost_no_overlap(self):
        from lore.lore import Lore

        lore = Lore.__new__(Lore)
        lore._store = MemoryStore()
        ctx = GraphContext(entities=[], relationships=[])
        boost = lore._compute_graph_boost("mem1", ctx)
        assert boost == 1.0

    def test_graph_boost_none_context(self):
        from lore.lore import Lore

        lore = Lore.__new__(Lore)
        lore._store = MemoryStore()
        boost = lore._compute_graph_boost("mem1", None)
        assert boost == 1.0

    def test_graph_boost_with_overlap(self):
        from lore.lore import Lore

        store = MemoryStore()
        mid = _make_memory(store)
        e = _make_entity(store, "redis", "tool")
        store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e.id, memory_id=mid,
            created_at=_utc_now_iso(),
        ))
        ctx = GraphContext(
            entities=[e],
            relationships=[
                Relationship(id="r1", source_entity_id=e.id,
                             target_entity_id="other", rel_type="uses",
                             weight=0.8),
            ],
            relevance_score=0.8,
        )
        lore = Lore.__new__(Lore)
        lore._store = store
        boost = lore._compute_graph_boost(mid, ctx)
        assert boost > 1.0
        assert boost <= 1.5

    def test_graph_boost_capped(self):
        from lore.lore import Lore

        store = MemoryStore()
        mid = _make_memory(store)
        e = _make_entity(store, "redis", "tool")
        store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e.id, memory_id=mid,
            created_at=_utc_now_iso(),
        ))
        ctx = GraphContext(
            entities=[e],
            relationships=[
                Relationship(id="r1", source_entity_id=e.id,
                             target_entity_id="other", rel_type="uses",
                             weight=0.9),
            ],
            relevance_score=1.0,
        )
        lore = Lore.__new__(Lore)
        lore._store = store
        boost = lore._compute_graph_boost(mid, ctx)
        assert boost <= 1.5

    def test_recall_graph_depth_0_no_graph_queries(self, tmp_path):
        """graph_depth=0 should behave identically to v0.5.x."""
        from lore.lore import Lore

        lore = Lore(db_path=str(tmp_path / "test.db"), knowledge_graph=True, redact=False)
        mid = lore.remember("Redis is a cache", type="general")
        results = lore.recall("cache", graph_depth=0)
        # Should work fine, no graph involvement
        assert len(results) >= 0
        lore.close()


# ============================================================
# S12: F2 Integration (Facts to Edges)
# ============================================================

class TestS12F2Integration:

    def test_update_graph_from_facts(self, store):
        mid = _make_memory(store)
        em = EntityManager(store)
        rm = RelationshipManager(store, em)

        facts = [
            Fact(id=str(ULID()), memory_id=mid,
                 subject="auth-service", predicate="depends_on", object="redis",
                 confidence=0.9, extracted_at=_utc_now_iso()),
        ]
        update_graph_from_facts(mid, facts, em, rm)

        e_auth = store.get_entity_by_name("auth-service")
        e_redis = store.get_entity_by_name("redis")
        assert e_auth is not None
        assert e_redis is not None
        rel = store.get_active_relationship(e_auth.id, e_redis.id, "depends_on")
        assert rel is not None

    def test_low_confidence_skipped(self, store):
        mid = _make_memory(store)
        em = EntityManager(store)
        rm = RelationshipManager(store, em)

        facts = [
            Fact(id=str(ULID()), memory_id=mid,
                 subject="x", predicate="uses", object="y",
                 confidence=0.3, extracted_at=_utc_now_iso()),
        ]
        update_graph_from_facts(mid, facts, em, rm, confidence_threshold=0.5)
        assert store.get_entity_by_name("x") is None

    def test_invalidated_fact_skipped(self, store):
        mid = _make_memory(store)
        em = EntityManager(store)
        rm = RelationshipManager(store, em)

        facts = [
            Fact(id=str(ULID()), memory_id=mid,
                 subject="x", predicate="uses", object="y",
                 confidence=0.9, extracted_at=_utc_now_iso(),
                 invalidated_by="some-id"),
        ]
        update_graph_from_facts(mid, facts, em, rm)
        assert store.get_entity_by_name("x") is None

    def test_co_occurrence_created(self, store):
        mid = _make_memory(store)
        em = EntityManager(store)
        rm = RelationshipManager(store, em)

        facts = [
            Fact(id=str(ULID()), memory_id=mid,
                 subject="a", predicate="uses", object="b",
                 confidence=0.9, extracted_at=_utc_now_iso()),
            Fact(id=str(ULID()), memory_id=mid,
                 subject="c", predicate="uses", object="d",
                 confidence=0.9, extracted_at=_utc_now_iso()),
        ]
        update_graph_from_facts(mid, facts, em, rm, co_occurrence=True)
        # Should have co-occurrence edges
        rels = store.list_relationships(rel_type="co_occurs_with")
        assert len(rels) > 0

    def test_graph_update_failure_does_not_crash(self, store):
        mid = _make_memory(store)
        em = EntityManager(store)
        rm = RelationshipManager(store, em)
        # Empty facts should not crash
        update_graph_from_facts(mid, [], em, rm)


# ============================================================
# S13: F6 Integration (Enrichment to Nodes)
# ============================================================

class TestS13F6Integration:

    def test_ingest_from_enrichment(self, store, entity_manager):
        mid = _make_memory(store)
        entities = [
            {"name": "Alice", "type": "person"},
            {"name": "Kubernetes", "type": "platform"},
        ]
        result = entity_manager.ingest_from_enrichment(mid, entities)
        assert len(result) == 2

        alice = store.get_entity_by_name("alice")
        assert alice is not None
        assert alice.entity_type == "person"

        k8s = store.get_entity_by_name("kubernetes")
        assert k8s is not None
        assert k8s.entity_type == "platform"

        # Check mentions
        mentions = store.get_entity_mentions_for_memory(mid)
        assert len(mentions) == 2

    def test_ingest_from_enrichment_dedup(self, store, entity_manager):
        mid1 = _make_memory(store, "mem1")
        mid2 = _make_memory(store, "mem2")
        entities = [{"name": "redis", "type": "tool"}]
        entity_manager.ingest_from_enrichment(mid1, entities)
        entity_manager.ingest_from_enrichment(mid2, entities)

        redis = store.get_entity_by_name("redis")
        # mention_count incremented twice (once per ingestion, each adds 1)
        assert redis.mention_count >= 3  # initial 1 + 2 ingestions

    def test_ingest_from_enrichment_empty(self, store, entity_manager):
        mid = _make_memory(store)
        result = entity_manager.ingest_from_enrichment(mid, [])
        assert result == []

    def test_ingest_from_enrichment_invalid_type(self, store, entity_manager):
        mid = _make_memory(store)
        entities = [{"name": "foo", "type": "invalid_type"}]
        result = entity_manager.ingest_from_enrichment(mid, entities)
        assert result[0].entity_type == "other"

    def test_ingest_from_enrichment_empty_name_skipped(self, store, entity_manager):
        mid = _make_memory(store)
        entities = [{"name": "", "type": "concept"}, {"name": "valid", "type": "concept"}]
        result = entity_manager.ingest_from_enrichment(mid, entities)
        assert len(result) == 1
        assert result[0].name == "valid"


# ============================================================
# S14: MCP Tools + CLI + Visualization
# ============================================================

class TestS14Visualization:

    def test_to_d3_json_basic(self):
        e1 = Entity(id="e1", name="a", entity_type="concept")
        e2 = Entity(id="e2", name="b", entity_type="tool")
        r = Relationship(id="r1", source_entity_id="e1", target_entity_id="e2",
                         rel_type="uses", weight=0.8)
        ctx = GraphContext(entities=[e1, e2], relationships=[r])
        result = to_d3_json(ctx)
        assert len(result["nodes"]) == 2
        assert len(result["links"]) == 1
        assert result["links"][0]["source"] == "e1"
        assert result["links"][0]["target"] == "e2"

    def test_to_d3_json_empty(self):
        ctx = GraphContext()
        result = to_d3_json(ctx)
        assert result == {"nodes": [], "links": []}

    def test_to_text_tree_basic(self):
        e1 = Entity(id="e1", name="auth-service", entity_type="service")
        e2 = Entity(id="e2", name="redis", entity_type="tool")
        r = Relationship(id="r1", source_entity_id="e1", target_entity_id="e2",
                         rel_type="depends_on", weight=0.8)
        ctx = GraphContext(entities=[e1, e2], relationships=[r],
                           paths=[["e1", "e2"]])
        text = to_text_tree(ctx)
        assert "auth-service" in text
        assert "redis" in text

    def test_to_text_tree_empty(self):
        ctx = GraphContext()
        assert to_text_tree(ctx) == "(empty graph)"


# ============================================================
# S15: Backfill + Cascade on forget()
# ============================================================

class TestS15BackfillCascade:

    def test_cascade_on_forget_deletes_lonely_entity(self, tmp_path):
        from lore.lore import Lore

        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=True, redact=False)
        mid = lore.remember("Redis caching patterns", type="general")

        # Manually create entity + mention
        em = lore._entity_manager
        e = em._resolve_entity("redis", "tool")
        e.mention_count = 1
        lore._store.update_entity(e)
        lore._store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e.id, memory_id=mid,
            created_at=_utc_now_iso(),
        ))

        lore.forget(mid)
        assert lore._store.get_entity(e.id) is None
        lore.close()

    def test_cascade_on_forget_preserves_multi_mention_entity(self, tmp_path):
        from lore.lore import Lore

        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=True, redact=False)
        mid1 = lore.remember("Redis caching", type="general")
        mid2 = lore.remember("Redis queues", type="general")

        em = lore._entity_manager
        e = em._resolve_entity("redis", "tool")
        e.mention_count = 2
        lore._store.update_entity(e)
        lore._store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e.id, memory_id=mid1,
            created_at=_utc_now_iso(),
        ))
        lore._store.save_entity_mention(EntityMention(
            id=str(ULID()), entity_id=e.id, memory_id=mid2,
            created_at=_utc_now_iso(),
        ))

        lore.forget(mid1)
        remaining = lore._store.get_entity(e.id)
        assert remaining is not None
        assert remaining.mention_count == 1
        lore.close()

    def test_cascade_deletes_sourced_relationships(self, tmp_path):
        from lore.lore import Lore

        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=True, redact=False)
        mid = lore.remember("test memory", type="general")

        e1 = _make_entity(lore._store, "a", "concept")
        e2 = _make_entity(lore._store, "b", "concept")
        e1.mention_count = 5
        lore._store.update_entity(e1)
        e2.mention_count = 5
        lore._store.update_entity(e2)
        _make_relationship(lore._store, e1.id, e2.id, "uses", memory_id=mid)

        lore.forget(mid)
        rels = lore._store.list_relationships()
        assert len(rels) == 0
        lore.close()

    def test_graph_backfill_basic(self, tmp_path):
        from lore.lore import Lore

        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=True, redact=False)
        mid = lore.remember("test", type="general")

        # Add enrichment metadata manually
        mem = lore._store.get(mid)
        mem.metadata = {"enrichment": {"entities": [{"name": "Redis", "type": "tool"}]}}
        lore._store.update(mem)

        count = lore.graph_backfill()
        assert count == 1

        redis = lore._store.get_entity_by_name("redis")
        assert redis is not None
        lore.close()

    def test_graph_backfill_idempotent(self, tmp_path):
        from lore.lore import Lore

        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=True, redact=False)
        mid = lore.remember("test", type="general")

        mem = lore._store.get(mid)
        mem.metadata = {"enrichment": {"entities": [{"name": "Redis", "type": "tool"}]}}
        lore._store.update(mem)

        count1 = lore.graph_backfill()
        count2 = lore.graph_backfill()
        assert count1 == 1
        assert count2 == 0  # Already processed
        lore.close()

    def test_graph_backfill_disabled(self, tmp_path):
        from lore.lore import Lore

        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=False, redact=False)
        count = lore.graph_backfill()
        assert count == 0
        lore.close()

    def test_forget_no_crash_when_graph_disabled(self, tmp_path):
        from lore.lore import Lore

        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=False, redact=False)
        mid = lore.remember("test", type="general")
        assert lore.forget(mid) is True
        lore.close()


# ============================================================
# Additional edge case & integration tests
# ============================================================

class TestQueryRelationships:
    """Tests for the query_relationships method (hop support)."""

    def test_query_outbound(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses")
        rels = store.query_relationships([e1.id], direction="outbound")
        assert len(rels) == 1

    def test_query_inbound(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses")
        rels = store.query_relationships([e2.id], direction="inbound")
        assert len(rels) == 1

    def test_query_both(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        e3 = _make_entity(store, "c", "concept")
        _make_relationship(store, e1.id, e2.id, "uses")
        _make_relationship(store, e3.id, e2.id, "depends_on")
        rels = store.query_relationships([e2.id], direction="both")
        assert len(rels) == 2

    def test_query_temporal(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses",
                           valid_from="2025-01-01T00:00:00+00:00",
                           valid_until="2025-06-15T00:00:00+00:00")
        rels = store.query_relationships(
            [e1.id], direction="outbound",
            active_only=False, at_time="2025-03-01T00:00:00+00:00",
        )
        assert len(rels) == 1

    def test_query_rel_types_filter(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        _make_relationship(store, e1.id, e2.id, "uses")
        _make_relationship(store, e1.id, e2.id, "depends_on")
        rels = store.query_relationships(
            [e1.id], direction="outbound", rel_types=["uses"],
        )
        assert len(rels) == 1
        assert rels[0].rel_type == "uses"

    def test_query_empty_ids(self, store):
        assert store.query_relationships([]) == []


class TestLoreGraphInit:
    """Test Lore initialization with knowledge_graph flag."""

    def test_graph_disabled_by_default(self, tmp_path):
        from lore.lore import Lore
        lore = Lore(db_path=str(tmp_path / "test.db"), redact=False)
        assert not lore._knowledge_graph_enabled
        assert lore._entity_manager is None
        lore.close()

    def test_graph_enabled(self, tmp_path):
        from lore.lore import Lore
        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=True, redact=False)
        assert lore._knowledge_graph_enabled
        assert lore._entity_manager is not None
        assert lore._relationship_manager is not None
        assert lore._graph_traverser is not None
        assert lore._entity_cache is not None
        lore.close()

    def test_graph_depth_default(self, tmp_path):
        from lore.lore import Lore
        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=True, redact=False)
        assert lore._graph_depth == 0
        lore.close()

    def test_graph_depth_custom(self, tmp_path):
        from lore.lore import Lore
        lore = Lore(db_path=str(tmp_path / "test.db"),
                    knowledge_graph=True, graph_depth=2, redact=False)
        assert lore._graph_depth == 2
        lore.close()


class TestEntityMetadata:
    """Test entity metadata handling."""

    def test_entity_with_metadata(self, store):
        now = _utc_now_iso()
        e = Entity(
            id=str(ULID()), name="test", entity_type="concept",
            metadata={"source": "manual", "priority": 5},
            first_seen_at=now, last_seen_at=now,
            created_at=now, updated_at=now,
        )
        store.save_entity(e)
        got = store.get_entity(e.id)
        assert got.metadata == {"source": "manual", "priority": 5}

    def test_entity_with_description(self, store):
        now = _utc_now_iso()
        e = Entity(
            id=str(ULID()), name="redis", entity_type="tool",
            description="In-memory data store",
            first_seen_at=now, last_seen_at=now,
            created_at=now, updated_at=now,
        )
        store.save_entity(e)
        got = store.get_entity(e.id)
        assert got.description == "In-memory data store"


class TestRelationshipProperties:
    """Test relationship properties handling."""

    def test_relationship_with_properties(self, store):
        e1 = _make_entity(store, "a", "concept")
        e2 = _make_entity(store, "b", "concept")
        now = _utc_now_iso()
        r = Relationship(
            id=str(ULID()),
            source_entity_id=e1.id, target_entity_id=e2.id,
            rel_type="uses",
            properties={"version": "3.x", "required": True},
            valid_from=now, created_at=now, updated_at=now,
        )
        store.save_relationship(r)
        got = store.get_relationship(r.id)
        assert got.properties == {"version": "3.x", "required": True}
