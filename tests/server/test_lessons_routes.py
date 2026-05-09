"""Tests for the lessons routes (Phase 1H, T8).

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


def _make_stored_memory(
    memory_id="mem-1",
    org_id="org-001",
    content="KeyError when dict key absent",
    context="Use .get() with a default instead of direct access",
    tags=("python", "dict"),
    source=None,
    project=None,
    upvotes=0,
    downvotes=0,
    meta=None,
    **kwargs,
):
    from lore.persistence.types import StoredMemory

    now = _utc_now()
    defaults = dict(
        id=memory_id,
        org_id=org_id,
        content=content,
        context=context,
        tags=list(tags),
        source=source,
        project=project,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=upvotes,
        downvotes=downvotes,
        meta=meta if meta is not None else {},
        access_count=0,
        last_accessed_at=None,
    )
    defaults.update(kwargs)
    return StoredMemory(**defaults)


def _make_exported_memory(
    memory_id="mem-exp",
    content="KeyError when dict key absent",
    context="Use .get() with a default",
    embedding=None,
    **kwargs,
):
    from lore.persistence.types import ExportedMemory

    now = _utc_now()
    defaults = dict(
        id=memory_id,
        org_id="org-001",
        content=content,
        context=context,
        tags=[],
        source=None,
        project=None,
        embedding=embedding,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )
    defaults.update(kwargs)
    return ExportedMemory(**defaults)


def _make_search_result_dict(
    memory_id="mem-1",
    content="KeyError when dict key absent",
    context="Use .get() with a default",
    score=0.87,
    **kwargs,
):
    now = _utc_now()
    defaults = dict(
        id=memory_id,
        content=content,
        context=context,
        tags=[],
        source=None,
        project=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
        score=score,
    )
    defaults.update(kwargs)
    return defaults


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
    from lore.server.routes.lessons import router
    from lore.services import lessons as lessons_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: mock_auth

    # Bypass require_role — replace the factory so every role check passes.
    monkeypatch.setattr(
        "lore.server.routes.lessons.require_role",
        lambda *roles: lambda: mock_auth,
    )

    yield TestClient(app), lessons_service, mock_auth


# ── create ────────────────────────────────────────────────────────────────────


def test_post_returns_201_with_id(client, monkeypatch):
    """POST /v1/lessons creates a lesson; service called with translated fields."""
    test_client, lessons_service, _ = client
    monkeypatch.setattr(
        lessons_service,
        "create",
        AsyncMock(return_value="lesson-new"),
    )
    resp = test_client.post(
        "/v1/lessons",
        json={
            "problem": "KeyError in dict access",
            "resolution": "Use .get() method",
            "tags": ["python"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "lesson-new"

    # Verify service was called with the translated fields (problem → problem kwarg)
    lessons_service.create.assert_called_once()
    call_kwargs = lessons_service.create.call_args.kwargs
    assert call_kwargs["problem"] == "KeyError in dict access"
    assert call_kwargs["resolution"] == "Use .get() method"
    assert call_kwargs["org_id"] == "org-001"


# ── search ────────────────────────────────────────────────────────────────────


def test_post_search_returns_results(client, monkeypatch):
    """POST /v1/lessons/search; response has lessons array with score field."""
    test_client, lessons_service, _ = client
    result = _make_search_result_dict(memory_id="mem-s1", score=0.95)
    monkeypatch.setattr(
        lessons_service,
        "search",
        AsyncMock(return_value=[result]),
    )
    resp = test_client.post(
        "/v1/lessons/search",
        json={"embedding": [0.1] * 384},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "lessons" in body
    assert len(body["lessons"]) == 1
    item = body["lessons"][0]
    assert item["id"] == "mem-s1"
    assert item["score"] == 0.95
    # Field translation: content → problem, context → resolution
    assert item["problem"] == result["content"]
    assert item["resolution"] == result["context"]


# ── access ────────────────────────────────────────────────────────────────────


def test_post_access_returns_dict(client, monkeypatch):
    """POST /v1/lessons/{id}/access returns 200 with access metadata."""
    test_client, lessons_service, _ = client
    now = _utc_now()
    monkeypatch.setattr(
        lessons_service,
        "record_access",
        AsyncMock(
            return_value={
                "id": "mem-1",
                "access_count": 5,
                "last_accessed_at": now,
            }
        ),
    )
    resp = test_client.post("/v1/lessons/mem-1/access")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "mem-1"
    assert body["access_count"] == 5


def test_post_access_404_on_missing(client, monkeypatch):
    """POST /v1/lessons/{id}/access raises 404 when lesson not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, lessons_service, _ = client
    monkeypatch.setattr(
        lessons_service,
        "record_access",
        AsyncMock(side_effect=StoreNotFoundError("memories", "mem-gone")),
    )
    resp = test_client.post("/v1/lessons/mem-gone/access")
    assert resp.status_code == 404


# ── get ───────────────────────────────────────────────────────────────────────


def test_get_returns_lesson_response(client, monkeypatch):
    """GET /v1/lessons/{id} returns lesson with correct field translation."""
    test_client, lessons_service, _ = client
    m = _make_stored_memory(
        memory_id="mem-1",
        content="NullPointerException on uninitialized field",
        context="Initialize fields in constructor",
    )
    monkeypatch.setattr(
        lessons_service,
        "get",
        AsyncMock(return_value=m),
    )
    resp = test_client.get("/v1/lessons/mem-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "mem-1"
    # Field translation: content → problem, context → resolution
    assert body["problem"] == "NullPointerException on uninitialized field"
    assert body["resolution"] == "Initialize fields in constructor"


def test_get_404_when_missing(client, monkeypatch):
    """GET /v1/lessons/{id} returns 404 when lesson not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, lessons_service, _ = client
    monkeypatch.setattr(
        lessons_service,
        "get",
        AsyncMock(side_effect=StoreNotFoundError("memories", "mem-gone")),
    )
    resp = test_client.get("/v1/lessons/mem-gone")
    assert resp.status_code == 404


# ── update ────────────────────────────────────────────────────────────────────


def test_patch_changes_field(client, monkeypatch):
    """PATCH /v1/lessons/{id} returns updated lesson with correct translation."""
    test_client, lessons_service, _ = client
    m = _make_stored_memory(
        memory_id="mem-1",
        content="KeyError in dict access",
        context="Use .get() with a default",
        tags=("python", "dict", "new-tag"),
    )
    monkeypatch.setattr(
        lessons_service,
        "update",
        AsyncMock(return_value=m),
    )
    resp = test_client.patch(
        "/v1/lessons/mem-1",
        json={"tags": ["python", "dict", "new-tag"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "mem-1"
    assert body["problem"] == "KeyError in dict access"
    assert body["resolution"] == "Use .get() with a default"


def test_patch_422_on_no_fields(client, monkeypatch):
    """PATCH /v1/lessons/{id} returns 422 when service raises 'No fields to update'."""
    test_client, lessons_service, _ = client
    monkeypatch.setattr(
        lessons_service,
        "update",
        AsyncMock(side_effect=ValueError("No fields to update")),
    )
    resp = test_client.patch("/v1/lessons/mem-1", json={})
    assert resp.status_code == 422
    assert "No fields to update" in resp.json()["detail"]


def test_patch_422_on_unsupported_vote_mode(client, monkeypatch):
    """PATCH /v1/lessons/{id} returns 422 when vote mode is not supported."""
    test_client, lessons_service, _ = client
    monkeypatch.setattr(
        lessons_service,
        "update",
        AsyncMock(side_effect=ValueError("Vote update mode not supported")),
    )
    resp = test_client.patch(
        "/v1/lessons/mem-1",
        json={"upvotes": "+1"},
    )
    assert resp.status_code == 422
    assert "Vote update mode not supported" in resp.json()["detail"]


def test_patch_404_on_missing(client, monkeypatch):
    """PATCH /v1/lessons/{id} returns 404 when lesson not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, lessons_service, _ = client
    monkeypatch.setattr(
        lessons_service,
        "update",
        AsyncMock(side_effect=StoreNotFoundError("memories", "mem-gone")),
    )
    resp = test_client.patch("/v1/lessons/mem-gone", json={"tags": ["x"]})
    assert resp.status_code == 404


# ── delete ────────────────────────────────────────────────────────────────────


def test_delete_returns_204(client, monkeypatch):
    """DELETE /v1/lessons/{id} returns 204 on success."""
    test_client, lessons_service, _ = client
    monkeypatch.setattr(
        lessons_service,
        "delete",
        AsyncMock(return_value=None),
    )
    resp = test_client.delete("/v1/lessons/mem-1")
    assert resp.status_code == 204


def test_delete_404_on_missing(client, monkeypatch):
    """DELETE /v1/lessons/{id} returns 404 when lesson not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, lessons_service, _ = client
    monkeypatch.setattr(
        lessons_service,
        "delete",
        AsyncMock(side_effect=StoreNotFoundError("memories", "mem-gone")),
    )
    resp = test_client.delete("/v1/lessons/mem-gone")
    assert resp.status_code == 404


# ── list ──────────────────────────────────────────────────────────────────────


def test_list_returns_paginated(client, monkeypatch):
    """GET /v1/lessons returns total + lessons array with pagination info."""
    test_client, lessons_service, _ = client
    memories = [
        _make_stored_memory(memory_id="mem-1"),
        _make_stored_memory(memory_id="mem-2"),
    ]
    monkeypatch.setattr(
        lessons_service,
        "list_lessons",
        AsyncMock(return_value=(10, memories)),
    )
    resp = test_client.get("/v1/lessons?limit=2&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 10
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["lessons"]) == 2
    assert body["lessons"][0]["id"] == "mem-1"
    assert body["lessons"][1]["id"] == "mem-2"


# ── export ────────────────────────────────────────────────────────────────────


def test_export_includes_embeddings(client, monkeypatch):
    """POST /v1/lessons/export returns lesson items with embedding list."""
    test_client, lessons_service, _ = client
    embedding = [0.1] * 384
    em = _make_exported_memory(memory_id="mem-exp", embedding=embedding)
    monkeypatch.setattr(
        lessons_service,
        "export",
        AsyncMock(return_value=[em]),
    )
    resp = test_client.post("/v1/lessons/export")
    assert resp.status_code == 200
    body = resp.json()
    assert "lessons" in body
    assert len(body["lessons"]) == 1
    item = body["lessons"][0]
    assert item["id"] == "mem-exp"
    assert item["embedding"] == embedding
    # Field translation verified
    assert item["problem"] == em.content
    assert item["resolution"] == em.context


# ── import ────────────────────────────────────────────────────────────────────


def test_import_returns_count(client, monkeypatch):
    """POST /v1/lessons/import returns imported count from service."""
    test_client, lessons_service, _ = client
    monkeypatch.setattr(
        lessons_service,
        "import_lessons",
        AsyncMock(return_value=3),
    )
    lessons_payload = [
        {
            "problem": f"Problem {i}",
            "resolution": f"Resolution {i}",
            "embedding": [0.1] * 384,
        }
        for i in range(3)
    ]
    resp = test_client.post(
        "/v1/lessons/import",
        json={"lessons": lessons_payload},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["imported"] == 3
