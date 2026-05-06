"""Contract tests for the RetentionOps slice of Store — policy CRUD.

These tests run against every Store implementation (Phase 1J: Postgres only).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from lore.persistence import (
    IntegrityError,
    NewRetentionPolicy,
    RetentionPolicyPatch,
    Store,
    StoredRetentionPolicy,
)

# ── helpers ────────────────────────────────────────────────────────────────────


async def _insert_policy(
    store,
    *,
    org_id: str = "test-org",
    name: str = "test-policy",
    retention_window: dict | None = None,
    snapshot_schedule: str | None = None,
    encryption_required: bool = False,
    max_snapshots: int = 50,
    is_active: bool = True,
) -> str:
    """Insert a retention_policies row via raw SQL and return its id."""
    from ulid import ULID

    policy_id = f"retpol_{ULID()}"
    import json

    rw = retention_window or {"working": 3600, "short": 604800, "long": None}
    await store._conn.execute(
        """
        INSERT INTO retention_policies
            (id, org_id, name, retention_window, snapshot_schedule,
             encryption_required, max_snapshots, is_active)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
        """,
        policy_id,
        org_id,
        name,
        json.dumps(rw),
        snapshot_schedule,
        encryption_required,
        max_snapshots,
        is_active,
    )
    return policy_id


# ── list_retention_policies ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_org_only_ordered_by_name(store: Store):
    await _insert_policy(store, org_id="org-list", name="charlie")
    await _insert_policy(store, org_id="org-list", name="alpha")
    await _insert_policy(store, org_id="org-list", name="bravo")
    await _insert_policy(store, org_id="other-org", name="alpha")

    results = await store.list_retention_policies("org-list")

    assert len(results) == 3
    assert [r.name for r in results] == ["alpha", "bravo", "charlie"]
    for r in results:
        assert r.org_id == "org-list"
        assert isinstance(r, StoredRetentionPolicy)


# ── get_retention_policy ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(store: Store):
    result = await store.get_retention_policy("retpol_does_not_exist", "org-x")
    assert result is None


@pytest.mark.asyncio
async def test_get_org_isolation(store: Store):
    policy_id = await _insert_policy(store, org_id="org-a", name="pol-a")

    # Correct org → found
    result = await store.get_retention_policy(policy_id, "org-a")
    assert result is not None
    assert result.id == policy_id

    # Wrong org → None
    result_wrong = await store.get_retention_policy(policy_id, "org-b")
    assert result_wrong is None


# ── create_retention_policy ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_round_trip(store: Store):
    window = {"working": 7200, "short": 86400, "long": None}
    policy = NewRetentionPolicy(
        org_id="org-create",
        name="my-policy",
        retention_window=window,
        snapshot_schedule="0 2 * * *",
        encryption_required=True,
        max_snapshots=10,
        is_active=True,
    )

    created = await store.create_retention_policy(policy)

    assert created.id.startswith("retpol_")
    assert created.org_id == "org-create"
    assert created.name == "my-policy"
    assert created.retention_window == window
    assert created.snapshot_schedule == "0 2 * * *"
    assert created.encryption_required is True
    assert created.max_snapshots == 10
    assert created.is_active is True
    assert isinstance(created.created_at, datetime)
    assert isinstance(created.updated_at, datetime)

    # Round-trip via get
    fetched = await store.get_retention_policy(created.id, "org-create")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.retention_window == window


@pytest.mark.asyncio
async def test_create_uniqueness_violation_raises_integrity(store: Store):
    policy = NewRetentionPolicy(org_id="org-dup", name="dup-policy")
    await store.create_retention_policy(policy)

    with pytest.raises(IntegrityError, match="dup-policy"):
        await store.create_retention_policy(
            NewRetentionPolicy(org_id="org-dup", name="dup-policy")
        )


# ── update_retention_policy ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_changes_field(store: Store):
    policy_id = await _insert_policy(
        store, org_id="org-upd", name="before", max_snapshots=20
    )

    patch = RetentionPolicyPatch(name="after", max_snapshots=99)
    result = await store.update_retention_policy(policy_id, "org-upd", patch)

    assert result is not None
    assert result.name == "after"
    assert result.max_snapshots == 99
    assert result.id == policy_id

    # Verify persisted
    fetched = await store.get_retention_policy(policy_id, "org-upd")
    assert fetched is not None
    assert fetched.name == "after"
    assert fetched.max_snapshots == 99


@pytest.mark.asyncio
async def test_update_returns_none_when_missing(store: Store):
    patch = RetentionPolicyPatch(name="ghost")
    result = await store.update_retention_policy(
        "retpol_nonexistent", "org-x", patch
    )
    assert result is None


@pytest.mark.asyncio
async def test_update_empty_patch_raises_value_error(store: Store):
    policy_id = await _insert_policy(store, org_id="org-empty", name="empty-patch")

    with pytest.raises(ValueError, match="empty patch"):
        await store.update_retention_policy(
            policy_id, "org-empty", RetentionPolicyPatch()
        )


# ── delete_retention_policy ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_returns_true_when_existed(store: Store):
    policy_id = await _insert_policy(store, org_id="org-del", name="to-delete")

    result = await store.delete_retention_policy(policy_id, "org-del")

    assert result is True

    # Confirm gone
    fetched = await store.get_retention_policy(policy_id, "org-del")
    assert fetched is None


@pytest.mark.asyncio
async def test_delete_returns_false_when_missing(store: Store):
    result = await store.delete_retention_policy("retpol_ghost", "org-ghost")
    assert result is False
