"""Tests for conversation REST API endpoints."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


class FakeConn:
    """Fake DB connection for testing."""

    def __init__(self):
        self.execute = AsyncMock()
        self.fetchrow = AsyncMock(return_value=None)


class FakePool:
    """Fake connection pool that provides async context manager."""

    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


@pytest.fixture
def db_conn():
    return FakeConn()


@pytest.fixture
def db_pool(db_conn):
    return FakePool(db_conn)


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
def client(db_pool, db_conn, mock_auth):
    """Create test client with mocked dependencies."""
    from lore.server.auth import get_auth_context, require_role
    from lore.server.routes.conversations import router

    app = FastAPI()
    app.include_router(router)

    # Override auth dependencies
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    # require_role returns a dependency function; override the actual dependency
    # by patching at the router level
    for route in router.routes:
        if hasattr(route, 'dependant'):
            for dep in route.dependant.dependencies:
                if hasattr(dep, 'dependency'):
                    if dep.dependency.__name__ if callable(dep.dependency) else "" in ("get_auth_context",):
                        dep.dependency = lambda: mock_auth

    async def fake_get_pool():
        return db_pool

    with patch("lore.server.routes.conversations.get_pool", fake_get_pool):
        with patch("lore.server.routes.conversations.require_role", return_value=lambda: mock_auth):
            # Re-import to get patched version
            yield TestClient(app), db_conn


class TestConversationEndpoints:
    def test_post_returns_202(self, client):
        test_client, conn = client
        resp = test_client.post("/v1/conversations", json={
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
        })
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "accepted"
        assert data["message_count"] == 2

    def test_post_empty_messages_returns_400(self, client):
        test_client, conn = client
        resp = test_client.post("/v1/conversations", json={
            "messages": [],
        })
        assert resp.status_code == 400

    def test_post_missing_role_returns_422(self, client):
        test_client, conn = client
        resp = test_client.post("/v1/conversations", json={
            "messages": [{"content": "no role"}],
        })
        assert resp.status_code in (400, 422)

    def test_get_not_found(self, client):
        test_client, conn = client
        conn.fetchrow.return_value = None
        resp = test_client.get("/v1/conversations/nonexistent-id")
        assert resp.status_code == 404

    def test_get_returns_status(self, client):
        test_client, conn = client
        conn.fetchrow.return_value = {
            "id": "job-001",
            "status": "completed",
            "message_count": 3,
            "memories_extracted": 2,
            "memory_ids": '["mem-1", "mem-2"]',
            "duplicates_skipped": 1,
            "processing_time_ms": 1500,
            "error": None,
        }
        resp = test_client.get("/v1/conversations/job-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "job-001"
        assert data["status"] == "completed"
        assert data["memories_extracted"] == 2
        assert data["memory_ids"] == ["mem-1", "mem-2"]
        assert data["duplicates_skipped"] == 1
