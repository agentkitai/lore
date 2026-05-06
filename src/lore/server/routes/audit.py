"""Audit log endpoints — GET /v1/audit."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, Query
except ImportError:
    raise ImportError("FastAPI is required.")

from pydantic import BaseModel

from lore.persistence import Store, StoredAuditEntry
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services import audit as audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/audit", tags=["audit"])


class AuditEntry(BaseModel):
    id: int
    org_id: str
    workspace_id: Optional[str] = None
    actor_id: str
    actor_type: str
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    metadata: Dict[str, Any] = {}
    ip_address: Optional[str] = None
    created_at: Optional[str] = None


def _to_audit_entry(e: StoredAuditEntry) -> AuditEntry:
    return AuditEntry(
        id=e.id,
        org_id=e.org_id,
        workspace_id=e.workspace_id,
        actor_id=e.actor_id,
        actor_type=e.actor_type,
        action=e.action,
        resource_type=e.resource_type,
        resource_id=e.resource_id,
        metadata=dict(e.metadata) if e.metadata else {},
        ip_address=str(e.ip_address) if e.ip_address else None,
        created_at=e.created_at.isoformat() if e.created_at else None,
    )


@router.get("", response_model=List[AuditEntry])
async def query_audit_log(
    workspace_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    actor_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None, description="ISO 8601 datetime"),
    limit: int = Query(50, ge=1, le=500),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[AuditEntry]:
    """Query the audit log with filters."""
    entries = await audit_service.query_audit_log(
        store,
        org_id=auth.org_id,
        workspace_id=workspace_id,
        action=action,
        actor_id=actor_id,
        since=since,
        limit=limit,
    )
    return [_to_audit_entry(e) for e in entries]
