"""Tests for OIDC JWT validation module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("jwt")

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from lore.server.oidc import OidcValidator

# ── RSA key fixtures ───────────────────────────────────────────────


@pytest.fixture
def rsa_private_key():
    """Generate an RSA private key for testing."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def rsa_public_key(rsa_private_key):
    return rsa_private_key.public_key()


@pytest.fixture
def rsa_public_key_pem(rsa_public_key):
    return rsa_public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _make_token(private_key, claims: dict, headers: dict | None = None) -> str:
    """Create a signed JWT."""
    return jwt.encode(claims, private_key, algorithm="RS256", headers=headers or {})


# ── Validator tests ────────────────────────────────────────────────


class TestOidcValidator:
    """Tests for OidcValidator."""

    def test_validate_valid_token(self, rsa_private_key, rsa_public_key):
        """Valid JWT returns OidcIdentity."""
        validator = OidcValidator(issuer="https://idp.example.com")

        claims = {
            "sub": "user-123",
            "email": "test@example.com",
            "name": "Test User",
            "tenant_id": "org-1",
            "role": "admin",
            "iss": "https://idp.example.com",
            "exp": time.time() + 900,  # 15 min
        }
        token = _make_token(rsa_private_key, claims)

        # Mock the JWKS client to return our key
        mock_signing_key = MagicMock()
        mock_signing_key.key = rsa_public_key
        with patch.object(validator, "_get_signing_key", return_value=mock_signing_key):
            result = validator.validate(token)

        assert result is not None
        assert result.sub == "user-123"
        assert result.email == "test@example.com"
        assert result.org_id == "org-1"
        assert result.role == "admin"

    def test_validate_expired_token(self, rsa_private_key, rsa_public_key):
        """Expired JWT returns None."""
        validator = OidcValidator(issuer="https://idp.example.com")

        claims = {
            "sub": "user-123",
            "iss": "https://idp.example.com",
            "exp": time.time() - 100,  # expired
        }
        token = _make_token(rsa_private_key, claims)

        mock_signing_key = MagicMock()
        mock_signing_key.key = rsa_public_key
        with patch.object(validator, "_get_signing_key", return_value=mock_signing_key):
            result = validator.validate(token)

        assert result is None

    def test_validate_bad_issuer(self, rsa_private_key, rsa_public_key):
        """Wrong issuer returns None."""
        validator = OidcValidator(issuer="https://idp.example.com")

        claims = {
            "sub": "user-123",
            "iss": "https://evil.example.com",
            "exp": time.time() + 900,
        }
        token = _make_token(rsa_private_key, claims)

        mock_signing_key = MagicMock()
        mock_signing_key.key = rsa_public_key
        with patch.object(validator, "_get_signing_key", return_value=mock_signing_key):
            result = validator.validate(token)

        assert result is None

    def test_validate_missing_sub(self, rsa_private_key, rsa_public_key):
        """Missing sub claim returns None."""
        validator = OidcValidator(issuer="https://idp.example.com")

        claims = {
            "iss": "https://idp.example.com",
            "exp": time.time() + 900,
        }
        token = _make_token(rsa_private_key, claims)

        mock_signing_key = MagicMock()
        mock_signing_key.key = rsa_public_key
        with patch.object(validator, "_get_signing_key", return_value=mock_signing_key):
            result = validator.validate(token)

        assert result is None

    def test_validate_audience_check(self, rsa_private_key, rsa_public_key):
        """When audience is set, tokens without it are rejected."""
        validator = OidcValidator(issuer="https://idp.example.com", audience="lore-api")

        claims = {
            "sub": "user-123",
            "iss": "https://idp.example.com",
            "aud": "wrong-audience",
            "exp": time.time() + 900,
        }
        token = _make_token(rsa_private_key, claims)

        mock_signing_key = MagicMock()
        mock_signing_key.key = rsa_public_key
        with patch.object(validator, "_get_signing_key", return_value=mock_signing_key):
            result = validator.validate(token)

        assert result is None

    def test_validate_audience_matches(self, rsa_private_key, rsa_public_key):
        """When audience matches, token is accepted."""
        validator = OidcValidator(issuer="https://idp.example.com", audience="lore-api")

        claims = {
            "sub": "user-123",
            "iss": "https://idp.example.com",
            "aud": "lore-api",
            "tenant_id": "org-1",
            "exp": time.time() + 900,
        }
        token = _make_token(rsa_private_key, claims)

        mock_signing_key = MagicMock()
        mock_signing_key.key = rsa_public_key
        with patch.object(validator, "_get_signing_key", return_value=mock_signing_key):
            result = validator.validate(token)

        assert result is not None
        assert result.sub == "user-123"

    def test_default_role_is_viewer(self, rsa_private_key, rsa_public_key):
        """Missing role claim defaults to viewer."""
        validator = OidcValidator(issuer="https://idp.example.com")

        claims = {
            "sub": "user-123",
            "iss": "https://idp.example.com",
            "tenant_id": "org-1",
            "exp": time.time() + 900,
        }
        token = _make_token(rsa_private_key, claims)

        mock_signing_key = MagicMock()
        mock_signing_key.key = rsa_public_key
        with patch.object(validator, "_get_signing_key", return_value=mock_signing_key):
            result = validator.validate(token)

        assert result is not None
        assert result.role == "viewer"

    def test_custom_claims(self, rsa_private_key, rsa_public_key):
        """Custom claim names are respected."""
        validator = OidcValidator(
            issuer="https://idp.example.com",
            role_claim="custom_role",
            org_claim="custom_org",
        )

        claims = {
            "sub": "user-123",
            "iss": "https://idp.example.com",
            "custom_org": "my-org",
            "custom_role": "writer",
            "exp": time.time() + 900,
        }
        token = _make_token(rsa_private_key, claims)

        mock_signing_key = MagicMock()
        mock_signing_key.key = rsa_public_key
        with patch.object(validator, "_get_signing_key", return_value=mock_signing_key):
            result = validator.validate(token)

        assert result is not None
        assert result.org_id == "my-org"
        assert result.role == "writer"

    def test_signing_key_none_returns_none(self):
        """If signing key retrieval fails, returns None."""
        validator = OidcValidator(issuer="https://idp.example.com")
        with patch.object(validator, "_get_signing_key", return_value=None):
            result = validator.validate("some.jwt.token")
        assert result is None

    def test_idp_unreachable_returns_none(self):
        """IdP unreachability is handled gracefully (fail-open)."""
        validator = OidcValidator(issuer="https://idp.example.com")
        with patch.object(validator, "_get_signing_key", side_effect=ConnectionError("timeout")):
            result = validator.validate("some.jwt.token")
        assert result is None

    def test_cache_bust_on_miss(self):
        """JWKS cache-bust-on-miss: force re-fetch when kid not found."""
        from jwt import PyJWKClientError
        validator = OidcValidator(issuer="https://idp.example.com")
        validator._last_force_fetch = 0.0  # ensure not throttled

        # Replace _jwk_client entirely to avoid version-dependent behavior
        mock_client = MagicMock()
        mock_key = MagicMock()
        mock_client.get_signing_key_from_jwt = MagicMock(
            side_effect=[PyJWKClientError("kid not found"), mock_key]
        )
        mock_client.get_jwk_set = MagicMock()
        validator._jwk_client = mock_client

        result = validator._get_signing_key("some.token")
        assert result == mock_key
        mock_client.get_jwk_set.assert_called_once_with(refresh=True)

    def test_cache_bust_throttled(self):
        """Cache bust is throttled to once per minute."""
        from jwt import PyJWKClientError
        validator = OidcValidator(issuer="https://idp.example.com")
        validator._last_force_fetch = time.monotonic()  # just fetched

        validator._jwk_client.get_signing_key_from_jwt = MagicMock(
            side_effect=PyJWKClientError("kid not found")
        )

        result = validator._get_signing_key("some.token")
        assert result is None  # throttled, returns None
