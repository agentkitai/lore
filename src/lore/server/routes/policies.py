"""Retention policies CRUD — /v1/policies endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.persistence import RetentionPolicyPatch, Store, StoredDrillResult, StoredRetentionPolicy
from lore.persistence.exceptions import IntegrityError, StoreNotFoundError
from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.services import policies as policies_service

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


# ── Translation helpers ────────────────────────────────────────────


def _to_policy_response(p: StoredRetentionPolicy) -> PolicyResponse:
    return PolicyResponse(
        id=p.id,
        org_id=p.org_id,
        name=p.name,
        retention_window=dict(p.retention_window),
        snapshot_schedule=p.snapshot_schedule,
        encryption_required=p.encryption_required,
        max_snapshots=p.max_snapshots,
        is_active=p.is_active,
        created_at=p.created_at.isoformat() if p.created_at else None,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


def _to_drill_response(d: StoredDrillResult) -> DrillResultResponse:
    return DrillResultResponse(
        id=d.id,
        snapshot_name=d.snapshot_name,
        started_at=d.started_at.isoformat() if d.started_at else None,
        completed_at=d.completed_at.isoformat() if d.completed_at else None,
        recovery_time_ms=d.recovery_time_ms,
        memories_restored=d.memories_restored,
        status=d.status,
        error=d.error,
    )


def _to_compliance_response(d: dict) -> ComplianceResponse:
    return ComplianceResponse(
        policy_id=d["policy_id"],
        policy_name=d["policy_name"],
        compliant=d["compliant"],
        issues=d["issues"],
    )


# ── Handlers ──────────────────────────────────────────────────────


@router.get("", response_model=List[PolicyResponse])
async def list_policies(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[PolicyResponse]:
    """List all retention policies."""
    results = await policies_service.list_policies(store, org_id=auth.org_id)
    return [_to_policy_response(p) for p in results]


@router.post("", response_model=PolicyResponse, status_code=201)
async def create_policy(
    body: PolicyCreateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> PolicyResponse:
    """Create a retention policy."""
    try:
        p = await policies_service.create_policy(
            store,
            org_id=auth.org_id,
            name=body.name,
            retention_window=body.retention_window,
            snapshot_schedule=body.snapshot_schedule,
            encryption_required=body.encryption_required,
            max_snapshots=body.max_snapshots,
            is_active=body.is_active,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Policy name already exists")
    return _to_policy_response(p)


@router.get("/compliance", response_model=List[ComplianceResponse])
async def check_compliance(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[ComplianceResponse]:
    """Cross-policy compliance summary."""
    results = await policies_service.check_compliance(store, org_id=auth.org_id)
    return [_to_compliance_response(r) for r in results]


@router.get("/{policy_id}", response_model=PolicyResponse)
async def get_policy(
    policy_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> PolicyResponse:
    """Get a policy."""
    try:
        p = await policies_service.get_policy(store, policy_id=policy_id, org_id=auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _to_policy_response(p)


@router.put("/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: str,
    body: PolicyUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> PolicyResponse:
    """Update a retention policy."""
    patch = RetentionPolicyPatch(
        name=body.name,
        retention_window=body.retention_window,
        snapshot_schedule=body.snapshot_schedule,
        encryption_required=body.encryption_required,
        max_snapshots=body.max_snapshots,
        is_active=body.is_active,
    )
    try:
        p = await policies_service.update_policy(
            store, policy_id=policy_id, org_id=auth.org_id, patch=patch
        )
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Policy not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="No fields to update")
    return _to_policy_response(p)


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: str,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> None:
    """Delete a retention policy."""
    try:
        await policies_service.delete_policy(store, policy_id=policy_id, org_id=auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Policy not found")


@router.post("/{policy_id}/drill", response_model=DrillResultResponse, status_code=201)
async def run_drill(
    policy_id: str,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> DrillResultResponse:
    """Execute a restore drill against the latest snapshot."""
    try:
        d = await policies_service.run_drill(store, policy_id=policy_id, org_id=auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _to_drill_response(d)


@router.get("/{policy_id}/drills", response_model=List[DrillResultResponse])
async def list_drills(
    policy_id: str,
    limit: int = Query(20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[DrillResultResponse]:
    """List drill results for a policy."""
    try:
        drills = await policies_service.list_drills(
            store, policy_id=policy_id, org_id=auth.org_id, limit=limit
        )
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Policy not found")
    return [_to_drill_response(d) for d in drills]
