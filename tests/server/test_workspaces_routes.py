"""Tests for the workspaces routes (Phase 1D, T13).

Each test uses a minimal FakeStore for dependency wiring and patches the
service-module functions with AsyncMock to control return values / side effects.
The `get_store`, `get_auth_context`, and `require_role` dependencies are all
bypassed via dependency_overrides or monkeypatch.
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


def _make_stored_workspace(
    workspace_id="ws_test",
    org_id="org-001",
    name="my-ws",
    slug="my-ws",
    **kwargs,
):
    from lore.persistence.types import StoredWorkspace
    now = _utc_now()
    defaults = dict(
        id=workspace_id,
        org_id=org_id,
        name=name,
        slug=slug,
        settings={},
        created_at=now,
        archived_at=None,
    )
    defaults.update(kwargs)
    return StoredWorkspace(**defaults)


def _make_stored_member(
    member_id="wsm_test",
    workspace_id="ws_test",
    user_id="u-1",
    **kwargs,
):
    from lore.persistence.types import StoredMember
    now = _utc_now()
    defaults = dict(
        id=member_id,
        workspace_id=workspace_id,
        user_id=user_id,
        role="member",
        invited_at=now,
        accepted_at=None,
    )
    defaults.update(kwargs)
    return StoredMember(**defaults)


class FakeStore:
    """Minimal Store stand-in — actual logic is mocked at the service layer."""

    async def close(self):
        pass


# ── fixtures ──────────────────────────────────────────────────────────────────


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
def client(monkeypatch, mock_auth):
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.workspaces import router
    from lore.services import workspaces as workspaces_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    # Bypass require_role — replace the factory so every role check passes.
    monkeypatch.setattr(
        "lore.server.routes.workspaces.require_role",
        lambda role: lambda: mock_auth,
    )

    yield TestClient(app), workspaces_service, mock_auth


# ── happy-path tests ──────────────────────────────────────────────────────────


def test_create_returns_201(client, monkeypatch):
    test_client, workspaces_service, _ = client
    ws = _make_stored_workspace(workspace_id="ws-new", name="new-ws", slug="new-ws")
    monkeypatch.setattr(workspaces_service, "create_workspace", AsyncMock(return_value=ws))
    resp = test_client.post(
        "/v1/workspaces",
        json={"name": "new-ws", "slug": "new-ws"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "ws-new"
    assert body["name"] == "new-ws"
    assert body["slug"] == "new-ws"


def test_list_returns_workspaces(client, monkeypatch):
    test_client, workspaces_service, _ = client
    ws = _make_stored_workspace(name="listed-ws")
    monkeypatch.setattr(workspaces_service, "list_workspaces", AsyncMock(return_value=[ws]))
    resp = test_client.get("/v1/workspaces")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "listed-ws"


def test_get_returns_workspace(client, monkeypatch):
    test_client, workspaces_service, _ = client
    ws = _make_stored_workspace(workspace_id="ws-abc", name="found-ws")
    monkeypatch.setattr(workspaces_service, "get_workspace", AsyncMock(return_value=ws))
    resp = test_client.get("/v1/workspaces/ws-abc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "ws-abc"
    assert body["name"] == "found-ws"


def test_patch_returns_updated(client, monkeypatch):
    test_client, workspaces_service, _ = client
    ws = _make_stored_workspace(workspace_id="ws-upd", name="updated-ws")
    monkeypatch.setattr(workspaces_service, "update_workspace", AsyncMock(return_value=ws))
    resp = test_client.patch(
        "/v1/workspaces/ws-upd",
        json={"name": "updated-ws"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated-ws"


def test_put_replaces_workspace(client, monkeypatch):
    test_client, workspaces_service, _ = client
    ws = _make_stored_workspace(workspace_id="ws-rep", name="replaced-ws")
    monkeypatch.setattr(workspaces_service, "replace_workspace", AsyncMock(return_value=ws))
    resp = test_client.put(
        "/v1/workspaces/ws-rep",
        json={"name": "replaced-ws"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "replaced-ws"


def test_delete_returns_204(client, monkeypatch):
    test_client, workspaces_service, _ = client
    monkeypatch.setattr(
        workspaces_service, "archive_workspace", AsyncMock(return_value=None)
    )
    resp = test_client.delete("/v1/workspaces/ws-del")
    assert resp.status_code == 204


def test_add_member_returns_201(client, monkeypatch):
    test_client, workspaces_service, _ = client
    member = _make_stored_member(member_id="wsm-new", workspace_id="ws-test", user_id="u-2")
    monkeypatch.setattr(workspaces_service, "add_member", AsyncMock(return_value=member))
    resp = test_client.post(
        "/v1/workspaces/ws-test/members",
        json={"user_id": "u-2", "role": "member"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "wsm-new"
    assert body["user_id"] == "u-2"


def test_list_members_returns_array(client, monkeypatch):
    test_client, workspaces_service, _ = client
    member = _make_stored_member(user_id="u-3")
    monkeypatch.setattr(workspaces_service, "list_members", AsyncMock(return_value=[member]))
    resp = test_client.get("/v1/workspaces/ws-test/members")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["user_id"] == "u-3"


def test_update_member_role_changes_role(client, monkeypatch):
    test_client, workspaces_service, _ = client
    member = _make_stored_member(user_id="u-1", role="admin")
    monkeypatch.setattr(workspaces_service, "update_member_role", AsyncMock(return_value=member))
    resp = test_client.patch(
        "/v1/workspaces/ws-test/members/u-1",
        json={"role": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_remove_member_returns_204(client, monkeypatch):
    test_client, workspaces_service, _ = client
    monkeypatch.setattr(workspaces_service, "remove_member", AsyncMock(return_value=None))
    resp = test_client.delete("/v1/workspaces/ws-test/members/u-1")
    assert resp.status_code == 204


# ── error-path tests ──────────────────────────────────────────────────────────


def test_create_409_on_slug_conflict(client, monkeypatch):
    from lore.persistence.exceptions import IntegrityError
    test_client, workspaces_service, _ = client
    monkeypatch.setattr(
        workspaces_service,
        "create_workspace",
        AsyncMock(side_effect=IntegrityError("duplicate slug")),
    )
    resp = test_client.post(
        "/v1/workspaces",
        json={"name": "dupe-ws", "slug": "dupe-ws"},
    )
    assert resp.status_code == 409


def test_get_404_when_missing(client, monkeypatch):
    from lore.persistence.exceptions import StoreNotFoundError
    test_client, workspaces_service, _ = client
    monkeypatch.setattr(
        workspaces_service,
        "get_workspace",
        AsyncMock(side_effect=StoreNotFoundError("workspaces", "ws-missing")),
    )
    resp = test_client.get("/v1/workspaces/ws-missing")
    assert resp.status_code == 404


def test_patch_400_on_empty_patch(client, monkeypatch):
    test_client, workspaces_service, _ = client
    monkeypatch.setattr(
        workspaces_service,
        "update_workspace",
        AsyncMock(side_effect=ValueError("update_workspace called with empty patch")),
    )
    resp = test_client.patch("/v1/workspaces/ws-x", json={})
    assert resp.status_code == 400


def test_delete_404_when_missing(client, monkeypatch):
    from lore.persistence.exceptions import StoreNotFoundError
    test_client, workspaces_service, _ = client
    monkeypatch.setattr(
        workspaces_service,
        "archive_workspace",
        AsyncMock(side_effect=StoreNotFoundError("workspaces", "ws-gone")),
    )
    resp = test_client.delete("/v1/workspaces/ws-gone")
    assert resp.status_code == 404
