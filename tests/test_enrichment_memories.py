"""Tests for F3: MCP Remember Enrichment via POST /v1/memories."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


def _make_stored_memory(memory_id: str = "mem-001", content: str = "Test content"):
    """Build a StoredMemory for use in tests."""
    from lore.persistence.types import StoredMemory
    now = datetime.now(timezone.utc)
    return StoredMemory(
        id=memory_id,
        org_id="org-001",
        content=content,
        context=None,
        tags=(),
        source=None,
        project=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
        access_count=0,
        last_accessed_at=None,
    )


class FakeStore:
    """Fake Store for testing route handlers (mirrors test_memories_server.py pattern)."""

    def __init__(self):
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
        with patch("lore.server.routes.memories.require_role", return_value=lambda: mock_auth):
            with patch("lore.server.routes.retrieve._get_embedder", return_value=mock_embedder):
                # PR B (graph-extraction wiring) adds a second
                # asyncio.create_task in the create handler. Force it
                # off here so the existing ``assert_called_once`` checks
                # in this file stay deterministic regardless of whether
                # ``claude`` is on PATH in the test environment.
                with patch.dict("os.environ", {"LORE_GRAPH_EXTRACTION_ENABLED": "false"}):
                    yield TestClient(app)


class TestEnrichmentTrigger:
    def test_create_memory_returns_201_without_enrichment(self, client):
        """Memory creation works even when enrichment is not enabled."""
        test_client = client
        with patch.dict("os.environ", {"LORE_ENRICHMENT_ENABLED": "false"}):
            resp = test_client.post("/v1/memories", json={
                "content": "FastAPI supports async handlers natively",
            })
        assert resp.status_code == 201
        assert "id" in resp.json()

    def test_create_memory_with_enrich_false(self, client):
        """Explicit enrich=false should skip enrichment."""
        test_client = client
        with patch("lore.server.routes.memories.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=[0.0] * 384)
            resp = test_client.post("/v1/memories", json={
                "content": "Test content",
                "enrich": False,
            })
        assert resp.status_code == 201
        # asyncio.create_task should NOT be called
        mock_asyncio.create_task.assert_not_called()

    def test_create_memory_with_enrich_true(self, client):
        """Explicit enrich=true should trigger enrichment."""
        test_client = client
        with patch("lore.server.routes.memories.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=[0.0] * 384)
            resp = test_client.post("/v1/memories", json={
                "content": "Docker containers are process isolation",
                "enrich": True,
            })
        assert resp.status_code == 201
        # asyncio.create_task should be called for enrichment
        mock_asyncio.create_task.assert_called_once()

    def test_create_memory_env_enrichment_enabled(self, client):
        """When LORE_ENRICHMENT_ENABLED=true and enrich is not set, should trigger."""
        test_client = client
        with patch("lore.server.routes.memories.asyncio") as mock_asyncio, \
             patch.dict("os.environ", {"LORE_ENRICHMENT_ENABLED": "true"}):
            mock_asyncio.to_thread = AsyncMock(return_value=[0.0] * 384)
            resp = test_client.post("/v1/memories", json={
                "content": "Kubernetes uses etcd for state storage",
            })
        assert resp.status_code == 201
        mock_asyncio.create_task.assert_called_once()

    def test_enrichment_does_not_block_response(self, client):
        """The POST response should return immediately, not wait for enrichment."""
        test_client = client
        # If enrichment blocked, this would hang or fail
        with patch("lore.server.routes.memories.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=[0.0] * 384)
            resp = test_client.post("/v1/memories", json={
                "content": "Redis supports pub/sub messaging",
                "enrich": True,
            })
        assert resp.status_code == 201
        # Verify fire-and-forget pattern: create_task was called
        mock_asyncio.create_task.assert_called_once()


class TestEnrichMemoryFunction:
    @pytest.mark.asyncio
    async def test_enrich_memory_updates_meta(self):
        """enrich_memory_async should update the memory's meta with enrichment data."""
        from lore.services.memories import enrich_memory_async

        mock_result = {
            "topics": ["docker", "containers"],
            "sentiment": {"label": "neutral", "score": 0.0},
            "entities": [{"name": "Docker", "type": "tool"}],
            "categories": ["infrastructure"],
        }

        fake_store = FakeStore()
        fake_store.enrich_memory_meta = AsyncMock()

        with patch("lore.enrichment.pipeline.EnrichmentPipeline.enrich", return_value=mock_result), \
             patch("lore.enrichment.llm.LLMClient.__init__", return_value=None):
            await enrich_memory_async(fake_store, memory_id="mem-001", content="Docker is great", context=None)

        # Verify the store's enrich_memory_meta was called with the enrichment data
        fake_store.enrich_memory_meta.assert_called_once_with("mem-001", mock_result)

    @pytest.mark.asyncio
    async def test_enrich_memory_handles_failure_gracefully(self):
        """enrich_memory_async should log but not raise on failure."""
        from lore.services.memories import enrich_memory_async

        fake_store = FakeStore()

        with patch("lore.enrichment.pipeline.EnrichmentPipeline.enrich", side_effect=Exception("LLM down")), \
             patch("lore.enrichment.llm.LLMClient.__init__", return_value=None):
            # Should not raise
            await enrich_memory_async(fake_store, memory_id="mem-001", content="test content", context=None)
