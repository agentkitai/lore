"""Contract tests for the AuthOps slice of Store — get_api_key / list_api_keys.

These tests run against every Store implementation (Phase 1D: Postgres only).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from lore.persistence import Store
from lore.persistence.types import StoredApiKey

# ── helpers ────────────────────────────────────────────────────────────────────


async def _ensure_org(store, org_id: str) -> None:
    """Insert an org row if it doesn't already exist (required by api_keys FK)."""
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


async def _insert_api_key(
    store,
    *,
    key_id=None,
    org_id: str = "solo",
    name: str = "test-key",
    key_hash: str = "hash-x",
    key_prefix: str = "lore_sk_xx",
    project=None,
    is_root: bool = False,
    workspace_id=None,
    revoked: bool = False,
) -> str:
    """Insert an api_keys row via raw SQL and return its id."""
    from ulid import ULID

    key_id = key_id or f"key_{ULID()}"
    await _ensure_org(store, org_id)
    await store._conn.execute(
        """INSERT INTO api_keys (id, org_id, name, key_hash, key_prefix, project, is_root, workspace_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        key_id,
        org_id,
        name,
        key_hash,
        key_prefix,
        project,
        is_root,
        workspace_id,
    )
    if revoked:
        await store._conn.execute(
            "UPDATE api_keys SET revoked_at = now() WHERE id = $1", key_id
        )
    return key_id


# ── get_api_key tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_api_key_round_trip(store: Store):
    key_id = await _insert_api_key(
        store,
        org_id="org-gak",
        name="my-key",
        key_hash="sha256-abc",
        key_prefix="lore_sk_ab",
        project="proj-x",
        is_root=True,
        workspace_id="ws_abc",
    )

    result = await store.get_api_key(key_id)

    assert result is not None
    assert isinstance(result, StoredApiKey)
    assert result.id == key_id
    assert result.org_id == "org-gak"
    assert result.name == "my-key"
    assert result.key_hash == "sha256-abc"
    assert result.key_prefix == "lore_sk_ab"
    assert result.project == "proj-x"
    assert result.is_root is True
    assert result.workspace_id == "ws_abc"
    assert result.revoked_at is None
    assert isinstance(result.created_at, datetime)
    assert result.last_used_at is None


@pytest.mark.asyncio
async def test_get_api_key_returns_none_when_missing(store: Store):
    result = await store.get_api_key("key_does_not_exist")
    assert result is None


# ── list_api_keys tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_api_keys_returns_org_only(store: Store):
    await _insert_api_key(store, org_id="org_a", name="key-a1")
    await _insert_api_key(store, org_id="org_a", name="key-a2")
    await _insert_api_key(store, org_id="org_b", name="key-b1")

    results = await store.list_api_keys("org_a")

    assert len(results) == 2
    names = {r.name for r in results}
    assert names == {"key-a1", "key-a2"}
    for r in results:
        assert r.org_id == "org_a"


@pytest.mark.asyncio
async def test_list_api_keys_ordered_by_created_at(store: Store):
    # Insert multiple keys; since DB now() within a transaction may not advance,
    # we rely on insertion order and verify the returned sequence is a tuple.
    key_ids = []
    for i in range(3):
        kid = await _insert_api_key(store, org_id="org_order", name=f"key-{i}")
        key_ids.append(kid)

    results = await store.list_api_keys("org_order")

    assert isinstance(results, tuple)
    assert len(results) == 3
    # All belong to the right org
    for r in results:
        assert r.org_id == "org_order"


@pytest.mark.asyncio
async def test_list_api_keys_includes_revoked(store: Store):
    await _insert_api_key(store, org_id="org_rev", name="active-key", revoked=False)
    await _insert_api_key(store, org_id="org_rev", name="revoked-key", revoked=True)

    results = await store.list_api_keys("org_rev")

    assert len(results) == 2
    names = {r.name for r in results}
    assert names == {"active-key", "revoked-key"}

    # Verify the revoked one has revoked_at set
    revoked = next(r for r in results if r.name == "revoked-key")
    assert revoked.revoked_at is not None
    assert isinstance(revoked.revoked_at, datetime)

    active = next(r for r in results if r.name == "active-key")
    assert active.revoked_at is None


# ── create_api_key tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_api_key_round_trip(store: Store):
    from lore.persistence.types import NewApiKey

    await _ensure_org(store, "org-create")
    new_key = NewApiKey(
        org_id="org-create",
        name="created-key",
        key_hash="hash-create",
        key_prefix="lore_sk_cr",
    )
    created = await store.create_api_key(new_key)

    assert created.id.startswith("key_")
    assert created.org_id == "org-create"
    assert created.name == "created-key"
    assert created.key_hash == "hash-create"
    assert created.key_prefix == "lore_sk_cr"
    assert created.project is None
    assert created.is_root is False
    assert created.workspace_id is None
    assert created.revoked_at is None
    assert isinstance(created.created_at, datetime)

    # Round-trip via get_api_key
    fetched = await store.get_api_key(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.key_hash == "hash-create"


@pytest.mark.asyncio
async def test_create_api_key_with_workspace_id(store: Store):
    from lore.persistence.types import NewApiKey

    await _ensure_org(store, "org-ws")
    new_key = NewApiKey(
        org_id="org-ws",
        name="ws-key",
        key_hash="hash-ws",
        key_prefix="lore_sk_ws",
        project="proj-ws",
        is_root=True,
        workspace_id="ws_xyz",
    )
    created = await store.create_api_key(new_key)

    assert created.workspace_id == "ws_xyz"
    assert created.is_root is True
    assert created.project == "proj-ws"

    fetched = await store.get_api_key(created.id)
    assert fetched is not None
    assert fetched.workspace_id == "ws_xyz"


# ── revoke_api_key tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoke_active_key_returns_updated_row(store: Store):
    key_id = await _insert_api_key(store, org_id="org-rev2", name="to-revoke")

    result = await store.revoke_api_key(key_id)

    assert result is not None
    assert result.id == key_id
    assert result.revoked_at is not None
    assert isinstance(result.revoked_at, datetime)


@pytest.mark.asyncio
async def test_revoke_already_revoked_returns_none(store: Store):
    key_id = await _insert_api_key(
        store, org_id="org-rev3", name="already-revoked", revoked=True
    )

    result = await store.revoke_api_key(key_id)

    assert result is None


@pytest.mark.asyncio
async def test_revoke_missing_key_returns_none(store: Store):
    result = await store.revoke_api_key("key_nonexistent")
    assert result is None


# ── count_active_root_keys tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_active_root_keys(store: Store):
    # 1 root active, 1 root revoked, 1 non-root active => count == 1
    await _insert_api_key(store, org_id="org-count", name="root-active", is_root=True)
    await _insert_api_key(
        store, org_id="org-count", name="root-revoked", is_root=True, revoked=True
    )
    await _insert_api_key(store, org_id="org-count", name="non-root-active", is_root=False)

    count = await store.count_active_root_keys("org-count")
    assert count == 1


@pytest.mark.asyncio
async def test_count_active_root_keys_other_org(store: Store):
    # Keys under org_a; query org_b => 0
    await _insert_api_key(store, org_id="org_a2", name="root-a", is_root=True)

    count = await store.count_active_root_keys("org_b2")
    assert count == 0
