"""Service-level tests for lore.services.profiles using a real Postgres store."""

from __future__ import annotations

import dataclasses
import pytest

from lore.persistence import ProfilePatch, StoreNotFoundError
from lore.persistence.exceptions import ProfileImmutableError
from lore.persistence.types import ResolvedProfile
from lore.services.profiles import (
    _cache_clear,
    create_profile,
    delete_profile_by_id,
    delete_profile_by_name,
    get_default_profiles,
    get_profile,
    list_profiles,
    resolve_profile,
    update_profile_by_id,
    update_profile_by_name,
)

# ── helpers ───────────────────────────────────────────────────────────────────

_ORG = "solo"
_GLOBAL = "__global__"
_PRESET_ID = "preset-coding"
_PRESET_NAME = "coding"


async def _make(store, *, name="test-profile", org_id=_ORG, **kw):
    return await create_profile(store, org_id=org_id, name=name, **kw)


# ── list_profiles ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_profiles_returns_sequence(store):
    """list_profiles passes through to store without error."""
    rows = await list_profiles(store, _ORG)
    assert isinstance(rows, (list, tuple))


# ── create / get round-trip ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_profile_round_trip(store):
    """Create a profile, then get it; all fields should match."""
    created = await _make(
        store,
        name="rt-profile",
        semantic_weight=1.2,
        graph_weight=0.8,
        recency_bias=45.0,
        min_score=0.4,
        max_results=7,
        rerank=True,
        include_graph=False,
    )
    fetched = await get_profile(store, created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == "rt-profile"
    assert fetched.org_id == _ORG
    assert fetched.semantic_weight == pytest.approx(1.2)
    assert fetched.graph_weight == pytest.approx(0.8)
    assert fetched.recency_bias == pytest.approx(45.0)
    assert fetched.min_score == pytest.approx(0.4)
    assert fetched.max_results == 7
    assert fetched.rerank is True
    assert fetched.include_graph is False
    assert fetched.is_preset is False


@pytest.mark.asyncio
async def test_get_profile_missing_raises_not_found(store):
    with pytest.raises(StoreNotFoundError):
        await get_profile(store, "00000000-0000-0000-0000-000000000000")


# ── alias logic: create ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_with_k_alias_syncs_max_results(store):
    """`k=5` with no `max_results` should store `max_results=5`."""
    row = await _make(store, name="k-alias", k=5)
    assert row.k == 5
    assert row.max_results == 5


@pytest.mark.asyncio
async def test_create_with_threshold_alias_syncs_min_score(store):
    """`threshold=0.7` with no `min_score` should store `min_score=0.7`."""
    row = await _make(store, name="threshold-alias", threshold=0.7)
    assert row.threshold == pytest.approx(0.7)
    assert row.min_score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_create_with_both_k_and_max_results_k_wins(store):
    """`k=5, max_results=10` — k always wins; max_results becomes 5.

    This matches the original routes/profiles.py behavior:
        max_results = body.k if body.k is not None else body.max_results
    """
    row = await _make(store, name="k-and-max", k=5, max_results=10)
    assert row.k == 5
    assert row.max_results == 5


# ── update_profile_by_id ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_profile_by_id_changes_field(store):
    """Happy path: update semantic_weight, row reflects change."""
    row = await _make(store, name="update-me")
    patch = ProfilePatch(semantic_weight=2.5)
    updated = await update_profile_by_id(store, row.id, _ORG, patch)
    assert updated.semantic_weight == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_update_profile_by_id_with_k_alias_also_updates_max_results(store):
    """`patch k=7` (no max_results) should also set max_results=7."""
    row = await _make(store, name="update-k-alias")
    patch = ProfilePatch(k=7)
    updated = await update_profile_by_id(store, row.id, _ORG, patch)
    assert updated.k == 7
    assert updated.max_results == 7


@pytest.mark.asyncio
async def test_update_profile_by_id_preset_raises_immutable(store):
    """Updating a preset profile must raise ProfileImmutableError."""
    patch = ProfilePatch(semantic_weight=0.5)
    with pytest.raises(ProfileImmutableError):
        await update_profile_by_id(store, _PRESET_ID, _GLOBAL, patch)


@pytest.mark.asyncio
async def test_update_profile_by_id_missing_raises_not_found(store):
    """Updating a non-existent id raises StoreNotFoundError."""
    patch = ProfilePatch(semantic_weight=0.5)
    with pytest.raises(StoreNotFoundError):
        await update_profile_by_id(
            store, "00000000-0000-0000-0000-000000000000", _ORG, patch
        )


@pytest.mark.asyncio
async def test_update_profile_by_id_org_mismatch_raises_not_found(store):
    """A valid profile id under a different org raises StoreNotFoundError."""
    row = await _make(store, name="org-mismatch")
    patch = ProfilePatch(semantic_weight=0.5)
    with pytest.raises(StoreNotFoundError):
        await update_profile_by_id(store, row.id, "other", patch)


@pytest.mark.asyncio
async def test_update_profile_empty_patch_raises_value_error(store):
    """All-None ProfilePatch raises ValueError before hitting the store."""
    row = await _make(store, name="empty-patch")
    with pytest.raises(ValueError, match="No fields to update"):
        await update_profile_by_id(store, row.id, _ORG, ProfilePatch())


# ── update_profile_by_name ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_profile_by_name_resolves_then_updates(store):
    row = await _make(store, name="named-update")
    patch = ProfilePatch(recency_bias=99.0)
    updated = await update_profile_by_name(store, _ORG, "named-update", patch)
    assert updated.recency_bias == pytest.approx(99.0)


# ── delete_profile_by_id ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_profile_by_id_happy_path(store):
    """Create, delete, then get returns None (not raises)."""
    row = await _make(store, name="delete-me")
    await delete_profile_by_id(store, row.id, _ORG)
    result = await store.get_profile(row.id)
    assert result is None


@pytest.mark.asyncio
async def test_delete_profile_by_id_preset_raises(store):
    """Deleting a preset must raise ProfileImmutableError."""
    with pytest.raises(ProfileImmutableError):
        await delete_profile_by_id(store, _PRESET_ID, _GLOBAL)


# ── delete_profile_by_name ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_profile_by_name_resolves_then_deletes(store):
    row = await _make(store, name="test-x")
    await delete_profile_by_name(store, _ORG, "test-x")
    result = await store.get_profile(row.id)
    assert result is None


# ── get_default_profiles ──────────────────────────────────────────────────────


def test_get_default_profiles_returns_three_keys():
    defaults = get_default_profiles()
    assert set(defaults.keys()) == {"precise", "broad", "balanced"}


# ── resolve_profile ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_profile_returns_stored(store):
    """A profile created in the store resolves with source='stored'."""
    _cache_clear()
    await _make(store, name="my-stored-profile", semantic_weight=1.3)
    resolved = await resolve_profile(store, _ORG, "my-stored-profile")
    assert resolved is not None
    assert resolved.source == "stored"
    assert resolved.semantic_weight == pytest.approx(1.3)


@pytest.mark.asyncio
async def test_resolve_profile_falls_back_to_default(store):
    """resolve('solo', 'balanced') returns the built-in default profile."""
    _cache_clear()
    resolved = await resolve_profile(store, _ORG, "balanced")
    assert resolved is not None
    assert resolved.source == "default"
    assert resolved.name == "balanced"


@pytest.mark.asyncio
async def test_resolve_profile_returns_none_for_unknown(store):
    """An unknown profile name returns None (no error)."""
    _cache_clear()
    result = await resolve_profile(store, _ORG, "no-such-thing")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_profile_uses_key_default_when_requested_name_is_none(store):
    """key_default is used when requested_name is None."""
    _cache_clear()
    resolved = await resolve_profile(store, _ORG, None, key_default="balanced")
    assert resolved is not None
    assert resolved.source == "default"
    assert resolved.name == "balanced"


@pytest.mark.asyncio
async def test_resolve_profile_caches_within_ttl(store, monkeypatch):
    """Second call within TTL hits cache; store is only called once."""
    _cache_clear()
    call_count = [0]
    original = store.resolve_profile_for_key

    async def counting_wrapper(org_id, name):
        call_count[0] += 1
        return await original(org_id, name)

    monkeypatch.setattr(store, "resolve_profile_for_key", counting_wrapper)
    await resolve_profile(store, _ORG, "balanced")
    await resolve_profile(store, _ORG, "balanced")
    assert call_count[0] == 1


@pytest.mark.asyncio
async def test_resolve_profile_cache_expires_after_ttl(store, monkeypatch):
    """After TTL passes, the store is called again."""
    import lore.services.profiles as _mod

    _cache_clear()
    call_count = [0]
    original = store.resolve_profile_for_key
    tick = [0.0]

    async def counting_wrapper(org_id, name):
        call_count[0] += 1
        return await original(org_id, name)

    monkeypatch.setattr(store, "resolve_profile_for_key", counting_wrapper)

    # Patch monotonic so we control time
    monkeypatch.setattr(_mod._time, "monotonic", lambda: tick[0])

    # First call — populates cache at t=0
    await resolve_profile(store, _ORG, "balanced")
    assert call_count[0] == 1

    # Advance past TTL
    tick[0] = _mod._PROFILE_CACHE_TTL + 1.0

    # Second call — cache expired, hits store again
    await resolve_profile(store, _ORG, "balanced")
    assert call_count[0] == 2
