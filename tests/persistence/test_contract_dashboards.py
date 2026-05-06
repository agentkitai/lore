"""Contract tests for the AuditOps slice of Store.

Covers query_audit_log with org isolation, workspace/action/actor/since
filters, ordering, and limit.
These tests run against every Store implementation (Phase 1I: Postgres only).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from lore.persistence import Store

# ── helpers ────────────────────────────────────────────────────────────────────


async def _ensure_org(store, org_id: str) -> None:
    """Insert an org row if it doesn't already exist (required by FK in other tables)."""
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


async def _insert_audit_entry(
    store,
    *,
    org_id: str = "org-a",
    workspace_id: str | None = None,
    actor_id: str = "actor-1",
    actor_type: str = "user",
    action: str = "memories.create",
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict | None = None,
    created_at: datetime | None = None,
) -> int:
    metadata_json = json.dumps(dict(metadata or {}))
    if created_at is None:
        row = await store._conn.fetchrow(
            """INSERT INTO audit_log (org_id, workspace_id, actor_id, actor_type, action, resource_type, resource_id, metadata)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb) RETURNING id""",
            org_id,
            workspace_id,
            actor_id,
            actor_type,
            action,
            resource_type,
            resource_id,
            metadata_json,
        )
    else:
        row = await store._conn.fetchrow(
            """INSERT INTO audit_log (org_id, workspace_id, actor_id, actor_type, action, resource_type, resource_id, metadata, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9) RETURNING id""",
            org_id,
            workspace_id,
            actor_id,
            actor_type,
            action,
            resource_type,
            resource_id,
            metadata_json,
            created_at,
        )
    return row["id"]


# ── query_audit_log tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_audit_log_returns_org_only(store: Store):
    """Entries from org_b must not appear when querying org_a."""
    id_a = await _insert_audit_entry(store, org_id="org-a")
    await _insert_audit_entry(store, org_id="org-b")

    results = await store.query_audit_log(org_id="org-a")

    ids = {r.id for r in results}
    assert id_a in ids
    # org-b entry must be absent
    assert all(r.org_id == "org-a" for r in results)


@pytest.mark.asyncio
async def test_query_audit_log_workspace_filter(store: Store):
    """workspace_id filter returns only matching entries."""
    id_ws1 = await _insert_audit_entry(store, org_id="org-wf", workspace_id="ws-1")
    id_ws2 = await _insert_audit_entry(store, org_id="org-wf", workspace_id="ws-2")
    id_none = await _insert_audit_entry(store, org_id="org-wf", workspace_id=None)

    results = await store.query_audit_log(org_id="org-wf", workspace_id="ws-1")

    ids = {r.id for r in results}
    assert id_ws1 in ids
    assert id_ws2 not in ids
    assert id_none not in ids


@pytest.mark.asyncio
async def test_query_audit_log_action_filter(store: Store):
    """action filter returns only entries with that action."""
    id_create = await _insert_audit_entry(store, org_id="org-af", action="memories.create")
    id_delete = await _insert_audit_entry(store, org_id="org-af", action="memories.delete")

    results = await store.query_audit_log(org_id="org-af", action="memories.create")

    ids = {r.id for r in results}
    assert id_create in ids
    assert id_delete not in ids


@pytest.mark.asyncio
async def test_query_audit_log_since_filter(store: Store):
    """since filter excludes entries older than the given timestamp."""
    old_dt = datetime.now(timezone.utc) - timedelta(hours=2)
    recent_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    id_old = await _insert_audit_entry(store, org_id="org-sf", created_at=old_dt)
    id_recent = await _insert_audit_entry(store, org_id="org-sf", created_at=recent_dt)

    results = await store.query_audit_log(org_id="org-sf", since=cutoff_ts)

    ids = {r.id for r in results}
    assert id_recent in ids
    assert id_old not in ids


@pytest.mark.asyncio
async def test_query_audit_log_orders_by_created_at_desc(store: Store):
    """Results must be ordered newest-first."""
    early_dt = datetime.now(timezone.utc) - timedelta(minutes=30)
    late_dt = datetime.now(timezone.utc) - timedelta(minutes=5)

    id_early = await _insert_audit_entry(store, org_id="org-ord", created_at=early_dt)
    id_late = await _insert_audit_entry(store, org_id="org-ord", created_at=late_dt)

    results = await store.query_audit_log(org_id="org-ord")

    assert len(results) >= 2
    ids = [r.id for r in results]
    assert ids.index(id_late) < ids.index(id_early)


@pytest.mark.asyncio
async def test_query_audit_log_respects_limit(store: Store):
    """limit parameter caps the number of returned rows."""
    for i in range(5):
        await _insert_audit_entry(store, org_id="org-lim", actor_id=f"actor-{i}")

    results = await store.query_audit_log(org_id="org-lim", limit=3)

    assert len(results) == 3
