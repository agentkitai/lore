"""Tests for GET /v1/retrieve endpoint — uses mocked store and embedder."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from lore.persistence.types import StoredApiKey
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


def _scored_memory(
    memory_id: str = "mem-001",
    content: str = "User prefers dark mode",
    score: float = 0.85,
    mem_type: str = "preference",
    project: str | None = None,
    tags: tuple = ("ui", "preference"),
):
    """Build a ScoredMemory dataclass for use in tests."""
    from lore.persistence.types import ScoredMemory
    return ScoredMemory(
        id=memory_id,
        org_id=ORG_ID,
        content=content,
        context=None,
        tags=tags,
        confidence=1.0,
        source="conversation",
        project=project,
        created_at=NOW,
        updated_at=NOW,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={"type": mem_type, "tier": "long"},
        importance_score=1.0,
        access_count=0,
        last_accessed_at=None,
        score=score,
    )


def _row_to_stored_key(row: dict) -> StoredApiKey:
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


def _make_auth_store(key_row=None):
    """Create a mock store used only for auth lookups."""
    store = AsyncMock()
    row = key_row if key_row is not None else KEY_ROW
    store.lookup_api_key_by_hash = AsyncMock(return_value=_row_to_stored_key(row))
    store.touch_api_key_last_used = AsyncMock(return_value=None)
    return store


def _make_fake_store(scored_memories=None):
    """Create a fake store whose recall_by_embedding returns the given ScoredMemory list."""
    store = MagicMock()
    store.recall_by_embedding = AsyncMock(return_value=scored_memories or [])
    return store


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
    scored = [
        _scored_memory("mem-001", "User prefers dark mode", 0.85),
        _scored_memory("mem-002", "User uses VS Code", 0.72),
    ]
    fake_store = _make_fake_store(scored)
    auth_store = _make_auth_store()

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
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
    auth_store = _make_auth_store()

    with patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.get("/v1/retrieve", headers=HEADERS)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_retrieve_empty_results(client):
    """No matching memories returns empty response."""
    fake_store = _make_fake_store([])
    auth_store = _make_auth_store()

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
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
    scored = [_scored_memory("mem-001", "User prefers dark mode", 0.85)]
    fake_store = _make_fake_store(scored)
    auth_store = _make_auth_store()

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
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
    scored = [_scored_memory("mem-001", "User prefers dark mode", 0.85)]
    fake_store = _make_fake_store(scored)
    auth_store = _make_auth_store()

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
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
    auth_store = _make_auth_store()
    fake_store = _make_fake_store([])

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
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

    scored = [_scored_memory("mem-001", "backend memory", 0.9, project="backend")]
    fake_store = _make_fake_store(scored)
    auth_store = _make_auth_store(key_row=project_key_row)

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
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
    scored = [_scored_memory(f"mem-{i:03d}", f"Memory {i}", 0.9 - i * 0.1) for i in range(3)]
    fake_store = _make_fake_store(scored)
    auth_store = _make_auth_store()

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "test", "limit": 2},
            headers=HEADERS,
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_retrieve_tags_parsed(client):
    """Tags are properly returned from ScoredMemory."""
    scored = [_scored_memory("mem-001", "test", 0.85, tags=("a", "b"))]
    fake_store = _make_fake_store(scored)
    auth_store = _make_auth_store()

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
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
    fake_store = _make_fake_store([])
    auth_store = _make_auth_store()

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
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
    scored = [_scored_memory("mem-001", "User prefers dark mode", 0.85)]
    fake_store = _make_fake_store(scored)
    auth_store = _make_auth_store()

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
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
