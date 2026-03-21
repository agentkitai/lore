"""SLO Dashboard endpoints — CRUD for SLO definitions, status, and alerts."""

from __future__ import annotations

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

router = APIRouter(prefix="/v1/slo", tags=["slo"])


# ── Request/Response Models ──────────────────────────────────────


class SloCreateRequest(BaseModel):
    name: str
    metric: str  # p50_latency, p95_latency, p99_latency, hit_rate
    operator: str  # lt, gt
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


VALID_METRICS = {"p50_latency", "p95_latency", "p99_latency", "hit_rate"}
VALID_OPERATORS = {"lt", "gt"}


def _ts(val) -> Optional[str]:
    if val is None:
        return None
    from datetime import datetime
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _parse_jsonb(val) -> List[Dict[str, Any]]:
    """Safely parse a JSONB column that may come back as str or list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        import json as _json
        try:
            parsed = _json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


# ── CRUD Endpoints ───────────────────────────────────────────────


@router.get("", response_model=List[SloResponse])
async def list_slos(
    auth: AuthContext = Depends(get_auth_context),
) -> List[SloResponse]:
    """List all SLO definitions for the org."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, org_id, name, metric, operator, threshold,
                      window_minutes, enabled, alert_channels, created_at, updated_at
               FROM slo_definitions
               WHERE org_id = $1
               ORDER BY created_at DESC""",
            auth.org_id,
        )
    return [
        SloResponse(
            id=r["id"], org_id=r["org_id"], name=r["name"],
            metric=r["metric"], operator=r["operator"],
            threshold=float(r["threshold"]),
            window_minutes=r["window_minutes"], enabled=r["enabled"],
            alert_channels=_parse_jsonb(r.get("alert_channels")),
            created_at=_ts(r["created_at"]), updated_at=_ts(r["updated_at"]),
        )
        for r in rows
    ]


@router.post("", response_model=SloResponse, status_code=201)
async def create_slo(
    body: SloCreateRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> SloResponse:
    """Create an SLO definition."""
    if body.metric not in VALID_METRICS:
        raise HTTPException(400, f"Invalid metric: {body.metric}. Must be one of: {VALID_METRICS}")
    if body.operator not in VALID_OPERATORS:
        raise HTTPException(400, f"Invalid operator: {body.operator}. Must be one of: {VALID_OPERATORS}")

    import json

    from ulid import ULID

    slo_id = str(ULID())
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO slo_definitions (id, org_id, name, metric, operator, threshold,
                                           window_minutes, enabled, alert_channels)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
               RETURNING id, org_id, name, metric, operator, threshold,
                         window_minutes, enabled, alert_channels, created_at, updated_at""",
            slo_id, auth.org_id, body.name, body.metric, body.operator,
            body.threshold, body.window_minutes, body.enabled,
            json.dumps(body.alert_channels),
        )

    return SloResponse(
        id=row["id"], org_id=row["org_id"], name=row["name"],
        metric=row["metric"], operator=row["operator"],
        threshold=float(row["threshold"]),
        window_minutes=row["window_minutes"], enabled=row["enabled"],
        alert_channels=row["alert_channels"] or [],
        created_at=_ts(row["created_at"]), updated_at=_ts(row["updated_at"]),
    )


@router.put("/{slo_id}", response_model=SloResponse)
async def update_slo(
    slo_id: str,
    body: SloUpdateRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> SloResponse:
    """Update an SLO definition."""
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM slo_definitions WHERE id = $1 AND org_id = $2",
            slo_id, auth.org_id,
        )
        if not existing:
            raise HTTPException(404, "SLO not found")

        updates = []
        params: list = [slo_id, auth.org_id]
        if body.name is not None:
            params.append(body.name)
            updates.append(f"name = ${len(params)}")
        if body.metric is not None:
            if body.metric not in VALID_METRICS:
                raise HTTPException(400, f"Invalid metric: {body.metric}")
            params.append(body.metric)
            updates.append(f"metric = ${len(params)}")
        if body.operator is not None:
            if body.operator not in VALID_OPERATORS:
                raise HTTPException(400, f"Invalid operator: {body.operator}")
            params.append(body.operator)
            updates.append(f"operator = ${len(params)}")
        if body.threshold is not None:
            params.append(body.threshold)
            updates.append(f"threshold = ${len(params)}")
        if body.window_minutes is not None:
            params.append(body.window_minutes)
            updates.append(f"window_minutes = ${len(params)}")
        if body.enabled is not None:
            params.append(body.enabled)
            updates.append(f"enabled = ${len(params)}")
        if body.alert_channels is not None:
            params.append(json.dumps(body.alert_channels))
            updates.append(f"alert_channels = ${len(params)}::jsonb")

        if not updates:
            raise HTTPException(400, "No fields to update")

        updates.append("updated_at = now()")
        set_clause = ", ".join(updates)

        row = await conn.fetchrow(
            f"""UPDATE slo_definitions SET {set_clause}
                WHERE id = $1 AND org_id = $2
                RETURNING id, org_id, name, metric, operator, threshold,
                          window_minutes, enabled, alert_channels, created_at, updated_at""",
            *params,
        )

    return SloResponse(
        id=row["id"], org_id=row["org_id"], name=row["name"],
        metric=row["metric"], operator=row["operator"],
        threshold=float(row["threshold"]),
        window_minutes=row["window_minutes"], enabled=row["enabled"],
        alert_channels=row["alert_channels"] or [],
        created_at=_ts(row["created_at"]), updated_at=_ts(row["updated_at"]),
    )


@router.delete("/{slo_id}", status_code=204)
async def delete_slo(
    slo_id: str,
    auth: AuthContext = Depends(require_role("admin")),
) -> None:
    """Delete an SLO definition."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM slo_definitions WHERE id = $1 AND org_id = $2",
            slo_id, auth.org_id,
        )
        if result == "DELETE 0":
            raise HTTPException(404, "SLO not found")


# ── Status & Alerts ──────────────────────────────────────────────


@router.get("/status", response_model=List[SloStatusResponse])
async def slo_status(
    auth: AuthContext = Depends(get_auth_context),
) -> List[SloStatusResponse]:
    """Get current pass/fail status for all SLOs."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        slos = await conn.fetch(
            """SELECT id, name, metric, operator, threshold, window_minutes, enabled
               FROM slo_definitions
               WHERE org_id = $1 AND enabled = TRUE
               ORDER BY name""",
            auth.org_id,
        )

        results: List[SloStatusResponse] = []
        for slo in slos:
            current_value = await _compute_metric(
                conn, auth.org_id, slo["metric"], slo["window_minutes"],
            )
            passing = _check_threshold(
                current_value, slo["operator"], float(slo["threshold"]),
            )
            results.append(SloStatusResponse(
                id=slo["id"], name=slo["name"],
                metric=slo["metric"],
                threshold=float(slo["threshold"]),
                operator=slo["operator"],
                current_value=current_value,
                passing=passing,
                window_minutes=slo["window_minutes"],
            ))

    return results


@router.get("/alerts", response_model=List[SloAlertResponse])
async def list_alerts(
    limit: int = Query(50, ge=1, le=500),
    slo_id: Optional[str] = Query(None),
    auth: AuthContext = Depends(get_auth_context),
) -> List[SloAlertResponse]:
    """Get alert history."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        params: list = [auth.org_id]
        where_parts = ["a.org_id = $1"]

        if slo_id:
            params.append(slo_id)
            where_parts.append(f"a.slo_id = ${len(params)}")

        params.append(limit)
        where_sql = " AND ".join(where_parts)

        rows = await conn.fetch(
            f"""SELECT a.id, a.slo_id, a.metric_value, a.threshold,
                       a.status, a.dispatched_to, a.created_at
                FROM slo_alerts a
                WHERE {where_sql}
                ORDER BY a.created_at DESC
                LIMIT ${len(params)}""",
            *params,
        )

    return [
        SloAlertResponse(
            id=r["id"], slo_id=r["slo_id"],
            metric_value=float(r["metric_value"]),
            threshold=float(r["threshold"]),
            status=r["status"],
            dispatched_to=_parse_jsonb(r.get("dispatched_to")),
            created_at=_ts(r["created_at"]),
        )
        for r in rows
    ]


@router.post("/{slo_id}/test", response_model=SloAlertResponse, status_code=201)
async def test_alert(
    slo_id: str,
    auth: AuthContext = Depends(require_role("admin")),
) -> SloAlertResponse:
    """Fire a test alert for an SLO."""
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        slo = await conn.fetchrow(
            "SELECT * FROM slo_definitions WHERE id = $1 AND org_id = $2",
            slo_id, auth.org_id,
        )
        if not slo:
            raise HTTPException(404, "SLO not found")

        row = await conn.fetchrow(
            """INSERT INTO slo_alerts (org_id, slo_id, metric_value, threshold, status, dispatched_to)
               VALUES ($1, $2, $3, $4, 'firing', $5::jsonb)
               RETURNING id, slo_id, metric_value, threshold, status, dispatched_to, created_at""",
            auth.org_id, slo_id, 0.0, float(slo["threshold"]),
            json.dumps([{"channel": "test", "sent_at": _ts(None)}]),
        )

    return SloAlertResponse(
        id=row["id"], slo_id=row["slo_id"],
        metric_value=float(row["metric_value"]),
        threshold=float(row["threshold"]),
        status=row["status"],
        dispatched_to=row["dispatched_to"] or [],
        created_at=_ts(row["created_at"]),
    )


# ── Timeseries (analytics extension) ────────────────────────────


@router.get("/timeseries")
async def slo_timeseries(
    metric: str = Query("p99_latency"),
    window_hours: int = Query(24, ge=1, le=720),
    bucket_minutes: int = Query(60, ge=1, le=1440),
    auth: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Time-series data for SLO charts."""
    if metric not in VALID_METRICS:
        raise HTTPException(400, f"Invalid metric: {metric}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        metric_sql = _metric_sql(metric)
        rows = await conn.fetch(
            f"""SELECT
                    date_trunc('hour', created_at) +
                    (EXTRACT(minute FROM created_at)::int / $3 * $3) * interval '1 minute'
                    AS bucket,
                    {metric_sql}
                FROM retrieval_events
                WHERE org_id = $1
                  AND created_at >= now() - make_interval(hours => $2)
                GROUP BY bucket
                ORDER BY bucket""",
            auth.org_id, window_hours, bucket_minutes,
        )

    return {
        "metric": metric,
        "window_hours": window_hours,
        "bucket_minutes": bucket_minutes,
        "data": [
            {
                "timestamp": _ts(r["bucket"]),
                "value": round(float(r["value"]), 4) if r["value"] is not None else None,
            }
            for r in rows
        ],
    }


# ── Helpers ──────────────────────────────────────────────────────


async def _compute_metric(
    conn, org_id: str, metric: str, window_minutes: int,
) -> Optional[float]:
    """Compute a metric value from retrieval_events within a window."""
    metric_sql = _metric_sql(metric)
    row = await conn.fetchrow(
        f"""SELECT {metric_sql}
            FROM retrieval_events
            WHERE org_id = $1
              AND created_at >= now() - make_interval(mins => $2)""",
        org_id, window_minutes,
    )
    if row and row["value"] is not None:
        return round(float(row["value"]), 4)
    return None


def _metric_sql(metric: str) -> str:
    """Return the SQL expression for a given metric."""
    return {
        "p50_latency": "percentile_cont(0.50) WITHIN GROUP (ORDER BY query_time_ms) AS value",
        "p95_latency": "percentile_cont(0.95) WITHIN GROUP (ORDER BY query_time_ms) AS value",
        "p99_latency": "percentile_cont(0.99) WITHIN GROUP (ORDER BY query_time_ms) AS value",
        "hit_rate": "(COUNT(*) FILTER (WHERE results_count > 0))::float / GREATEST(COUNT(*), 1) AS value",
    }[metric]


def _check_threshold(
    value: Optional[float], operator: str, threshold: float,
) -> bool:
    """Check if a metric value passes the SLO threshold."""
    if value is None:
        return True  # No data = passing (no violation)
    if operator == "lt":
        return value < threshold
    if operator == "gt":
        return value > threshold
    return True
