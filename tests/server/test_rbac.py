"""Tests for RBAC: role-based access control on Lore endpoints."""

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
from lore.server.auth import ROLE_PERMISSIONS, _map_api_key_role
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
    """Create a mock store configured for auth lookups."""
    store = AsyncMock()
    stored = _row_to_stored_key(key_row) if key_row is not None else None
    store.lookup_api_key_by_hash = AsyncMock(return_value=stored)
    store.touch_api_key_last_used = AsyncMock(return_value=None)
    store.list_api_keys = AsyncMock(return_value=[])
    return store


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
        assert "lessons:search" in ROLE_PERMISSIONS["reader"]
        # reader cannot write
        assert "lessons:write" not in ROLE_PERMISSIONS["reader"]
        # admin can manage keys
        assert "keys:manage" in ROLE_PERMISSIONS["admin"]


# ── Reader role cannot create lessons ──────────────────────────────


@pytest.mark.asyncio
async def test_reader_cannot_create_lesson(client):
    """Reader role gets 403 on POST /v1/lessons."""
    row = _key_row(role="reader", is_root=False)
    auth_store = _make_auth_store(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.post(
            "/v1/lessons",
            json={"problem": "test", "resolution": "test"},
            headers=headers,
        )
    assert resp.status_code == 403
    assert resp.json()["error"] == "insufficient_role"


# ── Writer role can create but cannot manage keys ──────────────────


@pytest.mark.asyncio
async def test_writer_cannot_manage_keys(client):
    """Writer role gets 403 on GET /v1/keys."""
    row = _key_row(role="writer", is_root=False)
    auth_store = _make_auth_store(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.get("/v1/keys", headers=headers)
    assert resp.status_code == 403


# ── Admin role can manage keys ─────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_list_keys(client):
    """Admin role can access key management."""
    row = _key_row(role="admin", is_root=True)
    auth_store = _make_auth_store(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.get("/v1/keys", headers=headers)
    assert resp.status_code == 200


# ── Reader can search ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reader_can_list_lessons(client):
    """Reader role can access GET /v1/lessons."""
    row = _key_row(role="reader", is_root=False)
    auth_store = _make_auth_store(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    # lessons route now goes through get_store (already overridden in the
    # client fixture via app.dependency_overrides[get_store])
    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.lessons.list_lessons", new=AsyncMock(return_value=(0, []))):
        resp = await client.get("/v1/lessons", headers=headers)
    assert resp.status_code == 200


# ── Existing API keys default to admin ─────────────────────────────


@pytest.mark.asyncio
async def test_existing_key_defaults_admin(client):
    """API keys without explicit role column default to admin (backward compat)."""
    row = _key_row(role=None, is_root=True)
    auth_store = _make_auth_store(key_row=row)
    headers = {"Authorization": f"Bearer {RAW_KEY}"}

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.get("/v1/keys", headers=headers)
    assert resp.status_code == 200  # admin can list keys
