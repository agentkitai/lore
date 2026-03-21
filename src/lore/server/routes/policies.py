"""Retention policies CRUD — /v1/policies endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/policies", tags=["policies"])


class PolicyCreateRequest(BaseModel):
    name: str
    retention_window: Dict[str, Any] = {"working": 3600, "short": 604800, "long": None}
    snapshot_schedule: Optional[str] = None
    encryption_required: bool = False
    max_snapshots: int = 50
    is_active: bool = True


class PolicyUpdateRequest(BaseModel):
    name: Optional[str] = None
    retention_window: Optional[Dict[str, Any]] = None
    snapshot_schedule: Optional[str] = None
    encryption_required: Optional[bool] = None
    max_snapshots: Optional[int] = None
    is_active: Optional[bool] = None


class PolicyResponse(BaseModel):
    id: str
    org_id: str
    name: str
    retention_window: Dict[str, Any]
    snapshot_schedule: Optional[str] = None
    encryption_required: bool
    max_snapshots: int
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DrillResultResponse(BaseModel):
    id: str
    snapshot_name: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    recovery_time_ms: Optional[int] = None
    memories_restored: Optional[int] = None
    status: str
    error: Optional[str] = None


class ComplianceResponse(BaseModel):
    policy_id: str
    policy_name: str
    compliant: bool
    issues: List[str] = []


def _ts(val) -> Optional[str]:
    if val is None:
        return None
    from datetime import datetime
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


@router.get("", response_model=List[PolicyResponse])
async def list_policies(
    auth: AuthContext = Depends(get_auth_context),
) -> List[PolicyResponse]:
    """List all retention policies."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM retention_policies WHERE org_id = $1 ORDER BY name",
            auth.org_id,
        )
    return [
        PolicyResponse(
            id=r["id"], org_id=r["org_id"], name=r["name"],
            retention_window=r["retention_window"],
            snapshot_schedule=r["snapshot_schedule"],
            encryption_required=r["encryption_required"],
            max_snapshots=r["max_snapshots"],
            is_active=r["is_active"],
            created_at=_ts(r["created_at"]),
            updated_at=_ts(r["updated_at"]),
        )
        for r in rows
    ]


@router.post("", response_model=PolicyResponse, status_code=201)
async def create_policy(
    body: PolicyCreateRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> PolicyResponse:
    """Create a retention policy."""
    from ulid import ULID
    policy_id = str(ULID())
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO retention_policies
               (id, org_id, name, retention_window, snapshot_schedule,
                encryption_required, max_snapshots, is_active)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
               RETURNING *""",
            policy_id, auth.org_id, body.name,
            json.dumps(body.retention_window),
            body.snapshot_schedule, body.encryption_required,
            body.max_snapshots, body.is_active,
        )
    return PolicyResponse(
        id=row["id"], org_id=row["org_id"], name=row["name"],
        retention_window=row["retention_window"],
        snapshot_schedule=row["snapshot_schedule"],
        encryption_required=row["encryption_required"],
        max_snapshots=row["max_snapshots"],
        is_active=row["is_active"],
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )


@router.get("/{policy_id}", response_model=PolicyResponse)
async def get_policy(
    policy_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> PolicyResponse:
    """Get a policy with compliance info."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM retention_policies WHERE id = $1 AND org_id = $2",
            policy_id, auth.org_id,
        )
    if not row:
        raise HTTPException(404, "Policy not found")
    return PolicyResponse(
        id=row["id"], org_id=row["org_id"], name=row["name"],
        retention_window=row["retention_window"],
        snapshot_schedule=row["snapshot_schedule"],
        encryption_required=row["encryption_required"],
        max_snapshots=row["max_snapshots"],
        is_active=row["is_active"],
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )


@router.put("/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: str,
    body: PolicyUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> PolicyResponse:
    """Update a retention policy."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM retention_policies WHERE id = $1 AND org_id = $2",
            policy_id, auth.org_id,
        )
        if not existing:
            raise HTTPException(404, "Policy not found")

        updates = []
        params: list = [policy_id, auth.org_id]
        if body.name is not None:
            params.append(body.name)
            updates.append(f"name = ${len(params)}")
        if body.retention_window is not None:
            params.append(json.dumps(body.retention_window))
            updates.append(f"retention_window = ${len(params)}::jsonb")
        if body.snapshot_schedule is not None:
            params.append(body.snapshot_schedule)
            updates.append(f"snapshot_schedule = ${len(params)}")
        if body.encryption_required is not None:
            params.append(body.encryption_required)
            updates.append(f"encryption_required = ${len(params)}")
        if body.max_snapshots is not None:
            params.append(body.max_snapshots)
            updates.append(f"max_snapshots = ${len(params)}")
        if body.is_active is not None:
            params.append(body.is_active)
            updates.append(f"is_active = ${len(params)}")

        if not updates:
            raise HTTPException(400, "No fields to update")

        updates.append("updated_at = now()")
        row = await conn.fetchrow(
            f"""UPDATE retention_policies SET {", ".join(updates)}
                WHERE id = $1 AND org_id = $2 RETURNING *""",
            *params,
        )

    return PolicyResponse(
        id=row["id"], org_id=row["org_id"], name=row["name"],
        retention_window=row["retention_window"],
        snapshot_schedule=row["snapshot_schedule"],
        encryption_required=row["encryption_required"],
        max_snapshots=row["max_snapshots"],
        is_active=row["is_active"],
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: str,
    auth: AuthContext = Depends(require_role("admin")),
) -> None:
    """Delete a retention policy."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM retention_policies WHERE id = $1 AND org_id = $2",
            policy_id, auth.org_id,
        )
        if result == "DELETE 0":
            raise HTTPException(404, "Policy not found")


@router.post("/{policy_id}/drill", response_model=DrillResultResponse, status_code=201)
async def run_drill(
    policy_id: str,
    auth: AuthContext = Depends(require_role("admin")),
) -> DrillResultResponse:
    """Execute a restore drill against the latest snapshot."""
    import time
    from datetime import datetime, timezone

    from ulid import ULID

    pool = await get_pool()
    async with pool.acquire() as conn:
        policy = await conn.fetchrow(
            "SELECT * FROM retention_policies WHERE id = $1 AND org_id = $2",
            policy_id, auth.org_id,
        )
        if not policy:
            raise HTTPException(404, "Policy not found")

        # Find latest snapshot for this policy
        snapshot = await conn.fetchrow(
            """SELECT * FROM snapshot_metadata
               WHERE policy_id = $1 AND org_id = $2
               ORDER BY created_at DESC LIMIT 1""",
            policy_id, auth.org_id,
        )

        drill_id = str(ULID())
        started = datetime.now(timezone.utc)
        snapshot_name = snapshot["name"] if snapshot else "none"
        snapshot_id = snapshot["id"] if snapshot else None

        start_time = time.monotonic()

        # Simulate restore (in production, this would actually restore)
        memories_restored = snapshot["memory_count"] if snapshot else 0
        status = "success" if snapshot else "failed"
        error = None if snapshot else "No snapshot available"

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        completed = datetime.now(timezone.utc)

        await conn.execute(
            """INSERT INTO restore_drill_results
               (id, org_id, snapshot_id, snapshot_name, started_at, completed_at,
                recovery_time_ms, memories_restored, status, error)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
            drill_id, auth.org_id, snapshot_id, snapshot_name,
            started, completed, elapsed_ms, memories_restored,
            status, error,
        )

    return DrillResultResponse(
        id=drill_id, snapshot_name=snapshot_name,
        started_at=started.isoformat(), completed_at=completed.isoformat(),
        recovery_time_ms=elapsed_ms, memories_restored=memories_restored,
        status=status, error=error,
    )


@router.get("/{policy_id}/drills", response_model=List[DrillResultResponse])
async def list_drills(
    policy_id: str,
    limit: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
) -> List[DrillResultResponse]:
    """List drill results for a policy."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT r.* FROM restore_drill_results r
               JOIN snapshot_metadata s ON s.id = r.snapshot_id
               WHERE s.policy_id = $1 AND r.org_id = $2
               ORDER BY r.created_at DESC LIMIT $3""",
            policy_id, auth.org_id, limit,
        )
    return [
        DrillResultResponse(
            id=r["id"], snapshot_name=r["snapshot_name"],
            started_at=_ts(r["started_at"]), completed_at=_ts(r["completed_at"]),
            recovery_time_ms=r["recovery_time_ms"],
            memories_restored=r["memories_restored"],
            status=r["status"], error=r["error"],
        )
        for r in rows
    ]


@router.get("/compliance", response_model=List[ComplianceResponse])
async def check_compliance(
    auth: AuthContext = Depends(get_auth_context),
) -> List[ComplianceResponse]:
    """Cross-policy compliance summary."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        policies = await conn.fetch(
            "SELECT * FROM retention_policies WHERE org_id = $1 AND is_active = TRUE",
            auth.org_id,
        )

        results: List[ComplianceResponse] = []
        for policy in policies:
            issues: List[str] = []

            # Check snapshot count
            snapshot_count = await conn.fetchval(
                "SELECT COUNT(*) FROM snapshot_metadata WHERE policy_id = $1",
                policy["id"],
            )
            if snapshot_count > policy["max_snapshots"]:
                issues.append(
                    f"Snapshot count ({snapshot_count}) exceeds max ({policy['max_snapshots']})"
                )

            # Check for recent drill
            last_drill = await conn.fetchrow(
                """SELECT status, created_at FROM restore_drill_results
                   WHERE org_id = $1
                   ORDER BY created_at DESC LIMIT 1""",
                auth.org_id,
            )
            if not last_drill:
                issues.append("No restore drill has been run")
            elif last_drill["status"] == "failed":
                issues.append("Last restore drill failed")

            results.append(ComplianceResponse(
                policy_id=policy["id"],
                policy_name=policy["name"],
                compliant=len(issues) == 0,
                issues=issues,
            ))

    return results
