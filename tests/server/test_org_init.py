"""Tests for org init endpoint — uses mocked database."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from lore.server.app import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _make_mock_pool(fetchval_return=None):
    """Create a mock asyncpg pool with context-manager connection."""
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=fetchval_return)
    mock_conn.execute = AsyncMock()

    # Mock transaction context manager
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


@pytest.mark.asyncio
async def test_org_init_creates_org(client):
    mock_pool, mock_conn = _make_mock_pool(fetchval_return=None)

    with patch("lore.server.app.get_pool", return_value=mock_pool):
        resp = await client.post("/v1/org/init", json={"name": "Test Org"})

    assert resp.status_code == 201
    data = resp.json()
    assert data["api_key"].startswith("lore_sk_")
    assert len(data["api_key"]) == 8 + 32  # "lore_sk_" + 32 hex chars
    assert data["key_prefix"] == data["api_key"][:15]
    assert "org_id" in data
    assert mock_conn.execute.call_count == 2


@pytest.mark.asyncio
async def test_org_init_conflict_when_exists(client):
    mock_pool, mock_conn = _make_mock_pool(fetchval_return="existing-org-id")

    with patch("lore.server.app.get_pool", return_value=mock_pool):
        resp = await client.post("/v1/org/init", json={"name": "Test Org"})

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_org_init_requires_name(client):
    mock_pool, _ = _make_mock_pool()

    with patch("lore.server.app.get_pool", return_value=mock_pool):
        resp = await client.post("/v1/org/init", json={})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_org_init_api_key_format(client):
    """Verify API key has correct format and hash properties."""
    import hashlib

    mock_pool, mock_conn = _make_mock_pool(fetchval_return=None)

    with patch("lore.server.app.get_pool", return_value=mock_pool):
        resp = await client.post("/v1/org/init", json={"name": "Test"})

    data = resp.json()
    key = data["api_key"]

    # Key format: lore_sk_ + 32 hex chars
    assert key.startswith("lore_sk_")
    hex_part = key[8:]
    assert len(hex_part) == 32
    int(hex_part, 16)  # Should not raise — valid hex

    # Verify the stored hash matches
    expected_hash = hashlib.sha256(key.encode()).hexdigest()
    # The second execute call is the api_key INSERT
    insert_call = mock_conn.execute.call_args_list[1]
    stored_hash = insert_call[0][4]  # $4 = key_hash (args: sql, id, org_id, name, key_hash, prefix)
    assert stored_hash == expected_hash
