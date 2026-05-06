"""Service-level tests for lore.services.policies.

Uses a real Postgres store (via conftest fixture) for integration tests.
"""

from __future__ import annotations

import pytest

from lore.persistence import (
    IntegrityError,
    RetentionPolicyPatch,
)
from lore.persistence.exceptions import StoreNotFoundError
from lore.services import policies

# ── helpers ───────────────────────────────────────────────────────────────────

_ORG = "svc-pol-test"


async def _insert_snapshot(
    store,
    *,
    policy_id=None,
    org_id=_ORG,
    name="snap-x",
    path="/tmp/snap-x",
    size_bytes=100,
    memory_count=10,
    encrypted=False,
) -> str:
    from ulid import ULID

    sid = f"snap_{ULID()}"
    await store._conn.execute(
        "INSERT INTO snapshot_metadata "
        "(id, org_id, policy_id, name, path, size_bytes, memory_count, encrypted) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        sid,
        org_id,
        policy_id,
        name,
        path,
        size_bytes,
        memory_count,
        encrypted,
    )
    return sid


# ── list_policies ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_policies_passthrough(store):
    """list_policies returns all policies for the org."""
    p1 = await policies.create_policy(store, org_id=_ORG, name="list-pol-a")
    p2 = await policies.create_policy(store, org_id=_ORG, name="list-pol-b")

    results = await policies.list_policies(store, org_id=_ORG)

    ids = [r.id for r in results]
    assert p1.id in ids
    assert p2.id in ids


# ── get_policy ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_policy_returns_stored(store):
    """get_policy returns the policy with matching id and org."""
    created = await policies.create_policy(store, org_id=_ORG, name="get-pol-ok")

    fetched = await policies.get_policy(store, policy_id=created.id, org_id=_ORG)

    assert fetched.id == created.id
    assert fetched.name == "get-pol-ok"
    assert fetched.org_id == _ORG


@pytest.mark.asyncio
async def test_get_policy_404(store):
    """get_policy raises StoreNotFoundError for unknown id."""
    with pytest.raises(StoreNotFoundError):
        await policies.get_policy(
            store, policy_id="retpol_nonexistent", org_id=_ORG
        )


# ── create_policy ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_policy_round_trip(store):
    """create_policy persists all fields and returns a StoredRetentionPolicy."""
    window = {"working": 7200, "short": 86400, "long": None}
    created = await policies.create_policy(
        store,
        org_id=_ORG,
        name="create-pol-rt",
        retention_window=window,
        snapshot_schedule="0 3 * * *",
        encryption_required=True,
        max_snapshots=20,
        is_active=False,
    )

    assert created.id.startswith("retpol_")
    assert created.org_id == _ORG
    assert created.name == "create-pol-rt"
    assert created.retention_window == window
    assert created.snapshot_schedule == "0 3 * * *"
    assert created.encryption_required is True
    assert created.max_snapshots == 20
    assert created.is_active is False

    # Round-trip via get
    fetched = await policies.get_policy(store, policy_id=created.id, org_id=_ORG)
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_create_policy_uniqueness_propagates(store):
    """Duplicate (org_id, name) raises IntegrityError from the store."""
    await policies.create_policy(store, org_id=_ORG, name="dup-pol")

    with pytest.raises(IntegrityError):
        await policies.create_policy(store, org_id=_ORG, name="dup-pol")


# ── update_policy ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_policy_changes_field(store):
    """update_policy patches the specified fields and returns updated policy."""
    created = await policies.create_policy(
        store, org_id=_ORG, name="upd-pol-before", max_snapshots=10
    )

    patch = RetentionPolicyPatch(name="upd-pol-after", max_snapshots=99)
    updated = await policies.update_policy(
        store, policy_id=created.id, org_id=_ORG, patch=patch
    )

    assert updated.id == created.id
    assert updated.name == "upd-pol-after"
    assert updated.max_snapshots == 99


@pytest.mark.asyncio
async def test_update_policy_404(store):
    """update_policy raises StoreNotFoundError for unknown policy."""
    patch = RetentionPolicyPatch(name="ghost")
    with pytest.raises(StoreNotFoundError):
        await policies.update_policy(
            store, policy_id="retpol_ghost", org_id=_ORG, patch=patch
        )


@pytest.mark.asyncio
async def test_update_policy_empty_patch_raises(store):
    """update_policy with all-None patch raises ValueError."""
    created = await policies.create_policy(store, org_id=_ORG, name="upd-pol-empty")

    with pytest.raises(ValueError, match="No fields to update"):
        await policies.update_policy(
            store, policy_id=created.id, org_id=_ORG, patch=RetentionPolicyPatch()
        )


# ── delete_policy ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_policy_404(store):
    """delete_policy raises StoreNotFoundError for unknown policy."""
    with pytest.raises(StoreNotFoundError):
        await policies.delete_policy(
            store, policy_id="retpol_ghost", org_id=_ORG
        )


# ── run_drill ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_drill_with_snapshot_succeeds(store):
    """run_drill succeeds when a snapshot exists; status is 'success'."""
    created = await policies.create_policy(store, org_id=_ORG, name="drill-pol-snap")
    await _insert_snapshot(
        store, policy_id=created.id, org_id=_ORG, name="snap-drill", memory_count=5
    )

    result = await policies.run_drill(store, policy_id=created.id, org_id=_ORG)

    assert result.status == "success"
    assert result.memories_restored == 5
    assert result.snapshot_name == "snap-drill"
    assert result.error is None
    assert result.org_id == _ORG


@pytest.mark.asyncio
async def test_run_drill_without_snapshot_marks_failed(store):
    """run_drill without a snapshot returns status='failed' with an error message."""
    created = await policies.create_policy(store, org_id=_ORG, name="drill-pol-nosnap")

    result = await policies.run_drill(store, policy_id=created.id, org_id=_ORG)

    assert result.status == "failed"
    assert result.error == "No snapshot available"
    assert result.snapshot_name == "none"
    assert result.memories_restored == 0


# ── check_compliance ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_compliance_active_only(store):
    """check_compliance skips inactive policies and includes active ones."""
    active = await policies.create_policy(
        store, org_id=_ORG, name="compliance-active", is_active=True
    )
    await policies.create_policy(
        store, org_id=_ORG, name="compliance-inactive", is_active=False
    )

    results = await policies.check_compliance(store, org_id=_ORG)

    policy_ids = [r["policy_id"] for r in results]
    assert active.id in policy_ids
    # inactive policy should not appear
    assert all(r["policy_id"] != "compliance-inactive" for r in results)
