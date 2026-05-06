"""Tests for API key auth dependency."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from lore.persistence.types import StoredApiKey
from lore.server.app import app
from lore.server.db import get_store


@pytest_asyncio.fixture
async def client():
    from lore.server.auth import _key_cache, _last_used_updates

    _key_cache.clear()
    _last_used_updates.clear()

    mock_store = AsyncMock()
    mock_store.list_api_keys = AsyncMock(return_value=[])
    mock_store.lookup_api_key_by_hash = AsyncMock(return_value=None)
    mock_store.touch_api_key_last_used = AsyncMock(return_value=None)
    app.dependency_overrides[get_store] = lambda: mock_store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Attach the store so tests can configure lookup behavior.
        c._mock_store = mock_store  # type: ignore[attr-defined]
        yield c
    _key_cache.clear()
    _last_used_updates.clear()
    app.dependency_overrides.pop(get_store, None)


RAW_KEY = "lore_sk_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()


def _valid_key(
    org_id="org-1",
    project=None,
    is_root=True,
    revoked_at=None,
    role=None,
):
    """Return a StoredApiKey instance for a valid key."""
    return StoredApiKey(
        id="key-1",
        org_id=org_id,
        name="test-key",
        key_hash=KEY_HASH,
        key_prefix="lore_sk_a1",
        project=project,
        is_root=is_root,
        workspace_id=None,
        revoked_at=revoked_at,
        created_at=datetime.now(timezone.utc),
        last_used_at=None,
        role=role,
    )


def _patch_store_lookup(client, return_value):
    """Configure the fixture's mock_store.lookup_api_key_by_hash."""
    client._mock_store.lookup_api_key_by_hash.return_value = return_value


# Patch get_store inside auth.py so the direct (non-Depends) call resolves
# to the same mock the fixture installs via dependency_overrides.
def _store_patch(client):
    return patch("lore.server.auth.get_store", return_value=client._mock_store)


# ── Health excluded from auth ──────────────────────────────────────


@pytest.mark.asyncio
async def test_health_no_auth_needed(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


# ── Missing key ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_auth_header(client):
    with _store_patch(client):
        resp = await client.get("/v1/keys")
    assert resp.status_code == 401
    assert resp.json()["error"] == "missing_api_key"


@pytest.mark.asyncio
async def test_missing_bearer_prefix(client):
    with _store_patch(client):
        resp = await client.get("/v1/keys", headers={"Authorization": RAW_KEY})
    assert resp.status_code == 401
    assert resp.json()["error"] == "missing_api_key"


# ── Invalid key ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_key(client):
    _patch_store_lookup(client, None)
    with _store_patch(client):
        resp = await client.get(
            "/v1/keys",
            headers={"Authorization": f"Bearer {RAW_KEY}"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_api_key"


# ── Revoked key ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoked_key(client):
    _patch_store_lookup(client, _valid_key(revoked_at=datetime.now(timezone.utc)))
    with _store_patch(client):
        resp = await client.get(
            "/v1/keys",
            headers={"Authorization": f"Bearer {RAW_KEY}"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "key_revoked"


# ── Valid key sets auth context ────────────────────────────────────


@pytest.mark.asyncio
async def test_valid_key_sets_context(client):
    _patch_store_lookup(client, _valid_key(org_id="org-42", project="backend", is_root=False))
    with _store_patch(client):
        resp = await client.get(
            "/v1/keys",
            headers={"Authorization": f"Bearer {RAW_KEY}"},
        )
    # Non-root key gets 403 from key management (proves auth worked)
    assert resp.status_code == 403


# ── Cache behavior ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_avoids_second_db_lookup(client):
    _patch_store_lookup(client, _valid_key())
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with _store_patch(client):
        await client.get("/v1/keys", headers=headers)
        await client.get("/v1/keys", headers=headers)

    assert client._mock_store.lookup_api_key_by_hash.call_count == 1


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(client):
    _patch_store_lookup(client, _valid_key())
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    from lore.server.auth import _key_cache

    with _store_patch(client):
        await client.get("/v1/keys", headers=headers)
        assert client._mock_store.lookup_api_key_by_hash.call_count == 1

        # Backdate cache entry to simulate TTL expiry
        for k in list(_key_cache):
            row_data, _ = _key_cache[k]
            _key_cache[k] = (row_data, time.monotonic() - 120)

        await client.get("/v1/keys", headers=headers)
        assert client._mock_store.lookup_api_key_by_hash.call_count == 2


# ── last_used_at debounced update ──────────────────────────────────


@pytest.mark.asyncio
async def test_last_used_at_fires_update(client):
    from lore.server.auth import _last_used_updates

    _patch_store_lookup(client, _valid_key())
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with _store_patch(client):
        await client.get("/v1/keys", headers=headers)

    assert "key-1" in _last_used_updates


# ── Key prefix validation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_key_without_prefix_rejected(client):
    with _store_patch(client):
        resp = await client.get(
            "/v1/keys",
            headers={"Authorization": "Bearer not_a_valid_key"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_api_key"
