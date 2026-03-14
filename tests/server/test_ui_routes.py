"""Tests for the UI visualization API routes."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from lore.server.ui_app import create_ui_app
from lore.store.memory import MemoryStore
from lore.types import Entity, EntityMention, Memory, Relationship


@pytest.fixture
def store():
    """Fresh in-memory store."""
    return MemoryStore()


@pytest.fixture
def app(store):
    """UI FastAPI app with in-memory store."""
    app = create_ui_app(static_dir="/nonexistent")
    app.state.store = store
    return app


@pytest.fixture
def client(app):
    """Test client."""
    return TestClient(app)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _make_memory(id, content="test content", type="general", project=None,
                 importance=0.5, tier="long", confidence=1.0, tags=None):
    now = _now_iso()
    return Memory(
        id=id, content=content, type=type, tier=tier,
        project=project, importance_score=importance,
        confidence=confidence, tags=tags or [],
        created_at=now, updated_at=now,
    )


def _make_entity(id, name, entity_type="tool", mention_count=1):
    now = _now_iso()
    return Entity(
        id=id, name=name, entity_type=entity_type,
        mention_count=mention_count,
        first_seen_at=now, last_seen_at=now,
        created_at=now, updated_at=now,
    )


def _make_relationship(id, source_id, target_id, rel_type="uses", weight=1.0):
    now = _now_iso()
    return Relationship(
        id=id, source_entity_id=source_id, target_entity_id=target_id,
        rel_type=rel_type, weight=weight,
        valid_from=now, created_at=now, updated_at=now,
    )


def _make_mention(id, entity_id, memory_id):
    now = _now_iso()
    return EntityMention(
        id=id, entity_id=entity_id, memory_id=memory_id,
        created_at=now,
    )


def _seed_store(store):
    """Seed store with test data."""
    m1 = _make_memory("mem_1", "Redis caching strategy", "code", "auth", 0.9)
    m2 = _make_memory("mem_2", "Python async patterns", "lesson", "backend", 0.7)
    m3 = _make_memory("mem_3", "Low importance note", "note", "auth", 0.2)
    for m in [m1, m2, m3]:
        store.save(m)

    e1 = _make_entity("ent_1", "Redis", "tool", 5)
    e2 = _make_entity("ent_2", "Python", "language", 10)
    for e in [e1, e2]:
        store.save_entity(e)

    r1 = _make_relationship("rel_1", "ent_1", "ent_2", "uses", 0.8)
    store.save_relationship(r1)

    em1 = _make_mention("em_1", "ent_1", "mem_1")
    em2 = _make_mention("em_2", "ent_2", "mem_2")
    for em in [em1, em2]:
        store.save_entity_mention(em)


# ── GET /v1/ui/graph ──


class TestGetGraph:
    def test_empty_database(self, client):
        resp = client.get("/v1/ui/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []
        assert data["edges"] == []
        assert data["stats"]["total_memories"] == 0

    def test_seeded_database(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        assert resp.status_code == 200
        data = resp.json()
        # 3 memories + 2 entities = 5 nodes
        assert len(data["nodes"]) == 5
        assert data["stats"]["total_memories"] == 3
        assert data["stats"]["total_entities"] == 2
        assert data["stats"]["total_relationships"] == 1

    def test_project_filter(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph?project=auth")
        data = resp.json()
        memory_nodes = [n for n in data["nodes"] if n["kind"] == "memory"]
        # Only mem_1 and mem_3 are in "auth"
        assert len(memory_nodes) == 2
        for n in memory_nodes:
            assert n["project"] == "auth"

    def test_min_importance_filter(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph?min_importance=0.8")
        data = resp.json()
        memory_nodes = [n for n in data["nodes"] if n["kind"] == "memory"]
        assert len(memory_nodes) == 1
        assert memory_nodes[0]["importance"] >= 0.8

    def test_limit(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph?limit=1")
        data = resp.json()
        memory_nodes = [n for n in data["nodes"] if n["kind"] == "memory"]
        assert len(memory_nodes) <= 1

    def test_entity_edges_both_endpoints(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        data = resp.json()
        node_ids = {n["id"] for n in data["nodes"]}
        for edge in data["edges"]:
            assert edge["source"] in node_ids
            assert edge["target"] in node_ids

    def test_stats_filtered_counts(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        data = resp.json()
        assert data["stats"]["filtered_nodes"] == len(data["nodes"])
        assert data["stats"]["filtered_edges"] == len(data["edges"])

    def test_include_orphans_false(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph?include_orphans=false")
        data = resp.json()
        memory_nodes = [n for n in data["nodes"] if n["kind"] == "memory"]
        # mem_3 has no mentions, should be excluded
        mem_ids = {n["id"] for n in memory_nodes}
        assert "mem_3" not in mem_ids

    def test_memory_content_not_included(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        data = resp.json()
        for node in data["nodes"]:
            if node["kind"] == "memory":
                # Label should be truncated, not full content
                assert len(node["label"]) <= 63  # 60 + "..."


# ── GET /v1/ui/memory/{id} ──


class TestGetMemoryDetail:
    def test_known_memory(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/memory/mem_1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "mem_1"
        assert "Redis" in data["content"]
        assert data["type"] == "code"
        assert data["project"] == "auth"
        assert len(data["connected_entities"]) == 1
        assert data["connected_entities"][0]["name"] == "Redis"

    def test_unknown_memory(self, client):
        resp = client.get("/v1/ui/memory/nonexistent")
        assert resp.status_code == 404


# ── GET /v1/ui/entity/{id} ──


class TestGetEntityDetail:
    def test_known_entity(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/entity/ent_1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "ent_1"
        assert data["name"] == "Redis"
        assert data["entity_type"] == "tool"
        assert data["mention_count"] == 5
        # Should have connected entity via relationship
        assert len(data["connected_entities"]) == 1
        # Should have connected memory via mention
        assert len(data["connected_memories"]) == 1

    def test_unknown_entity(self, client):
        resp = client.get("/v1/ui/entity/nonexistent")
        assert resp.status_code == 404


# ── POST /v1/ui/search ──


class TestSearch:
    def test_keyword_search(self, client, store):
        _seed_store(store)
        resp = client.post("/v1/ui/search", json={
            "query": "Redis",
            "mode": "keyword",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        assert any(r["id"] == "mem_1" for r in data["results"])

    def test_empty_query(self, client, store):
        _seed_store(store)
        resp = client.post("/v1/ui/search", json={"query": ""})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_limit(self, client, store):
        _seed_store(store)
        resp = client.post("/v1/ui/search", json={
            "query": "a",  # should match multiple
            "limit": 1,
        })
        assert resp.status_code == 200
        assert len(resp.json()["results"]) <= 1

    def test_results_have_score(self, client, store):
        _seed_store(store)
        resp = client.post("/v1/ui/search", json={"query": "Redis"})
        data = resp.json()
        for r in data["results"]:
            assert "score" in r
            assert r["score"] > 0

    def test_query_time_ms(self, client, store):
        _seed_store(store)
        resp = client.post("/v1/ui/search", json={"query": "Redis"})
        assert resp.json()["query_time_ms"] >= 0

    def test_entity_search(self, client, store):
        _seed_store(store)
        resp = client.post("/v1/ui/search", json={"query": "Python"})
        data = resp.json()
        entity_results = [r for r in data["results"] if r["kind"] == "entity"]
        assert len(entity_results) >= 1
        assert entity_results[0]["label"] == "Python"

    def test_unknown_mode(self, client, store):
        resp = client.post("/v1/ui/search", json={
            "query": "test",
            "mode": "invalid_mode",
        })
        assert resp.status_code == 400

    def test_project_filter(self, client, store):
        _seed_store(store)
        resp = client.post("/v1/ui/search", json={
            "query": "Redis",
            "filters": {"project": "backend"},
        })
        data = resp.json()
        # Redis memory is in "auth" not "backend", so no memory match
        memory_results = [r for r in data["results"] if r["kind"] == "memory"]
        assert len(memory_results) == 0


# ── GET /v1/ui/graph/clusters ──


class TestClusters:
    def test_cluster_by_project(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph/clusters?group_by=project")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["clusters"]) >= 1
        # Each cluster's node_ids should be in the nodes list
        node_ids = {n["id"] for n in data["nodes"]}
        for cluster in data["clusters"]:
            for nid in cluster["node_ids"]:
                assert nid in node_ids

    def test_cluster_by_type(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph/clusters?group_by=type")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["clusters"]) >= 1

    def test_empty_database(self, client):
        resp = client.get("/v1/ui/graph/clusters")
        assert resp.status_code == 200
        data = resp.json()
        assert data["clusters"] == []


# ── GET /v1/ui/stats ──


class TestStats:
    def test_stats(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_memories"] == 3
        assert data["total_entities"] == 2
        assert data["total_relationships"] == 1
        assert "code" in data["by_type"]
        assert data["avg_importance"] > 0
        assert len(data["top_entities"]) > 0
        # Top entities sorted by mention_count
        mentions = [e["mention_count"] for e in data["top_entities"]]
        assert mentions == sorted(mentions, reverse=True)

    def test_stats_project_filter(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/stats?project=auth")
        data = resp.json()
        assert data["total_memories"] == 2  # mem_1 and mem_3

    def test_empty_database(self, client):
        resp = client.get("/v1/ui/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_memories"] == 0
        assert data["avg_importance"] == 0

    def test_recent_counts(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/stats")
        data = resp.json()
        # All memories were just created, should be in recent
        assert data["recent_24h"] == 3
        assert data["recent_7d"] == 3


# ── GET /v1/ui/timeline ──


class TestTimeline:
    def test_timeline_daily(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/timeline?bucket=day")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["buckets"]) >= 1
        for bucket in data["buckets"]:
            assert bucket["count"] > 0
            assert "by_type" in bucket

    def test_timeline_range(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/timeline")
        data = resp.json()
        assert data["range"]["start"] is not None
        assert data["range"]["end"] is not None

    def test_empty_database(self, client):
        resp = client.get("/v1/ui/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["buckets"] == []


# ── GET /health ──


class TestHealth:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── UI App Factory ──


class TestUIApp:
    def test_create_ui_app(self):
        app = create_ui_app(static_dir="/nonexistent")
        # Should have the UI routes
        routes = [r.path for r in app.routes]
        assert "/v1/ui/graph" in routes
        assert "/health" in routes

    def test_static_serving(self, client):
        # Root should return something (even if static dir missing)
        # With missing static dir, root is not mounted, so 404 is expected
        resp = client.get("/")
        # May be 404 if static dir doesn't exist
        assert resp.status_code in (200, 404)


# ── Additional QA Tests ──


class TestGraphMentionEdges:
    """Verify memory↔entity mention edges appear in graph response."""

    def test_mention_edges_present(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        data = resp.json()
        mention_edges = [e for e in data["edges"] if e["rel_type"] == "mentions"]
        # em_1 (mem_1 → ent_1) and em_2 (mem_2 → ent_2)
        assert len(mention_edges) == 2

    def test_mention_edge_sources_are_memories(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        data = resp.json()
        memory_ids = {n["id"] for n in data["nodes"] if n["kind"] == "memory"}
        mention_edges = [e for e in data["edges"] if e["rel_type"] == "mentions"]
        for edge in mention_edges:
            assert edge["source"] in memory_ids

    def test_entity_entity_edges_present(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        data = resp.json()
        rel_edges = [e for e in data["edges"] if e["rel_type"] != "mentions"]
        # rel_1 (ent_1 → ent_2)
        assert len(rel_edges) == 1
        assert rel_edges[0]["rel_type"] == "uses"

    def test_total_edge_count(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        data = resp.json()
        # 2 mention + 1 relationship = 3 edges
        assert len(data["edges"]) == 3


class TestGraphNodeStructure:
    """Verify node fields match PRD specification."""

    def test_memory_node_fields(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        data = resp.json()
        mem_node = next(n for n in data["nodes"] if n["id"] == "mem_1")
        assert mem_node["kind"] == "memory"
        assert mem_node["type"] == "code"
        assert mem_node["tier"] == "long"
        assert mem_node["project"] == "auth"
        assert mem_node["importance"] == 0.9
        assert "label" in mem_node
        assert mem_node["confidence"] is not None

    def test_entity_node_fields(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/graph")
        data = resp.json()
        ent_node = next(n for n in data["nodes"] if n["id"] == "ent_1")
        assert ent_node["kind"] == "entity"
        assert ent_node["type"] == "tool"
        assert ent_node["label"] == "Redis"
        assert ent_node["mention_count"] == 5


class TestSearchEdgeCases:
    """Additional search edge cases."""

    def test_search_case_insensitive(self, client, store):
        _seed_store(store)
        resp = client.post("/v1/ui/search", json={"query": "redis"})
        data = resp.json()
        # Should match "Redis" case-insensitively
        assert data["total"] > 0

    def test_search_entity_by_alias(self, client, store):
        """Entities with matching aliases should appear in search."""
        _seed_store(store)
        # Add entity with alias
        e = _make_entity("ent_3", "PostgreSQL", "tool", 3)
        e.aliases = ["pg", "postgres"]
        store.save_entity(e)

        resp = client.post("/v1/ui/search", json={"query": "pg"})
        data = resp.json()
        entity_results = [r for r in data["results"] if r["id"] == "ent_3"]
        assert len(entity_results) == 1


class TestTimelineBuckets:
    """Test different timeline bucket sizes."""

    def test_hourly_buckets(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/timeline?bucket=hour")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["buckets"]) >= 1
        # Hourly format: YYYY-MM-DDTHH:00
        assert "T" in data["buckets"][0]["date"]

    def test_monthly_buckets(self, client, store):
        _seed_store(store)
        resp = client.get("/v1/ui/timeline?bucket=month")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["buckets"]) >= 1
        # Monthly format: YYYY-MM
        assert len(data["buckets"][0]["date"]) == 7


class TestMemoryDetailConnections:
    """Verify memory detail endpoint returns connections."""

    def test_connected_memories_via_shared_entity(self, client, store):
        """Two memories mentioning the same entity should be connected."""
        _seed_store(store)
        # mem_1 mentions ent_1, add another memory also mentioning ent_1
        m4 = _make_memory("mem_4", "Another Redis thing", "code", "auth", 0.8)
        store.save(m4)
        em3 = _make_mention("em_3", "ent_1", "mem_4")
        store.save_entity_mention(em3)

        resp = client.get("/v1/ui/memory/mem_1")
        data = resp.json()
        connected_mem_ids = [c["id"] for c in data["connected_memories"]]
        assert "mem_4" in connected_mem_ids
