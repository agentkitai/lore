"""Tests for REST ingestion endpoints (F7-S7, S8, S10)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from lore.ingest.dedup import DedupResult
from lore.ingest.pipeline import IngestResult, IngestionPipeline
from lore.ingest.rate_limit import IngestRateLimiter

# Use FastAPI test client
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")


def _make_app(
    pipeline_result=None,
    api_keys=None,
    adapter_secrets=None,
    queue=None,
    batch_max=100,
):
    """Create a test FastAPI app with ingest routes."""
    from lore.server.routes.ingest import router

    app = FastAPI()
    app.include_router(router)

    # Configure app state
    pipeline = MagicMock(spec=IngestionPipeline)
    if pipeline_result:
        pipeline.ingest.return_value = pipeline_result
    else:
        pipeline.ingest.return_value = IngestResult(status="ingested", memory_id="mem-123")

    app.state.ingest_enabled = True
    app.state.ingest_pipeline = pipeline
    app.state.ingest_api_keys = api_keys or {
        "test-key": {"key_id": "k1", "scopes": ["ingest"], "project": None},
    }
    app.state.adapter_secrets = adapter_secrets or {}
    app.state.ingest_rate_limiter = IngestRateLimiter()
    app.state.ingest_queue = queue
    app.state.ingest_batch_max = batch_max

    return app, pipeline


class TestSingleIngest:
    def test_basic_ingest(self):
        app, pipeline = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/ingest?key=test-key",
            json={"content": "test memory", "source": "raw"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "ingested"
        assert data["memory_id"] == "mem-123"
        assert data["source"] == "raw"

    def test_raw_shorthand(self):
        app, pipeline = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/ingest",
            json={"content": "text", "source": "raw", "user": "alice"},
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 201

    def test_missing_api_key(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/ingest", json={"content": "test"})
        assert resp.status_code == 401

    def test_invalid_api_key(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/ingest?key=bad-key", json={"content": "test"})
        assert resp.status_code == 401

    def test_scope_enforcement(self):
        app, _ = _make_app(api_keys={
            "no-scope-key": {"key_id": "k2", "scopes": ["read"], "project": None},
        })
        client = TestClient(app)
        resp = client.post("/ingest?key=no-scope-key", json={"content": "test"})
        assert resp.status_code == 403

    def test_source_restriction(self):
        app, _ = _make_app(api_keys={
            "slack-only": {"key_id": "k3", "scopes": ["ingest"], "allowed_sources": ["slack"], "project": None},
        })
        client = TestClient(app)
        resp = client.post(
            "/ingest?key=slack-only",
            json={"source": "telegram", "payload": {"message": {}}},
        )
        assert resp.status_code == 403

    def test_duplicate_rejection(self):
        result = IngestResult(
            status="duplicate_rejected",
            duplicate_of="existing-1",
            similarity=1.0,
            dedup_strategy="exact_id",
        )
        app, _ = _make_app(pipeline_result=result)
        client = TestClient(app)
        resp = client.post("/ingest?key=test-key", json={"content": "dup"})
        assert resp.status_code == 409
        data = resp.json()
        assert data["status"] == "duplicate_rejected"
        assert data["duplicate_of"] == "existing-1"

    def test_unknown_adapter(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/ingest?key=test-key",
            json={"source": "unknown_adapter", "payload": {}},
        )
        assert resp.status_code == 400
        assert "Unknown source adapter" in resp.text


class TestBatchIngest:
    def test_basic_batch(self):
        app, pipeline = _make_app()
        pipeline.ingest.return_value = IngestResult(status="ingested", memory_id="m1")
        client = TestClient(app)
        resp = client.post(
            "/ingest/batch?key=test-key",
            json={
                "items": [{"content": "A"}, {"content": "B"}],
                "source": "raw",
                "project": "p1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["ingested"] == 2

    def test_batch_limit_exceeded(self):
        app, _ = _make_app(batch_max=2)
        client = TestClient(app)
        resp = client.post(
            "/ingest/batch?key=test-key",
            json={"items": [{"content": "A"}, {"content": "B"}, {"content": "C"}]},
        )
        assert resp.status_code == 400
        assert "exceeds maximum" in resp.text

    def test_partial_failure_207(self):
        app, pipeline = _make_app()
        # First call succeeds, second fails
        pipeline.ingest.side_effect = [
            IngestResult(status="ingested", memory_id="m1"),
            IngestResult(status="failed", error="empty content"),
        ]
        client = TestClient(app)
        resp = client.post(
            "/ingest/batch?key=test-key",
            json={"items": [{"content": "A"}, {"content": ""}]},
        )
        assert resp.status_code == 207
        data = resp.json()
        assert data["ingested"] == 1
        assert data["failed"] == 1


class TestWebhookEndpoint:
    def test_slack_url_verification(self):
        app, _ = _make_app(adapter_secrets={"slack": {"signing_secret": "test"}})
        client = TestClient(app)
        # Slack doesn't verify url_verification challenges with real HMAC in tests
        # so we need the adapter to return True for verify
        with patch("lore.ingest.adapters.slack.SlackAdapter.verify", return_value=True):
            resp = client.post(
                "/ingest/webhook/slack?key=test-key",
                content=json.dumps({"type": "url_verification", "challenge": "abc123"}),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.json()["challenge"] == "abc123"

    def test_webhook_signature_failure(self):
        app, _ = _make_app(adapter_secrets={"slack": {"signing_secret": "real-secret"}})
        client = TestClient(app)
        resp = client.post(
            "/ingest/webhook/slack?key=test-key",
            content=b'{"event":{"text":"hi"}}',
            headers={
                "Content-Type": "application/json",
                "x-slack-request-timestamp": "0",
                "x-slack-signature": "v0=invalid",
            },
        )
        assert resp.status_code == 401
        assert "Webhook signature verification failed" in resp.text

    def test_bot_message_ignored(self):
        app, pipeline = _make_app(adapter_secrets={"slack": {"signing_secret": "test"}})
        client = TestClient(app)
        with patch("lore.ingest.adapters.slack.SlackAdapter.verify", return_value=True):
            resp = client.post(
                "/ingest/webhook/slack?key=test-key",
                content=json.dumps({"event": {"subtype": "bot_message", "text": "bot"}}),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        pipeline.ingest.assert_not_called()


class TestRateLimiting:
    def test_rate_limit_headers(self):
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/ingest?key=test-key", json={"content": "test"})
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers

    def test_rate_limit_exceeded(self):
        # Set very low rate limit
        app, _ = _make_app()
        app.state.ingest_rate_limiter = IngestRateLimiter(per_key_limit=2)
        client = TestClient(app)

        # First two succeed
        client.post("/ingest?key=test-key", json={"content": "1"})
        client.post("/ingest?key=test-key", json={"content": "2"})
        # Third should fail
        resp = client.post("/ingest?key=test-key", json={"content": "3"})
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


class TestQueueMode:
    def test_queue_returns_202(self):
        from lore.ingest.queue import IngestionQueue
        import asyncio

        queue = IngestionQueue(max_size=10)
        app, _ = _make_app(queue=queue)
        client = TestClient(app)
        resp = client.post("/ingest?key=test-key", json={"content": "queued item"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert "tracking_id" in data

    def test_queue_status_endpoint(self):
        from lore.ingest.queue import IngestionQueue, QueueItem

        queue = IngestionQueue(max_size=10)
        item = QueueItem(
            tracking_id="track-123",
            adapter_name="raw",
            payload={"content": "test"},
            status="processing",
        )
        queue._items["track-123"] = item

        app, _ = _make_app(queue=queue)
        client = TestClient(app)

        # Auth not needed for status endpoint in this test setup
        resp = client.get("/ingest/status/track-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"

    def test_queue_status_not_found(self):
        from lore.ingest.queue import IngestionQueue

        queue = IngestionQueue()
        app, _ = _make_app(queue=queue)
        client = TestClient(app)
        resp = client.get("/ingest/status/nonexistent")
        assert resp.status_code == 404
