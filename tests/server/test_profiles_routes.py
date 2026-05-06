"""Tests for the profiles routes (Phase 1C, T11).

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


def _make_stored_profile(
    profile_id="prof_test",
    org_id="org-001",
    name="test",
    **kwargs,
):
    from lore.persistence.types import StoredProfile
    now = _utc_now()
    defaults = dict(
        id=profile_id,
        org_id=org_id,
        name=name,
        semantic_weight=1.0,
        graph_weight=1.0,
        recency_bias=30.0,
        tier_filters=None,
        min_score=0.3,
        max_results=10,
        is_preset=False,
        k=None,
        threshold=None,
        rerank=False,
        include_graph=True,
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return StoredProfile(**defaults)


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
    from lore.server.routes.profiles import router
    from lore.server.db import get_store
    from lore.server.auth import get_auth_context
    from lore.services import profiles as profiles_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    # Bypass require_role — replace the factory so every role check passes.
    monkeypatch.setattr(
        "lore.server.routes.profiles.require_role",
        lambda role: lambda: mock_auth,
    )

    yield TestClient(app), profiles_service, mock_auth


# ── happy-path tests ──────────────────────────────────────────────────────────


def test_list_returns_profiles(client, monkeypatch):
    test_client, profiles_service, _ = client
    sp = _make_stored_profile(name="my-profile")
    monkeypatch.setattr(profiles_service, "list_profiles", AsyncMock(return_value=[sp]))
    resp = test_client.get("/v1/profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "my-profile"


def test_get_returns_profile(client, monkeypatch):
    test_client, profiles_service, _ = client
    sp = _make_stored_profile(profile_id="prof-abc", name="custom")
    monkeypatch.setattr(profiles_service, "get_profile", AsyncMock(return_value=sp))
    resp = test_client.get("/v1/profiles/prof-abc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "prof-abc"
    assert body["name"] == "custom"


def test_create_returns_201(client, monkeypatch):
    test_client, profiles_service, _ = client
    sp = _make_stored_profile(profile_id="prof-new", name="new-profile")
    monkeypatch.setattr(profiles_service, "create_profile", AsyncMock(return_value=sp))
    resp = test_client.post(
        "/v1/profiles",
        json={"name": "new-profile", "semantic_weight": 1.0},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "new-profile"
    assert body["id"] == "prof-new"


def test_update_by_id_returns_profile(client, monkeypatch):
    test_client, profiles_service, _ = client
    updated = _make_stored_profile(profile_id="prof-upd", name="updated-name")
    monkeypatch.setattr(
        profiles_service, "update_profile_by_id", AsyncMock(return_value=updated)
    )
    resp = test_client.put(
        "/v1/profiles/prof-upd",
        json={"name": "updated-name"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated-name"


def test_delete_by_id_returns_204(client, monkeypatch):
    test_client, profiles_service, _ = client
    monkeypatch.setattr(
        profiles_service, "delete_profile_by_id", AsyncMock(return_value=None)
    )
    resp = test_client.delete("/v1/profiles/prof-del")
    assert resp.status_code == 204


def test_get_defaults_returns_three_keys(client):
    test_client, _, _ = client
    resp = test_client.get("/v1/profiles/defaults")
    assert resp.status_code == 200
    body = resp.json()
    assert "precise" in body
    assert "broad" in body
    assert "balanced" in body


def test_update_by_name_routes_to_service(client, monkeypatch):
    test_client, profiles_service, _ = client
    sp = _make_stored_profile(profile_id="prof-n1", name="my-profile")
    monkeypatch.setattr(
        profiles_service, "update_profile_by_name", AsyncMock(return_value=sp)
    )
    resp = test_client.put(
        "/v1/profiles/name/my-profile",
        json={"semantic_weight": 1.5},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "my-profile"


def test_delete_by_name_returns_204(client, monkeypatch):
    test_client, profiles_service, _ = client
    monkeypatch.setattr(
        profiles_service, "delete_profile_by_name", AsyncMock(return_value=None)
    )
    resp = test_client.delete("/v1/profiles/name/my-profile")
    assert resp.status_code == 204


# ── error-path tests ──────────────────────────────────────────────────────────


def test_get_404_when_missing(client, monkeypatch):
    from lore.persistence.exceptions import StoreNotFoundError
    test_client, profiles_service, _ = client
    monkeypatch.setattr(
        profiles_service,
        "get_profile",
        AsyncMock(side_effect=StoreNotFoundError("retrieval_profiles", "prof-missing")),
    )
    resp = test_client.get("/v1/profiles/prof-missing")
    assert resp.status_code == 404


def test_create_409_on_uniqueness(client, monkeypatch):
    from lore.persistence.exceptions import IntegrityError
    test_client, profiles_service, _ = client
    monkeypatch.setattr(
        profiles_service,
        "create_profile",
        AsyncMock(side_effect=IntegrityError("duplicate")),
    )
    resp = test_client.post("/v1/profiles", json={"name": "dupe"})
    assert resp.status_code == 409


def test_update_by_id_403_on_preset(client, monkeypatch):
    from lore.persistence.exceptions import ProfileImmutableError
    test_client, profiles_service, _ = client
    monkeypatch.setattr(
        profiles_service,
        "update_profile_by_id",
        AsyncMock(side_effect=ProfileImmutableError("Cannot modify preset profile")),
    )
    resp = test_client.put("/v1/profiles/preset-id", json={"name": "attempt"})
    assert resp.status_code == 403


def test_update_by_id_404_on_missing(client, monkeypatch):
    from lore.persistence.exceptions import StoreNotFoundError
    test_client, profiles_service, _ = client
    monkeypatch.setattr(
        profiles_service,
        "update_profile_by_id",
        AsyncMock(
            side_effect=StoreNotFoundError("retrieval_profiles", "prof-gone")
        ),
    )
    resp = test_client.put("/v1/profiles/prof-gone", json={"name": "x"})
    assert resp.status_code == 404


def test_update_by_id_400_on_empty_patch(client, monkeypatch):
    test_client, profiles_service, _ = client
    monkeypatch.setattr(
        profiles_service,
        "update_profile_by_id",
        AsyncMock(side_effect=ValueError("No fields to update")),
    )
    resp = test_client.put("/v1/profiles/prof-x", json={})
    assert resp.status_code == 400


def test_delete_by_id_403_on_preset(client, monkeypatch):
    from lore.persistence.exceptions import ProfileImmutableError
    test_client, profiles_service, _ = client
    monkeypatch.setattr(
        profiles_service,
        "delete_profile_by_id",
        AsyncMock(side_effect=ProfileImmutableError("Cannot delete preset profile")),
    )
    resp = test_client.delete("/v1/profiles/preset-id")
    assert resp.status_code == 403
