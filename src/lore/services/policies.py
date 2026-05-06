"""Retention policies service."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from lore.persistence import (
    NewDrillResult,
    NewRetentionPolicy,
    RetentionPolicyPatch,
    Store,
    StoredDrillResult,
    StoredRetentionPolicy,
)
from lore.persistence.exceptions import StoreNotFoundError

logger = logging.getLogger(__name__)


async def list_policies(store: Store, *, org_id: str) -> Sequence[StoredRetentionPolicy]:
    return await store.list_retention_policies(org_id)


async def get_policy(store: Store, *, policy_id: str, org_id: str) -> StoredRetentionPolicy:
    p = await store.get_retention_policy(policy_id, org_id)
    if p is None:
        raise StoreNotFoundError("retention_policies", policy_id)
    return p


async def create_policy(
    store: Store,
    *,
    org_id: str,
    name: str,
    retention_window: Optional[Mapping[str, Any]] = None,
    snapshot_schedule: Optional[str] = None,
    encryption_required: bool = False,
    max_snapshots: int = 50,
    is_active: bool = True,
) -> StoredRetentionPolicy:
    nm_retention = retention_window if retention_window is not None else {"working": 3600, "short": 604800, "long": None}
    new_policy = NewRetentionPolicy(
        org_id=org_id, name=name,
        retention_window=dict(nm_retention),
        snapshot_schedule=snapshot_schedule,
        encryption_required=encryption_required,
        max_snapshots=max_snapshots,
        is_active=is_active,
    )
    return await store.create_retention_policy(new_policy)


async def update_policy(
    store: Store,
    *,
    policy_id: str,
    org_id: str,
    patch: RetentionPolicyPatch,
) -> StoredRetentionPolicy:
    # Pre-fetch for clean 404
    existing = await store.get_retention_policy(policy_id, org_id)
    if existing is None:
        raise StoreNotFoundError("retention_policies", policy_id)
    # Empty-patch check (Store also raises but service-layer is cleaner)
    has_field = any(getattr(patch, f) is not None for f in
                    ("name", "retention_window", "snapshot_schedule",
                     "encryption_required", "max_snapshots", "is_active"))
    if not has_field:
        raise ValueError("No fields to update")
    updated = await store.update_retention_policy(policy_id, org_id, patch)
    if updated is None:
        # Race; treat as not-found
        raise StoreNotFoundError("retention_policies", policy_id)
    return updated


async def delete_policy(store: Store, *, policy_id: str, org_id: str) -> None:
    deleted = await store.delete_retention_policy(policy_id, org_id)
    if not deleted:
        raise StoreNotFoundError("retention_policies", policy_id)


async def run_drill(
    store: Store,
    *,
    policy_id: str,
    org_id: str,
) -> StoredDrillResult:
    """Execute a (simulated) restore drill. Records a drill_results row."""
    # Verify policy exists; fetches via get_policy raises 404
    await get_policy(store, policy_id=policy_id, org_id=org_id)
    snapshot = await store.get_latest_snapshot_for_policy(policy_id, org_id)

    started = datetime.now(timezone.utc)
    start_time = time.monotonic()
    snapshot_name = snapshot.name if snapshot else "none"
    snapshot_id = snapshot.id if snapshot else None
    memories_restored = snapshot.memory_count if (snapshot and snapshot.memory_count is not None) else 0
    status = "success" if snapshot else "failed"
    error: Optional[str] = None if snapshot else "No snapshot available"
    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    completed = datetime.now(timezone.utc)

    drill = NewDrillResult(
        org_id=org_id,
        snapshot_id=snapshot_id,
        snapshot_name=snapshot_name,
        started_at=started,
        completed_at=completed,
        recovery_time_ms=elapsed_ms,
        memories_restored=memories_restored,
        status=status,
        error=error,
    )
    return await store.record_drill_result(drill)


async def list_drills(
    store: Store,
    *,
    policy_id: str,
    org_id: str,
    limit: int = 20,
) -> Sequence[StoredDrillResult]:
    # Verify policy exists for clean 404
    await get_policy(store, policy_id=policy_id, org_id=org_id)
    return await store.list_drill_results_for_policy(policy_id, org_id, limit=limit)


async def check_compliance(store: Store, *, org_id: str) -> list[dict]:
    """Cross-policy compliance summary."""
    policies = await store.list_retention_policies(org_id)
    last_drill = await store.get_latest_drill_result(org_id)
    results: list[dict] = []
    for policy in policies:
        if not policy.is_active:
            continue
        issues: list[str] = []
        snapshot_count = await store.count_snapshots_for_policy(policy.id)
        if snapshot_count > policy.max_snapshots:
            issues.append(
                f"Snapshot count ({snapshot_count}) exceeds max ({policy.max_snapshots})"
            )
        if last_drill is None:
            issues.append("No restore drill has been run")
        elif last_drill.status == "failed":
            issues.append("Last restore drill failed")
        results.append({
            "policy_id": policy.id,
            "policy_name": policy.name,
            "compliant": len(issues) == 0,
            "issues": issues,
        })
    return results
