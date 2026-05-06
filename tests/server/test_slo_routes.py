"""Tests for the SLO routes (Phase 1K, T6).

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


def _make_stored_slo(
    slo_id="slo-1",
    org_id="org-001",
    name="test-slo",
    metric="p99_latency",
    operator="lt",
    threshold=500.0,
    window_minutes=60,
    enabled=True,
    alert_channels=None,
):
    from lore.persistence.types import StoredSloDefinition

    now = _utc_now()
    return StoredSloDefinition(
        id=slo_id,
        org_id=org_id,
        name=name,
        metric=metric,
        operator=operator,
        threshold=threshold,
        window_minutes=window_minutes,
        enabled=enabled,
        alert_channels=alert_channels or [],
        created_at=now,
        updated_at=now,
    )


def _make_stored_alert(
    alert_id=1,
    org_id="org-001",
    slo_id="slo-1",
    metric_value=0.0,
    threshold=500.0,
    status="firing",
    dispatched_to=None,
):
    from lore.persistence.types import StoredSloAlert

    now = _utc_now()
    return StoredSloAlert(
        id=alert_id,
        org_id=org_id,
        slo_id=slo_id,
        metric_value=metric_value,
        threshold=threshold,
        status=status,
        dispatched_to=dispatched_to or [{"channel": "test", "sent_at": None}],
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
    from lore.server.routes.slo import router
    from lore.services import slo as slo_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    monkeypatch.setattr(
        "lore.server.routes.slo.require_role",
        lambda *roles: lambda: mock_auth,
    )

    yield TestClient(app), slo_service, mock_auth


# ── list_slos ─────────────────────────────────────────────────────────────────


def test_list_returns_slos(client, monkeypatch):
    """GET /v1/slo returns a list of SLO definitions."""
    test_client, svc, _ = client
    s1 = _make_stored_slo(slo_id="slo-1", name="alpha")
    s2 = _make_stored_slo(slo_id="slo-2", name="beta")
    monkeypatch.setattr(svc, "list_slos", AsyncMock(return_value=[s1, s2]))

    resp = test_client.get("/v1/slo")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["id"] == "slo-1"
    assert body[0]["name"] == "alpha"
    assert body[1]["id"] == "slo-2"


# ── create_slo ────────────────────────────────────────────────────────────────


def test_post_returns_201_with_id(client, monkeypatch):
    """POST /v1/slo creates an SLO and returns 201."""
    test_client, svc, _ = client
    s = _make_stored_slo(slo_id="slo-new", name="new-slo")
    monkeypatch.setattr(svc, "create_slo", AsyncMock(return_value=s))

    resp = test_client.post(
        "/v1/slo",
        json={
            "name": "new-slo",
            "metric": "p99_latency",
            "operator": "lt",
            "threshold": 500.0,
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "slo-new"
    assert body["name"] == "new-slo"

    svc.create_slo.assert_called_once()
    kwargs = svc.create_slo.call_args.kwargs
    assert kwargs["org_id"] == "org-001"
    assert kwargs["metric"] == "p99_latency"


def test_post_400_on_invalid_metric(client, monkeypatch):
    """POST /v1/slo returns 400 for an invalid metric."""
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "create_slo", AsyncMock(side_effect=ValueError("Invalid metric: bad_metric"))
    )

    resp = test_client.post(
        "/v1/slo",
        json={
            "name": "bad",
            "metric": "bad_metric",
            "operator": "lt",
            "threshold": 100.0,
        },
    )

    assert resp.status_code == 400
    assert "Invalid metric" in resp.json()["detail"]


def test_post_400_on_invalid_operator(client, monkeypatch):
    """POST /v1/slo returns 400 for an invalid operator."""
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "create_slo", AsyncMock(side_effect=ValueError("Invalid operator: bad_op"))
    )

    resp = test_client.post(
        "/v1/slo",
        json={
            "name": "bad",
            "metric": "p99_latency",
            "operator": "bad_op",
            "threshold": 100.0,
        },
    )

    assert resp.status_code == 400
    assert "Invalid operator" in resp.json()["detail"]


# ── update_slo ────────────────────────────────────────────────────────────────


def test_put_returns_updated_slo(client, monkeypatch):
    """PUT /v1/slo/{id} returns the updated SLO."""
    test_client, svc, _ = client
    s = _make_stored_slo(slo_id="slo-1", name="updated-slo", threshold=300.0)
    monkeypatch.setattr(svc, "update_slo", AsyncMock(return_value=s))

    resp = test_client.put("/v1/slo/slo-1", json={"name": "updated-slo", "threshold": 300.0})

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "updated-slo"
    assert body["threshold"] == 300.0


def test_put_404_on_missing(client, monkeypatch):
    """PUT /v1/slo/{id} returns 404 when SLO not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "update_slo", AsyncMock(side_effect=StoreNotFoundError("slo_definitions", "slo-gone"))
    )

    resp = test_client.put("/v1/slo/slo-gone", json={"name": "x"})

    assert resp.status_code == 404


def test_put_400_on_empty_patch(client, monkeypatch):
    """PUT /v1/slo/{id} returns 400 when no fields provided."""
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "update_slo", AsyncMock(side_effect=ValueError("No fields to update"))
    )

    resp = test_client.put("/v1/slo/slo-1", json={})

    assert resp.status_code == 400
    assert "No fields to update" in resp.json()["detail"]


# ── delete_slo ────────────────────────────────────────────────────────────────


def test_delete_returns_204(client, monkeypatch):
    """DELETE /v1/slo/{id} returns 204 on success."""
    test_client, svc, _ = client
    monkeypatch.setattr(svc, "delete_slo", AsyncMock(return_value=None))

    resp = test_client.delete("/v1/slo/slo-1")

    assert resp.status_code == 204


def test_delete_404_on_missing(client, monkeypatch):
    """DELETE /v1/slo/{id} returns 404 when SLO not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "delete_slo", AsyncMock(side_effect=StoreNotFoundError("slo_definitions", "slo-gone"))
    )

    resp = test_client.delete("/v1/slo/slo-gone")

    assert resp.status_code == 404


# ── slo_status ────────────────────────────────────────────────────────────────


def test_get_status_returns_list(client, monkeypatch):
    """GET /v1/slo/status returns per-SLO status dicts."""
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "slo_status",
        AsyncMock(
            return_value=[
                {
                    "id": "slo-1",
                    "name": "latency-slo",
                    "metric": "p99_latency",
                    "threshold": 500.0,
                    "operator": "lt",
                    "current_value": 120.5,
                    "passing": True,
                    "window_minutes": 60,
                }
            ]
        ),
    )

    resp = test_client.get("/v1/slo/status")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "slo-1"
    assert body[0]["passing"] is True
    assert body[0]["current_value"] == 120.5


# ── list_alerts ───────────────────────────────────────────────────────────────


def test_get_alerts_returns_list(client, monkeypatch):
    """GET /v1/slo/alerts returns alert history."""
    test_client, svc, _ = client
    a1 = _make_stored_alert(alert_id=1, slo_id="slo-1")
    monkeypatch.setattr(svc, "list_alerts", AsyncMock(return_value=[a1]))

    resp = test_client.get("/v1/slo/alerts")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == 1
    assert body[0]["slo_id"] == "slo-1"
    assert body[0]["status"] == "firing"


# ── test_alert ────────────────────────────────────────────────────────────────


def test_post_test_alert_returns_201(client, monkeypatch):
    """POST /v1/slo/{id}/test returns 201 with the test alert."""
    test_client, svc, _ = client
    a = _make_stored_alert(alert_id=42, slo_id="slo-1", status="firing")
    monkeypatch.setattr(svc, "test_alert", AsyncMock(return_value=a))

    resp = test_client.post("/v1/slo/slo-1/test")

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == 42
    assert body["slo_id"] == "slo-1"
    assert body["status"] == "firing"


def test_post_test_alert_404_on_missing(client, monkeypatch):
    """POST /v1/slo/{id}/test returns 404 when SLO not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "test_alert", AsyncMock(side_effect=StoreNotFoundError("slo_definitions", "slo-gone"))
    )

    resp = test_client.post("/v1/slo/slo-gone/test")

    assert resp.status_code == 404


# ── timeseries ────────────────────────────────────────────────────────────────


def test_get_timeseries_returns_data(client, monkeypatch):
    """GET /v1/slo/timeseries returns metric time-series data."""
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "slo_timeseries",
        AsyncMock(
            return_value={
                "metric": "p99_latency",
                "window_hours": 24,
                "bucket_minutes": 60,
                "data": [{"timestamp": "2026-05-06T00:00:00+00:00", "value": 150.0}],
            }
        ),
    )

    resp = test_client.get("/v1/slo/timeseries?metric=p99_latency&window_hours=24&bucket_minutes=60")

    assert resp.status_code == 200
    body = resp.json()
    assert body["metric"] == "p99_latency"
    assert len(body["data"]) == 1
    assert body["data"][0]["value"] == 150.0


def test_get_timeseries_400_on_invalid_metric(client, monkeypatch):
    """GET /v1/slo/timeseries returns 400 for invalid metric."""
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "slo_timeseries", AsyncMock(side_effect=ValueError("Invalid metric: bad_metric"))
    )

    resp = test_client.get("/v1/slo/timeseries?metric=bad_metric")

    assert resp.status_code == 400
    assert "Invalid metric" in resp.json()["detail"]
