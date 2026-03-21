"""Audit log endpoints — GET /v1/audit."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, Query
except ImportError:
    raise ImportError("FastAPI is required.")

from pydantic import BaseModel

from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_pool

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


def _ts(val) -> Optional[str]:
    if val is None:
        return None
    from datetime import datetime
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


@router.get("", response_model=List[AuditEntry])
async def query_audit_log(
    workspace_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    actor_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None, description="ISO 8601 datetime"),
    limit: int = Query(50, ge=1, le=500),
    auth: AuthContext = Depends(get_auth_context),
) -> List[AuditEntry]:
    """Query the audit log with filters."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        params: list = [auth.org_id]
        where_parts = ["org_id = $1"]

        if workspace_id:
            params.append(workspace_id)
            where_parts.append(f"workspace_id = ${len(params)}")
        if action:
            params.append(action)
            where_parts.append(f"action = ${len(params)}")
        if actor_id:
            params.append(actor_id)
            where_parts.append(f"actor_id = ${len(params)}")
        if since:
            params.append(since)
            where_parts.append(f"created_at >= ${len(params)}::timestamptz")

        params.append(limit)
        where_sql = " AND ".join(where_parts)

        rows = await conn.fetch(
            f"""SELECT * FROM audit_log
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ${len(params)}""",
            *params,
        )

    return [
        AuditEntry(
            id=r["id"], org_id=r["org_id"],
            workspace_id=r["workspace_id"],
            actor_id=r["actor_id"], actor_type=r["actor_type"],
            action=r["action"],
            resource_type=r["resource_type"],
            resource_id=r["resource_id"],
            metadata=r["metadata"] or {},
            ip_address=str(r["ip_address"]) if r["ip_address"] else None,
            created_at=_ts(r["created_at"]),
        )
        for r in rows
    ]
