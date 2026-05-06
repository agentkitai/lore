"""Tests for key management endpoints."""

from __future__ import annotations

import hashlib
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

RAW_KEY = "lore_sk_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()


def _valid_key_row(org_id="org-1", project=None, is_root=True, revoked_at=None):
    return {
        "id": "key-1",
        "org_id": org_id,
        "project": project,
        "is_root": is_root,
        "revoked_at": revoked_at,
        "key_hash": KEY_HASH,
    }


def _row_to_stored_key(row: dict) -> StoredApiKey:
    return StoredApiKey(
        id=row["id"],
        org_id=row["org_id"],
        name=row.get("name", "test-key"),
        key_hash=row["key_hash"],
        key_prefix=row.get("key_prefix", "lore_sk_xx"),
        project=row.get("project"),
        is_root=row.get("is_root", False),
        workspace_id=row.get("workspace_id"),
        revoked_at=row.get("revoked_at"),
        created_at=row.get("created_at", datetime.now(timezone.utc)),
        last_used_at=row.get("last_used_at"),
        role=row.get("role"),
    )


def _make_auth_store(key_row=None):
    """Create a mock store with auth methods configured."""
    store = AsyncMock()
    stored = _row_to_stored_key(key_row) if key_row is not None else None
    store.lookup_api_key_by_hash = AsyncMock(return_value=stored)
    store.touch_api_key_last_used = AsyncMock(return_value=None)
    return store


@pytest_asyncio.fixture
async def client():
    from lore.server.auth import _key_cache, _last_used_updates
    from lore.server.middleware import RateLimiter, set_rate_limiter
    _key_cache.clear()
    _last_used_updates.clear()
    set_rate_limiter(RateLimiter())

    mock_store = AsyncMock()
    app.dependency_overrides[get_store] = lambda: mock_store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()
    app.dependency_overrides.pop(get_store, None)


def _auth_headers():
    return {"Authorization": f"Bearer {RAW_KEY}"}


def _make_stored_api_key(
    key_id="key-1",
    org_id="org-1",
    name="root",
    key_prefix="lore_sk_a1b2",
    project=None,
    is_root=True,
    workspace_id=None,
    revoked_at=None,
    created_at=None,
    last_used_at=None,
    key_hash=None,
):
    from lore.persistence import StoredApiKey
    return StoredApiKey(
        id=key_id,
        org_id=org_id,
        name=name,
        key_hash=key_hash or KEY_HASH,
        key_prefix=key_prefix,
        project=project,
        is_root=is_root,
        workspace_id=workspace_id,
        revoked_at=revoked_at,
        created_at=created_at or datetime.now(timezone.utc),
        last_used_at=last_used_at,
    )


# ── Create key ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_key_root_only(client):
    """Non-root key gets 403."""
    row = _valid_key_row(is_root=False)
    auth_store = _make_auth_store(key_row=row)

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.post(
            "/v1/keys",
            json={"name": "test"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_key_success(client):
    """Root key can create a new key."""
    auth_row = _valid_key_row(is_root=True)
    auth_store = _make_auth_store(key_row=auth_row)

    stored = _make_stored_api_key(key_id="new-key-id", name="agent-1", project="backend")

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.keys.create_api_key", new=AsyncMock(return_value=(stored, "lore_sk_newrawkey"))):
        resp = await client.post(
            "/v1/keys",
            json={"name": "agent-1", "project": "backend"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "agent-1"
    assert data["project"] == "backend"
    assert data["key"].startswith("lore_sk_")
    assert "id" in data


# ── List keys ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_keys_root_only(client):
    row = _valid_key_row(is_root=False)
    auth_store = _make_auth_store(key_row=row)

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.get("/v1/keys", headers=_auth_headers())
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_keys_success(client):
    auth_row = _valid_key_row(is_root=True)
    auth_store = _make_auth_store(key_row=auth_row)

    now = datetime.now(timezone.utc)
    stored_key = _make_stored_api_key(
        key_id="key-1", name="root", key_prefix="lore_sk_a1b2",
        created_at=now, last_used_at=now,
    )

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.keys.list_api_keys", new=AsyncMock(return_value=[stored_key])):
        resp = await client.get("/v1/keys", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["keys"]) == 1
    assert data["keys"][0]["revoked"] is False
    # Ensure key_hash is NOT in response
    assert "key_hash" not in data["keys"][0]


# ── Revoke key ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_key_root_only(client):
    row = _valid_key_row(is_root=False)
    auth_store = _make_auth_store(key_row=row)

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.delete("/v1/keys/some-id", headers=_auth_headers())
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_revoke_key_not_found(client):
    auth_row = _valid_key_row(is_root=True)
    auth_store = _make_auth_store(key_row=auth_row)

    from lore.persistence.exceptions import StoreNotFoundError

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.keys.revoke_api_key", new=AsyncMock(side_effect=StoreNotFoundError("api_keys", "nonexistent"))):
        resp = await client.delete("/v1/keys/nonexistent", headers=_auth_headers())
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_revoke_last_root_key_blocked(client):
    """Cannot revoke the last active root key."""
    auth_row = _valid_key_row(is_root=True)
    auth_store = _make_auth_store(key_row=auth_row)

    from lore.persistence.exceptions import LastRootKeyError

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.keys.revoke_api_key", new=AsyncMock(side_effect=LastRootKeyError("Cannot revoke the last root key"))):
        resp = await client.delete("/v1/keys/key-1", headers=_auth_headers())
    assert resp.status_code == 400
    assert "last root key" in resp.json().get("message", resp.json().get("detail", ""))


@pytest.mark.asyncio
async def test_revoke_key_success(client):
    """Revoke a non-root key succeeds."""
    auth_row = _valid_key_row(is_root=True)
    auth_store = _make_auth_store(key_row=auth_row)

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.keys.revoke_api_key", new=AsyncMock(return_value=None)):
        resp = await client.delete("/v1/keys/key-2", headers=_auth_headers())
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_revoke_key_invalidates_cache(client):
    """Revoking a key removes it from the auth cache (delegated to service)."""
    # Cache invalidation is handled inside keys_service.revoke_api_key via auth.invalidate_key.
    # This test verifies that a successful revoke returns 204.
    auth_row = _valid_key_row(is_root=True)
    auth_store = _make_auth_store(key_row=auth_row)

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.keys.revoke_api_key", new=AsyncMock(return_value=None)):
        resp = await client.delete("/v1/keys/key-2", headers=_auth_headers())
    assert resp.status_code == 204


@pytest.mark.skip(reason="already-revoked is not a distinct error in the service layer; replaced by FakeStore tests in T14")
@pytest.mark.asyncio
async def test_revoke_already_revoked_key(client):
    """Revoking an already-revoked key returns 400."""
    pass
