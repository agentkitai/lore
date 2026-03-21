"""Retrieval profiles CRUD — GET/POST/PUT/DELETE /v1/profiles."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/profiles", tags=["profiles"])


class ProfileCreateRequest(BaseModel):
    name: str
    semantic_weight: float = 1.0
    graph_weight: float = 1.0
    recency_bias: float = 30.0
    tier_filters: Optional[List[str]] = None
    min_score: float = 0.3
    max_results: int = 10


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    semantic_weight: Optional[float] = None
    graph_weight: Optional[float] = None
    recency_bias: Optional[float] = None
    tier_filters: Optional[List[str]] = None
    min_score: Optional[float] = None
    max_results: Optional[int] = None


class ProfileResponse(BaseModel):
    id: str
    org_id: str
    name: str
    semantic_weight: float
    graph_weight: float
    recency_bias: float
    tier_filters: Optional[List[str]] = None
    min_score: float
    max_results: int
    is_preset: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _ts(val) -> Optional[str]:
    if val is None:
        return None
    from datetime import datetime
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _row_to_response(row) -> ProfileResponse:
    return ProfileResponse(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        semantic_weight=float(row["semantic_weight"]),
        graph_weight=float(row["graph_weight"]),
        recency_bias=float(row["recency_bias"]),
        tier_filters=list(row["tier_filters"]) if row["tier_filters"] else None,
        min_score=float(row["min_score"]),
        max_results=row["max_results"],
        is_preset=row["is_preset"],
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )


# In-memory cache for profiles (60s TTL)
import time as _time
_profile_cache: Dict[str, tuple] = {}  # key -> (profile_dict, timestamp)
_PROFILE_CACHE_TTL = 60.0


def _get_cached_profile(key: str) -> Optional[Dict[str, Any]]:
    cached = _profile_cache.get(key)
    if cached and _time.monotonic() - cached[1] < _PROFILE_CACHE_TTL:
        return cached[0]
    return None


def _set_cached_profile(key: str, profile: Dict[str, Any]) -> None:
    _profile_cache[key] = (profile, _time.monotonic())


@router.get("", response_model=List[ProfileResponse])
async def list_profiles(
    auth: AuthContext = Depends(get_auth_context),
) -> List[ProfileResponse]:
    """List profiles (org + global presets)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM retrieval_profiles
               WHERE org_id = $1 OR org_id = '__global__'
               ORDER BY is_preset DESC, name""",
            auth.org_id,
        )
    return [_row_to_response(r) for r in rows]


@router.get("/{profile_id}", response_model=ProfileResponse)
async def get_profile(
    profile_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ProfileResponse:
    """Get a specific profile."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM retrieval_profiles
               WHERE id = $1 AND (org_id = $2 OR org_id = '__global__')""",
            profile_id, auth.org_id,
        )
    if not row:
        raise HTTPException(404, "Profile not found")
    return _row_to_response(row)


@router.post("", response_model=ProfileResponse, status_code=201)
async def create_profile(
    body: ProfileCreateRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> ProfileResponse:
    """Create a custom profile."""
    from ulid import ULID
    profile_id = str(ULID())
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """INSERT INTO retrieval_profiles
                   (id, org_id, name, semantic_weight, graph_weight, recency_bias,
                    tier_filters, min_score, max_results, is_preset)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, FALSE)
                   RETURNING *""",
                profile_id, auth.org_id, body.name,
                body.semantic_weight, body.graph_weight, body.recency_bias,
                body.tier_filters, body.min_score, body.max_results,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, f"Profile '{body.name}' already exists")
            raise
    return _row_to_response(row)


@router.put("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: str,
    body: ProfileUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> ProfileResponse:
    """Update a profile (not presets)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT is_preset FROM retrieval_profiles WHERE id = $1 AND org_id = $2",
            profile_id, auth.org_id,
        )
        if not existing:
            raise HTTPException(404, "Profile not found")
        if existing["is_preset"]:
            raise HTTPException(403, "Cannot modify preset profiles")

        updates = []
        params: list = [profile_id, auth.org_id]
        if body.name is not None:
            params.append(body.name)
            updates.append(f"name = ${len(params)}")
        if body.semantic_weight is not None:
            params.append(body.semantic_weight)
            updates.append(f"semantic_weight = ${len(params)}")
        if body.graph_weight is not None:
            params.append(body.graph_weight)
            updates.append(f"graph_weight = ${len(params)}")
        if body.recency_bias is not None:
            params.append(body.recency_bias)
            updates.append(f"recency_bias = ${len(params)}")
        if body.tier_filters is not None:
            params.append(body.tier_filters)
            updates.append(f"tier_filters = ${len(params)}")
        if body.min_score is not None:
            params.append(body.min_score)
            updates.append(f"min_score = ${len(params)}")
        if body.max_results is not None:
            params.append(body.max_results)
            updates.append(f"max_results = ${len(params)}")

        if not updates:
            raise HTTPException(400, "No fields to update")

        updates.append("updated_at = now()")
        set_clause = ", ".join(updates)

        row = await conn.fetchrow(
            f"""UPDATE retrieval_profiles SET {set_clause}
                WHERE id = $1 AND org_id = $2
                RETURNING *""",
            *params,
        )

    # Invalidate cache
    _profile_cache.pop(f"{auth.org_id}:{row['name']}", None)
    return _row_to_response(row)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    auth: AuthContext = Depends(require_role("admin")),
) -> None:
    """Delete a profile (not presets)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT is_preset FROM retrieval_profiles WHERE id = $1",
            profile_id,
        )
        if not existing:
            raise HTTPException(404, "Profile not found")
        if existing["is_preset"]:
            raise HTTPException(403, "Cannot delete preset profiles")

        await conn.execute(
            "DELETE FROM retrieval_profiles WHERE id = $1 AND org_id = $2",
            profile_id, auth.org_id,
        )


async def resolve_profile(
    conn, org_id: str, profile_name: Optional[str], key_default: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Resolve a profile by name: explicit param > key default > None."""
    name = profile_name or key_default
    if not name:
        return None

    # Check cache
    cache_key = f"{org_id}:{name}"
    cached = _get_cached_profile(cache_key)
    if cached:
        return cached

    row = await conn.fetchrow(
        """SELECT * FROM retrieval_profiles
           WHERE name = $1 AND (org_id = $2 OR org_id = '__global__')
           ORDER BY CASE WHEN org_id = $2 THEN 0 ELSE 1 END
           LIMIT 1""",
        name, org_id,
    )
    if row:
        profile = dict(row)
        _set_cached_profile(cache_key, profile)
        return profile
    return None
