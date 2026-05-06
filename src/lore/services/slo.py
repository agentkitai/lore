"""SLO service — definition CRUD, alerts, status, and metric computation."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

from lore.persistence import (
    NewSloAlert,
    NewSloDefinition,
    SloDefinitionPatch,
    Store,
    StoredSloAlert,
    StoredSloDefinition,
    TimeseriesPoint,
)
from lore.persistence.exceptions import StoreNotFoundError

logger = logging.getLogger(__name__)


VALID_METRICS = {
    "p50_latency", "p95_latency", "p99_latency", "hit_rate",
    "retrieval_latency_p95", "retrieval_recall", "uptime_pct",
}
VALID_OPERATORS = {"lt", "gt", "gte", "lte"}


def _check_threshold(
    value: Optional[float], operator: str, threshold: float,
) -> bool:
    """No data = passing (matches pre-1K behaviour)."""
    if value is None:
        return True
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    return True


async def list_slos(
    store: Store,
    *,
    org_id: Optional[str] = None,
) -> Sequence[StoredSloDefinition]:
    """Passthrough to store.list_slo_definitions."""
    return await store.list_slo_definitions(org_id)


async def create_slo(
    store: Store,
    *,
    org_id: str,
    name: str,
    metric: str,
    operator: str,
    threshold: float,
    window_minutes: int = 60,
    enabled: bool = True,
    alert_channels: Sequence[Mapping[str, Any]] = (),
) -> StoredSloDefinition:
    """Create a new SLO definition after validating metric and operator."""
    if metric not in VALID_METRICS:
        raise ValueError(f"Invalid metric: {metric}")
    if operator not in VALID_OPERATORS:
        raise ValueError(f"Invalid operator: {operator}")
    slo = NewSloDefinition(
        org_id=org_id,
        name=name,
        metric=metric,
        operator=operator,
        threshold=threshold,
        window_minutes=window_minutes,
        enabled=enabled,
        alert_channels=list(alert_channels),
    )
    return await store.create_slo_definition(slo)


async def update_slo(
    store: Store,
    *,
    slo_id: str,
    org_id: str,
    patch: SloDefinitionPatch,
) -> StoredSloDefinition:
    """Update an SLO definition.

    Pre-fetches for a clean 404, validates metric/operator if set,
    and raises ValueError if the patch is fully empty.
    """
    existing = await store.get_slo_definition(slo_id, org_id)
    if existing is None:
        raise StoreNotFoundError("slo_definitions", slo_id)

    if patch.metric is not None and patch.metric not in VALID_METRICS:
        raise ValueError(f"Invalid metric: {patch.metric}")
    if patch.operator is not None and patch.operator not in VALID_OPERATORS:
        raise ValueError(f"Invalid operator: {patch.operator}")

    # Check that at least one field is set
    has_field = any(
        getattr(patch, f) is not None
        for f in ("name", "metric", "operator", "threshold", "window_minutes", "enabled", "alert_channels")
    )
    if not has_field:
        raise ValueError("No fields to update")

    updated = await store.update_slo_definition(slo_id, org_id, patch)
    if updated is None:
        # Race condition — treat as not-found
        raise StoreNotFoundError("slo_definitions", slo_id)
    return updated


async def delete_slo(
    store: Store,
    *,
    slo_id: str,
    org_id: str,
) -> None:
    """Delete an SLO definition; raises StoreNotFoundError if it doesn't exist."""
    deleted = await store.delete_slo_definition(slo_id, org_id)
    if not deleted:
        raise StoreNotFoundError("slo_definitions", slo_id)


async def slo_status(store: Store) -> list[dict]:
    """Compute current pass/fail status for all enabled SLOs.

    Uses org_id=None to mirror pre-1K behaviour (no auth filtering on status).
    """
    all_slos = await store.list_slo_definitions(org_id=None)
    enabled_slos = [s for s in all_slos if s.enabled]
    results: list[dict] = []
    for slo in enabled_slos:
        value = await store.compute_metric_value(
            org_id=slo.org_id,
            metric=slo.metric,
            window_minutes=slo.window_minutes,
        )
        passing = _check_threshold(value, slo.operator, slo.threshold)
        results.append({
            "id": slo.id,
            "name": slo.name,
            "metric": slo.metric,
            "threshold": slo.threshold,
            "operator": slo.operator,
            "current_value": value,
            "passing": passing,
            "window_minutes": slo.window_minutes,
        })
    return results


async def list_alerts(
    store: Store,
    *,
    slo_id: Optional[str] = None,
    limit: int = 50,
) -> Sequence[StoredSloAlert]:
    """Passthrough to store.list_slo_alerts."""
    return await store.list_slo_alerts(slo_id=slo_id, limit=limit)


async def test_alert(
    store: Store,
    *,
    slo_id: str,
    org_id: str,
) -> StoredSloAlert:
    """Fire a test alert for an SLO (creates a 'firing' alert with metric_value=0.0)."""
    slo = await store.get_slo_definition(slo_id, org_id)
    if slo is None:
        raise StoreNotFoundError("slo_definitions", slo_id)
    alert = NewSloAlert(
        org_id=org_id,
        slo_id=slo_id,
        metric_value=0.0,
        threshold=slo.threshold,
        status="firing",
        dispatched_to=[{"channel": "test", "sent_at": None}],
    )
    return await store.record_slo_alert(alert)


async def slo_timeseries(
    store: Store,
    *,
    org_id: str,
    metric: str,
    window_hours: int,
    bucket_minutes: int,
) -> dict:
    """Retrieve time-series data for SLO charts."""
    if metric not in VALID_METRICS:
        raise ValueError(f"Invalid metric: {metric}")
    points: Sequence[TimeseriesPoint] = await store.compute_metric_timeseries(
        org_id=org_id,
        metric=metric,
        window_hours=window_hours,
        bucket_minutes=bucket_minutes,
    )
    return {
        "metric": metric,
        "window_hours": window_hours,
        "bucket_minutes": bucket_minutes,
        "data": [
            {
                "timestamp": p.timestamp.isoformat() if p.timestamp else None,
                "value": p.value,
            }
            for p in points
        ],
    }
