"""Tests for lesson CRUD endpoints — uses mocked database."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from lore.server.app import app
from lore.server.auth import _key_cache, _last_used_updates

# ── Fixtures ───────────────────────────────────────────────────────

RAW_KEY = "lore_sk_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()
ORG_ID = "org-001"
HEADERS = {"Authorization": f"Bearer {RAW_KEY}"}

SAMPLE_EMBEDDING = [0.1] * 384

KEY_ROW = {
    "id": "key-001",
    "org_id": ORG_ID,
    "project": None,
    "is_root": True,
    "revoked_at": None,
    "key_hash": KEY_HASH,
}

PROJECT_KEY_ROW = {
    **KEY_ROW,
    "id": "key-002",
    "project": "backend",
    "is_root": False,
}

NOW = datetime.now(timezone.utc)


def _lesson_row(
    lesson_id: str = "lesson-001",
    project: str = None,
    **overrides,
) -> dict:
    base = {
        "id": lesson_id,
        "org_id": ORG_ID,
        "problem": "test problem",
        "resolution": "test resolution",
        "context": None,
        "tags": json.dumps(["tag1"]),
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


def _make_mock_pool(
    key_row=None,
    fetchrow_return=None,
    fetch_return=None,
    fetchval_return=None,
    execute_return="DELETE 1",
):
    """Create a mock pool with auth key lookup + lesson operations."""
    mock_conn = AsyncMock()

    # fetchrow calls: first for auth, then for lesson operations
    fetchrow_results = []
    if key_row is not None:
        fetchrow_results.append(key_row)
    if fetchrow_return is not None:
        fetchrow_results.append(fetchrow_return)

    if fetchrow_results:
        mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_results)
    else:
        mock_conn.fetchrow = AsyncMock(return_value=None)

    mock_conn.fetch = AsyncMock(return_value=fetch_return or [])
    mock_conn.fetchval = AsyncMock(return_value=fetchval_return)
    mock_conn.execute = AsyncMock(return_value=execute_return)

    # Transaction context manager
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    mock_pool = AsyncMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=mock_conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=acm)

    return mock_pool, mock_conn


@pytest_asyncio.fixture
async def client():
    _key_cache.clear()
    _last_used_updates.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()


# ── Create Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_lesson(client):
    mock_pool, mock_conn = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons",
            headers=HEADERS,
            json={
                "problem": "test problem",
                "resolution": "test resolution",
                "embedding": SAMPLE_EMBEDDING,
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data


@pytest.mark.asyncio
async def test_create_lesson_missing_fields(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons",
            headers=HEADERS,
            json={"problem": "only problem"},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_lesson_invalid_embedding_size(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons",
            headers=HEADERS,
            json={
                "problem": "test",
                "resolution": "test",
                "embedding": [0.1] * 100,
            },
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_lesson_empty_problem(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons",
            headers=HEADERS,
            json={
                "problem": "",
                "resolution": "test",
                "embedding": SAMPLE_EMBEDDING,
            },
        )

    assert resp.status_code == 422


# ── Get Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_lesson(client):
    row = _lesson_row()
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW, fetchrow_return=row)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/lessons/lesson-001", headers=HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "lesson-001"
    assert data["problem"] == "test problem"
    assert "embedding" not in data


@pytest.mark.asyncio
async def test_get_lesson_not_found(client):
    mock_pool, mock_conn = _make_mock_pool(key_row=KEY_ROW)
    # After auth fetchrow, lesson fetchrow returns None
    mock_conn.fetchrow = AsyncMock(side_effect=[KEY_ROW, None])

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/lessons/nonexistent", headers=HEADERS)

    assert resp.status_code == 404


# ── Update Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_lesson_confidence(client):
    updated_row = _lesson_row(confidence=0.9)
    mock_pool, mock_conn = _make_mock_pool(key_row=KEY_ROW, fetchrow_return=updated_row)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.patch(
            "/v1/lessons/lesson-001",
            headers=HEADERS,
            json={"confidence": 0.9},
        )

    assert resp.status_code == 200
    assert resp.json()["confidence"] == 0.9


@pytest.mark.asyncio
async def test_update_lesson_atomic_upvote(client):
    updated_row = _lesson_row(upvotes=1)
    mock_pool, mock_conn = _make_mock_pool(key_row=KEY_ROW, fetchrow_return=updated_row)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.patch(
            "/v1/lessons/lesson-001",
            headers=HEADERS,
            json={"upvotes": "+1"},
        )

    assert resp.status_code == 200
    assert resp.json()["upvotes"] == 1


@pytest.mark.asyncio
async def test_update_lesson_no_fields(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.patch(
            "/v1/lessons/lesson-001",
            headers=HEADERS,
            json={},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_lesson_not_found(client):
    mock_pool, mock_conn = _make_mock_pool(key_row=KEY_ROW)
    mock_conn.fetchrow = AsyncMock(side_effect=[KEY_ROW, None])

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.patch(
            "/v1/lessons/lesson-001",
            headers=HEADERS,
            json={"confidence": 0.9},
        )

    assert resp.status_code == 404


# ── Delete Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_lesson(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW, execute_return="DELETE 1")

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.delete("/v1/lessons/lesson-001", headers=HEADERS)

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_lesson_not_found(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW, execute_return="DELETE 0")

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.delete("/v1/lessons/nonexistent", headers=HEADERS)

    assert resp.status_code == 404


# ── List Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_lessons(client):
    rows = [_lesson_row("lesson-001"), _lesson_row("lesson-002")]
    mock_pool, mock_conn = _make_mock_pool(
        key_row=KEY_ROW, fetch_return=rows, fetchval_return=2
    )

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/lessons", headers=HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["lessons"]) == 2
    assert data["limit"] == 50
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_list_lessons_pagination(client):
    mock_pool, _ = _make_mock_pool(
        key_row=KEY_ROW, fetch_return=[], fetchval_return=0
    )

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/lessons?limit=10&offset=20", headers=HEADERS
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] == 10
    assert data["offset"] == 20


@pytest.mark.asyncio
async def test_list_lessons_limit_exceeds_max(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW, fetchval_return=0)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/lessons?limit=500", headers=HEADERS)

    assert resp.status_code == 422


# ── Project Scoping Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_scoped_key_returns_404_for_other_project(client):
    """Project-scoped key should get 404 for lessons in other projects."""
    mock_pool, mock_conn = _make_mock_pool(key_row=PROJECT_KEY_ROW)
    # Auth returns project-scoped key, lesson fetch returns None (scoped query misses)
    mock_conn.fetchrow = AsyncMock(side_effect=[PROJECT_KEY_ROW, None])

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/lessons/lesson-other", headers=HEADERS)

    assert resp.status_code == 404


# ── Export Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_lessons(client):
    rows = [
        {
            **_lesson_row("lesson-001"),
            "embedding": json.dumps(SAMPLE_EMBEDDING),
        }
    ]
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW, fetch_return=rows)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post("/v1/lessons/export", headers=HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lessons"]) == 1
    assert data["lessons"][0]["embedding"] is not None
    assert len(data["lessons"][0]["embedding"]) == 384


# ── Import Tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_lessons(client):
    mock_pool, mock_conn = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/import",
            headers=HEADERS,
            json={
                "lessons": [
                    {
                        "problem": "p1",
                        "resolution": "r1",
                        "embedding": SAMPLE_EMBEDDING,
                    },
                    {
                        "problem": "p2",
                        "resolution": "r2",
                        "embedding": SAMPLE_EMBEDDING,
                    },
                ]
            },
        )

    assert resp.status_code == 200
    assert resp.json()["imported"] == 2


@pytest.mark.asyncio
async def test_import_empty_list(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/import",
            headers=HEADERS,
            json={"lessons": []},
        )

    assert resp.status_code == 200
    assert resp.json()["imported"] == 0


# ── Search Tests ───────────────────────────────────────────────────


def _search_row(lesson_id: str = "lesson-001", score: float = 0.85, **overrides) -> dict:
    """Create a mock row that includes the score column."""
    row = _lesson_row(lesson_id, **overrides)
    row["score"] = score
    return row


@pytest.mark.asyncio
async def test_search_basic(client):
    rows = [_search_row("lesson-001", score=0.85), _search_row("lesson-002", score=0.72)]
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW, fetch_return=rows)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": SAMPLE_EMBEDDING},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lessons"]) == 2
    assert data["lessons"][0]["score"] == 0.85
    assert data["lessons"][1]["score"] == 0.72
    assert data["lessons"][0]["id"] == "lesson-001"


@pytest.mark.asyncio
async def test_search_empty_results(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW, fetch_return=[])

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": SAMPLE_EMBEDDING},
        )

    assert resp.status_code == 200
    assert resp.json()["lessons"] == []


@pytest.mark.asyncio
async def test_search_wrong_embedding_dim(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": [0.1] * 100},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_empty_embedding(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": []},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_with_tags(client):
    rows = [_search_row("lesson-001", score=0.9)]
    mock_pool, mock_conn = _make_mock_pool(key_row=KEY_ROW, fetch_return=rows)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": SAMPLE_EMBEDDING, "tags": ["stripe", "api"]},
        )

    assert resp.status_code == 200
    assert len(resp.json()["lessons"]) == 1
    # Verify tags param was passed in the SQL query
    call_args = mock_conn.fetch.call_args
    assert json.dumps(["stripe", "api"]) in call_args[0]


@pytest.mark.asyncio
async def test_search_with_project(client):
    rows = [_search_row("lesson-001", score=0.8)]
    mock_pool, mock_conn = _make_mock_pool(key_row=KEY_ROW, fetch_return=rows)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": SAMPLE_EMBEDDING, "project": "backend"},
        )

    assert resp.status_code == 200
    # Verify project param was passed
    call_args = mock_conn.fetch.call_args
    assert "backend" in call_args[0]


@pytest.mark.asyncio
async def test_search_project_scoped_key_overrides(client):
    """Project-scoped key should override body project."""
    rows = [_search_row("lesson-001", score=0.7)]
    mock_pool, mock_conn = _make_mock_pool(key_row=PROJECT_KEY_ROW, fetch_return=rows)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": SAMPLE_EMBEDDING, "project": "frontend"},
        )

    assert resp.status_code == 200
    # Should use "backend" from key, not "frontend" from body
    call_args = mock_conn.fetch.call_args
    assert "backend" in call_args[0]


@pytest.mark.asyncio
async def test_search_limit_default(client):
    mock_pool, mock_conn = _make_mock_pool(key_row=KEY_ROW, fetch_return=[])

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": SAMPLE_EMBEDDING},
        )

    assert resp.status_code == 200
    # Default limit is 5 — verify it was passed
    call_args = mock_conn.fetch.call_args
    assert 5 in call_args[0]


@pytest.mark.asyncio
async def test_search_limit_exceeds_max(client):
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": SAMPLE_EMBEDDING, "limit": 100},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_min_confidence_filters(client):
    """Lessons with score below min_confidence should be filtered out."""
    rows = [
        _search_row("lesson-001", score=0.85),
        _search_row("lesson-002", score=0.1),
    ]
    mock_pool, _ = _make_mock_pool(key_row=KEY_ROW, fetch_return=rows)

    with patch("lore.server.routes.lessons.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.post(
            "/v1/lessons/search",
            headers=HEADERS,
            json={"embedding": SAMPLE_EMBEDDING, "min_confidence": 0.5},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["lessons"]) == 1
    assert data["lessons"][0]["score"] == 0.85


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    resp = await client.post(
        "/v1/lessons/search",
        json={"embedding": SAMPLE_EMBEDDING},
    )
    assert resp.status_code == 401


# ── Auth Required ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_endpoints_require_auth(client):
    """All lesson endpoints require authentication."""
    for method, path in [
        ("GET", "/v1/lessons"),
        ("POST", "/v1/lessons"),
        ("GET", "/v1/lessons/some-id"),
        ("PATCH", "/v1/lessons/some-id"),
        ("DELETE", "/v1/lessons/some-id"),
        ("POST", "/v1/lessons/export"),
        ("POST", "/v1/lessons/import"),
        ("POST", "/v1/lessons/search"),
    ]:
        resp = await getattr(client, method.lower())(path)
        assert resp.status_code == 401, f"{method} {path} should require auth"
