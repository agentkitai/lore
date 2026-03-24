"""Tests for the /ready readiness probe endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from lore.server.app import app


@pytest_asyncio.fixture
async def client():
    # Reset pgvector cache between tests so each test controls its own state
    import lore.server.app as app_mod
    app_mod._pgvector_available = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_ready_no_pool(client):
    """When DB pool is not initialized, /ready returns 503."""
    import lore.server.db as db_mod
    original = db_mod._pool
    db_mod._pool = None
    try:
        resp = await client.get("/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["db"] is False
        assert data["checks"]["pgvector"] is False
    finally:
        db_mod._pool = original


@pytest.mark.asyncio
async def test_ready_healthy(client):
    """When DB and pgvector are available, /ready returns 200."""
    mock_conn = AsyncMock()
    # First call: SELECT 1, second call: pgvector check
    mock_conn.fetchval = AsyncMock(side_effect=[1, True])
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    import lore.server.db as db_mod
    original = db_mod._pool
    db_mod._pool = mock_pool
    try:
        resp = await client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["checks"]["db"] is True
        assert data["checks"]["pgvector"] is True
    finally:
        db_mod._pool = original


@pytest.mark.asyncio
async def test_ready_db_up_no_pgvector(client):
    """When DB is up but pgvector is not installed, /ready returns 503."""
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(side_effect=[1, False])
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    import lore.server.db as db_mod
    original = db_mod._pool
    db_mod._pool = mock_pool
    try:
        resp = await client.get("/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["db"] is True
        assert data["checks"]["pgvector"] is False
    finally:
        db_mod._pool = original


@pytest.mark.asyncio
async def test_ready_db_error(client):
    """When DB query throws, /ready returns 503."""
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(side_effect=Exception("connection refused"))
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    import lore.server.db as db_mod
    original = db_mod._pool
    db_mod._pool = mock_pool
    try:
        resp = await client.get("/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
    finally:
        db_mod._pool = original
