"""Tests for the health endpoint — no database required."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from lore.server.app import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "openbrain"}
