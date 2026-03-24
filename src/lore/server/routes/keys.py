"""Key management endpoints for Lore Cloud Server."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

try:
    from ulid import ULID
except ImportError:
    raise ImportError("python-ulid is required. Install with: pip install python-ulid")

from lore.server.auth import AuthContext, _key_cache, get_auth_context
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/keys", tags=["keys"])

# ── Models ─────────────────────────────────────────────────────────


class KeyCreateRequest(BaseModel):
    name: str
    project: Optional[str] = None
    is_root: bool = False
    workspace_id: Optional[str] = None


class KeyCreateResponse(BaseModel):
    id: str
    key: str
    name: str
    project: Optional[str]
    workspace_id: Optional[str] = None


class KeyInfo(BaseModel):
    id: str
    name: str
    key_prefix: str
    project: Optional[str]
    is_root: bool
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked: bool
    workspace_id: Optional[str] = None


class KeyListResponse(BaseModel):
    keys: List[KeyInfo]


# ── Helpers ────────────────────────────────────────────────────────


def _require_root(auth: AuthContext) -> None:
    """Raise 403 if the caller is not a root key / admin role."""
    if not auth.is_root and auth.role != "admin":
        raise HTTPException(status_code=403, detail="Root key required")


# ── Create ─────────────────────────────────────────────────────────


@router.post("", response_model=KeyCreateResponse, status_code=201)
async def create_key(
    body: KeyCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> KeyCreateResponse:
    """Create a new API key. Root key required."""
    _require_root(auth)

    raw_key = "lore_sk_" + secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:12]
    key_id = str(ULID())

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Check if workspace_id column exists to stay backward-compatible
        has_ws_col = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name='api_keys' AND column_name='workspace_id')"
        )
        if has_ws_col and body.workspace_id:
            await conn.execute(
                """INSERT INTO api_keys (id, org_id, name, key_hash, key_prefix, project, is_root, workspace_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                key_id, auth.org_id, body.name, key_hash, key_prefix,
                body.project, body.is_root, body.workspace_id,
            )
        else:
            await conn.execute(
                """INSERT INTO api_keys (id, org_id, name, key_hash, key_prefix, project, is_root)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                key_id, auth.org_id, body.name, key_hash, key_prefix,
                body.project, body.is_root,
            )

    return KeyCreateResponse(
        id=key_id,
        key=raw_key,
        name=body.name,
        project=body.project,
        workspace_id=body.workspace_id,
    )


# ── List ───────────────────────────────────────────────────────────


@router.get("", response_model=KeyListResponse)
async def list_keys(
    auth: AuthContext = Depends(get_auth_context),
) -> KeyListResponse:
    """List all API keys for the org. Root key required."""
    _require_root(auth)

    pool = await get_pool()
    async with pool.acquire() as conn:
        has_ws_col = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name='api_keys' AND column_name='workspace_id')"
        )
        cols = "id, name, key_prefix, project, is_root, created_at, last_used_at, revoked_at"
        if has_ws_col:
            cols += ", workspace_id"
        rows = await conn.fetch(
            f"SELECT {cols} FROM api_keys WHERE org_id = $1 ORDER BY created_at",
            auth.org_id,
        )

    keys = [
        KeyInfo(
            id=r["id"],
            name=r["name"],
            key_prefix=r["key_prefix"],
            project=r["project"],
            is_root=r["is_root"],
            created_at=r["created_at"],
            last_used_at=r["last_used_at"],
            revoked=r["revoked_at"] is not None,
            workspace_id=r.get("workspace_id"),
        )
        for r in rows
    ]

    return KeyListResponse(keys=keys)


# ── Revoke ─────────────────────────────────────────────────────────


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    """Revoke an API key. Root key required. Cannot revoke last root key."""
    _require_root(auth)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock the target row to prevent race conditions
            target = await conn.fetchrow(
                "SELECT id, is_root, key_hash, revoked_at FROM api_keys "
                "WHERE id = $1 AND org_id = $2 FOR UPDATE",
                key_id,
                auth.org_id,
            )

            if target is None:
                raise HTTPException(status_code=404, detail="Key not found")

            if target["revoked_at"] is not None:
                raise HTTPException(status_code=400, detail="Key already revoked")

            # Protect last root key
            if target["is_root"]:
                active_root_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM api_keys WHERE org_id = $1 AND is_root = TRUE AND revoked_at IS NULL",
                    auth.org_id,
                )
                if active_root_count <= 1:
                    raise HTTPException(status_code=400, detail="Cannot revoke the last root key")

            # Revoke
            await conn.execute(
                "UPDATE api_keys SET revoked_at = $1 WHERE id = $2",
                datetime.now(timezone.utc),
                key_id,
            )

    # Invalidate auth cache for this key's hash
    target_hash = target["key_hash"]
    _key_cache.pop(target_hash, None)
