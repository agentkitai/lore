"""Service tests for lore.services.audit."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from lore.services import audit

# ── helpers ───────────────────────────────────────────────────────────────────


async def _insert_audit_entry(
    store,
    *,
    org_id: str = "org-audit",
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
            """INSERT INTO audit_log
               (org_id, workspace_id, actor_id, actor_type, action,
                resource_type, resource_id, metadata)
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
            """INSERT INTO audit_log
               (org_id, workspace_id, actor_id, actor_type, action,
                resource_type, resource_id, metadata, created_at)
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


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_passthrough_returns_entries(store):
    """query_audit_log returns entries for the given org."""
    org = "audit-t1"
    entry_id = await _insert_audit_entry(store, org_id=org, action="memories.create")

    results = await audit.query_audit_log(store, org_id=org)

    ids = {r.id for r in results}
    assert entry_id in ids
    assert all(r.org_id == org for r in results)


@pytest.mark.asyncio
async def test_workspace_filter(store):
    """workspace_id filter restricts results to matching entries only."""
    org = "audit-t2"
    id_ws1 = await _insert_audit_entry(store, org_id=org, workspace_id="ws-a")
    id_ws2 = await _insert_audit_entry(store, org_id=org, workspace_id="ws-b")

    results = await audit.query_audit_log(store, org_id=org, workspace_id="ws-a")

    ids = {r.id for r in results}
    assert id_ws1 in ids
    assert id_ws2 not in ids


@pytest.mark.asyncio
async def test_limit_respected(store):
    """limit parameter caps the number of returned entries."""
    org = "audit-t3"
    for i in range(5):
        await _insert_audit_entry(store, org_id=org, action=f"event.{i}")

    results = await audit.query_audit_log(store, org_id=org, limit=2)

    assert len(results) == 2
