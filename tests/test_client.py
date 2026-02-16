"""Tests for LoreClient — SDK hardening (LO-E10)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lore.client import LoreClient

# ── Helpers ───────────────────────────────────────────────────────────


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    """Create a fake httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data or {},
        request=httpx.Request("POST", "http://test/v1/lessons"),
    )
    return resp


# ── Retry logic ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_503_503_200():
    """Server returns 503 twice, then 200 — should succeed after retries."""
    client = LoreClient(url="http://test", api_key="k", timeout=1.0)

    responses = [
        _mock_response(503, {"error": "unavailable"}),
        _mock_response(503, {"error": "unavailable"}),
        _mock_response(200, {"id": "lesson-123"}),
    ]
    call_count = 0

    async def mock_request(method, path, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    client._http = AsyncMock()
    client._http.request = mock_request
    client._http.aclose = AsyncMock()

    with patch("lore.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client.save(problem="test", resolution="fix")

    assert result == "lesson-123"
    assert call_count == 3
    # Check backoff delays
    assert mock_sleep.await_count == 2
    mock_sleep.assert_any_await(0.5)
    mock_sleep.assert_any_await(1.0)

    await client.close()


@pytest.mark.asyncio
async def test_retry_exhausted_returns_none():
    """Server returns 503 on all attempts — save() returns None (graceful degradation)."""
    client = LoreClient(url="http://test", api_key="k", timeout=1.0)

    async def mock_request(method, path, **kwargs):
        return _mock_response(503, {"error": "unavailable"})

    client._http = AsyncMock()
    client._http.request = mock_request
    client._http.aclose = AsyncMock()

    with patch("lore.client.asyncio.sleep", new_callable=AsyncMock):
        result = await client.save(problem="test", resolution="fix")

    assert result is None
    await client.close()


# ── Graceful degradation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_connection_refused_returns_none():
    """Connection refused → save() returns None, no exception raised."""
    client = LoreClient(url="http://test", api_key="k", timeout=1.0)

    async def mock_request(method, path, **kwargs):
        raise httpx.ConnectError("Connection refused")

    client._http = AsyncMock()
    client._http.request = mock_request
    client._http.aclose = AsyncMock()

    with patch("lore.client.asyncio.sleep", new_callable=AsyncMock):
        result = await client.save(problem="test", resolution="fix")

    assert result is None
    await client.close()


@pytest.mark.asyncio
async def test_recall_connection_refused_returns_empty():
    """Connection refused → recall() returns [], no exception raised."""
    client = LoreClient(url="http://test", api_key="k", timeout=1.0)

    async def mock_request(method, path, **kwargs):
        raise httpx.ConnectError("Connection refused")

    client._http = AsyncMock()
    client._http.request = mock_request
    client._http.aclose = AsyncMock()

    with patch("lore.client.asyncio.sleep", new_callable=AsyncMock):
        result = await client.recall("how to fix thing")

    assert result == []
    await client.close()


@pytest.mark.asyncio
async def test_recall_timeout_returns_empty():
    """Timeout → recall() returns [], no exception raised."""
    client = LoreClient(url="http://test", api_key="k", timeout=1.0)

    async def mock_request(method, path, **kwargs):
        raise httpx.TimeoutException("timed out")

    client._http = AsyncMock()
    client._http.request = mock_request
    client._http.aclose = AsyncMock()

    with patch("lore.client.asyncio.sleep", new_callable=AsyncMock):
        result = await client.recall("how to fix thing")

    assert result == []
    await client.close()


# ── Env var defaults ──────────────────────────────────────────────────


def test_env_var_defaults(monkeypatch):
    """Constructor reads LORE_URL, LORE_API_KEY, LORE_ORG_ID, LORE_TIMEOUT."""
    monkeypatch.setenv("LORE_URL", "http://custom:9999")
    monkeypatch.setenv("LORE_API_KEY", "sk-test-key")
    monkeypatch.setenv("LORE_ORG_ID", "org-42")
    monkeypatch.setenv("LORE_TIMEOUT", "10")

    client = LoreClient()
    assert client._url == "http://custom:9999"
    assert client._api_key == "sk-test-key"
    assert client._org_id == "org-42"
    assert client._timeout == 10.0
    assert client._http.headers["authorization"] == "Bearer sk-test-key"
    assert client._http.headers["x-org-id"] == "org-42"


# ── Connection pooling ────────────────────────────────────────────────


def test_connection_pooling():
    """Client reuses the same httpx.AsyncClient instance."""
    client = LoreClient(url="http://test", api_key="k")
    http1 = client._http
    http2 = client._http
    assert http1 is http2
    assert isinstance(http1, httpx.AsyncClient)


# ── Batching ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_flush_on_size():
    """Batch flushes when buffer reaches batch_size."""
    client = LoreClient(url="http://test", api_key="k", batch=True, batch_size=2, batch_interval=999)

    saved_payloads: list = []

    async def mock_request(method, path, **kwargs):
        saved_payloads.append(kwargs.get("json"))
        return _mock_response(200, {"id": "x"})

    client._http = AsyncMock()
    client._http.request = mock_request
    client._http.aclose = AsyncMock()

    async with client:
        await client.save(problem="p1", resolution="r1")
        assert len(saved_payloads) == 0  # not flushed yet
        await client.save(problem="p2", resolution="r2")
        # Should have flushed at batch_size=2
        assert len(saved_payloads) == 2


@pytest.mark.asyncio
async def test_batch_flush_on_close():
    """Remaining items flush on close."""
    client = LoreClient(url="http://test", api_key="k", batch=True, batch_size=100, batch_interval=999)

    saved_payloads: list = []

    async def mock_request(method, path, **kwargs):
        saved_payloads.append(kwargs.get("json"))
        return _mock_response(200, {"id": "x"})

    client._http = AsyncMock()
    client._http.request = mock_request
    client._http.aclose = AsyncMock()

    async with client:
        await client.save(problem="p1", resolution="r1")
        assert len(saved_payloads) == 0

    # After close, should have flushed
    assert len(saved_payloads) == 1


# ── Retry only on 5xx (not 4xx) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_no_retry_on_4xx():
    """4xx errors should NOT be retried — fail immediately."""
    client = LoreClient(url="http://test", api_key="k", timeout=1.0)
    call_count = 0

    async def mock_request(method, path, **kwargs):
        nonlocal call_count
        call_count += 1
        return _mock_response(422, {"error": "validation error"})

    client._http = AsyncMock()
    client._http.request = mock_request
    client._http.aclose = AsyncMock()

    # save() should gracefully return None (HTTPStatusError caught)
    result = await client.save(problem="test", resolution="fix")
    assert result is None
    assert call_count == 1  # No retries
    await client.close()


@pytest.mark.asyncio
async def test_recall_success():
    """Normal recall returns lessons."""
    client = LoreClient(url="http://test", api_key="k", timeout=1.0)

    async def mock_request(method, path, **kwargs):
        return _mock_response(200, {"lessons": [{"id": "1", "problem": "p", "score": 0.9}]})

    client._http = AsyncMock()
    client._http.request = mock_request
    client._http.aclose = AsyncMock()

    result = await client.recall("test query")
    assert len(result) == 1
    assert result[0]["id"] == "1"
    await client.close()
