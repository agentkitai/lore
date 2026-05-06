"""Tests for the keys routes (Phase 1D, T14).

Each test uses a minimal FakeStore for dependency wiring and patches the
service-module functions with AsyncMock to control return values / side effects.
The `get_store` and `get_auth_context` dependencies are bypassed via
dependency_overrides.  The root check is performed by `_require_root` inside
each handler, so 403 tests simply supply a non-root AuthContext — no service
mock is needed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


# ── helpers ───────────────────────────────────────────────────────────────────


def _utc_now():
    return datetime.now(timezone.utc)


def _make_stored_api_key(key_id="key_test", org_id="org-001", **kwargs):
    from lore.persistence.types import StoredApiKey
    now = _utc_now()
    defaults = dict(
        id=key_id,
        org_id=org_id,
        name="test",
        key_hash="hash",
        key_prefix="lore_sk_xxxx",
        project=None,
        is_root=False,
        workspace_id=None,
        revoked_at=None,
        created_at=now,
        last_used_at=None,
    )
    defaults.update(kwargs)
    return StoredApiKey(**defaults)


class FakeStore:
    """Minimal Store stand-in — actual logic is mocked at the service layer."""

    async def close(self):
        pass


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_root_auth():
    from lore.server.auth import AuthContext
    return AuthContext(
        org_id="org-001",
        project=None,
        is_root=True,
        key_id="key-001",
        role="admin",
    )


def _make_non_root_auth():
    from lore.server.auth import AuthContext
    return AuthContext(
        org_id="org-001",
        project=None,
        is_root=False,
        key_id="key-002",
        role="writer",
    )


@pytest.fixture
def client_root():
    """TestClient wired with root auth."""
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.keys import router
    from lore.services import keys as keys_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = _make_root_auth

    yield TestClient(app), keys_service


@pytest.fixture
def client_non_root():
    """TestClient wired with non-root auth."""
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.keys import router
    from lore.services import keys as keys_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = _make_non_root_auth

    yield TestClient(app), keys_service


# ── happy-path tests ──────────────────────────────────────────────────────────


def test_create_returns_201(client_root, monkeypatch):
    test_client, keys_service = client_root
    stored = _make_stored_api_key()
    raw_key = "lore_sk_test_raw_key_value_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    monkeypatch.setattr(keys_service, "create_api_key", AsyncMock(return_value=(stored, raw_key)))
    resp = test_client.post("/v1/keys", json={"name": "test"})
    assert resp.status_code == 201
    assert resp.json()["key"] == raw_key


def test_list_returns_keys(client_root, monkeypatch):
    test_client, keys_service = client_root
    stored = _make_stored_api_key(key_id="key-listed", name="listed-key")
    monkeypatch.setattr(keys_service, "list_api_keys", AsyncMock(return_value=[stored]))
    resp = test_client.get("/v1/keys")
    assert resp.status_code == 200
    data = resp.json()
    assert "keys" in data
    assert isinstance(data["keys"], list)
    assert len(data["keys"]) == 1
    assert data["keys"][0]["id"] == "key-listed"


def test_delete_returns_204(client_root, monkeypatch):
    test_client, keys_service = client_root
    monkeypatch.setattr(keys_service, "revoke_api_key", AsyncMock(return_value=None))
    resp = test_client.delete("/v1/keys/key-to-revoke")
    assert resp.status_code == 204


# ── error-path tests ──────────────────────────────────────────────────────────


def test_create_403_when_not_root(client_non_root):
    test_client, _ = client_non_root
    resp = test_client.post("/v1/keys", json={"name": "test"})
    assert resp.status_code == 403


def test_list_403_when_not_root(client_non_root):
    test_client, _ = client_non_root
    resp = test_client.get("/v1/keys")
    assert resp.status_code == 403


def test_delete_404_when_missing(client_root, monkeypatch):
    from lore.persistence.exceptions import StoreNotFoundError
    test_client, keys_service = client_root
    monkeypatch.setattr(
        keys_service,
        "revoke_api_key",
        AsyncMock(side_effect=StoreNotFoundError("keys", "key-missing")),
    )
    resp = test_client.delete("/v1/keys/key-missing")
    assert resp.status_code == 404


def test_delete_400_last_root_key(client_root, monkeypatch):
    from lore.persistence.exceptions import LastRootKeyError
    test_client, keys_service = client_root
    monkeypatch.setattr(
        keys_service,
        "revoke_api_key",
        AsyncMock(side_effect=LastRootKeyError("cannot revoke last root key")),
    )
    resp = test_client.delete("/v1/keys/key-last-root")
    assert resp.status_code == 400


def test_delete_403_when_not_root(client_non_root):
    test_client, _ = client_non_root
    resp = test_client.delete("/v1/keys/key-any")
    assert resp.status_code == 403
