"""API key + OIDC JWT authentication for FastAPI."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

try:
    from fastapi import Depends, HTTPException, Request
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.server.config import settings
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

# ── Auth context ───────────────────────────────────────────────────


@dataclass
class AuthContext:
    """Resolved authentication context injected into endpoints."""

    org_id: str
    project: Optional[str]
    is_root: bool
    key_id: str
    role: str = "admin"  # default for backward compat with existing API keys


class AuthError(HTTPException):
    """Auth error that returns {"error": "code"} directly."""

    def __init__(self, error_code: str, status_code: int = 401):
        super().__init__(status_code=status_code, detail=error_code)
        self.error_code = error_code


# ── RBAC ───────────────────────────────────────────────────────────

# Role hierarchy: reader < writer < admin
ROLE_PERMISSIONS: Dict[str, set] = {
    "reader": {"lessons:read", "lessons:search"},
    "writer": {"lessons:read", "lessons:search", "lessons:write", "lessons:rate"},
    "admin": {"lessons:read", "lessons:search", "lessons:write", "lessons:rate", "keys:manage"},
}


def require_role(*roles: str):
    """FastAPI dependency that checks the caller has one of the given roles.

    Usage:
        @app.post("/v1/lessons")
        async def create(auth: AuthContext = Depends(require_role("writer", "admin"))):
            ...
    """
    async def _check(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if auth.role not in roles:
            raise AuthError("insufficient_role", status_code=403)
        return auth
    return _check


# ── In-memory cache ────────────────────────────────────────────────

# Cache: key_hash -> (row_dict, monotonic_timestamp)
_key_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
CACHE_TTL_SECONDS = 60.0
CACHE_MAX_SIZE = 10_000

# Debounced last_used_at updates: key_id -> monotonic_timestamp of last fire
_last_used_updates: Dict[str, float] = {}
LAST_USED_DEBOUNCE_SECONDS = 60.0


# ── OIDC validator (lazy init) ─────────────────────────────────────

_oidc_validator = None


def get_oidc_validator():
    """Lazy-init the OIDC validator from settings."""
    global _oidc_validator
    if _oidc_validator is not None:
        return _oidc_validator
    if not settings.oidc_issuer:
        return None
    from lore.server.oidc import OidcValidator
    _oidc_validator = OidcValidator(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        role_claim=settings.oidc_role_claim,
        org_claim=settings.oidc_org_claim,
    )
    return _oidc_validator


def _reset_oidc_validator():
    """Reset for testing."""
    global _oidc_validator
    _oidc_validator = None


# ── Helpers ────────────────────────────────────────────────────────


def _auth_error(error_code: str, status: int = 401) -> AuthError:
    """Create a consistent auth error."""
    return AuthError(error_code=error_code, status_code=status)


def _map_api_key_role(is_root: bool, db_role: Optional[str] = None) -> str:
    """Map API key properties to a role string.

    - Explicit role column takes precedence
    - Fallback: is_root=true → admin, is_root=false → writer
    """
    if db_role:
        return db_role
    return "admin" if is_root else "writer"


# ── Main auth dependency ───────────────────────────────────────────


async def get_auth_context(request: Request) -> AuthContext:
    """FastAPI dependency: validate API key or JWT and return AuthContext.

    Behavior depends on AUTH_MODE:
    - "api-key-only" (default): only lore_sk_ keys accepted
    - "dual": both API keys and JWTs accepted
    - "oidc-required": only JWTs accepted
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise _auth_error("missing_api_key")

    token = auth_header[7:]
    mode = settings.auth_mode

    # API key path — accept both ob_sk_ and lore_sk_ prefixes
    if token.startswith("lore_sk_") or token.startswith("ob_sk_"):
        if mode == "oidc-required":
            raise _auth_error("api_key_not_allowed", 401)
        return await _resolve_api_key(token)

    # JWT path
    if mode == "api-key-only":
        raise _auth_error("invalid_api_key")

    return await _resolve_jwt(token)


async def _resolve_jwt(token: str) -> AuthContext:
    """Validate a JWT and return AuthContext."""
    validator = get_oidc_validator()
    if validator is None:
        raise _auth_error("oidc_not_configured", 401)

    identity = validator.validate(token)
    if identity is None:
        raise _auth_error("invalid_token", 401)

    # Org resolution (QA: G5)
    if not identity.org_id:
        raise _auth_error("missing_org_claim", 403)

    # Map role: use the claim value, validate it's known
    role = identity.role if identity.role in ROLE_PERMISSIONS else "reader"

    return AuthContext(
        org_id=identity.org_id,
        project=None,  # JWT users see all projects
        is_root=(role == "admin"),
        key_id=f"oidc:{identity.sub}",
        role=role,
    )


async def _resolve_api_key(raw_key: str) -> AuthContext:
    """Validate an API key and return AuthContext (existing logic)."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    # Check cache
    cached = _key_cache.get(key_hash)
    if cached is not None:
        row, cached_at = cached
        if time.monotonic() - cached_at < CACHE_TTL_SECONDS:
            return _validate_row(row)

    # DB lookup
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, org_id, project, is_root, revoked_at, key_hash, role
               FROM api_keys WHERE key_hash = $1""",
            key_hash,
        )

    if row is None:
        raise _auth_error("invalid_api_key")

    row_dict = dict(row)

    # Timing-safe comparison
    if not hmac.compare_digest(row_dict["key_hash"], key_hash):
        raise _auth_error("invalid_api_key")

    # Cache (with size limit)
    if len(_key_cache) >= CACHE_MAX_SIZE:
        sorted_keys = sorted(_key_cache, key=lambda k: _key_cache[k][1])
        for k in sorted_keys[: len(sorted_keys) // 2]:
            del _key_cache[k]
    _key_cache[key_hash] = (row_dict, time.monotonic())

    ctx = _validate_row(row_dict)
    _maybe_update_last_used(ctx.key_id)
    return ctx


def _validate_row(row: Dict[str, Any]) -> AuthContext:
    """Check revocation and build AuthContext from a DB row dict."""
    if row.get("revoked_at") is not None:
        raise _auth_error("key_revoked")

    is_root = row.get("is_root", False)
    role = _map_api_key_role(is_root, row.get("role"))

    return AuthContext(
        org_id=row["org_id"],
        project=row.get("project"),
        is_root=is_root,
        key_id=row["id"],
        role=role,
    )


def _maybe_update_last_used(key_id: str) -> None:
    """Schedule a debounced last_used_at update."""
    now = time.monotonic()
    last = _last_used_updates.get(key_id, 0.0)
    if now - last < LAST_USED_DEBOUNCE_SECONDS:
        return

    _last_used_updates[key_id] = now

    async def _do_update() -> None:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE api_keys SET last_used_at = now() WHERE id = $1",
                    key_id,
                )
        except Exception:
            logger.debug("Failed to update last_used_at for key %s", key_id, exc_info=True)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_update())
    except RuntimeError:
        pass
