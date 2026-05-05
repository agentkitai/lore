"""Tests for the refactored graph + review routes (Phase 1B).

Each test uses a FakeStore (subset of the Store protocol) whose methods are
AsyncMock instances. The `get_store` dependency is overridden via FastAPI's
dependency_overrides to inject the fake.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence
from unittest.mock import AsyncMock, patch

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


# ── helpers ──────────────────────────────────────────────────────


def _utc_now():
    return datetime.now(timezone.utc)


def _make_stored_memory(memory_id="mem-001", content="hello world", **kwargs):
    from lore.persistence.types import StoredMemory
    now = _utc_now()
    defaults = dict(
        id=memory_id,
        org_id="solo",
        content=content,
        context=None,
        tags=("python",),
        confidence=0.9,
        source=None,
        project="lore",
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={"type": "lesson"},
        importance_score=1.0,
        access_count=0,
        last_accessed_at=None,
    )
    defaults.update(kwargs)
    return StoredMemory(**defaults)


def _make_stored_entity(entity_id="ent-001", name="topic", entity_type="topic", **kwargs):
    from lore.persistence.types import StoredEntity
    now = _utc_now()
    defaults = dict(
        id=entity_id,
        name=name,
        entity_type=entity_type,
        aliases=(),
        description=None,
        metadata={},
        mention_count=1,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return StoredEntity(**defaults)


def _make_stored_relationship(rel_id="rel-001", source_id="ent-001", target_id="ent-002", **kwargs):
    from lore.persistence.types import StoredRelationship
    now = _utc_now()
    defaults = dict(
        id=rel_id,
        source_entity_id=source_id,
        target_entity_id=target_id,
        rel_type="uses",
        weight=0.7,
        properties={},
        source_fact_id=None,
        source_memory_id=None,
        valid_from=now,
        valid_until=None,
        status="approved",
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return StoredRelationship(**defaults)


def _make_stored_mention(mention_id="emen-001", entity_id="ent-001", memory_id="mem-001", **kwargs):
    from lore.persistence.types import StoredMention
    now = _utc_now()
    defaults = dict(
        id=mention_id,
        entity_id=entity_id,
        memory_id=memory_id,
        mention_type="explicit",
        confidence=1.0,
        created_at=now,
    )
    defaults.update(kwargs)
    return StoredMention(**defaults)


def _make_pending_row(rel_id="rel-pending", **kwargs):
    from lore.persistence.types import PendingRelationshipRow
    now = _utc_now()
    defaults = dict(
        id=rel_id,
        source_entity_id="ent-a",
        target_entity_id="ent-b",
        rel_type="uses",
        weight=0.5,
        source_memory_id=None,
        created_at=now,
        source_name="alpha",
        source_entity_type="topic",
        source_mentions=2,
        target_name="beta",
        target_entity_type="topic",
        target_mentions=3,
    )
    defaults.update(kwargs)
    return PendingRelationshipRow(**defaults)


def _make_graph_stats(**kwargs):
    from lore.persistence.types import GraphStats
    defaults = dict(
        total_memories=0,
        total_entities=0,
        total_relationships=0,
        by_type={},
        by_project={},
        by_entity_type={},
        top_entities=[],
        avg_importance=0.0,
        recent_24h=0,
        recent_7d=0,
        oldest_memory=None,
        newest_memory=None,
    )
    defaults.update(kwargs)
    return GraphStats(**defaults)


class FakeStore:
    """AsyncMock-backed Store implementation. Tests configure return values per call."""
    def __init__(self):
        self.list_memories = AsyncMock(return_value=[])
        self.list_entities = AsyncMock(return_value=[])
        self.get_memory = AsyncMock(return_value=None)
        self.get_entity = AsyncMock(return_value=None)
        self.get_entity_by_name = AsyncMock(return_value=None)
        self.get_mentions_for_memory = AsyncMock(return_value=[])
        self.get_mentions_for_entity = AsyncMock(return_value=[])
        self.query_relationships = AsyncMock(return_value=[])
        self.get_memories_by_entities = AsyncMock(return_value=[])
        self.count_memories_for_entity = AsyncMock(return_value=0)
        self.get_graph_stats = AsyncMock(return_value=_make_graph_stats())
        self.get_timeline_buckets = AsyncMock(return_value=[])
        self.search_memories_text = AsyncMock(return_value=[])
        self.list_pending_relationships = AsyncMock(return_value=[])
        self.get_relationship = AsyncMock(return_value=None)
        self.update_relationship_status = AsyncMock()
        self.save_rejected_pattern = AsyncMock(return_value=None)

    async def close(self):
        pass


@pytest.fixture
def fake_store():
    return FakeStore()


def _build_app_with_store(routers, fake_store):
    """Build a FastAPI app that includes the given routers with the fake store injected."""
    from lore.server.db import get_store

    app = FastAPI()
    for r in routers:
        app.include_router(r)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    return app


# ── routes/graph/memories.py (T15) — 3 handlers ──────────────────


def test_get_graph_returns_response(fake_store):
    from lore.server.routes.graph.router import router
    fake_store.list_memories.return_value = [_make_stored_memory()]
    fake_store.list_entities.return_value = [_make_stored_entity(entity_id="ent-x", name="x")]
    fake_store.get_graph_stats.return_value = _make_graph_stats(
        total_memories=1, total_entities=1, total_relationships=0,
    )
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body
    assert "edges" in body
    assert "stats" in body


def test_post_search(fake_store):
    from lore.server.routes.graph.router import router
    fake_store.search_memories_text.return_value = [
        _make_stored_memory(content="kafka stream")
    ]
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.post("/v1/ui/search", json={"query": "kafka", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert "kafka" in body["results"][0]["content"]


def test_get_memory_detail_404_on_missing(fake_store):
    from lore.server.routes.graph.router import router
    fake_store.get_memory.return_value = None
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/memory/mem-missing")
    assert resp.status_code == 404


# ── routes/graph/entities.py (T16) — 1 handler ──────────────────


def test_get_entity_detail_returns_data(fake_store):
    from lore.server.routes.graph.router import router
    e = _make_stored_entity(entity_id="ent-1", name="postgres")
    fake_store.get_entity.return_value = e
    fake_store.get_memories_by_entities.return_value = []
    fake_store.query_relationships.return_value = []
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/entity/ent-1")
    assert resp.status_code == 200
    assert resp.json()["name"] == "postgres"


def test_get_entity_detail_404(fake_store):
    from lore.server.routes.graph.router import router
    fake_store.get_entity.return_value = None
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/entity/ent-missing")
    assert resp.status_code == 404


# ── routes/graph/stats.py (T17) — 3 handlers ────────────────────


def test_get_stats_returns_response(fake_store):
    from lore.server.routes.graph.router import router
    fake_store.get_graph_stats.return_value = _make_graph_stats(total_memories=42)
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/stats")
    assert resp.status_code == 200
    assert resp.json()["total_memories"] == 42


def test_get_clusters_invalid_group_by(fake_store):
    from lore.server.routes.graph.router import router
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/graph/clusters?group_by=bogus")
    assert resp.status_code == 400


def test_get_clusters_returns_clusters(fake_store):
    from lore.server.routes.graph.router import router
    fake_store.list_memories.return_value = [
        _make_stored_memory(memory_id="m1", project="alpha"),
        _make_stored_memory(memory_id="m2", project="beta"),
    ]
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/graph/clusters?group_by=project")
    assert resp.status_code == 200
    body = resp.json()
    labels = {c["label"] for c in body["clusters"]}
    assert {"alpha", "beta"}.issubset(labels)


def test_get_timeline_invalid_bucket(fake_store):
    from lore.server.routes.graph.router import router
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/timeline?bucket=century")
    assert resp.status_code == 400


def test_get_timeline_basic(fake_store):
    from lore.server.routes.graph.router import router
    from lore.persistence.types import TimelineBucketRow
    now = _utc_now()
    fake_store.get_timeline_buckets.return_value = [
        TimelineBucketRow(bucket_date=now, mem_type="lesson", count=2),
    ]
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/timeline?bucket=day")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["buckets"]) >= 1


# ── routes/graph/topics.py (T18) — 2 handlers ───────────────────


def test_get_topics_returns_topic_list(fake_store):
    from lore.server.routes.graph.router import router
    fake_store.list_entities.return_value = [
        _make_stored_entity(entity_id="ent-pop", name="popular", mention_count=10)
    ]
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/topics")
    assert resp.status_code == 200
    body = resp.json()
    assert any(t["name"] == "popular" for t in body["topics"])


def test_get_topic_detail_404(fake_store):
    from lore.server.routes.graph.router import router
    fake_store.get_entity_by_name.return_value = None
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/topics/no_such_topic")
    assert resp.status_code == 404


def test_get_topic_detail_returns_dict(fake_store):
    from lore.server.routes.graph.router import router
    fake_store.get_entity_by_name.return_value = _make_stored_entity(
        entity_id="ent-redis", name="redis"
    )
    fake_store.query_relationships.return_value = []
    fake_store.get_memories_by_entities.return_value = []
    fake_store.count_memories_for_entity.return_value = 0
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/ui/topics/redis")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity"]["name"] == "redis"


# ── routes/review.py (T20) — 4 handlers ────────────────────────


def test_get_pending_reviews_empty(fake_store):
    from lore.server.routes.review import router
    fake_store.list_pending_relationships.return_value = []
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/review")
    assert resp.status_code == 200
    assert resp.json()["pending"] == []


def test_get_pending_reviews_with_data(fake_store):
    from lore.server.routes.review import router
    fake_store.list_pending_relationships.return_value = [_make_pending_row()]
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.get("/v1/review")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pending"]) == 1
    assert body["pending"][0]["risk_score"] is not None


def test_review_inbox_filters_by_min_risk(fake_store):
    from lore.server.routes.review import router
    fake_store.list_pending_relationships.return_value = [_make_pending_row()]
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    # min_risk=200 is impossibly high → empty
    resp = client.get("/v1/review/inbox?min_risk=200")
    assert resp.status_code == 200
    assert resp.json()["pending"] == []


def test_review_relationship_invalid_action(fake_store):
    from lore.server.routes.review import router
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.post("/v1/review/rel-x", json={"action": "archive"})
    assert resp.status_code == 400


def test_review_relationship_404(fake_store):
    from lore.server.routes.review import router
    fake_store.get_relationship.return_value = None
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.post("/v1/review/rel-missing", json={"action": "approve"})
    assert resp.status_code == 404


def test_review_relationship_approve(fake_store):
    from lore.server.routes.review import router
    rel = _make_stored_relationship(rel_id="rel-1", status="pending")
    fake_store.get_relationship.return_value = rel
    fake_store.update_relationship_status.return_value = _make_stored_relationship(
        rel_id="rel-1", status="approved"
    )
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.post("/v1/review/rel-1", json={"action": "approve"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["previous_status"] == "pending"


def test_bulk_review_invalid_action(fake_store):
    from lore.server.routes.review import router
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.post("/v1/review/bulk", json={"action": "archive", "ids": []})
    assert resp.status_code == 400


def test_bulk_review_empty(fake_store):
    from lore.server.routes.review import router
    app = _build_app_with_store([router], fake_store)
    client = TestClient(app)
    resp = client.post("/v1/review/bulk", json={"action": "approve", "ids": []})
    assert resp.status_code == 200
    assert resp.json() == {"updated": 0, "action": "approve"}
