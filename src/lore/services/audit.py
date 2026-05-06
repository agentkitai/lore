"""Audit-log query service — passthrough to AuditOps."""

from __future__ import annotations

from typing import Optional, Sequence

from lore.persistence import Store, StoredAuditEntry


async def query_audit_log(
    store: Store,
    *,
    org_id: str,
    workspace_id: Optional[str] = None,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 50,
) -> Sequence[StoredAuditEntry]:
    """Query audit log with filters."""
    return await store.query_audit_log(
        org_id=org_id,
        workspace_id=workspace_id,
        action=action,
        actor_id=actor_id,
        since=since,
        limit=limit,
    )
