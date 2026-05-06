"""Tests for JWT dual-auth in get_auth_context."""

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
from lore.server.auth import _reset_oidc_validator
from lore.server.config import Settings
from lore.server.db import get_store


@pytest_asyncio.fixture
async def client():
    from lore.server.auth import _key_cache, _last_used_updates
    _key_cache.clear()
    _last_used_updates.clear()
    _reset_oidc_validator()

    mock_store = AsyncMock()
    app.dependency_overrides[get_store] = lambda: mock_store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()
    _reset_oidc_validator()
    app.dependency_overrides.pop(get_store, None)


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
    """Create a mock store configured for auth lookups."""
    store = AsyncMock()
    stored = _row_to_stored_key(key_row) if key_row is not None else None
    store.lookup_api_key_by_hash = AsyncMock(return_value=stored)
    store.touch_api_key_last_used = AsyncMock(return_value=None)
    return store


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
    auth_store = _make_auth_store()
    settings_patch = Settings(auth_mode="oidc-required", oidc_issuer="https://idp.example.com")

    with patch("lore.server.auth.settings", settings_patch), \
         patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.get(
            "/v1/lessons",
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
            "/v1/lessons",
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

    with patch("lore.server.auth.settings", settings_patch), \
         patch("lore.server.auth.get_oidc_validator", return_value=mock_validator), \
         patch("lore.services.lessons.list_lessons", new=AsyncMock(return_value=(0, []))):
        resp = await client.get(
            "/v1/lessons",
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
            "/v1/lessons",
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
            "/v1/lessons",
            headers={"Authorization": "Bearer bad.jwt.token"},
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_token"


# ── API key still works in dual mode ──────────────────────────────


@pytest.mark.asyncio
async def test_api_key_works_in_dual_mode(client):
    """API keys still work in dual mode (backward compat)."""
    row = _valid_key_row()
    auth_store = _make_auth_store(key_row=row)

    settings_patch = Settings(auth_mode="dual", oidc_issuer="https://idp.example.com")

    with patch("lore.server.auth.settings", settings_patch), \
         patch("lore.server.auth.get_store", return_value=auth_store), \
         patch("lore.services.lessons.list_lessons", new=AsyncMock(return_value=(0, []))):
        resp = await client.get(
            "/v1/lessons",
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
            "/v1/lessons",
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
        # Reader can't create lessons
        resp = await client.post(
            "/v1/lessons",
            json={"problem": "test", "resolution": "test"},
            headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.fake.token"},
        )
    assert resp.status_code == 403
