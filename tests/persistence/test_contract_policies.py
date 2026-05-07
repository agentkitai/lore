"""Contract tests for the RetentionOps slice of Store — policy CRUD.

These tests run against every Store implementation (Phase 1J: Postgres only).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lore.persistence import (
    IntegrityError,
    NewDrillResult,
    NewRetentionPolicy,
    RetentionPolicyPatch,
    Store,
    StoredRetentionPolicy,
)
from tests.persistence.conftest import _is_sqlite

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
    if _is_sqlite(store):
        await store._conn.execute(
            """
            INSERT INTO retention_policies
                (id, org_id, name, retention_window, snapshot_schedule,
                 encryption_required, max_snapshots, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy_id,
                org_id,
                name,
                json.dumps(rw),
                snapshot_schedule,
                1 if encryption_required else 0,
                max_snapshots,
                1 if is_active else 0,
            ),
        )
        await store._conn.commit()
    else:
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


# ── snapshot helpers + tests ───────────────────────────────────────────────────


async def _insert_snapshot(
    store,
    *,
    policy_id=None,
    org_id="solo",
    name="snap-x",
    path="/tmp/snap-x",
    size_bytes=100,
    memory_count=10,
    encrypted=False,
    created_at=None,
) -> str:
    from ulid import ULID

    sid = f"snap_{ULID()}"
    if _is_sqlite(store):
        if created_at is None:
            await store._conn.execute(
                "INSERT INTO snapshot_metadata (id, org_id, policy_id, name, path, size_bytes, memory_count, encrypted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sid,
                    org_id,
                    policy_id,
                    name,
                    path,
                    size_bytes,
                    memory_count,
                    1 if encrypted else 0,
                ),
            )
        else:
            await store._conn.execute(
                "INSERT INTO snapshot_metadata (id, org_id, policy_id, name, path, size_bytes, memory_count, encrypted, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sid,
                    org_id,
                    policy_id,
                    name,
                    path,
                    size_bytes,
                    memory_count,
                    1 if encrypted else 0,
                    created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                ),
            )
        await store._conn.commit()
    else:
        if created_at is None:
            await store._conn.execute(
                "INSERT INTO snapshot_metadata (id, org_id, policy_id, name, path, size_bytes, memory_count, encrypted) "
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
        else:
            await store._conn.execute(
                "INSERT INTO snapshot_metadata (id, org_id, policy_id, name, path, size_bytes, memory_count, encrypted, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                sid,
                org_id,
                policy_id,
                name,
                path,
                size_bytes,
                memory_count,
                encrypted,
                created_at,
            )
    return sid


@pytest.mark.asyncio
async def test_get_latest_snapshot_returns_most_recent(store: Store):
    policy_id = await _insert_policy(store, org_id="org-snap", name="snap-policy")
    t1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2024, 1, 2, tzinfo=timezone.utc)
    t3 = datetime(2024, 1, 3, tzinfo=timezone.utc)
    await _insert_snapshot(store, policy_id=policy_id, org_id="org-snap", name="oldest", created_at=t1)
    await _insert_snapshot(store, policy_id=policy_id, org_id="org-snap", name="middle", created_at=t2)
    await _insert_snapshot(store, policy_id=policy_id, org_id="org-snap", name="newest", created_at=t3)

    result = await store.get_latest_snapshot_for_policy(policy_id, "org-snap")

    assert result is not None
    assert result.name == "newest"
    assert result.policy_id == policy_id


@pytest.mark.asyncio
async def test_get_latest_snapshot_returns_none_when_none(store: Store):
    policy_id = await _insert_policy(store, org_id="org-nosnap", name="no-snap-pol")

    result = await store.get_latest_snapshot_for_policy(policy_id, "org-nosnap")

    assert result is None


@pytest.mark.asyncio
async def test_count_snapshots_for_policy(store: Store):
    policy_id = await _insert_policy(store, org_id="org-count", name="count-policy")
    await _insert_snapshot(store, policy_id=policy_id, org_id="org-count", name="s1")
    await _insert_snapshot(store, policy_id=policy_id, org_id="org-count", name="s2")
    await _insert_snapshot(store, policy_id=policy_id, org_id="org-count", name="s3")

    count = await store.count_snapshots_for_policy(policy_id)

    assert count == 3


# ── drill result tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_drill_result_round_trip(store: Store):
    now = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    drill = NewDrillResult(
        org_id="org-drill",
        snapshot_id=None,
        snapshot_name="snap-test",
        started_at=now,
        completed_at=None,
        recovery_time_ms=1500,
        memories_restored=42,
        status="success",
        error=None,
    )

    stored = await store.record_drill_result(drill)

    assert stored.id.startswith("drill_")
    assert stored.org_id == "org-drill"
    assert stored.snapshot_name == "snap-test"
    assert stored.recovery_time_ms == 1500
    assert stored.memories_restored == 42
    assert stored.status == "success"
    assert stored.error is None


@pytest.mark.asyncio
async def test_list_drill_results_joins_snapshot(store: Store):
    policy_id = await _insert_policy(store, org_id="org-list-drill", name="drill-policy")
    snap_id = await _insert_snapshot(store, policy_id=policy_id, org_id="org-list-drill", name="snap-drill")

    now = datetime(2024, 7, 1, tzinfo=timezone.utc)
    drill = NewDrillResult(
        org_id="org-list-drill",
        snapshot_id=snap_id,
        snapshot_name="snap-drill",
        started_at=now,
        completed_at=None,
        recovery_time_ms=200,
        memories_restored=5,
        status="success",
    )
    await store.record_drill_result(drill)

    results = await store.list_drill_results_for_policy(policy_id, "org-list-drill")

    assert len(results) == 1
    assert results[0].snapshot_id == snap_id
    assert results[0].snapshot_name == "snap-drill"


@pytest.mark.asyncio
async def test_list_drill_results_org_isolation(store: Store):
    policy_id = await _insert_policy(store, org_id="org-iso-a", name="iso-policy")
    snap_id = await _insert_snapshot(store, policy_id=policy_id, org_id="org-iso-a", name="snap-iso")

    now = datetime(2024, 8, 1, tzinfo=timezone.utc)
    drill_a = NewDrillResult(
        org_id="org-iso-a",
        snapshot_id=snap_id,
        snapshot_name="snap-iso",
        started_at=now,
        completed_at=None,
        recovery_time_ms=100,
        memories_restored=1,
        status="success",
    )
    drill_b = NewDrillResult(
        org_id="org-iso-b",
        snapshot_id=snap_id,
        snapshot_name="snap-iso",
        started_at=now,
        completed_at=None,
        recovery_time_ms=100,
        memories_restored=1,
        status="success",
    )
    await store.record_drill_result(drill_a)
    await store.record_drill_result(drill_b)

    results_a = await store.list_drill_results_for_policy(policy_id, "org-iso-a")
    results_b = await store.list_drill_results_for_policy(policy_id, "org-iso-b")

    assert len(results_a) == 1
    assert results_a[0].org_id == "org-iso-a"
    assert len(results_b) == 1
    assert results_b[0].org_id == "org-iso-b"


@pytest.mark.asyncio
async def test_get_latest_drill_result_org_filtered(store: Store):
    now = datetime(2024, 9, 1, tzinfo=timezone.utc)
    drill_x = NewDrillResult(
        org_id="org-latest-x",
        snapshot_id=None,
        snapshot_name="snap-x",
        started_at=now,
        completed_at=None,
        recovery_time_ms=50,
        memories_restored=3,
        status="success",
    )
    drill_y = NewDrillResult(
        org_id="org-latest-y",
        snapshot_id=None,
        snapshot_name="snap-y",
        started_at=now,
        completed_at=None,
        recovery_time_ms=75,
        memories_restored=7,
        status="success",
    )
    await store.record_drill_result(drill_x)
    await store.record_drill_result(drill_y)

    result = await store.get_latest_drill_result("org-latest-x")

    assert result is not None
    assert result.org_id == "org-latest-x"
    assert result.snapshot_name == "snap-x"


@pytest.mark.asyncio
async def test_get_latest_drill_result_returns_none_when_none(store: Store):
    result = await store.get_latest_drill_result("org-no-drills-at-all")
    assert result is None
