"""Service-level tests for lore.services.keys using a real Postgres store."""

from __future__ import annotations

import uuid

import pytest

import lore.server.auth as auth_module
from lore.persistence.exceptions import LastRootKeyError, StoreNotFoundError
from lore.services.keys import (
    _generate_key,
    create_api_key,
    list_api_keys,
    revoke_api_key,
)

# ── helpers ───────────────────────────────────────────────────────────────────

_ORG = "solo"
_OTHER_ORG = "other-org"


def _name() -> str:
    return f"key-{uuid.uuid4().hex[:8]}"


async def _make_key(store, *, org_id=_ORG, name=None, is_root=False, **kw):
    return await create_api_key(
        store,
        org_id=org_id,
        name=name or _name(),
        is_root=is_root,
        **kw,
    )


# ── pure-function tests ────────────────────────────────────────────────────────


def test_generate_key_format():
    """_generate_key returns expected shapes."""
    raw_key, key_hash, key_prefix = _generate_key()
    assert raw_key.startswith("lore_sk_"), "raw_key must start with lore_sk_"
    assert len(raw_key) == 72, f"raw_key should be 72 chars, got {len(raw_key)}"
    assert len(key_hash) == 64, f"key_hash should be 64 hex chars, got {len(key_hash)}"
    assert all(c in "0123456789abcdef" for c in key_hash), "key_hash must be hex"
    assert len(key_prefix) == 12, f"key_prefix should be 12 chars, got {len(key_prefix)}"
    assert key_prefix == raw_key[:12]


# ── create round-trip ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_api_key_round_trip(store):
    """create_api_key returns a StoredApiKey with key_ prefix id; appears in list."""
    stored, raw_key = await _make_key(store)
    assert stored.id.startswith("key_"), f"id should start with key_, got {stored.id!r}"
    assert stored.org_id == _ORG
    assert raw_key.startswith("lore_sk_")

    keys = await store.list_api_keys(_ORG)
    ids = {k.id for k in keys}
    assert stored.id in ids


@pytest.mark.asyncio
async def test_create_api_key_with_workspace_id(store):
    """workspace_id passes through to the stored row."""
    ws_id = f"ws_{uuid.uuid4().hex[:8]}"
    stored, _ = await _make_key(store, workspace_id=ws_id)
    assert stored.workspace_id == ws_id


# ── list passthrough ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_api_keys_passthrough(store):
    """Service list_api_keys returns the same result as store.list_api_keys."""
    await _make_key(store)
    service_result = await list_api_keys(store, _ORG)
    store_result = await store.list_api_keys(_ORG)
    assert list(service_result) == list(store_result)


# ── revoke ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_active_non_root_key(store, monkeypatch):
    """Happy path: revoking an active non-root key succeeds."""
    stored, _ = await _make_key(store, is_root=False)

    calls = []
    monkeypatch.setattr(auth_module, "invalidate_key", lambda h: calls.append(h))

    await revoke_api_key(store, stored.id, _ORG)
    assert calls == [stored.key_hash]


@pytest.mark.asyncio
async def test_revoke_active_root_when_only_one_raises_last_root_key_error(store):
    """One active root key in org → revoking it raises LastRootKeyError."""
    stored, _ = await _make_key(store, is_root=True)
    with pytest.raises(LastRootKeyError):
        await revoke_api_key(store, stored.id, _ORG)


@pytest.mark.asyncio
async def test_revoke_active_root_when_multiple_succeeds(store, monkeypatch):
    """Two active root keys in org → revoking one succeeds."""
    # Use the pre-existing "solo" org; transaction rolls back after the test
    stored1, _ = await _make_key(store, org_id=_ORG, is_root=True)
    stored2, _ = await _make_key(store, org_id=_ORG, is_root=True)  # noqa: F841

    monkeypatch.setattr(auth_module, "invalidate_key", lambda h: None)
    # Should not raise
    await revoke_api_key(store, stored1.id, _ORG)


@pytest.mark.asyncio
async def test_revoke_already_revoked_succeeds_silently(store, monkeypatch):
    """Revoking an already-revoked key succeeds silently (idempotent)."""
    stored, _ = await _make_key(store, is_root=False)

    calls = []
    monkeypatch.setattr(auth_module, "invalidate_key", lambda h: calls.append(h))

    # Revoke once
    await revoke_api_key(store, stored.id, _ORG)
    calls.clear()

    # Revoke again — should not raise, no LastRootKey check (revoked_at is set)
    await revoke_api_key(store, stored.id, _ORG)


@pytest.mark.asyncio
async def test_revoke_missing_raises_not_found(store):
    """Random key id → StoreNotFoundError."""
    with pytest.raises(StoreNotFoundError):
        await revoke_api_key(store, "key_00000000000000000000000000", _ORG)


@pytest.mark.asyncio
async def test_revoke_other_org_raises_not_found(store):
    """Key under org_a; revoke under org_b → StoreNotFoundError."""
    # Use pre-existing org IDs (FK constraint)
    stored, _ = await _make_key(store, org_id="org_a")
    with pytest.raises(StoreNotFoundError):
        await revoke_api_key(store, stored.id, "org_b")


@pytest.mark.asyncio
async def test_revoke_invalidates_cache(store, monkeypatch):
    """invalidate_key is called exactly once with the row's key_hash on successful revoke."""
    stored, _ = await _make_key(store, is_root=False)

    calls = []
    monkeypatch.setattr(auth_module, "invalidate_key", lambda h: calls.append(h))

    await revoke_api_key(store, stored.id, _ORG)

    assert len(calls) == 1, f"Expected 1 call to invalidate_key, got {len(calls)}"
    assert calls[0] == stored.key_hash
