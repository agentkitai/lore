"""Tests for dashboard routes: recent, audit, analytics, topics (Phase 1I, T9).

Each test uses a minimal FakeStore for dependency wiring and patches the
service-module functions with AsyncMock to control return values.
The `get_store` and `get_auth_context` dependencies are bypassed via
dependency_overrides.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


# ── helpers ───────────────────────────────────────────────────────────────────


def _utc_now():
    return datetime.now(timezone.utc)


def _make_stored_memory(
    memory_id="mem-1",
    org_id="org-001",
    content="some content",
    context="some context",
    tags=(),
    project=None,
    **kwargs,
):
    from lore.persistence.types import StoredMemory

    now = _utc_now()
    defaults = dict(
        id=memory_id,
        org_id=org_id,
        content=content,
        context=context,
        tags=list(tags),
        source=None,
        project=project,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
        access_count=0,
        last_accessed_at=None,
    )
    defaults.update(kwargs)
    return StoredMemory(**defaults)


def _make_stored_audit_entry(id=1, **kwargs):
    from lore.persistence.types import StoredAuditEntry

    now = _utc_now()
    defaults = dict(
        id=id,
        org_id="org-001",
        workspace_id=None,
        actor_id="actor-1",
        actor_type="user",
        action="memories.create",
        resource_type=None,
        resource_id=None,
        metadata={},
        ip_address=None,
        created_at=now,
    )
    defaults.update(kwargs)
    return StoredAuditEntry(**defaults)


def _make_analytics_dict(**overrides):
    defaults = dict(
        total_queries=100,
        queries_with_results=80,
        queries_empty=20,
        hit_rate=0.8,
        avg_results_per_query=3.5,
        avg_score=0.75,
        avg_max_score=0.92,
        avg_latency_ms=42.0,
        p95_latency_ms=120.0,
        score_distribution=[],
        top_queries=[],
        memory_utilization=0.65,
        unique_memories_retrieved=50,
        total_memories=200,
        daily_stats=[],
        lookback_days=7,
    )
    defaults.update(overrides)
    return defaults


class FakeStore:
    """Minimal Store stand-in — actual logic is mocked at the service layer."""

    async def close(self):
        pass


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_auth():
    from lore.server.auth import AuthContext

    return AuthContext(
        org_id="org-001",
        project=None,
        is_root=True,
        key_id="key-001",
        role="admin",
    )


@pytest.fixture
def client(monkeypatch, mock_auth):
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.analytics import router as analytics_router
    from lore.server.routes.audit import router as audit_router
    from lore.server.routes.recent import router as recent_router
    from lore.server.routes.topics import router as topics_router

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(recent_router)
    app.include_router(audit_router)
    app.include_router(analytics_router)
    app.include_router(topics_router)

    app.dependency_overrides[get_store] = lambda: fake_store
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    yield TestClient(app), fake_store


# ── recent ────────────────────────────────────────────────────────────────────


def test_recent_returns_groups(client, monkeypatch):
    """GET /v1/recent with format=structured returns groups array."""
    from lore.services import recent as recent_service

    test_client, _store = client
    m1 = _make_stored_memory(memory_id="mem-1", project="proj-a", content="memory one")
    m2 = _make_stored_memory(memory_id="mem-2", project="proj-b", content="memory two")
    monkeypatch.setattr(
        recent_service,
        "get_recent_activity",
        AsyncMock(return_value=[m1, m2]),
    )
    resp = test_client.get("/v1/recent?format=structured")
    assert resp.status_code == 200
    body = resp.json()
    assert "groups" in body
    assert isinstance(body["groups"], list)
    assert len(body["groups"]) == 2
    project_names = {g["project"] for g in body["groups"]}
    assert project_names == {"proj-a", "proj-b"}


def test_recent_invalid_format_422(client, monkeypatch):
    """GET /v1/recent with format=bogus returns 422."""
    from lore.services import recent as recent_service

    test_client, _store = client
    monkeypatch.setattr(
        recent_service,
        "get_recent_activity",
        AsyncMock(return_value=[]),
    )
    resp = test_client.get("/v1/recent?format=bogus")
    assert resp.status_code == 422


def test_recent_filters_project(client, monkeypatch):
    """GET /v1/recent?project=myproj calls service with project arg."""
    from lore.services import recent as recent_service

    test_client, _store = client
    mock_get = AsyncMock(return_value=[])
    monkeypatch.setattr(recent_service, "get_recent_activity", mock_get)

    resp = test_client.get("/v1/recent?format=structured&project=myproj")
    assert resp.status_code == 200
    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["project"] == "myproj"


# ── audit ─────────────────────────────────────────────────────────────────────


def test_audit_returns_entries(client, monkeypatch):
    """GET /v1/audit returns list of AuditEntry objects."""
    from lore.services import audit as audit_service

    test_client, _store = client
    entry = _make_stored_audit_entry(id=42, action="memories.create")
    monkeypatch.setattr(
        audit_service,
        "query_audit_log",
        AsyncMock(return_value=[entry]),
    )
    resp = test_client.get("/v1/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    item = body[0]
    assert item["id"] == 42
    assert item["action"] == "memories.create"
    assert item["actor_id"] == "actor-1"
    assert item["org_id"] == "org-001"


def test_audit_workspace_filter(client, monkeypatch):
    """GET /v1/audit?workspace_id=ws-1 passes workspace_id to service."""
    from lore.services import audit as audit_service

    test_client, _store = client
    mock_query = AsyncMock(return_value=[])
    monkeypatch.setattr(audit_service, "query_audit_log", mock_query)

    resp = test_client.get("/v1/audit?workspace_id=ws-1")
    assert resp.status_code == 200
    mock_query.assert_called_once()
    call_kwargs = mock_query.call_args.kwargs
    assert call_kwargs["workspace_id"] == "ws-1"


def test_audit_action_filter(client, monkeypatch):
    """GET /v1/audit?action=memories.delete passes action to service."""
    from lore.services import audit as audit_service

    test_client, _store = client
    mock_query = AsyncMock(return_value=[])
    monkeypatch.setattr(audit_service, "query_audit_log", mock_query)

    resp = test_client.get("/v1/audit?action=memories.delete")
    assert resp.status_code == 200
    mock_query.assert_called_once()
    call_kwargs = mock_query.call_args.kwargs
    assert call_kwargs["action"] == "memories.delete"


# ── analytics ─────────────────────────────────────────────────────────────────


def test_analytics_returns_full_shape(client, monkeypatch):
    """GET /v1/analytics/retrieval returns all top-level keys from service dict."""
    from lore.services import analytics as analytics_service

    test_client, _store = client
    result = _make_analytics_dict()
    monkeypatch.setattr(
        analytics_service,
        "get_retrieval_analytics",
        AsyncMock(return_value=result),
    )
    resp = test_client.get("/v1/analytics/retrieval")
    assert resp.status_code == 200
    body = resp.json()
    expected_keys = {
        "total_queries",
        "queries_with_results",
        "queries_empty",
        "hit_rate",
        "avg_results_per_query",
        "avg_score",
        "avg_max_score",
        "avg_latency_ms",
        "p95_latency_ms",
        "score_distribution",
        "top_queries",
        "memory_utilization",
        "unique_memories_retrieved",
        "total_memories",
        "daily_stats",
        "lookback_days",
    }
    assert expected_keys.issubset(body.keys())
    assert body["total_queries"] == 100
    assert body["hit_rate"] == 0.8


def test_analytics_zero_state(client, monkeypatch):
    """GET /v1/analytics/retrieval with all-zero service result returns zeros."""
    from lore.services import analytics as analytics_service

    test_client, _store = client
    result = _make_analytics_dict(
        total_queries=0,
        queries_with_results=0,
        queries_empty=0,
        hit_rate=0.0,
        avg_results_per_query=0.0,
        avg_score=None,
        avg_max_score=None,
        avg_latency_ms=None,
        p95_latency_ms=None,
        memory_utilization=None,
        unique_memories_retrieved=0,
        total_memories=0,
        lookback_days=7,
    )
    monkeypatch.setattr(
        analytics_service,
        "get_retrieval_analytics",
        AsyncMock(return_value=result),
    )
    resp = test_client.get("/v1/analytics/retrieval")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_queries"] == 0
    assert body["hit_rate"] == 0.0
    assert body["avg_score"] is None


def test_analytics_project_filter(client, monkeypatch):
    """GET /v1/analytics/retrieval?project=myproj passes project to service."""
    from lore.services import analytics as analytics_service

    test_client, _store = client
    mock_get = AsyncMock(return_value=_make_analytics_dict())
    monkeypatch.setattr(analytics_service, "get_retrieval_analytics", mock_get)

    resp = test_client.get("/v1/analytics/retrieval?project=myproj")
    assert resp.status_code == 200
    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["project"] == "myproj"


# ── topics ────────────────────────────────────────────────────────────────────


def test_list_topics_returns_array(client, monkeypatch):
    """GET /v1/topics returns dict with topics array from service."""
    from lore.services import topics_dashboard as topics_service

    test_client, _store = client
    monkeypatch.setattr(
        topics_service,
        "list_topics",
        AsyncMock(
            return_value={
                "topics": [
                    {"name": "python", "mention_count": 10, "entity_type": "technology"},
                    {"name": "fastapi", "mention_count": 5, "entity_type": "technology"},
                ],
                "total": 2,
            }
        ),
    )
    resp = test_client.get("/v1/topics")
    assert resp.status_code == 200
    body = resp.json()
    assert "topics" in body
    assert len(body["topics"]) == 2
    assert body["topics"][0]["name"] == "python"


def test_get_topic_detail_returns_data(client, monkeypatch):
    """GET /v1/topics/{name} returns detail dict from service."""
    from lore.services import topics_dashboard as topics_service

    test_client, _store = client
    detail = {
        "name": "python",
        "mention_count": 10,
        "entity_type": "technology",
        "memories": [],
        "summary": "Python programming language",
    }
    monkeypatch.setattr(
        topics_service,
        "get_topic_detail",
        AsyncMock(return_value=detail),
    )
    resp = test_client.get("/v1/topics/python")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "python"
    assert body["mention_count"] == 10
    assert body["summary"] == "Python programming language"


def test_get_topic_detail_404_when_missing(client, monkeypatch):
    """GET /v1/topics/{name} returns 404 when service returns None."""
    from lore.services import topics_dashboard as topics_service

    test_client, _store = client
    monkeypatch.setattr(
        topics_service,
        "get_topic_detail",
        AsyncMock(return_value=None),
    )
    resp = test_client.get("/v1/topics/nonexistent-topic")
    assert resp.status_code == 404
