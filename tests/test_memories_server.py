"""Tests for the /v1/memories CRUD endpoints (v0.9.0+)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

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
        self.fetchval = AsyncMock(return_value=0)
        self.fetch = AsyncMock(return_value=[])


class FakePool:
    """Fake connection pool."""

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
    from lore.server.auth import get_auth_context
    from lore.server.routes.memories import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    async def fake_get_pool():
        return db_pool

    with patch("lore.server.routes.memories.get_pool", fake_get_pool):
        with patch("lore.server.routes.memories.require_role", return_value=lambda: mock_auth):
            yield TestClient(app), db_conn


class TestMemoryCreate:
    def test_post_returns_201(self, client):
        test_client, conn = client
        resp = test_client.post("/v1/memories", json={
            "content": "Python uses GIL for thread safety",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data

    def test_post_with_context(self, client):
        test_client, conn = client
        resp = test_client.post("/v1/memories", json={
            "content": "Use asyncio for concurrent I/O",
            "context": "Python performance optimization",
            "tags": ["python", "async"],
            "source": "code-review",
        })
        assert resp.status_code == 201
        assert "id" in resp.json()

    def test_post_empty_content_fails(self, client):
        test_client, conn = client
        resp = test_client.post("/v1/memories", json={
            "content": "",
        })
        assert resp.status_code == 422


class TestMemoryRead:
    def test_get_not_found(self, client):
        test_client, conn = client
        conn.fetchrow.return_value = None
        resp = test_client.get("/v1/memories/nonexistent")
        assert resp.status_code == 404

    def test_get_returns_memory(self, client):
        test_client, conn = client
        now = datetime.now(timezone.utc)
        conn.fetchrow.return_value = {
            "id": "mem-001",
            "content": "Use type hints everywhere",
            "context": "Python best practices",
            "tags": '["python"]',
            "confidence": 0.9,
            "source": "manual",
            "project": "lore",
            "created_at": now,
            "updated_at": now,
            "expires_at": None,
            "upvotes": 3,
            "downvotes": 0,
            "meta": "{}",
        }
        resp = test_client.get("/v1/memories/mem-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "mem-001"
        assert data["content"] == "Use type hints everywhere"
        assert data["context"] == "Python best practices"


class TestMemoryList:
    def test_list_empty(self, client):
        test_client, conn = client
        conn.fetchval.return_value = 0
        conn.fetch.return_value = []
        resp = test_client.get("/v1/memories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["memories"] == []
        assert data["total"] == 0

    def test_list_with_query_filter(self, client):
        test_client, conn = client
        conn.fetchval.return_value = 0
        conn.fetch.return_value = []
        resp = test_client.get("/v1/memories?query=python")
        assert resp.status_code == 200


class TestMemoryUpdate:
    def test_patch_not_found(self, client):
        test_client, conn = client
        conn.fetchrow.return_value = None
        resp = test_client.patch("/v1/memories/nonexistent", json={
            "confidence": 0.8,
        })
        assert resp.status_code == 404

    def test_patch_no_fields(self, client):
        test_client, conn = client
        resp = test_client.patch("/v1/memories/mem-001", json={})
        assert resp.status_code == 422


class TestMemoryDelete:
    def test_delete_not_found(self, client):
        test_client, conn = client
        conn.execute.return_value = "DELETE 0"
        resp = test_client.delete("/v1/memories/nonexistent")
        assert resp.status_code == 404

    def test_delete_success(self, client):
        test_client, conn = client
        conn.execute.return_value = "DELETE 1"
        resp = test_client.delete("/v1/memories/mem-001")
        assert resp.status_code == 204


class TestMemoryModels:
    def test_create_request_fields(self):
        from lore.server.models import MemoryCreateRequest
        req = MemoryCreateRequest(content="test memory")
        assert req.content == "test memory"
        assert req.context is None
        assert req.confidence == 0.5
        assert req.tags == []
        assert req.enrich is None

    def test_response_fields(self):
        from lore.server.models import MemoryResponse
        now = datetime.now(timezone.utc)
        resp = MemoryResponse(
            id="mem-001",
            content="test",
            context="ctx",
            confidence=0.9,
            created_at=now,
            updated_at=now,
        )
        assert resp.content == "test"
        assert resp.context == "ctx"

    def test_search_request_embedding_validation(self):
        from lore.server.models import MemorySearchRequest
        with pytest.raises(Exception):
            MemorySearchRequest(embedding=[0.1, 0.2])  # wrong dim
