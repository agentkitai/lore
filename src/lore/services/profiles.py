"""Profiles service — retrieval-profile CRUD, caching, and resolution."""

from __future__ import annotations

import dataclasses
import time as _time
from typing import Any, Dict, Mapping, Optional, Sequence

from lore.persistence import (
    NewProfile,
    ProfilePatch,
    ResolvedProfile,
    Store,
    StoredProfile,
)
from lore.persistence.exceptions import ProfileImmutableError, StoreNotFoundError


# ── Built-in default adaptive retrieval profiles ──────────────────────────────
# Lifted verbatim from routes/profiles.py:71-82 (pre-refactor source).
DEFAULT_PROFILES: Mapping[str, Mapping[str, Any]] = {
    "precise": {
        "k": 3,
        "threshold": 0.8,
        "rerank": True,
        "include_graph": False,
        "semantic_weight": 1.5,
        "graph_weight": 0.5,
        "recency_bias": 15.0,
        "min_score": 0.8,
        "max_results": 3,
    },
    "broad": {
        "k": 10,
        "threshold": 0.5,
        "rerank": False,
        "include_graph": True,
        "semantic_weight": 0.8,
        "graph_weight": 1.2,
        "recency_bias": 60.0,
        "min_score": 0.5,
        "max_results": 10,
    },
    "balanced": {
        "k": 5,
        "threshold": 0.65,
        "rerank": True,
        "include_graph": True,
        "semantic_weight": 1.0,
        "graph_weight": 1.0,
        "recency_bias": 30.0,
        "min_score": 0.65,
        "max_results": 5,
    },
}


# ── In-memory resolution cache (per-process, 60s TTL) ─────────────────────────
_PROFILE_CACHE_TTL = 60.0
_profile_cache: Dict[str, "tuple[ResolvedProfile, float]"] = {}


def _cache_get(key: str) -> Optional[ResolvedProfile]:
    cached = _profile_cache.get(key)
    if cached and _time.monotonic() - cached[1] < _PROFILE_CACHE_TTL:
        return cached[0]
    return None


def _cache_set(key: str, value: ResolvedProfile) -> None:
    _profile_cache[key] = (value, _time.monotonic())


def _cache_clear() -> None:
    """Reset the cache. Used by tests."""
    _profile_cache.clear()


# ── Alias sync helpers ─────────────────────────────────────────────────────────


def _apply_create_aliases(
    *,
    k: Optional[int],
    threshold: Optional[float],
    max_results: int,
    min_score: float,
) -> tuple[int, float]:
    """Return (max_results, min_score) after alias logic for create.

    Mirrors the original routes/profiles.py create handler:
        max_results = body.k if body.k is not None else body.max_results
        min_score   = body.threshold if body.threshold is not None else body.min_score

    If k is provided, k always wins for max_results (regardless of max_results value).
    If threshold is provided, threshold always wins for min_score.
    """
    if k is not None:
        max_results = k
    if threshold is not None:
        min_score = threshold
    return max_results, min_score


def _apply_patch_aliases(patch: ProfilePatch) -> ProfilePatch:
    """Return a new ProfilePatch with alias fields synced.

    If `k` is set and `max_results` is None, also set max_results = k.
    If `threshold` is set and `min_score` is None, also set min_score = threshold.
    """
    extra: Dict[str, Any] = {}
    if patch.k is not None and patch.max_results is None:
        extra["max_results"] = patch.k
    if patch.threshold is not None and patch.min_score is None:
        extra["min_score"] = patch.threshold
    if extra:
        return dataclasses.replace(patch, **extra)
    return patch


# ── Public service functions ───────────────────────────────────────────────────


async def list_profiles(store: Store, org_id: str) -> Sequence[StoredProfile]:
    """Return all profiles visible to *org_id* (org-owned + global presets)."""
    return await store.list_profiles(org_id)


async def get_profile(store: Store, profile_id: str) -> StoredProfile:
    """Return the profile row or raise StoreNotFoundError."""
    row = await store.get_profile(profile_id)
    if row is None:
        raise StoreNotFoundError("retrieval_profiles", profile_id)
    return row


async def create_profile(
    store: Store,
    *,
    org_id: str,
    name: str,
    semantic_weight: float = 1.0,
    graph_weight: float = 1.0,
    recency_bias: float = 30.0,
    tier_filters: Optional[Sequence[str]] = None,
    min_score: float = 0.3,
    max_results: int = 10,
    k: Optional[int] = None,
    threshold: Optional[float] = None,
    rerank: bool = False,
    include_graph: bool = True,
) -> StoredProfile:
    """Insert a new profile, applying alias logic for k/max_results and threshold/min_score."""
    max_results, min_score = _apply_create_aliases(
        k=k,
        threshold=threshold,
        max_results=max_results,
        min_score=min_score,
    )
    return await store.create_profile(
        NewProfile(
            org_id=org_id,
            name=name,
            semantic_weight=semantic_weight,
            graph_weight=graph_weight,
            recency_bias=recency_bias,
            tier_filters=tier_filters,
            min_score=min_score,
            max_results=max_results,
            is_preset=False,
            k=k,
            threshold=threshold,
            rerank=rerank,
            include_graph=include_graph,
        )
    )


async def update_profile_by_id(
    store: Store,
    profile_id: str,
    org_id: str,
    patch: ProfilePatch,
) -> StoredProfile:
    """Update profile by id with ownership and immutability checks."""
    row = await store.get_profile(profile_id)
    if row is None or row.org_id != org_id:
        raise StoreNotFoundError("retrieval_profiles", profile_id)
    if row.is_preset:
        raise ProfileImmutableError("Cannot modify preset profile")

    # Sync alias fields in the patch
    synced = _apply_patch_aliases(patch)

    # Reject empty patches (every field is None)
    if all(
        getattr(synced, f.name) is None
        for f in dataclasses.fields(synced)
    ):
        raise ValueError("No fields to update")

    updated = await store.update_profile(profile_id, synced)
    if updated is None:
        raise StoreNotFoundError("retrieval_profiles", profile_id)

    # Invalidate cache for old name and (if renamed) new name
    _profile_cache.pop(f"{org_id}:{row.name}", None)
    if patch.name is not None and patch.name != row.name:
        _profile_cache.pop(f"{org_id}:{patch.name}", None)

    return updated


async def update_profile_by_name(
    store: Store,
    org_id: str,
    name: str,
    patch: ProfilePatch,
) -> StoredProfile:
    """Look up profile by (org_id, name) then delegate to update_profile_by_id."""
    row = await store.get_profile_by_name(org_id, name)
    if row is None:
        raise StoreNotFoundError("retrieval_profiles", name)
    return await update_profile_by_id(store, row.id, org_id, patch)


async def delete_profile_by_id(
    store: Store,
    profile_id: str,
    org_id: str,
) -> None:
    """Delete profile by id with ownership and immutability checks."""
    row = await store.get_profile(profile_id)
    if row is None or row.org_id != org_id:
        raise StoreNotFoundError("retrieval_profiles", profile_id)
    if row.is_preset:
        raise ProfileImmutableError("Cannot delete preset profile")

    await store.delete_profile(profile_id, org_id)

    # Invalidate cache
    _profile_cache.pop(f"{org_id}:{row.name}", None)


async def delete_profile_by_name(
    store: Store,
    org_id: str,
    name: str,
) -> None:
    """Look up profile by (org_id, name) then delegate to delete_profile_by_id."""
    row = await store.get_profile_by_name(org_id, name)
    if row is None:
        raise StoreNotFoundError("retrieval_profiles", name)
    await delete_profile_by_id(store, row.id, org_id)


def get_default_profiles() -> Mapping[str, Mapping[str, Any]]:
    """Return the built-in default adaptive retrieval profiles. Pure; no store call."""
    return DEFAULT_PROFILES


async def resolve_profile(
    store: Store,
    org_id: str,
    requested_name: Optional[str],
    key_default: Optional[str] = None,
) -> Optional[ResolvedProfile]:
    """Resolve a profile name to a ResolvedProfile, with cache and default fallback.

    Resolution order:
    1. Cache hit (within TTL) → return cached value.
    2. Store lookup via resolve_profile_for_key → source="stored".
    3. Built-in DEFAULT_PROFILES match → source="default".
    4. None.
    """
    name = requested_name or key_default
    if name is None:
        return None

    cache_key = f"{org_id}:{name}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    row = await store.resolve_profile_for_key(org_id, name)
    if row is not None:
        resolved = ResolvedProfile(
            name=row.name,
            source="stored",
            semantic_weight=row.semantic_weight,
            graph_weight=row.graph_weight,
            recency_bias=row.recency_bias,
            min_score=row.min_score,
            max_results=row.max_results,
            tier_filters=row.tier_filters,
            k=row.k,
            threshold=row.threshold,
            rerank=row.rerank,
            include_graph=row.include_graph,
        )
        _cache_set(cache_key, resolved)
        return resolved

    if name in DEFAULT_PROFILES:
        d = DEFAULT_PROFILES[name]
        resolved = ResolvedProfile(
            name=name,
            source="default",
            semantic_weight=d["semantic_weight"],
            graph_weight=d["graph_weight"],
            recency_bias=d["recency_bias"],
            min_score=d["min_score"],
            max_results=d["max_results"],
            tier_filters=None,
            k=d.get("k"),
            threshold=d.get("threshold"),
            rerank=d.get("rerank", False),
            include_graph=d.get("include_graph", True),
        )
        _cache_set(cache_key, resolved)
        return resolved

    return None
