"""Tests for RBAC: role-based access control on Lore endpoints."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from lore.server.app import app
from lore.server.auth import ROLE_PERMISSIONS, _map_api_key_role


@pytest_asyncio.fixture
async def client():
    from lore.server.auth import _key_cache, _last_used_updates
    _key_cache.clear()
    _last_used_updates.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()


def _make_mock_pool_with_key(key_row=None, fetch_rows=None):
    """Create a mock pool."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=key_row)
    mock_conn.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_conn.fetchval = AsyncMock(return_value=0)
    mock_conn.execute = AsyncMock()

    mock_pool = AsyncMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=mock_conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=acm)
    return mock_pool, mock_conn


RAW_KEY = "lore_sk_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()


def _key_row(role=None, is_root=True):
    return {
        "id": "key-1",
        "org_id": "org-1",
        "project": None,
        "is_root": is_root,
        "revoked_at": None,
        "key_hash": KEY_HASH,
        "role": role,
    }


# ── Role mapping tests ────────────────────────────────────────────


class TestRoleMapping:
    def test_root_key_defaults_to_admin(self):
        assert _map_api_key_role(True) == "admin"

    def test_non_root_key_defaults_to_writer(self):
        assert _map_api_key_role(False) == "writer"

    def test_explicit_role_overrides(self):
        assert _map_api_key_role(False, "reader") == "reader"
        assert _map_api_key_role(True, "reader") == "reader"

    def test_role_permissions_defined(self):
        assert "reader" in ROLE_PERMISSIONS
        assert "writer" in ROLE_PERMISSIONS
        assert "admin" in ROLE_PERMISSIONS
        # reader can search
        assert "memories:search" in ROLE_PERMISSIONS["reader"]
        # reader cannot write
        assert "memories:write" not in ROLE_PERMISSIONS["reader"]
        # admin can manage keys
        assert "keys:manage" in ROLE_PERMISSIONS["admin"]


# ── Reader role cannot create memories ─────────────────────────────


@pytest.mark.asyncio
async def test_reader_cannot_create_memory(client):
    """Reader role gets 403 on POST /v1/memories."""
    row = _key_row(role="reader", is_root=False)
    mock_pool, _ = _make_mock_pool_with_key(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/memories",
            json={"content": "test"},
            headers=headers,
        )
    assert resp.status_code == 403
    assert resp.json()["error"] == "insufficient_role"


# ── Writer role can create but cannot manage keys ──────────────────


@pytest.mark.asyncio
async def test_writer_cannot_manage_keys(client):
    """Writer role gets 403 on GET /v1/keys."""
    row = _key_row(role="writer", is_root=False)
    mock_pool, _ = _make_mock_pool_with_key(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/keys", headers=headers)
    assert resp.status_code == 403


# ── Admin role can manage keys ─────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_list_keys(client):
    """Admin role can access key management."""
    row = _key_row(role="admin", is_root=True)
    mock_pool, _ = _make_mock_pool_with_key(key_row=row, fetch_rows=[])
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_pool", return_value=mock_pool), \
         patch("lore.server.routes.keys.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/keys", headers=headers)
    assert resp.status_code == 200


# ── Reader can search ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reader_can_list_memories(client):
    """Reader role can access GET /v1/memories."""
    row = _key_row(role="reader", is_root=False)
    mock_pool, mock_conn = _make_mock_pool_with_key(key_row=row)
    mock_conn.fetchval = AsyncMock(return_value=0)
    mock_conn.fetch = AsyncMock(return_value=[])
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_pool", return_value=mock_pool), \
         patch("lore.server.routes.memories.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/memories", headers=headers)
    assert resp.status_code == 200


# ── Existing API keys default to admin ─────────────────────────────


@pytest.mark.asyncio
async def test_existing_key_defaults_admin(client):
    """API keys without explicit role column default to admin (backward compat)."""
    row = _key_row(role=None, is_root=True)
    mock_pool, _ = _make_mock_pool_with_key(key_row=row, fetch_rows=[])
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_pool", return_value=mock_pool), \
         patch("lore.server.routes.keys.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/keys", headers=headers)
    assert resp.status_code == 200  # admin can list keys
