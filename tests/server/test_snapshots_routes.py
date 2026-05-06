"""Tests for the snapshots routes (Phase 1E, T12).

Each test uses a minimal FakeStore for dependency wiring and patches the
service-module functions with AsyncMock to control return values.
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


def _make_stored_snapshot_memory(memory_id="mem_test", **kwargs):
    from lore.persistence.types import StoredMemory
    now = _utc_now()
    defaults = dict(
        id=memory_id,
        org_id="org-001",
        content="snapshot content",
        context=None,
        tags=("session_snapshot", "sess_abc"),
        confidence=1.0,
        source=None,
        project=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={
            "session_id": "sess_abc",
            "title": "snapshot content",
            "extraction_method": "raw",
            "type": "session_snapshot",
            "tier": "long",
        },
        importance_score=0.95,
        access_count=0,
        last_accessed_at=None,
    )
    defaults.update(kwargs)
    return StoredMemory(**defaults)


def _make_writer_auth():
    from lore.server.auth import AuthContext
    return AuthContext(
        org_id="org-001",
        project=None,
        is_root=False,
        key_id="key-001",
        role="writer",
    )


def _make_reader_auth():
    from lore.server.auth import AuthContext
    return AuthContext(
        org_id="org-001",
        project=None,
        is_root=False,
        key_id="key-002",
        role="reader",
    )


class FakeStore:
    """Minimal Store stand-in — actual logic is mocked at the service layer."""

    async def close(self):
        pass


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    """TestClient wired with writer auth and require_role bypassed."""
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.snapshots import router
    from lore.services import snapshots as snapshots_service

    writer_auth = _make_writer_auth()
    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: writer_auth

    # Bypass require_role — replace the factory so every role check passes.
    monkeypatch.setattr(
        "lore.server.routes.snapshots.require_role",
        lambda *roles: (lambda: writer_auth),
    )

    yield TestClient(app), snapshots_service


# ── happy-path tests ──────────────────────────────────────────────────────────


def test_create_returns_201(client, monkeypatch):
    test_client, snapshots_service = client
    stored = _make_stored_snapshot_memory()
    monkeypatch.setattr(snapshots_service, "create_snapshot", AsyncMock(return_value=stored))
    resp = test_client.post(
        "/v1/snapshots",
        json={"content": "snapshot content"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "mem_test"
    assert body["session_id"] == "sess_abc"
    assert body["title"] == "snapshot content"
    assert body["extraction_method"] == "raw"
    assert "created_at" in body


def test_create_with_explicit_session_id(client, monkeypatch):
    test_client, snapshots_service = client
    stored = _make_stored_snapshot_memory(
        meta={
            "session_id": "user_session_123",
            "title": "snapshot content",
            "extraction_method": "raw",
            "type": "session_snapshot",
            "tier": "long",
        }
    )
    monkeypatch.setattr(snapshots_service, "create_snapshot", AsyncMock(return_value=stored))
    resp = test_client.post(
        "/v1/snapshots",
        json={"content": "snapshot content", "session_id": "user_session_123"},
    )
    assert resp.status_code == 201
    assert resp.json()["session_id"] == "user_session_123"


def test_create_default_title_from_content(client, monkeypatch):
    long_content = "A" * 100
    truncated_title = long_content[:80].strip()
    stored = _make_stored_snapshot_memory(
        content=long_content,
        meta={
            "session_id": "sess_abc",
            "title": truncated_title,
            "extraction_method": "raw",
            "type": "session_snapshot",
            "tier": "long",
        },
    )
    test_client, snapshots_service = client
    monkeypatch.setattr(snapshots_service, "create_snapshot", AsyncMock(return_value=stored))
    resp = test_client.post(
        "/v1/snapshots",
        json={"content": long_content},
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == truncated_title


def test_create_passes_through_user_tags(client, monkeypatch):
    test_client, snapshots_service = client
    mock_create = AsyncMock(return_value=_make_stored_snapshot_memory())
    monkeypatch.setattr(snapshots_service, "create_snapshot", mock_create)
    resp = test_client.post(
        "/v1/snapshots",
        json={"content": "snapshot content", "tags": ["important", "daily"]},
    )
    assert resp.status_code == 201
    _, kwargs = mock_create.call_args
    assert kwargs["tags"] == ["important", "daily"]


def test_create_403_when_role_not_writer_or_admin(monkeypatch):
    """Non-writer role is rejected by the real require_role decorator."""
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.snapshots import router

    reader_auth = _make_reader_auth()
    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: reader_auth

    # Do NOT monkeypatch require_role — let the real decorator check the role.
    test_client = TestClient(app, raise_server_exceptions=False)
    resp = test_client.post(
        "/v1/snapshots",
        json={"content": "snapshot content"},
    )
    assert resp.status_code == 403
