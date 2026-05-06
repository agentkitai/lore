"""Tests for conversation REST API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from lore.persistence import StoredConversationJob

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


def _make_job(
    job_id: str = "job-001",
    org_id: str = "org-001",
    status: str = "accepted",
    message_count: int = 2,
    messages_json: str = "[]",
    memories_extracted: int = 0,
    memory_ids=None,
    duplicates_skipped: int = 0,
    processing_time_ms: int = 0,
    error=None,
) -> StoredConversationJob:
    return StoredConversationJob(
        id=job_id,
        org_id=org_id,
        status=status,
        message_count=message_count,
        messages_json=messages_json,
        user_id=None,
        session_id=None,
        project=None,
        memory_ids=memory_ids or [],
        memories_extracted=memories_extracted,
        duplicates_skipped=duplicates_skipped,
        error=error,
        processing_time_ms=processing_time_ms,
        created_at=datetime.now(timezone.utc),
        completed_at=None,
    )


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
def fake_store():
    """Minimal fake Store for conversation route tests."""

    class FakeStore:
        def __init__(self):
            self.created_job = None
            self.job_to_return = None

        async def create_conversation_job(self, new_job):
            self.created_job = _make_job(
                job_id="job-generated",
                org_id=new_job.org_id,
                status="accepted",
                message_count=new_job.message_count,
                messages_json=new_job.messages_json,
            )
            return self.created_job

        async def get_conversation_job(self, job_id, org_id):
            return self.job_to_return

    return FakeStore()


@pytest.fixture
def client(fake_store, mock_auth):
    """Create test client with mocked dependencies."""
    from lore.server.auth import get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.conversations import router

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_auth_context] = lambda: mock_auth
    app.dependency_overrides[get_store] = lambda: fake_store

    with patch("lore.server.routes.conversations.require_role", return_value=lambda: mock_auth):
        with patch("lore.server.routes.conversations.conversations_service.process_job_async", new=AsyncMock()):
            yield TestClient(app), fake_store


class TestConversationEndpoints:
    def test_post_returns_202(self, client):
        test_client, store = client
        resp = test_client.post("/v1/conversations", json={
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
        })
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "accepted"
        assert data["message_count"] == 2

    def test_post_empty_messages_returns_400(self, client):
        test_client, store = client
        resp = test_client.post("/v1/conversations", json={
            "messages": [],
        })
        assert resp.status_code == 400

    def test_post_missing_role_returns_422(self, client):
        test_client, store = client
        resp = test_client.post("/v1/conversations", json={
            "messages": [{"content": "no role"}],
        })
        assert resp.status_code in (400, 422)

    def test_get_not_found(self, client):
        test_client, store = client
        store.job_to_return = None
        resp = test_client.get("/v1/conversations/nonexistent-id")
        assert resp.status_code == 404

    def test_get_returns_status(self, client):
        test_client, store = client
        store.job_to_return = _make_job(
            job_id="job-001",
            status="completed",
            message_count=3,
            memories_extracted=2,
            memory_ids=["mem-1", "mem-2"],
            duplicates_skipped=1,
            processing_time_ms=1500,
        )
        resp = test_client.get("/v1/conversations/job-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "job-001"
        assert data["status"] == "completed"
        assert data["memories_extracted"] == 2
        assert data["memory_ids"] == ["mem-1", "mem-2"]
        assert data["duplicates_skipped"] == 1
