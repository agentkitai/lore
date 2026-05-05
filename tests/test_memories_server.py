"""Tests for the /v1/memories CRUD endpoints (v0.9.0+)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


def _make_stored_memory(
    memory_id: str = "mem-001",
    content: str = "Use type hints everywhere",
    context: Optional[str] = "Python best practices",
    tags: Sequence[str] = ("python",),
    confidence: float = 0.9,
    source: Optional[str] = "manual",
    project: Optional[str] = "lore",
    upvotes: int = 3,
    downvotes: int = 0,
    meta: Mapping[str, Any] = None,
):
    """Build a StoredMemory dataclass for use in tests."""
    from lore.persistence.types import StoredMemory
    now = datetime.now(timezone.utc)
    return StoredMemory(
        id=memory_id,
        org_id="org-001",
        content=content,
        context=context,
        tags=tuple(tags),
        confidence=confidence,
        source=source,
        project=project,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=upvotes,
        downvotes=downvotes,
        meta=dict(meta or {}),
        importance_score=1.0,
        access_count=0,
        last_accessed_at=None,
    )


class FakeStore:
    """Fake Store for testing route handlers."""

    def __init__(self):
        self._stored = _make_stored_memory()
        self.insert_memory = AsyncMock(return_value=_make_stored_memory())
        self.get_memory = AsyncMock(return_value=None)
        self.update_memory = AsyncMock(return_value=_make_stored_memory())
        self.delete_memory = AsyncMock(return_value=True)
        self.list_memories = AsyncMock(return_value=[])
        self.recall_by_embedding = AsyncMock(return_value=[])
        self.vote_memory = AsyncMock(return_value=_make_stored_memory())

    async def close(self):
        pass


@pytest.fixture
def fake_store():
    return FakeStore()


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
def client(fake_store, mock_auth):
    """Create test client with mocked store and auth."""
    from lore.server.auth import get_auth_context
    from lore.server.routes.memories import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    async def fake_get_store():
        return fake_store

    # Mock the embedder so tests don't need ONNX models loaded
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.0] * 384

    with patch("lore.server.routes.memories.get_store", fake_get_store):
        with patch("lore.server.routes.memories.get_pool", AsyncMock()):
            with patch("lore.server.routes.memories.require_role", return_value=lambda: mock_auth):
                with patch("lore.server.routes.retrieve._get_embedder", return_value=mock_embedder):
                    yield TestClient(app), fake_store


class TestMemoryCreate:
    def test_post_returns_201(self, client):
        test_client, store = client
        resp = test_client.post("/v1/memories", json={
            "content": "Python uses GIL for thread safety",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data

    def test_post_with_context(self, client):
        test_client, store = client
        resp = test_client.post("/v1/memories", json={
            "content": "Use asyncio for concurrent I/O",
            "context": "Python performance optimization",
            "tags": ["python", "async"],
            "source": "code-review",
        })
        assert resp.status_code == 201
        assert "id" in resp.json()

    def test_post_empty_content_fails(self, client):
        test_client, store = client
        resp = test_client.post("/v1/memories", json={
            "content": "",
        })
        assert resp.status_code == 422


class TestMemoryRead:
    def test_get_not_found(self, client):
        test_client, store = client
        store.get_memory.return_value = None
        resp = test_client.get("/v1/memories/nonexistent")
        assert resp.status_code == 404

    def test_get_returns_memory(self, client):
        test_client, store = client
        stored = _make_stored_memory(
            memory_id="mem-001",
            content="Use type hints everywhere",
            context="Python best practices",
            tags=("python",),
            upvotes=3,
            downvotes=0,
        )
        store.get_memory.return_value = stored
        resp = test_client.get("/v1/memories/mem-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "mem-001"
        assert data["content"] == "Use type hints everywhere"
        assert data["context"] == "Python best practices"


class TestMemoryList:
    def test_list_empty(self, client):
        test_client, store = client
        store.list_memories.return_value = []
        resp = test_client.get("/v1/memories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["memories"] == []
        assert data["total"] == 0

    def test_list_with_query_filter(self, client):
        test_client, store = client
        store.list_memories.return_value = []
        resp = test_client.get("/v1/memories?query=python")
        assert resp.status_code == 200


class TestMemoryUpdate:
    def test_patch_not_found(self, client):
        test_client, store = client
        from lore.persistence.exceptions import StoreNotFoundError
        store.update_memory.side_effect = StoreNotFoundError("memory", "nonexistent")
        resp = test_client.patch("/v1/memories/nonexistent", json={
            "confidence": 0.8,
        })
        assert resp.status_code == 404

    def test_patch_no_fields(self, client):
        test_client, store = client
        resp = test_client.patch("/v1/memories/mem-001", json={})
        assert resp.status_code == 422


class TestMemoryDelete:
    def test_delete_not_found(self, client):
        test_client, store = client
        store.delete_memory.return_value = False
        resp = test_client.delete("/v1/memories/nonexistent")
        assert resp.status_code == 404

    def test_delete_success(self, client):
        test_client, store = client
        store.delete_memory.return_value = True
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
