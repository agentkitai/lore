"""Tests for API key auth dependency."""

from __future__ import annotations

import hashlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock

from lore.server.app import app
from lore.server.db import get_store


@pytest_asyncio.fixture
async def client():
    from lore.server.auth import _key_cache, _last_used_updates

    _key_cache.clear()
    _last_used_updates.clear()

    mock_store = AsyncMock()
    mock_store.list_api_keys = AsyncMock(return_value=[])
    app.dependency_overrides[get_store] = lambda: mock_store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()
    app.dependency_overrides.pop(get_store, None)


def _make_mock_pool_with_key(key_row=None):
    """Create a mock pool that returns key_row on fetchrow."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=key_row)
    mock_conn.execute = AsyncMock()

    mock_pool = AsyncMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=mock_conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=acm)

    return mock_pool, mock_conn


RAW_KEY = "lore_sk_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()


def _valid_key_row(
    org_id="org-1",
    project=None,
    is_root=True,
    revoked_at=None,
):
    """Return a dict-like mock row for a valid key."""
    row = {
        "id": "key-1",
        "org_id": org_id,
        "project": project,
        "is_root": is_root,
        "revoked_at": revoked_at,
        "key_hash": KEY_HASH,
    }
    return row


# ── Health excluded from auth ──────────────────────────────────────


@pytest.mark.asyncio
async def test_health_no_auth_needed(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


# ── Missing key ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_auth_header(client):
    mock_pool, _ = _make_mock_pool_with_key()
    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/keys")
    assert resp.status_code == 401
    assert resp.json()["error"] == "missing_api_key"


@pytest.mark.asyncio
async def test_missing_bearer_prefix(client):
    mock_pool, _ = _make_mock_pool_with_key()
    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/keys", headers={"Authorization": RAW_KEY}
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "missing_api_key"


# ── Invalid key ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_key(client):
    mock_pool, _ = _make_mock_pool_with_key(key_row=None)
    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/keys",
            headers={"Authorization": f"Bearer {RAW_KEY}"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_api_key"


# ── Revoked key ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoked_key(client):
    from datetime import datetime, timezone

    row = _valid_key_row(revoked_at=datetime.now(timezone.utc))
    mock_pool, _ = _make_mock_pool_with_key(key_row=row)
    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/keys",
            headers={"Authorization": f"Bearer {RAW_KEY}"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "key_revoked"


# ── Valid key sets auth context ────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_key_sets_context(client):
    row = _valid_key_row(org_id="org-42", project="backend", is_root=False)
    mock_pool, _ = _make_mock_pool_with_key(key_row=row)
    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        # Use a test endpoint that echoes auth context
        resp = await client.get(
            "/v1/keys",
            headers={"Authorization": f"Bearer {RAW_KEY}"},
        )
    # Non-root key gets 403 from key management (proves auth worked)
    assert resp.status_code == 403


# ── Cache behavior ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_avoids_second_db_lookup(client):
    row = _valid_key_row()
    mock_pool, mock_conn = _make_mock_pool_with_key(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        await client.get("/v1/keys", headers=headers)
        await client.get("/v1/keys", headers=headers)

    # Auth fetchrow called once; keys router also calls fetch (list)
    assert mock_conn.fetchrow.call_count == 1


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(client):
    row = _valid_key_row()
    mock_pool, mock_conn = _make_mock_pool_with_key(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    # Directly manipulate the cache to simulate expiry
    from lore.server.auth import _key_cache

    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        # First request — populates cache
        await client.get("/v1/keys", headers=headers)
        assert mock_conn.fetchrow.call_count == 1

        # Expire the cache entry by backdating its timestamp
        for k in list(_key_cache):
            row_data, _ = _key_cache[k]
            _key_cache[k] = (row_data, time.monotonic() - 120)

        # Second request — cache expired, hits DB again
        await client.get("/v1/keys", headers=headers)
        assert mock_conn.fetchrow.call_count == 2


# ── last_used_at debounced update ──────────────────────────────────


@pytest.mark.asyncio
async def test_last_used_at_fires_update(client):
    from lore.server.auth import _last_used_updates

    row = _valid_key_row()
    mock_pool, mock_conn = _make_mock_pool_with_key(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        await client.get("/v1/keys", headers=headers)

    # Should have scheduled an update
    assert "key-1" in _last_used_updates


# ── Key prefix validation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_key_without_prefix_rejected(client):
    mock_pool, _ = _make_mock_pool_with_key()
    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/keys",
            headers={"Authorization": "Bearer not_a_valid_key"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_api_key"
