"""Tests for the conversations routes (Phase 1G, T8).

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


def _make_stored_job(
    id="job_test",
    org_id="org-001",
    status="accepted",
    message_count=2,
    **kwargs,
):
    from lore.persistence.types import StoredConversationJob

    now = datetime.now(timezone.utc)
    defaults = dict(
        id=id,
        org_id=org_id,
        status=status,
        message_count=message_count,
        messages_json="[]",
        user_id=None,
        session_id=None,
        project=None,
        memory_ids=(),
        memories_extracted=0,
        duplicates_skipped=0,
        error=None,
        processing_time_ms=0,
        created_at=now,
        completed_at=None,
    )
    defaults.update(kwargs)
    return StoredConversationJob(**defaults)


class FakeStore:
    """Minimal Store stand-in — actual logic is mocked at the service layer."""

    async def close(self):
        pass


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_writer_auth():
    return _make_writer_auth()


@pytest.fixture
def mock_reader_auth():
    return _make_reader_auth()


@pytest.fixture
def client_writer(monkeypatch, mock_writer_auth):
    """TestClient wired with writer auth and require_role bypassed."""
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.conversations import router
    from lore.services import conversations as conversations_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: mock_writer_auth

    monkeypatch.setattr(
        "lore.server.routes.conversations.require_role",
        lambda *roles: (lambda: mock_writer_auth),
    )

    yield TestClient(app), conversations_service, mock_writer_auth


@pytest.fixture
def client_reader(monkeypatch, mock_reader_auth):
    """TestClient wired with reader auth; require_role raises 403."""
    from fastapi import HTTPException

    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.conversations import router
    from lore.services import conversations as conversations_service

    fake_store = FakeStore()
    app = FastAPI()
    app.include_router(router)

    async def fake_get_store():
        return fake_store

    app.dependency_overrides[get_store] = fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: mock_reader_auth

    def _reject(*roles):
        def _check():
            raise HTTPException(status_code=403, detail="Insufficient role")

        return _check

    monkeypatch.setattr("lore.server.routes.conversations.require_role", _reject)

    yield TestClient(app), conversations_service, mock_reader_auth


# ── tests ─────────────────────────────────────────────────────────────────────


def test_post_returns_202_and_schedules_processing(client_writer, monkeypatch):
    """POST /v1/conversations returns 202 and schedules background extraction."""
    test_client, conversations_service, auth = client_writer
    fake_job = _make_stored_job(id="job_test", message_count=2)
    monkeypatch.setattr(
        conversations_service,
        "create_job",
        AsyncMock(return_value=fake_job),
    )
    process_mock = AsyncMock()
    monkeypatch.setattr(conversations_service, "process_job_async", process_mock)

    resp = test_client.post(
        "/v1/conversations",
        json={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hi"},
            ]
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == "job_test"
    assert body["status"] == "accepted"
    assert body["message_count"] == 2

    # The TestClient runs asyncio via anyio; create_task fires within the request.
    process_mock.assert_called_once()
    args = process_mock.call_args.args
    assert args[1] == "job_test"  # job_id
    assert args[2] == "org-001"  # org_id


def test_post_400_on_empty_messages(client_writer, monkeypatch):
    """POST with empty messages list → service raises ValueError → 400."""
    test_client, conversations_service, _auth = client_writer
    monkeypatch.setattr(
        conversations_service,
        "create_job",
        AsyncMock(side_effect=ValueError("messages must be non-empty")),
    )
    resp = test_client.post("/v1/conversations", json={"messages": []})
    assert resp.status_code == 400
    assert "messages must be non-empty" in resp.json()["detail"]


def test_post_400_on_missing_role(client_writer, monkeypatch):
    """POST with malformed message dict → service raises ValueError → 400."""
    test_client, conversations_service, _auth = client_writer
    monkeypatch.setattr(
        conversations_service,
        "create_job",
        AsyncMock(
            side_effect=ValueError("Each message must have 'role' and 'content'")
        ),
    )
    resp = test_client.post(
        "/v1/conversations",
        json={"messages": [{"text": "no role here"}]},
    )
    assert resp.status_code == 400
    assert "Each message must have 'role' and 'content'" in resp.json()["detail"]


def test_post_403_when_role_not_writer_or_admin(client_reader):
    """Caller with reader role is rejected by require_role dependency."""
    test_client, _svc, _auth = client_reader
    resp = test_client.post(
        "/v1/conversations",
        json={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hi"},
            ]
        },
    )
    assert resp.status_code == 403


def test_get_returns_status_response(client_writer, monkeypatch):
    """GET /v1/conversations/{job_id} returns 200 with full job status."""
    test_client, conversations_service, _auth = client_writer
    fake_job = _make_stored_job(
        id="job_abc",
        status="completed",
        message_count=3,
        memory_ids=("m1", "m2"),
        memories_extracted=2,
        duplicates_skipped=1,
        processing_time_ms=500,
    )
    monkeypatch.setattr(
        conversations_service,
        "get_job_status",
        AsyncMock(return_value=fake_job),
    )
    resp = test_client.get("/v1/conversations/job_abc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "job_abc"
    assert body["status"] == "completed"
    assert body["message_count"] == 3
    assert body["memories_extracted"] == 2
    assert body["duplicates_skipped"] == 1
    assert body["processing_time_ms"] == 500
    assert body["error"] is None
    assert body["memory_ids"] == ["m1", "m2"]


def test_get_404_when_job_missing(client_writer, monkeypatch):
    """GET /v1/conversations/{job_id} returns 404 when job not found."""
    from lore.persistence.exceptions import StoreNotFoundError

    test_client, conversations_service, _auth = client_writer
    monkeypatch.setattr(
        conversations_service,
        "get_job_status",
        AsyncMock(side_effect=StoreNotFoundError("ConversationJob", "nonexistent")),
    )
    resp = test_client.get("/v1/conversations/nonexistent")
    assert resp.status_code == 404
    assert "Job not found" in resp.json()["detail"]


def test_get_includes_memory_ids_array(client_writer, monkeypatch):
    """memory_ids tuple in Python is returned as a JSON list."""
    test_client, conversations_service, _auth = client_writer
    fake_job = _make_stored_job(
        id="job_ids",
        status="completed",
        memory_ids=("m1", "m2"),
        memories_extracted=2,
    )
    monkeypatch.setattr(
        conversations_service,
        "get_job_status",
        AsyncMock(return_value=fake_job),
    )
    resp = test_client.get("/v1/conversations/job_ids")
    assert resp.status_code == 200
    ids = resp.json()["memory_ids"]
    assert isinstance(ids, list)
    assert ids == ["m1", "m2"]
