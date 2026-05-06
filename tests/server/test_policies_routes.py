"""Tests for the policies routes (Phase 1J, T6).

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


def _make_stored_policy(
    policy_id="pol-1",
    org_id="org-001",
    name="test-policy",
    retention_window=None,
    snapshot_schedule=None,
    encryption_required=False,
    max_snapshots=50,
    is_active=True,
    **kwargs,
):
    from lore.persistence.types import StoredRetentionPolicy

    now = _utc_now()
    return StoredRetentionPolicy(
        id=policy_id,
        org_id=org_id,
        name=name,
        retention_window=retention_window or {"working": 3600, "short": 604800, "long": None},
        snapshot_schedule=snapshot_schedule,
        encryption_required=encryption_required,
        max_snapshots=max_snapshots,
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


def _make_stored_drill(
    drill_id="drill-1",
    org_id="org-001",
    snapshot_name="snap-test",
    status="success",
    recovery_time_ms=100,
    memories_restored=5,
    error=None,
    **kwargs,
):
    from lore.persistence.types import StoredDrillResult

    now = _utc_now()
    return StoredDrillResult(
        id=drill_id,
        org_id=org_id,
        snapshot_id=None,
        snapshot_name=snapshot_name,
        started_at=now,
        completed_at=now,
        recovery_time_ms=recovery_time_ms,
        memories_restored=memories_restored,
        status=status,
        error=error,
        created_at=now,
    )


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
    from lore.server.routes.policies import router
    from lore.services import policies as policies_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    monkeypatch.setattr(
        "lore.server.routes.policies.require_role",
        lambda *roles: lambda: mock_auth,
    )

    yield TestClient(app), policies_service, mock_auth


# ── list ──────────────────────────────────────────────────────────────────────


def test_list_returns_policies(client, monkeypatch):
    """GET /v1/policies returns a list of policies."""
    test_client, svc, _ = client
    p1 = _make_stored_policy(policy_id="pol-1", name="alpha")
    p2 = _make_stored_policy(policy_id="pol-2", name="beta")
    monkeypatch.setattr(svc, "list_policies", AsyncMock(return_value=[p1, p2]))

    resp = test_client.get("/v1/policies")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["id"] == "pol-1"
    assert body[0]["name"] == "alpha"
    assert body[1]["id"] == "pol-2"


# ── create ────────────────────────────────────────────────────────────────────


def test_post_returns_201_with_id(client, monkeypatch):
    """POST /v1/policies creates a policy and returns 201."""
    test_client, svc, _ = client
    p = _make_stored_policy(policy_id="pol-new", name="new-policy")
    monkeypatch.setattr(svc, "create_policy", AsyncMock(return_value=p))

    resp = test_client.post("/v1/policies", json={"name": "new-policy"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "pol-new"
    assert body["name"] == "new-policy"

    svc.create_policy.assert_called_once()
    kwargs = svc.create_policy.call_args.kwargs
    assert kwargs["name"] == "new-policy"
    assert kwargs["org_id"] == "org-001"


def test_post_409_on_duplicate(client, monkeypatch):
    """POST /v1/policies returns 409 when name already exists."""
    from lore.persistence.exceptions import IntegrityError

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "create_policy", AsyncMock(side_effect=IntegrityError("dup"))
    )

    resp = test_client.post("/v1/policies", json={"name": "dup-policy"})

    assert resp.status_code == 409


# ── get ───────────────────────────────────────────────────────────────────────


def test_get_returns_policy(client, monkeypatch):
    """GET /v1/policies/{id} returns policy with all fields."""
    test_client, svc, _ = client
    p = _make_stored_policy(policy_id="pol-1", name="my-policy", max_snapshots=30)
    monkeypatch.setattr(svc, "get_policy", AsyncMock(return_value=p))

    resp = test_client.get("/v1/policies/pol-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "pol-1"
    assert body["name"] == "my-policy"
    assert body["max_snapshots"] == 30


def test_get_404_on_missing(client, monkeypatch):
    """GET /v1/policies/{id} returns 404 when policy not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "get_policy", AsyncMock(side_effect=StoreNotFoundError("retention_policies", "pol-gone"))
    )

    resp = test_client.get("/v1/policies/pol-gone")

    assert resp.status_code == 404


# ── update ────────────────────────────────────────────────────────────────────


def test_put_returns_updated_policy(client, monkeypatch):
    """PUT /v1/policies/{id} returns updated policy."""
    test_client, svc, _ = client
    p = _make_stored_policy(policy_id="pol-1", name="updated-name", max_snapshots=99)
    monkeypatch.setattr(svc, "update_policy", AsyncMock(return_value=p))

    resp = test_client.put(
        "/v1/policies/pol-1", json={"name": "updated-name", "max_snapshots": 99}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "updated-name"
    assert body["max_snapshots"] == 99


def test_put_404_on_missing(client, monkeypatch):
    """PUT /v1/policies/{id} returns 404 when policy not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "update_policy",
        AsyncMock(side_effect=StoreNotFoundError("retention_policies", "pol-gone")),
    )

    resp = test_client.put("/v1/policies/pol-gone", json={"name": "x"})

    assert resp.status_code == 404


def test_put_400_on_empty_patch(client, monkeypatch):
    """PUT /v1/policies/{id} returns 400 when no fields provided."""
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "update_policy",
        AsyncMock(side_effect=ValueError("No fields to update")),
    )

    resp = test_client.put("/v1/policies/pol-1", json={})

    assert resp.status_code == 400
    assert "No fields to update" in resp.json()["detail"]


# ── delete ────────────────────────────────────────────────────────────────────


def test_delete_returns_204(client, monkeypatch):
    """DELETE /v1/policies/{id} returns 204 on success."""
    test_client, svc, _ = client
    monkeypatch.setattr(svc, "delete_policy", AsyncMock(return_value=None))

    resp = test_client.delete("/v1/policies/pol-1")

    assert resp.status_code == 204


def test_delete_404_on_missing(client, monkeypatch):
    """DELETE /v1/policies/{id} returns 404 when policy not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "delete_policy",
        AsyncMock(side_effect=StoreNotFoundError("retention_policies", "pol-gone")),
    )

    resp = test_client.delete("/v1/policies/pol-gone")

    assert resp.status_code == 404


# ── run_drill ──────────────────────────────────────────────────────────────────


def test_post_drill_returns_201(client, monkeypatch):
    """POST /v1/policies/{id}/drill returns 201 with drill result."""
    test_client, svc, _ = client
    d = _make_stored_drill(drill_id="drill-1", status="success", memories_restored=42)
    monkeypatch.setattr(svc, "run_drill", AsyncMock(return_value=d))

    resp = test_client.post("/v1/policies/pol-1/drill")

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "drill-1"
    assert body["status"] == "success"
    assert body["memories_restored"] == 42


def test_post_drill_404_on_missing_policy(client, monkeypatch):
    """POST /v1/policies/{id}/drill returns 404 when policy not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "run_drill",
        AsyncMock(side_effect=StoreNotFoundError("retention_policies", "pol-gone")),
    )

    resp = test_client.post("/v1/policies/pol-gone/drill")

    assert resp.status_code == 404


# ── list_drills ────────────────────────────────────────────────────────────────


def test_get_drills_returns_list(client, monkeypatch):
    """GET /v1/policies/{id}/drills returns drill results list."""
    test_client, svc, _ = client
    d = _make_stored_drill(drill_id="drill-1")
    monkeypatch.setattr(svc, "list_drills", AsyncMock(return_value=[d]))

    resp = test_client.get("/v1/policies/pol-1/drills")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "drill-1"


def test_get_drills_404_on_missing_policy(client, monkeypatch):
    """GET /v1/policies/{id}/drills returns 404 when policy not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "list_drills",
        AsyncMock(side_effect=StoreNotFoundError("retention_policies", "pol-gone")),
    )

    resp = test_client.get("/v1/policies/pol-gone/drills")

    assert resp.status_code == 404


# ── compliance ─────────────────────────────────────────────────────────────────


def test_get_compliance_returns_summary(client, monkeypatch):
    """GET /v1/policies/compliance returns compliance summary."""
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "check_compliance",
        AsyncMock(
            return_value=[
                {
                    "policy_id": "pol-1",
                    "policy_name": "test-policy",
                    "compliant": True,
                    "issues": [],
                }
            ]
        ),
    )

    resp = test_client.get("/v1/policies/compliance")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["policy_id"] == "pol-1"
    assert body[0]["compliant"] is True
    assert body[0]["issues"] == []
