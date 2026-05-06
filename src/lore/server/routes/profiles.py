"""Retrieval profiles CRUD — GET/POST/PUT/DELETE /v1/profiles."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.persistence import ProfilePatch, Store, StoredProfile
from lore.persistence.exceptions import (
    IntegrityError,
    ProfileImmutableError,
    StoreNotFoundError,
)
from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.services import profiles as profiles_service

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
    k: Optional[int] = None  # number of results (alias for max_results)
    threshold: Optional[float] = None  # similarity threshold (alias for min_score)
    rerank: bool = False  # whether to apply reranking
    include_graph: bool = True  # whether to include graph context


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    semantic_weight: Optional[float] = None
    graph_weight: Optional[float] = None
    recency_bias: Optional[float] = None
    tier_filters: Optional[List[str]] = None
    min_score: Optional[float] = None
    max_results: Optional[int] = None
    k: Optional[int] = None
    threshold: Optional[float] = None
    rerank: Optional[bool] = None
    include_graph: Optional[bool] = None


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
    k: Optional[int] = None
    threshold: Optional[float] = None
    rerank: bool = False
    include_graph: bool = True
    is_preset: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _to_response(p: StoredProfile) -> ProfileResponse:
    return ProfileResponse(
        id=p.id,
        org_id=p.org_id,
        name=p.name,
        semantic_weight=p.semantic_weight,
        graph_weight=p.graph_weight,
        recency_bias=p.recency_bias,
        tier_filters=list(p.tier_filters) if p.tier_filters else None,
        min_score=p.min_score,
        max_results=p.max_results,
        k=p.k,
        threshold=p.threshold,
        rerank=p.rerank,
        include_graph=p.include_graph,
        is_preset=p.is_preset,
        created_at=p.created_at.isoformat() if p.created_at else None,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


def _patch_from_body(body: ProfileUpdateRequest) -> ProfilePatch:
    """Build a ProfilePatch from a ProfileUpdateRequest, dropping None fields."""
    return ProfilePatch(
        name=body.name,
        semantic_weight=body.semantic_weight,
        graph_weight=body.graph_weight,
        recency_bias=body.recency_bias,
        tier_filters=body.tier_filters,
        min_score=body.min_score,
        max_results=body.max_results,
        k=body.k,
        threshold=body.threshold,
        rerank=body.rerank,
        include_graph=body.include_graph,
    )


@router.get("", response_model=List[ProfileResponse])
async def list_profiles(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[ProfileResponse]:
    """List profiles (org + global presets)."""
    profiles = await profiles_service.list_profiles(store, auth.org_id)
    return [_to_response(p) for p in profiles]


@router.get("/defaults", response_model=Dict[str, Any])
async def get_default_profiles() -> Dict[str, Any]:
    """Return the built-in default adaptive retrieval profiles."""
    return dict(profiles_service.get_default_profiles())


@router.get("/{profile_id}", response_model=ProfileResponse)
async def get_profile(
    profile_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> ProfileResponse:
    """Get a specific profile."""
    try:
        profile = await profiles_service.get_profile(store, profile_id)
    except StoreNotFoundError:
        raise HTTPException(404, "Profile not found")
    return _to_response(profile)


@router.post("", response_model=ProfileResponse, status_code=201)
async def create_profile(
    body: ProfileCreateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> ProfileResponse:
    """Create a custom profile."""
    try:
        profile = await profiles_service.create_profile(
            store,
            org_id=auth.org_id,
            name=body.name,
            semantic_weight=body.semantic_weight,
            graph_weight=body.graph_weight,
            recency_bias=body.recency_bias,
            tier_filters=body.tier_filters,
            min_score=body.min_score,
            max_results=body.max_results,
            k=body.k,
            threshold=body.threshold,
            rerank=body.rerank,
            include_graph=body.include_graph,
        )
    except IntegrityError:
        raise HTTPException(409, f"Profile '{body.name}' already exists")
    return _to_response(profile)


@router.put("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: str,
    body: ProfileUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> ProfileResponse:
    """Update a profile (not presets)."""
    patch = _patch_from_body(body)
    try:
        profile = await profiles_service.update_profile_by_id(
            store, profile_id, auth.org_id, patch
        )
    except StoreNotFoundError:
        raise HTTPException(404, "Profile not found")
    except ProfileImmutableError:
        raise HTTPException(403, "Cannot modify preset profiles")
    except ValueError as exc:
        if "No fields to update" in str(exc):
            raise HTTPException(400, "No fields to update")
        raise
    return _to_response(profile)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: str,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> None:
    """Delete a profile (not presets)."""
    try:
        await profiles_service.delete_profile_by_id(store, profile_id, auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(404, "Profile not found")
    except ProfileImmutableError:
        raise HTTPException(403, "Cannot delete preset profiles")


@router.put("/name/{profile_name}", response_model=ProfileResponse)
async def update_profile_by_name(
    profile_name: str,
    body: ProfileUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> ProfileResponse:
    """Update a profile by name (for convenience)."""
    patch = _patch_from_body(body)
    try:
        profile = await profiles_service.update_profile_by_name(
            store, auth.org_id, profile_name, patch
        )
    except StoreNotFoundError:
        raise HTTPException(404, f"Profile '{profile_name}' not found")
    except ProfileImmutableError:
        raise HTTPException(403, "Cannot modify preset profiles")
    except ValueError as exc:
        if "No fields to update" in str(exc):
            raise HTTPException(400, "No fields to update")
        raise
    return _to_response(profile)


@router.delete("/name/{profile_name}", status_code=204)
async def delete_profile_by_name(
    profile_name: str,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> None:
    """Delete a profile by name (for convenience)."""
    try:
        await profiles_service.delete_profile_by_name(store, auth.org_id, profile_name)
    except StoreNotFoundError:
        raise HTTPException(404, f"Profile '{profile_name}' not found")
    except ProfileImmutableError:
        raise HTTPException(403, "Cannot delete preset profiles")
