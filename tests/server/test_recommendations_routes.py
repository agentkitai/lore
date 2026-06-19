"""Tests for the recommendations routes (Phase 1F, T8).

Each test uses a minimal FakeStore for dependency wiring and patches the
service-module functions with AsyncMock to control return values.
The `get_store` and `get_auth_context` dependencies are bypassed via
dependency_overrides.
"""

from __future__ import annotations

from types import SimpleNamespace
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


def _make_admin_auth():
    from lore.server.auth import AuthContext

    return AuthContext(
        org_id="org-001",
        project=None,
        is_root=True,
        key_id="key-admin",
        role="admin",
    )


def _make_fake_rec(
    memory_id="mem-1",
    content_preview="some content",
    score=0.92345,
    explanation="related to your context",
    reason="entity_overlap",
    confidence=0.88,
):
    return SimpleNamespace(
        memory_id=memory_id,
        content_preview=content_preview,
        score=score,
        explanation=explanation,
        reason=reason,
        confidence=confidence,
    )


class FakeStore:
    """Minimal Store stand-in — actual logic is mocked at the service layer."""

    async def close(self):
        pass


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_auth():
    return _make_admin_auth()


@pytest.fixture
def client(monkeypatch, mock_auth):
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.recommendations import router
    from lore.services import recommendations as recommendations_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    yield TestClient(app), recommendations_service, mock_auth


# ── tests ─────────────────────────────────────────────────────────────────────


def test_get_blank_context_returns_empty_list(client):
    """GET /v1/recommendations with no context short-circuits to []."""
    test_client, _svc, _auth = client
    resp = test_client.get("/v1/recommendations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_wires_to_recommend_service(client, monkeypatch):
    """GET with context calls the real recommend service and maps results."""
    test_client, recommendations_service, _auth = client
    fake_rec = _make_fake_rec()
    mock_recommend = AsyncMock(return_value=[fake_rec])
    monkeypatch.setattr(recommendations_service, "recommend", mock_recommend)

    resp = test_client.get(
        "/v1/recommendations",
        params={"context": "debugging memory leak", "max_results": 2},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["memory_id"] == "mem-1"
    mock_recommend.assert_called_once()
    call_kwargs = mock_recommend.call_args.kwargs
    assert call_kwargs["context"] == "debugging memory leak"
    assert call_kwargs["max_results"] == 2


def test_post_returns_engine_results(client, monkeypatch):
    """POST with context returns recommendation objects mapped to response shape."""
    test_client, recommendations_service, _auth = client
    fake_rec = _make_fake_rec()
    monkeypatch.setattr(
        recommendations_service,
        "recommend",
        AsyncMock(return_value=[fake_rec]),
    )
    resp = test_client.post(
        "/v1/recommendations",
        json={"context": "debugging memory leak", "session_entities": [], "max_results": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    item = body[0]
    assert item["memory_id"] == "mem-1"
    assert item["content_preview"] == "some content"
    assert item["score"] == round(0.92345, 4)
    assert item["explanation"] == "related to your context"
    assert item["reason"] == "entity_overlap"
    assert item["confidence"] == 0.88


def test_post_returns_empty_when_context_blank(client, monkeypatch):
    """POST with blank context → service returns [] → response is empty list."""
    test_client, recommendations_service, _auth = client
    monkeypatch.setattr(
        recommendations_service,
        "recommend",
        AsyncMock(return_value=[]),
    )
    resp = test_client.post(
        "/v1/recommendations",
        json={"context": "", "session_entities": [], "max_results": 3},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_proactive_parses_entities(client, monkeypatch):
    """GET /proactive with entities query param parses comma-separated list."""
    test_client, recommendations_service, _auth = client
    mock_recommend = AsyncMock(return_value=[])
    monkeypatch.setattr(recommendations_service, "recommend", mock_recommend)

    resp = test_client.get(
        "/v1/recommendations/proactive",
        params={"context": "some context", "entities": "alpha,beta,gamma"},
    )
    assert resp.status_code == 200
    mock_recommend.assert_called_once()
    call_kwargs = mock_recommend.call_args.kwargs
    assert call_kwargs["session_entities"] == ["alpha", "beta", "gamma"]


def test_proactive_with_blank_entities(client, monkeypatch):
    """GET /proactive with entities="" → service called with session_entities=None."""
    test_client, recommendations_service, _auth = client
    mock_recommend = AsyncMock(return_value=[])
    monkeypatch.setattr(recommendations_service, "recommend", mock_recommend)

    resp = test_client.get(
        "/v1/recommendations/proactive",
        params={"context": "some context", "entities": ""},
    )
    assert resp.status_code == 200
    mock_recommend.assert_called_once()
    call_kwargs = mock_recommend.call_args.kwargs
    assert call_kwargs["session_entities"] is None


def test_feedback_records_and_returns_status(client, monkeypatch):
    """POST feedback for a valid memory returns 200 with recorded status."""
    test_client, recommendations_service, _auth = client
    monkeypatch.setattr(
        recommendations_service,
        "submit_feedback",
        AsyncMock(return_value=None),
    )
    resp = test_client.post(
        "/v1/recommendations/mem-1/feedback",
        json={"feedback": "positive"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "recorded"
    assert body["memory_id"] == "mem-1"
    assert body["feedback"] == "positive"


def test_feedback_400_on_invalid_value(client, monkeypatch):
    """POST feedback with invalid value → service raises ValueError → 400."""
    test_client, recommendations_service, _auth = client
    monkeypatch.setattr(
        recommendations_service,
        "submit_feedback",
        AsyncMock(side_effect=ValueError("Feedback must be 'positive' or 'negative'")),
    )
    resp = test_client.post(
        "/v1/recommendations/mem-1/feedback",
        json={"feedback": "meh"},
    )
    assert resp.status_code == 400
    assert "Feedback must be 'positive' or 'negative'" in resp.json()["detail"]


def test_get_config_returns_dict_as_response(client, monkeypatch):
    """GET /config returns ConfigResponse-shaped JSON from service dict."""
    test_client, recommendations_service, _auth = client
    monkeypatch.setattr(
        recommendations_service,
        "get_config",
        AsyncMock(
            return_value={
                "aggressiveness": 0.5,
                "enabled": True,
                "max_suggestions": 3,
                "cooldown_minutes": 15,
            }
        ),
    )
    resp = test_client.get("/v1/recommendations/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["aggressiveness"] == 0.5
    assert body["enabled"] is True
    assert body["max_suggestions"] == 3
    assert body["cooldown_minutes"] == 15


def test_patch_config_returns_updated_values(client, monkeypatch):
    """PATCH /config with full body returns updated ConfigResponse."""
    test_client, recommendations_service, _auth = client
    monkeypatch.setattr(
        recommendations_service,
        "update_config",
        AsyncMock(
            return_value={
                "aggressiveness": 0.8,
                "enabled": False,
                "max_suggestions": 5,
                "cooldown_minutes": 30,
            }
        ),
    )
    resp = test_client.patch(
        "/v1/recommendations/config",
        json={"aggressiveness": 0.8, "enabled": False, "max_suggestions": 5, "cooldown_minutes": 30},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["aggressiveness"] == 0.8
    assert body["enabled"] is False
    assert body["max_suggestions"] == 5
    assert body["cooldown_minutes"] == 30


def test_patch_config_passes_only_set_fields_to_service(client, monkeypatch):
    """PATCH /config with partial body uses model_dump(exclude_unset=True)."""
    test_client, recommendations_service, _auth = client
    mock_update = AsyncMock(
        return_value={
            "aggressiveness": 0.9,
            "enabled": True,
            "max_suggestions": 3,
            "cooldown_minutes": 15,
        }
    )
    monkeypatch.setattr(recommendations_service, "update_config", mock_update)

    resp = test_client.patch(
        "/v1/recommendations/config",
        json={"aggressiveness": 0.9},
    )
    assert resp.status_code == 200

    mock_update.assert_called_once()
    call_kwargs = mock_update.call_args.kwargs
    assert call_kwargs.get("aggressiveness") == 0.9
    assert "enabled" not in call_kwargs
    assert "max_suggestions" not in call_kwargs
    assert "cooldown_minutes" not in call_kwargs
