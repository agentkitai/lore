"""Tests for /metrics endpoint and Prometheus metrics collection."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from lore.server.app import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_metrics_endpoint(client):
    """GET /metrics returns Prometheus text format."""
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "lore_memories_saved_total" in body
    assert "lore_recall_queries_total" in body
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body


@pytest.mark.asyncio
async def test_metrics_disabled(client, monkeypatch):
    """When METRICS_ENABLED=false, /metrics returns 404."""
    from lore.server import config
    original = config.settings.metrics_enabled
    config.settings.metrics_enabled = False
    try:
        resp = await client.get("/metrics")
        assert resp.status_code == 404
    finally:
        config.settings.metrics_enabled = original


def test_counter_increment():
    """Counter increments correctly."""
    from lore.server.metrics import _Counter
    c = _Counter("test_total", "test", ["method"])
    c.inc(method="GET")
    c.inc(method="GET")
    c.inc(method="POST")
    output = c.collect()
    assert 'test_total{method="GET"} 2' in output
    assert 'test_total{method="POST"} 1' in output


def test_histogram_observe():
    """Histogram collects observations."""
    from lore.server.metrics import _Histogram
    h = _Histogram("test_seconds", "test")
    h.observe(0.1)
    h.observe(0.5)
    output = h.collect()
    assert "test_seconds_count" in output
    assert "test_seconds_sum" in output
    assert "test_seconds_bucket" in output


def test_gauge_set():
    """Gauge tracks values."""
    from lore.server.metrics import _Gauge
    g = _Gauge("test_gauge", "test")
    g.set(42.0)
    output = g.collect()
    assert "test_gauge 42.0" in output


class TestNormalizePath:
    """Tests for path normalization to prevent metric label cardinality explosion."""

    def test_uuid_replaced(self):
        from lore.server.middleware import normalize_path
        assert normalize_path("/v1/memories/550e8400-e29b-41d4-a716-446655440000") == "/v1/memories/:id"

    def test_mongo_objectid_replaced(self):
        from lore.server.middleware import normalize_path
        assert normalize_path("/v1/memories/507f1f77bcf86cd799439011") == "/v1/memories/:id"

    def test_numeric_id_replaced(self):
        from lore.server.middleware import normalize_path
        assert normalize_path("/v1/orgs/42/memories") == "/v1/orgs/:id/memories"

    def test_static_path_unchanged(self):
        from lore.server.middleware import normalize_path
        assert normalize_path("/v1/memories") == "/v1/memories"
        assert normalize_path("/health") == "/health"

    def test_multiple_dynamic_segments(self):
        from lore.server.middleware import normalize_path
        assert normalize_path("/v1/orgs/123/memories/550e8400-e29b-41d4-a716-446655440000") == "/v1/orgs/:id/memories/:id"

    def test_root_path(self):
        from lore.server.middleware import normalize_path
        assert normalize_path("/") == "/"

    def test_mixed_segments(self):
        from lore.server.middleware import normalize_path
        # 'v1' should NOT be replaced (not purely numeric, not a UUID/hex ID)
        assert normalize_path("/v1/memories") == "/v1/memories"
