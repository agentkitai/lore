"""Tests for JWT dual-auth in get_auth_context."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from lore.server.app import app
from lore.server.auth import _reset_oidc_validator
from lore.server.config import Settings


@pytest_asyncio.fixture
async def client():
    from lore.server.auth import _key_cache, _last_used_updates
    _key_cache.clear()
    _last_used_updates.clear()
    _reset_oidc_validator()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()
    _reset_oidc_validator()


def _make_mock_pool():
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchval = AsyncMock(return_value=0)
    mock_conn.execute = AsyncMock()

    mock_pool = AsyncMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=mock_conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=acm)
    return mock_pool


RAW_KEY = "lore_sk_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()


def _valid_key_row():
    return {
        "id": "key-1",
        "org_id": "org-1",
        "project": None,
        "is_root": True,
        "revoked_at": None,
        "key_hash": KEY_HASH,
        "role": "admin",
    }


# ── API key rejected in oidc-required mode ─────────────────────────


@pytest.mark.asyncio
async def test_api_key_rejected_in_oidc_required_mode(client):
    """API keys are rejected when AUTH_MODE=oidc-required."""
    mock_pool = _make_mock_pool()
    settings_patch = Settings(auth_mode="oidc-required", oidc_issuer="https://idp.example.com")

    with patch("lore.server.auth.settings", settings_patch), \
         patch("lore.server.auth.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/memories",
            headers={"Authorization": f"Bearer {RAW_KEY}"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "api_key_not_allowed"


# ── JWT rejected in api-key-only mode ──────────────────────────────


@pytest.mark.asyncio
async def test_jwt_rejected_in_api_key_only_mode(client):
    """JWTs are rejected when AUTH_MODE=api-key-only."""
    settings_patch = Settings(auth_mode="api-key-only")

    with patch("lore.server.auth.settings", settings_patch):
        resp = await client.get(
            "/v1/memories",
            headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.fake.token"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_api_key"


# ── JWT with valid identity in dual mode ───────────────────────────


@pytest.mark.asyncio
async def test_jwt_valid_in_dual_mode(client):
    """Valid JWT works in dual mode."""
    from lore.server.oidc import OidcIdentity

    mock_identity = OidcIdentity(
        sub="user-123", email="test@example.com", name="Test",
        org_id="org-1", role="admin",
    )
    mock_validator = MagicMock()
    mock_validator.validate = MagicMock(return_value=mock_identity)

    settings_patch = Settings(auth_mode="dual", oidc_issuer="https://idp.example.com")
    mock_pool = _make_mock_pool()

    with patch("lore.server.auth.settings", settings_patch), \
         patch("lore.server.auth.get_oidc_validator", return_value=mock_validator), \
         patch("lore.server.routes.memories.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/memories",
            headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.fake.token"},
        )
    assert resp.status_code == 200


# ── JWT missing org claim ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_jwt_missing_org_claim(client):
    """JWT without org claim gets 403."""
    from lore.server.oidc import OidcIdentity

    mock_identity = OidcIdentity(
        sub="user-123", email="test@example.com", name="Test",
        org_id=None, role="admin",  # no org
    )
    mock_validator = MagicMock()
    mock_validator.validate = MagicMock(return_value=mock_identity)

    settings_patch = Settings(auth_mode="dual", oidc_issuer="https://idp.example.com")

    with patch("lore.server.auth.settings", settings_patch), \
         patch("lore.server.auth.get_oidc_validator", return_value=mock_validator):
        resp = await client.get(
            "/v1/memories",
            headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.fake.token"},
        )
    assert resp.status_code == 403
    assert resp.json()["error"] == "missing_org_claim"


# ── Invalid JWT returns 401 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_jwt_invalid_returns_401(client):
    """Invalid JWT returns 401."""
    mock_validator = MagicMock()
    mock_validator.validate = MagicMock(return_value=None)

    settings_patch = Settings(auth_mode="dual", oidc_issuer="https://idp.example.com")

    with patch("lore.server.auth.settings", settings_patch), \
         patch("lore.server.auth.get_oidc_validator", return_value=mock_validator):
        resp = await client.get(
            "/v1/memories",
            headers={"Authorization": "Bearer bad.jwt.token"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_token"


# ── API key still works in dual mode ──────────────────────────────


@pytest.mark.asyncio
async def test_api_key_works_in_dual_mode(client):
    """API keys still work in dual mode (backward compat)."""
    row = _valid_key_row()
    mock_pool = _make_mock_pool()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=row)
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchval = AsyncMock(return_value=0)
    mock_conn.execute = AsyncMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=mock_conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=acm)

    settings_patch = Settings(auth_mode="dual", oidc_issuer="https://idp.example.com")

    with patch("lore.server.auth.settings", settings_patch), \
         patch("lore.server.auth.get_pool", return_value=mock_pool), \
         patch("lore.server.routes.memories.get_pool", return_value=mock_pool):
        resp = await client.get(
            "/v1/memories",
            headers={"Authorization": f"Bearer {RAW_KEY}"},
        )
    assert resp.status_code == 200


# ── OIDC not configured returns 401 ───────────────────────────────


@pytest.mark.asyncio
async def test_jwt_without_oidc_configured(client):
    """JWT attempt without OIDC configured returns 401."""
    settings_patch = Settings(auth_mode="dual", oidc_issuer=None)

    with patch("lore.server.auth.settings", settings_patch):
        resp = await client.get(
            "/v1/memories",
            headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.fake.token"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "oidc_not_configured"


# ── JWT role mapping ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jwt_unknown_role_defaults_to_reader(client):
    """Unknown role in JWT claim defaults to reader."""
    from lore.server.oidc import OidcIdentity

    mock_identity = OidcIdentity(
        sub="user-123", email="test@example.com", name="Test",
        org_id="org-1", role="unknown_role",
    )
    mock_validator = MagicMock()
    mock_validator.validate = MagicMock(return_value=mock_identity)

    settings_patch = Settings(auth_mode="dual", oidc_issuer="https://idp.example.com")

    with patch("lore.server.auth.settings", settings_patch), \
         patch("lore.server.auth.get_oidc_validator", return_value=mock_validator):
        # Reader can't create memories
        resp = await client.post(
            "/v1/memories",
            json={"content": "test memory"},
            headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.fake.token"},
        )
    assert resp.status_code == 403
