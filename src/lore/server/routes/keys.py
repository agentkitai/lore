"""Key management endpoints for Lore Cloud Server."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Response
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence import Store, StoredApiKey
from lore.persistence.exceptions import LastRootKeyError, StoreNotFoundError
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services import keys as keys_service

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


def _to_key_info(k: StoredApiKey) -> KeyInfo:
    return KeyInfo(
        id=k.id,
        name=k.name,
        key_prefix=k.key_prefix,
        project=k.project,
        is_root=k.is_root,
        created_at=k.created_at,
        last_used_at=k.last_used_at,
        revoked=k.revoked_at is not None,
        workspace_id=k.workspace_id,
    )


# ── Create ─────────────────────────────────────────────────────────


@router.post("", response_model=KeyCreateResponse, status_code=201)
async def create_key(
    body: KeyCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> KeyCreateResponse:
    """Create a new API key. Root key required."""
    _require_root(auth)
    stored, raw_key = await keys_service.create_api_key(
        store,
        org_id=auth.org_id,
        name=body.name,
        project=body.project,
        is_root=body.is_root,
        workspace_id=body.workspace_id,
    )
    return KeyCreateResponse(
        id=stored.id,
        key=raw_key,
        name=stored.name,
        project=stored.project,
        workspace_id=stored.workspace_id,
    )


# ── List ───────────────────────────────────────────────────────────


@router.get("", response_model=KeyListResponse)
async def list_keys(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> KeyListResponse:
    """List all API keys for the org. Root key required."""
    _require_root(auth)
    keys = await keys_service.list_api_keys(store, auth.org_id)
    return KeyListResponse(keys=[_to_key_info(k) for k in keys])


# ── Revoke ─────────────────────────────────────────────────────────


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> Response:
    """Revoke an API key. Root key required. Cannot revoke last root key."""
    _require_root(auth)
    try:
        await keys_service.revoke_api_key(store, key_id, auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Key not found")
    except LastRootKeyError:
        raise HTTPException(status_code=400, detail="Cannot revoke the last root key")
    return Response(status_code=204)
