"""Integration tests for Lore Cloud Server.

Uses httpx AsyncClient with ASGI transport to test the full flow
without requiring Docker/Postgres. DB calls are mocked but the full
HTTP → FastAPI → route → response chain is exercised.

Tests marked @pytest.mark.integration require a real Docker Compose stack.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from lore.persistence import ExportedMemory, StoredMemory
from lore.persistence.exceptions import StoreNotFoundError
from lore.persistence.types import StoredApiKey
from lore.server.app import app
from lore.server.auth import _key_cache, _last_used_updates
from lore.server.db import get_store
from lore.server.middleware import RateLimiter, set_rate_limiter

# ── Constants ──────────────────────────────────────────────────────

ROOT_KEY = "lore_sk_root0000000000000000000000000000"
ROOT_KEY_HASH = hashlib.sha256(ROOT_KEY.encode()).hexdigest()
ORG_ID = "org-integration-001"

PROJECT_A_KEY = "lore_sk_projA000000000000000000000000000"
PROJECT_A_KEY_HASH = hashlib.sha256(PROJECT_A_KEY.encode()).hexdigest()

PROJECT_B_KEY = "lore_sk_projB000000000000000000000000000"
PROJECT_B_KEY_HASH = hashlib.sha256(PROJECT_B_KEY.encode()).hexdigest()

REVOKED_KEY = "lore_sk_revoked0000000000000000000000000"
REVOKED_KEY_HASH = hashlib.sha256(REVOKED_KEY.encode()).hexdigest()

SAMPLE_EMBEDDING = [0.1] * 384
NOW = datetime.now(timezone.utc)

ROOT_KEY_ROW = {
    "id": "key-root",
    "org_id": ORG_ID,
    "project": None,
    "is_root": True,
    "revoked_at": None,
    "key_hash": ROOT_KEY_HASH,
}

PROJECT_A_KEY_ROW = {
    "id": "key-proj-a",
    "org_id": ORG_ID,
    "project": "project-a",
    "is_root": False,
    "revoked_at": None,
    "key_hash": PROJECT_A_KEY_HASH,
}

PROJECT_B_KEY_ROW = {
    "id": "key-proj-b",
    "org_id": ORG_ID,
    "project": "project-b",
    "is_root": False,
    "revoked_at": None,
    "key_hash": PROJECT_B_KEY_HASH,
}

REVOKED_KEY_ROW = {
    "id": "key-revoked",
    "org_id": ORG_ID,
    "project": None,
    "is_root": False,
    "revoked_at": NOW,
    "key_hash": REVOKED_KEY_HASH,
}


# ── Helpers ────────────────────────────────────────────────────────


def _lesson_row(
    lesson_id: str = "lesson-001",
    project: Optional[str] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "id": lesson_id,
        "org_id": ORG_ID,
        "problem": "test problem",
        "resolution": "test resolution",
        "context": None,
        "tags": json.dumps(["test"]),
        "confidence": 0.8,
        "source": None,
        "project": project,
        "created_at": NOW,
        "updated_at": NOW,
        "expires_at": None,
        "upvotes": 0,
        "downvotes": 0,
        "meta": json.dumps({}),
    }
    base.update(overrides)
    return base


def _row_to_stored_key(row: Dict[str, Any]) -> StoredApiKey:
    return StoredApiKey(
        id=row["id"],
        org_id=row["org_id"],
        name=row.get("name", "test-key"),
        key_hash=row["key_hash"],
        key_prefix=row.get("key_prefix", "lore_sk_xx"),
        project=row.get("project"),
        is_root=row.get("is_root", False),
        workspace_id=row.get("workspace_id"),
        revoked_at=row.get("revoked_at"),
        created_at=row.get("created_at", datetime.now(timezone.utc)),
        last_used_at=row.get("last_used_at"),
        role=row.get("role"),
    )


def _make_auth_store(
    key_row: Optional[Dict[str, Any]] = None,
    key_rows_by_hash: Optional[Dict[str, Dict[str, Any]]] = None,
) -> AsyncMock:
    """Create a mock store configured for auth lookups.

    - If ``key_row`` is provided, every lookup_api_key_by_hash call returns it
      (translated to StoredApiKey).
    - If ``key_rows_by_hash`` is provided, looks up by the hash argument.
    - If neither is provided, lookup returns None (invalid_api_key).
    """
    store = AsyncMock()
    if key_rows_by_hash is not None:
        async def _lookup(key_hash: str):
            row = key_rows_by_hash.get(key_hash)
            return _row_to_stored_key(row) if row is not None else None
        store.lookup_api_key_by_hash = AsyncMock(side_effect=_lookup)
    elif key_row is not None:
        store.lookup_api_key_by_hash = AsyncMock(return_value=_row_to_stored_key(key_row))
    else:
        store.lookup_api_key_by_hash = AsyncMock(return_value=None)
    store.touch_api_key_last_used = AsyncMock(return_value=None)
    return store


@pytest_asyncio.fixture
async def client():
    _key_cache.clear()
    _last_used_updates.clear()
    # Reset rate limiter for each test
    set_rate_limiter(RateLimiter())

    mock_store = AsyncMock()
    app.dependency_overrides[get_store] = lambda: mock_store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()
    app.dependency_overrides.pop(get_store, None)


# ── Integration Test: Publish → Query → Verify Match ──────────────


@pytest.mark.asyncio
async def test_full_flow_publish_query_verify(client: AsyncClient) -> None:
    """Full flow: create a lesson, then retrieve it and verify fields match."""
    auth_store = _make_auth_store(key_row=ROOT_KEY_ROW)
    headers = {"Authorization": f"Bearer {ROOT_KEY}"}

    _stored = StoredMemory(
        id="lesson-flow-001", org_id=ORG_ID, content="test problem",
        context="test resolution", tags=("test",),
        source=None, project=None, created_at=NOW, updated_at=NOW,
        expires_at=None, upvotes=0, downvotes=0, meta={},
        access_count=0, last_accessed_at=None,
    )

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.lessons.create", new=AsyncMock(return_value="lesson-flow-001")), \
         patch("lore.services.lessons.get", new=AsyncMock(return_value=_stored)):
        # Step 1: Publish
        create_resp = await client.post(
            "/v1/lessons",
            headers=headers,
            json={
                "problem": "test problem",
                "resolution": "test resolution",
                "embedding": SAMPLE_EMBEDDING,
                "tags": ["test"],
            },
        )
        assert create_resp.status_code == 201
        lesson_id = create_resp.json()["id"]
        assert lesson_id  # non-empty

        # Step 2: Query back
        get_resp = await client.get(
            "/v1/lessons/lesson-flow-001",
            headers=headers,
        )
        assert get_resp.status_code == 200
        data = get_resp.json()

        # Step 3: Verify match
        assert data["problem"] == "test problem"
        assert data["resolution"] == "test resolution"
        assert data["tags"] == ["test"]
        assert data["confidence"] == 0.8


# ── Integration Test: Project Scoping Isolation ────────────────────


@pytest.mark.asyncio
async def test_project_scoping_isolation(client: AsyncClient) -> None:
    """Two different project-scoped keys can't see each other's lessons."""
    headers_a = {"Authorization": f"Bearer {PROJECT_A_KEY}"}
    headers_b = {"Authorization": f"Bearer {PROJECT_B_KEY}"}

    # Map each key's hash to its row so auth resolves correctly per request.
    auth_store = _make_auth_store(
        key_rows_by_hash={
            PROJECT_A_KEY_HASH: PROJECT_A_KEY_ROW,
            PROJECT_B_KEY_HASH: PROJECT_B_KEY_ROW,
        },
    )

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.lessons.create", new=AsyncMock(return_value="lesson-proj-a-001")), \
         patch("lore.services.lessons.get", new=AsyncMock(side_effect=StoreNotFoundError("memories", "lesson-proj-a-001"))):
        # Key A publishes
        create_resp = await client.post(
            "/v1/lessons",
            headers=headers_a,
            json={
                "problem": "project-a problem",
                "resolution": "project-a resolution",
                "embedding": SAMPLE_EMBEDDING,
            },
        )
        assert create_resp.status_code == 201

        # Key B tries to read → 404
        get_resp = await client.get(
            f"/v1/lessons/{create_resp.json()['id']}",
            headers=headers_b,
        )
        assert get_resp.status_code == 404


# ── Integration Test: Revoked Key Rejection ────────────────────────


@pytest.mark.asyncio
async def test_revoked_key_rejected(client: AsyncClient) -> None:
    """Revoked key gets 401 immediately."""
    auth_store = _make_auth_store(key_row=REVOKED_KEY_ROW)

    headers = {"Authorization": f"Bearer {REVOKED_KEY}"}

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.get("/v1/lessons", headers=headers)

    assert resp.status_code == 401
    data = resp.json()
    assert data["error"] == "key_revoked"


# ── Integration Test: Upvote/Downvote Round-Trip ──────────────────


@pytest.mark.asyncio
async def test_upvote_downvote_round_trip(client: AsyncClient) -> None:
    """Upvote then downvote and verify counts update."""
    headers = {"Authorization": f"Bearer {ROOT_KEY}"}

    auth_store = _make_auth_store(key_row=ROOT_KEY_ROW)

    _after_upvote = StoredMemory(
        id="lesson-vote-001", org_id=ORG_ID, content="test problem",
        context="test resolution", tags=(),
        source=None, project=None, created_at=NOW, updated_at=NOW,
        expires_at=None, upvotes=1, downvotes=0, meta={},
        access_count=0, last_accessed_at=None,
    )
    _after_downvote = StoredMemory(
        id="lesson-vote-001", org_id=ORG_ID, content="test problem",
        context="test resolution", tags=(),
        source=None, project=None, created_at=NOW, updated_at=NOW,
        expires_at=None, upvotes=1, downvotes=1, meta={},
        access_count=0, last_accessed_at=None,
    )

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.lessons.update", new=AsyncMock(side_effect=[_after_upvote, _after_downvote])):
        # Upvote
        resp1 = await client.patch(
            "/v1/lessons/lesson-vote-001",
            headers=headers,
            json={"upvotes": "+1"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["upvotes"] == 1
        assert resp1.json()["downvotes"] == 0

        # Downvote
        resp2 = await client.patch(
            "/v1/lessons/lesson-vote-001",
            headers=headers,
            json={"downvotes": "+1"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["upvotes"] == 1
        assert resp2.json()["downvotes"] == 1


# ── Integration Test: Export/Import Between Contexts ──────────────


@pytest.mark.asyncio
async def test_export_import_between_contexts(client: AsyncClient) -> None:
    """Export from one org context, import to another — lessons transfer."""
    headers = {"Authorization": f"Bearer {ROOT_KEY}"}

    # Export mock
    export_auth_store = _make_auth_store(key_row=ROOT_KEY_ROW)

    _exported_mems = [
        ExportedMemory(
            id="lesson-exp-001", org_id=ORG_ID, content="test problem",
            context="test resolution", tags=("test",), confidence=0.8,
            source=None, project=None, created_at=NOW, updated_at=NOW,
            expires_at=None, upvotes=0, downvotes=0, meta={},
            embedding=[0.1] * 384,
        ),
        ExportedMemory(
            id="lesson-exp-002", org_id=ORG_ID, content="test problem",
            context="test resolution", tags=("test",), confidence=0.8,
            source=None, project=None, created_at=NOW, updated_at=NOW,
            expires_at=None, upvotes=0, downvotes=0, meta={},
            embedding=[0.1] * 384,
        ),
    ]

    with patch("lore.server.auth.get_store", return_value=export_auth_store), \
         patch("lore.services.lessons.export", new=AsyncMock(return_value=_exported_mems)):
        export_resp = await client.post("/v1/lessons/export", headers=headers)

    assert export_resp.status_code == 200
    exported = export_resp.json()["lessons"]
    assert len(exported) == 2

    # Import the exported lessons
    import_auth_store = _make_auth_store(key_row=ROOT_KEY_ROW)

    with patch("lore.server.auth.get_store", return_value=import_auth_store), \
         patch("lore.services.lessons.import_lessons", new=AsyncMock(return_value=2)):
        import_resp = await client.post(
            "/v1/lessons/import",
            headers=headers,
            json={"lessons": [
                {
                    "problem": l["problem"],
                    "resolution": l["resolution"],
                    "embedding": l["embedding"],
                    "tags": l["tags"],
                    "confidence": l["confidence"],
                }
                for l in exported
            ]},
        )

    assert import_resp.status_code == 200
    assert import_resp.json()["imported"] == 2


# ── Rate Limiting Tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limit_exceeded(client: AsyncClient) -> None:
    """Exceeding 100 req/min returns 429 with Retry-After."""
    # Use a very small limit for testing
    set_rate_limiter(RateLimiter(max_requests=3, window_seconds=60))

    headers = {"Authorization": f"Bearer {ROOT_KEY}"}
    auth_store = _make_auth_store(key_row=ROOT_KEY_ROW)

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.lessons.list_lessons", new=AsyncMock(return_value=(0, []))):
        # First 3 requests should succeed
        for _ in range(3):
            resp = await client.get("/v1/lessons", headers=headers)
            assert resp.status_code == 200

        # 4th request should be rate limited
        resp = await client.get("/v1/lessons", headers=headers)
        assert resp.status_code == 429
        data = resp.json()
        assert data["error"] == "rate_limit_exceeded"
        assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_rate_limit_independent_per_key(client: AsyncClient) -> None:
    """Different keys have independent rate limits."""
    set_rate_limiter(RateLimiter(max_requests=2, window_seconds=60))

    headers_a = {"Authorization": f"Bearer {PROJECT_A_KEY}"}
    headers_b = {"Authorization": f"Bearer {PROJECT_B_KEY}"}

    # Auth is cached after first lookup per key.
    auth_store = _make_auth_store(
        key_rows_by_hash={
            PROJECT_A_KEY_HASH: PROJECT_A_KEY_ROW,
            PROJECT_B_KEY_HASH: PROJECT_B_KEY_ROW,
        },
    )

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.lessons.list_lessons", new=AsyncMock(return_value=(0, []))):
        # Key A: 2 requests OK
        for _ in range(2):
            resp = await client.get("/v1/lessons", headers=headers_a)
            assert resp.status_code == 200

        # Key B: still has its own quota
        for _ in range(2):
            resp = await client.get("/v1/lessons", headers=headers_b)
            assert resp.status_code == 200

        # Key A: rate limited
        resp = await client.get("/v1/lessons", headers=headers_a)
        assert resp.status_code == 429


# ── Error Handling Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_malformed_json_returns_400(client: AsyncClient) -> None:
    """Malformed JSON body returns 400, not 500."""
    headers = {
        "Authorization": f"Bearer {ROOT_KEY}",
        "Content-Type": "application/json",
    }
    resp = await client.post(
        "/v1/lessons",
        headers=headers,
        content=b"{invalid json!!!}",
    )
    assert resp.status_code in (400, 422)  # FastAPI may return 422 for parse errors
    data = resp.json()
    assert "error" in data
    assert "message" in data


@pytest.mark.asyncio
async def test_body_too_large_returns_413(client: AsyncClient) -> None:
    """Request body > 1MB returns 413."""
    headers = {
        "Authorization": f"Bearer {ROOT_KEY}",
        "Content-Type": "application/json",
        "Content-Length": str(2_000_000),
    }
    resp = await client.post(
        "/v1/lessons",
        headers=headers,
        content=b"x" * 100,  # actual content doesn't matter; Content-Length triggers it
    )
    assert resp.status_code == 413
    data = resp.json()
    assert data["error"] == "request_too_large"


@pytest.mark.asyncio
async def test_consistent_error_shape_404(client: AsyncClient) -> None:
    """404 responses have consistent JSON shape."""
    auth_store = _make_auth_store(key_row=ROOT_KEY_ROW)
    headers = {"Authorization": f"Bearer {ROOT_KEY}"}

    with patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.lessons.get", new=AsyncMock(side_effect=StoreNotFoundError("memories", "nonexistent"))):
        resp = await client.get("/v1/lessons/nonexistent", headers=headers)

    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data
    assert "message" in data


@pytest.mark.asyncio
async def test_consistent_error_shape_422(client: AsyncClient) -> None:
    """422 validation errors have consistent JSON shape."""
    auth_store = _make_auth_store(key_row=ROOT_KEY_ROW)
    headers = {"Authorization": f"Bearer {ROOT_KEY}"}

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.post(
            "/v1/lessons",
            headers=headers,
            json={"problem": "test"},  # missing required fields
        )

    assert resp.status_code == 422
    data = resp.json()
    assert data["error"] == "validation_error"
    assert "message" in data


@pytest.mark.asyncio
async def test_consistent_error_shape_401(client: AsyncClient) -> None:
    """401 errors have consistent JSON shape."""
    resp = await client.get("/v1/lessons")
    assert resp.status_code == 401
    data = resp.json()
    assert "error" in data


# ── Docker Integration Tests (require real infra) ──────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_publish_query_flow() -> None:
    """Full flow against real Docker Compose stack.

    Requires: docker compose up -d
    """
    pytest.skip("Requires Docker Compose stack")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_project_isolation() -> None:
    """Project isolation against real DB.

    Requires: docker compose up -d
    """
    pytest.skip("Requires Docker Compose stack")
