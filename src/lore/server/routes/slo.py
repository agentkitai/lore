"""SLO Dashboard endpoints — CRUD for SLO definitions, status, and alerts."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.persistence import SloDefinitionPatch, Store, StoredSloAlert, StoredSloDefinition
from lore.persistence.exceptions import StoreNotFoundError
from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.services import slo as slo_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/slo", tags=["slo"])


# ── Request/Response Models ──────────────────────────────────────


class SloCreateRequest(BaseModel):
    name: str
    metric: str  # p50_latency, p95_latency, p99_latency, hit_rate
    operator: str  # lt, gt, gte, lte
    threshold: float
    window_minutes: int = 60
    enabled: bool = True
    alert_channels: List[Dict[str, Any]] = []


class SloUpdateRequest(BaseModel):
    name: Optional[str] = None
    metric: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[float] = None
    window_minutes: Optional[int] = None
    enabled: Optional[bool] = None
    alert_channels: Optional[List[Dict[str, Any]]] = None


class SloResponse(BaseModel):
    id: str
    org_id: str
    name: str
    metric: str
    operator: str
    threshold: float
    window_minutes: int
    enabled: bool
    alert_channels: List[Dict[str, Any]] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SloStatusResponse(BaseModel):
    id: str
    name: str
    metric: str
    threshold: float
    operator: str
    current_value: Optional[float] = None
    passing: bool = True
    window_minutes: int = 60


class SloAlertResponse(BaseModel):
    id: int
    slo_id: str
    metric_value: float
    threshold: float
    status: str
    dispatched_to: List[Dict[str, Any]] = []
    created_at: Optional[str] = None


# ── Translation helpers ──────────────────────────────────────────


def _to_slo_response(slo: StoredSloDefinition) -> SloResponse:
    return SloResponse(
        id=slo.id,
        org_id=slo.org_id,
        name=slo.name,
        metric=slo.metric,
        operator=slo.operator,
        threshold=slo.threshold,
        window_minutes=slo.window_minutes,
        enabled=slo.enabled,
        alert_channels=list(slo.alert_channels),
        created_at=slo.created_at.isoformat() if slo.created_at else None,
        updated_at=slo.updated_at.isoformat() if slo.updated_at else None,
    )


def _to_alert_response(alert: StoredSloAlert) -> SloAlertResponse:
    return SloAlertResponse(
        id=alert.id,
        slo_id=alert.slo_id,
        metric_value=alert.metric_value,
        threshold=alert.threshold,
        status=alert.status,
        dispatched_to=list(alert.dispatched_to),
        created_at=alert.created_at.isoformat() if alert.created_at else None,
    )


# ── CRUD Endpoints ───────────────────────────────────────────────


@router.get("", response_model=List[SloResponse])
async def list_slos(
    store: Store = Depends(get_store),
) -> List[SloResponse]:
    """List all SLO definitions."""
    results = await slo_service.list_slos(store)
    return [_to_slo_response(s) for s in results]


@router.post("", response_model=SloResponse, status_code=201)
async def create_slo(
    body: SloCreateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> SloResponse:
    """Create an SLO definition."""
    try:
        s = await slo_service.create_slo(
            store,
            org_id=auth.org_id,
            name=body.name,
            metric=body.metric,
            operator=body.operator,
            threshold=body.threshold,
            window_minutes=body.window_minutes,
            enabled=body.enabled,
            alert_channels=body.alert_channels,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_slo_response(s)


@router.put("/{slo_id}", response_model=SloResponse)
async def update_slo(
    slo_id: str,
    body: SloUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> SloResponse:
    """Update an SLO definition."""
    patch = SloDefinitionPatch(
        name=body.name,
        metric=body.metric,
        operator=body.operator,
        threshold=body.threshold,
        window_minutes=body.window_minutes,
        enabled=body.enabled,
        alert_channels=body.alert_channels,
    )
    try:
        s = await slo_service.update_slo(store, slo_id=slo_id, org_id=auth.org_id, patch=patch)
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="SLO not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_slo_response(s)


@router.delete("/{slo_id}", status_code=204)
async def delete_slo(
    slo_id: str,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> None:
    """Delete an SLO definition."""
    try:
        await slo_service.delete_slo(store, slo_id=slo_id, org_id=auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="SLO not found")


# ── Status & Alerts ──────────────────────────────────────────────


@router.get("/status", response_model=List[SloStatusResponse])
async def slo_status(
    store: Store = Depends(get_store),
) -> List[SloStatusResponse]:
    """Get current pass/fail status for all SLOs."""
    results = await slo_service.slo_status(store)
    return [
        SloStatusResponse(
            id=r["id"],
            name=r["name"],
            metric=r["metric"],
            threshold=r["threshold"],
            operator=r["operator"],
            current_value=r["current_value"],
            passing=r["passing"],
            window_minutes=r["window_minutes"],
        )
        for r in results
    ]


@router.get("/alerts", response_model=List[SloAlertResponse])
async def list_alerts(
    limit: int = Query(50, ge=1, le=500),
    slo_id: Optional[str] = Query(None),
    store: Store = Depends(get_store),
) -> List[SloAlertResponse]:
    """Get alert history."""
    alerts = await slo_service.list_alerts(store, slo_id=slo_id, limit=limit)
    return [_to_alert_response(a) for a in alerts]


@router.post("/{slo_id}/test", response_model=SloAlertResponse, status_code=201)
async def test_alert(
    slo_id: str,
    auth: AuthContext = Depends(require_role("admin")),
    store: Store = Depends(get_store),
) -> SloAlertResponse:
    """Fire a test alert for an SLO."""
    try:
        alert = await slo_service.test_alert(store, slo_id=slo_id, org_id=auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="SLO not found")
    return _to_alert_response(alert)


# ── Timeseries (analytics extension) ────────────────────────────


@router.get("/timeseries")
async def slo_timeseries(
    metric: str = Query("p99_latency"),
    window_hours: int = Query(24, ge=1, le=720),
    bucket_minutes: int = Query(60, ge=1, le=1440),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> Dict[str, Any]:
    """Time-series data for SLO charts."""
    try:
        return await slo_service.slo_timeseries(
            store,
            org_id=auth.org_id,
            metric=metric,
            window_hours=window_hours,
            bucket_minutes=bucket_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
