"""Tests for the sharing routes (Phase 1L, T7).

Each test uses a minimal FakeStore for dependency wiring and patches the
service-module functions with AsyncMock to control return values / side effects.
The `get_store` and `get_auth_context` dependencies are bypassed via
dependency_overrides.
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_config(
    enabled=False,
    human_review_enabled=False,
    rate_limit_per_hour=100,
    volume_alert_threshold=1000,
):
    from lore.persistence.types import SharingConfigData

    return SharingConfigData(
        enabled=enabled,
        human_review_enabled=human_review_enabled,
        rate_limit_per_hour=rate_limit_per_hour,
        volume_alert_threshold=volume_alert_threshold,
        updated_at=_utc_now(),
    )


def _make_agent_config(agent_id="agent-1", enabled=True, categories=("a", "b")):
    from lore.persistence.types import AgentSharingConfigData

    return AgentSharingConfigData(
        agent_id=agent_id,
        enabled=enabled,
        categories=tuple(categories),
        updated_at=_utc_now(),
    )


def _make_deny_rule(rule_id="rule-1", pattern="^secret"):
    from lore.persistence.types import DenyListRuleData

    return DenyListRuleData(
        id=rule_id,
        pattern=pattern,
        is_regex=True,
        reason="r",
        created_at=_utc_now(),
    )


def _make_audit_event(event_id="ev-1", event_type="share"):
    from lore.persistence.types import AuditEventData

    return AuditEventData(
        id=event_id,
        event_type=event_type,
        lesson_id=None,
        query_text=None,
        initiated_by="key-001",
        created_at=_utc_now(),
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
def client(mock_auth):
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.sharing import rate_router, router
    from lore.services import sharing as sharing_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)
    app.include_router(rate_router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    yield TestClient(app), sharing_service, mock_auth


# ── config ────────────────────────────────────────────────────────────────────


def test_get_config_returns_defaults(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "get_or_init_config", AsyncMock(return_value=_make_config()),
    )

    resp = test_client.get("/v1/sharing/config")

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["rate_limit_per_hour"] == 100


def test_put_config_passes_patch(client, monkeypatch):
    test_client, svc, _ = client
    updated = _make_config(enabled=True, rate_limit_per_hour=500)
    update_mock = AsyncMock(return_value=updated)
    monkeypatch.setattr(svc, "update_config", update_mock)

    resp = test_client.put(
        "/v1/sharing/config",
        json={"enabled": True, "rate_limit_per_hour": 500},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["rate_limit_per_hour"] == 500
    update_mock.assert_called_once()
    kwargs = update_mock.call_args.kwargs
    assert kwargs["org_id"] == "org-001"
    assert kwargs["patch"].enabled is True
    assert kwargs["patch"].rate_limit_per_hour == 500


# ── agents ────────────────────────────────────────────────────────────────────


def test_list_agents_returns_list(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "list_agent_configs",
        AsyncMock(return_value=[_make_agent_config(), _make_agent_config(agent_id="agent-2")]),
    )

    resp = test_client.get("/v1/sharing/agents")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["agent_id"] == "agent-1"


def test_put_agent_upserts(client, monkeypatch):
    test_client, svc, _ = client
    upsert_mock = AsyncMock(return_value=_make_agent_config(agent_id="agent-z"))
    monkeypatch.setattr(svc, "upsert_agent_config", upsert_mock)

    resp = test_client.put(
        "/v1/sharing/agents/agent-z",
        json={"enabled": True, "categories": ["x", "y"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "agent-z"
    upsert_mock.assert_called_once()
    kwargs = upsert_mock.call_args.kwargs
    assert kwargs["agent_id"] == "agent-z"
    assert kwargs["enabled"] is True
    assert kwargs["categories"] == ["x", "y"]


# ── deny-list ─────────────────────────────────────────────────────────────────


def test_get_deny_rules_returns_list(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "list_deny_rules", AsyncMock(return_value=[_make_deny_rule()]),
    )

    resp = test_client.get("/v1/sharing/deny-list")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["pattern"] == "^secret"


def test_post_deny_rule_returns_201(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc, "create_deny_rule", AsyncMock(return_value=_make_deny_rule(rule_id="r-new")),
    )

    resp = test_client.post(
        "/v1/sharing/deny-list",
        json={"pattern": "^secret", "is_regex": True, "reason": "r"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "r-new"


def test_delete_deny_rule_returns_204(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(svc, "delete_deny_rule", AsyncMock(return_value=True))

    resp = test_client.delete("/v1/sharing/deny-list/rule-1")

    assert resp.status_code == 204


def test_delete_deny_rule_404_when_missing(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(svc, "delete_deny_rule", AsyncMock(return_value=False))

    resp = test_client.delete("/v1/sharing/deny-list/rule-gone")

    assert resp.status_code == 404


# ── audit ─────────────────────────────────────────────────────────────────────


def test_get_audit_returns_list(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "list_audit_events",
        AsyncMock(return_value=[_make_audit_event(), _make_audit_event(event_id="ev-2")]),
    )

    resp = test_client.get("/v1/sharing/audit?event_type=share&limit=10")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2


# ── stats ─────────────────────────────────────────────────────────────────────


def test_get_stats_returns_counts(client, monkeypatch):
    from lore.persistence.types import SharingStatsData

    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "get_stats",
        AsyncMock(
            return_value=SharingStatsData(
                count_shared=42,
                last_shared=_utc_now(),
                audit_summary={"share": 12, "rate": 8},
            )
        ),
    )

    resp = test_client.get("/v1/sharing/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["countShared"] == 42
    assert body["auditSummary"] == {"share": 12, "rate": 8}


# ── purge ─────────────────────────────────────────────────────────────────────


def test_purge_returns_count(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(svc, "purge", AsyncMock(return_value=7))

    resp = test_client.post(
        "/v1/sharing/purge", json={"confirmation": "PURGE"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted_lessons"] == 7
    assert body["status"] == "purged"


def test_purge_400_on_bad_confirmation(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "purge",
        AsyncMock(side_effect=ValueError("Confirmation must be 'PURGE'")),
    )

    resp = test_client.post(
        "/v1/sharing/purge", json={"confirmation": "no"},
    )

    assert resp.status_code == 400
    assert "PURGE" in resp.json()["detail"]


# ── rate ──────────────────────────────────────────────────────────────────────


def test_rate_lesson_returns_score(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(svc, "rate_lesson", AsyncMock(return_value=11))

    resp = test_client.post(
        "/v1/lessons/lesson-1/rate", json={"delta": 1},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reputation_score"] == 11


def test_rate_lesson_404_when_missing(client, monkeypatch):
    test_client, svc, _ = client
    monkeypatch.setattr(svc, "rate_lesson", AsyncMock(return_value=None))

    resp = test_client.post(
        "/v1/lessons/lesson-gone/rate", json={"delta": 1},
    )

    assert resp.status_code == 404


def test_rate_lesson_400_on_bad_delta(client):
    """Pydantic's model_post_init rejects delta != ±1 before the route runs."""
    test_client, _, _ = client

    resp = test_client.post(
        "/v1/lessons/lesson-1/rate", json={"delta": 2},
    )

    assert resp.status_code == 422 or resp.status_code == 400


def test_rate_lesson_400_when_service_raises(client, monkeypatch):
    """Service-side ValueError (defensive validation) maps to 400."""
    test_client, svc, _ = client
    monkeypatch.setattr(
        svc,
        "rate_lesson",
        AsyncMock(side_effect=ValueError("delta must be 1 or -1")),
    )

    resp = test_client.post(
        "/v1/lessons/lesson-1/rate", json={"delta": 1},
    )

    assert resp.status_code == 400
    assert "delta" in resp.json()["detail"]
