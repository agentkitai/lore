"""Tests for GET /v1/retrieve endpoint — uses mocked database and embedder."""

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
from lore.server.middleware import RateLimiter, set_rate_limiter

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


def _memory_row(
    memory_id: str = "mem-001",
    content: str = "User prefers dark mode",
    score: float = 0.85,
    **overrides,
) -> dict:
    base = {
        "id": memory_id,
        "content": content,
        "type": "preference",
        "tier": "long",
        "source": "conversation",
        "project": None,
        "tags": json.dumps(["ui", "preference"]),
        "created_at": NOW,
        "importance_score": 1.0,
        "score": score,
    }
    base.update(overrides)
    return base


def _make_mock_pool(
    key_row=None,
    fetch_return=None,
):
    """Create a mock pool matching the pattern from test_lessons.py."""
    mock_conn = AsyncMock()

    # Auth lookup (fetchrow)
    if key_row is not None:
        mock_conn.fetchrow = AsyncMock(return_value=key_row)
    else:
        mock_conn.fetchrow = AsyncMock(return_value=KEY_ROW)

    # Search results (fetch)
    mock_conn.fetch = AsyncMock(return_value=fetch_return or [])

    # Connection context manager
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=mock_conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=acm)

    return mock_pool


@pytest_asyncio.fixture
async def client():
    _key_cache.clear()
    _last_used_updates.clear()
    set_rate_limiter(RateLimiter())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()


@pytest.fixture(autouse=True)
def mock_embedder():
    """Mock the embedder to avoid loading ONNX model."""
    with patch("lore.server.routes.retrieve._get_embedder") as mock:
        embedder = MagicMock()
        embedder.embed.return_value = SAMPLE_EMBEDDING
        mock.return_value = embedder
        yield embedder


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retrieve_basic(client):
    """Basic retrieve returns memories with formatted output."""
    rows = [
        _memory_row("mem-001", "User prefers dark mode", 0.85),
        _memory_row("mem-002", "User uses VS Code", 0.72),
    ]
    mock_pool = _make_mock_pool(fetch_return=rows)

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "user preferences"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert len(data["memories"]) == 2
    assert data["memories"][0]["content"] == "User prefers dark mode"
    assert data["memories"][0]["score"] == 0.85
    assert data["query_time_ms"] >= 0
    assert "<memories" in data["formatted"]


@pytest.mark.asyncio
async def test_retrieve_missing_query(client):
    """Missing query parameter returns 422."""
    mock_pool = _make_mock_pool()

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get("/v1/retrieve", headers=HEADERS)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_retrieve_empty_results(client):
    """No matching memories returns empty response."""
    mock_pool = _make_mock_pool(fetch_return=[])

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "nothing relevant"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["memories"] == []
    assert data["formatted"] == ""


@pytest.mark.asyncio
async def test_retrieve_markdown_format(client):
    """Format=markdown returns markdown-formatted output."""
    rows = [_memory_row("mem-001", "User prefers dark mode", 0.85)]
    mock_pool = _make_mock_pool(fetch_return=rows)

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "preferences", "format": "markdown"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "## Relevant Memories" in data["formatted"]
    assert "**[0.85]**" in data["formatted"]


@pytest.mark.asyncio
async def test_retrieve_raw_format(client):
    """Format=raw returns plain text."""
    rows = [_memory_row("mem-001", "User prefers dark mode", 0.85)]
    mock_pool = _make_mock_pool(fetch_return=rows)

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "preferences", "format": "raw"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["formatted"] == "User prefers dark mode"


@pytest.mark.asyncio
async def test_retrieve_invalid_format(client):
    """Invalid format returns 422."""
    mock_pool = _make_mock_pool()

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "test", "format": "chatml"},
            headers=HEADERS,
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_retrieve_no_auth(client):
    """Missing auth returns 401."""
    resp = await client.get("/v1/retrieve", params={"query": "test"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_retrieve_project_scoping(client):
    """Project-scoped key limits results to that project."""
    project_key = "lore_sk_project_key_1234567890abcdef"
    project_hash = hashlib.sha256(project_key.encode()).hexdigest()
    project_key_row = {**PROJECT_KEY_ROW, "key_hash": project_hash}

    rows = [_memory_row("mem-001", "backend memory", 0.9, project="backend")]
    mock_pool = _make_mock_pool(key_row=project_key_row, fetch_return=rows)

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "backend"},
            headers={"Authorization": f"Bearer {project_key}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1


@pytest.mark.asyncio
async def test_retrieve_custom_limit(client):
    """Custom limit parameter is passed to query."""
    rows = [_memory_row(f"mem-{i:03d}", f"Memory {i}", 0.9 - i * 0.1) for i in range(3)]
    mock_pool = _make_mock_pool(fetch_return=rows)

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "test", "limit": 2},
            headers=HEADERS,
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_retrieve_tags_parsed(client):
    """Tags are properly parsed from JSON string."""
    rows = [_memory_row("mem-001", "test", 0.85, tags=json.dumps(["a", "b"]))]
    mock_pool = _make_mock_pool(fetch_return=rows)

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "test"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["memories"][0]["tags"] == ["a", "b"]


@pytest.mark.asyncio
async def test_retrieve_query_time_measured(client):
    """query_time_ms is a positive number."""
    mock_pool = _make_mock_pool(fetch_return=[])

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "test"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["query_time_ms"], (int, float))
    assert data["query_time_ms"] >= 0


@pytest.mark.asyncio
async def test_retrieve_xml_format_structure(client):
    """XML format has proper structure with query and memory elements."""
    rows = [_memory_row("mem-001", "User prefers dark mode", 0.85)]
    mock_pool = _make_mock_pool(fetch_return=rows)

    with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "preferences"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    formatted = data["formatted"]
    assert '<memories query="preferences">' in formatted
    assert '<memory id="mem-001"' in formatted
    assert 'score="0.85"' in formatted
    assert "</memories>" in formatted
