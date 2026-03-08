"""Tests for F3: MCP Remember Enrichment via POST /v1/memories."""

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
    def __init__(self):
        self.execute = AsyncMock()
        self.fetchrow = AsyncMock(return_value=None)
        self.fetchval = AsyncMock(return_value=0)
        self.fetch = AsyncMock(return_value=[])


class FakePool:
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


class TestEnrichmentTrigger:
    def test_create_memory_returns_201_without_enrichment(self, client):
        """Memory creation works even when enrichment is not enabled."""
        test_client, conn = client
        with patch.dict("os.environ", {"LORE_ENRICHMENT_ENABLED": "false"}):
            resp = test_client.post("/v1/memories", json={
                "content": "FastAPI supports async handlers natively",
            })
        assert resp.status_code == 201
        assert "id" in resp.json()

    def test_create_memory_with_enrich_false(self, client):
        """Explicit enrich=false should skip enrichment."""
        test_client, conn = client
        with patch("lore.server.routes.memories.asyncio") as mock_asyncio:
            resp = test_client.post("/v1/memories", json={
                "content": "Test content",
                "enrich": False,
            })
        assert resp.status_code == 201
        # asyncio.create_task should NOT be called
        mock_asyncio.create_task.assert_not_called()

    def test_create_memory_with_enrich_true(self, client):
        """Explicit enrich=true should trigger enrichment."""
        test_client, conn = client
        with patch("lore.server.routes.memories.asyncio") as mock_asyncio:
            resp = test_client.post("/v1/memories", json={
                "content": "Docker containers are process isolation",
                "enrich": True,
            })
        assert resp.status_code == 201
        # asyncio.create_task should be called for enrichment
        mock_asyncio.create_task.assert_called_once()

    def test_create_memory_env_enrichment_enabled(self, client):
        """When LORE_ENRICHMENT_ENABLED=true and enrich is not set, should trigger."""
        test_client, conn = client
        with patch("lore.server.routes.memories.asyncio") as mock_asyncio, \
             patch.dict("os.environ", {"LORE_ENRICHMENT_ENABLED": "true"}):
            resp = test_client.post("/v1/memories", json={
                "content": "Kubernetes uses etcd for state storage",
            })
        assert resp.status_code == 201
        mock_asyncio.create_task.assert_called_once()

    def test_enrichment_does_not_block_response(self, client):
        """The POST response should return immediately, not wait for enrichment."""
        test_client, conn = client
        # If enrichment blocked, this would hang or fail
        with patch("lore.server.routes.memories.asyncio") as mock_asyncio:
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
        """_enrich_memory should update the memory's meta with enrichment data."""
        from dataclasses import dataclass

        from lore.server.routes.memories import _enrich_memory

        fake_conn = FakeConn()
        fake_pool = FakePool(fake_conn)

        @dataclass
        class FakeSentiment:
            label: str = "neutral"
            score: float = 0.0

        @dataclass
        class FakeEntity:
            name: str = ""
            type: str = ""

        mock_result = MagicMock()
        mock_result.topics = ["docker", "containers"]
        mock_result.sentiment = FakeSentiment()
        mock_result.entities = [FakeEntity(name="Docker", type="tool")]
        mock_result.categories = ["infrastructure"]

        with patch("lore.server.routes.memories.get_pool", AsyncMock(return_value=fake_pool)), \
             patch("lore.enrichment.pipeline.EnrichmentPipeline.enrich", return_value=mock_result), \
             patch("lore.enrichment.llm.LLMClient.__init__", return_value=None):
            await _enrich_memory("mem-001", "Docker is great", None)

        # Verify the UPDATE was called
        fake_conn.execute.assert_called_once()
        call_args = fake_conn.execute.call_args
        assert "mem-001" in call_args[0]
        enrichment_json = call_args[0][2]
        enrichment = json.loads(enrichment_json)
        assert "docker" in enrichment["topics"]
        assert enrichment["entities"][0]["name"] == "Docker"

    @pytest.mark.asyncio
    async def test_enrich_memory_handles_failure_gracefully(self):
        """_enrich_memory should log but not raise on failure."""
        from lore.server.routes.memories import _enrich_memory

        with patch("lore.enrichment.pipeline.EnrichmentPipeline.enrich", side_effect=Exception("LLM down")), \
             patch("lore.enrichment.llm.LLMClient.__init__", return_value=None):
            # Should not raise
            await _enrich_memory("mem-001", "test content", None)
