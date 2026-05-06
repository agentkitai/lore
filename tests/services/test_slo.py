"""Service-level tests for lore.services.slo.

Uses a real Postgres store (via conftest fixture) for integration tests.
"""

from __future__ import annotations

import pytest

from lore.persistence import (
    SloDefinitionPatch,
)
from lore.persistence.exceptions import StoreNotFoundError
from lore.persistence.types import NewRetrievalEvent
from lore.services import slo

# ── helpers ───────────────────────────────────────────────────────────────────

_ORG = "svc-slo-test"


async def _create_slo(
    store,
    *,
    org_id: str = _ORG,
    name: str = "test-slo",
    metric: str = "p99_latency",
    operator: str = "lt",
    threshold: float = 500.0,
    window_minutes: int = 60,
    enabled: bool = True,
):
    """Shortcut: create an SLO via the service layer."""
    return await slo.create_slo(
        store,
        org_id=org_id,
        name=name,
        metric=metric,
        operator=operator,
        threshold=threshold,
        window_minutes=window_minutes,
        enabled=enabled,
    )


async def _seed_retrieval_events(store, *, org_id: str, latencies: list) -> None:
    """Seed retrieval_events rows with the given query_time_ms values."""
    for ms in latencies:
        await store.record_retrieval_event(
            NewRetrievalEvent(
                org_id=org_id,
                query="seed query",
                results_count=1 if ms > 0 else 0,
                scores=[0.9] if ms > 0 else [],
                memory_ids=["m1"] if ms > 0 else [],
                avg_score=0.9 if ms > 0 else None,
                max_score=0.9 if ms > 0 else None,
                min_score_threshold=0.3,
                query_time_ms=float(ms),
            )
        )


# ── list_slos ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_slos_passthrough(store):
    """list_slos returns all SLOs (org_id=None passthrough)."""
    s1 = await _create_slo(store, org_id="list-slo-org", name="list-slo-a")
    s2 = await _create_slo(store, org_id="list-slo-org", name="list-slo-b")

    results = await slo.list_slos(store, org_id="list-slo-org")

    ids = [r.id for r in results]
    assert s1.id in ids
    assert s2.id in ids


# ── create_slo ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_slo_validates_metric(store):
    """create_slo raises ValueError for an unknown metric."""
    with pytest.raises(ValueError, match="Invalid metric"):
        await slo.create_slo(
            store,
            org_id=_ORG,
            name="bad-metric",
            metric="nonexistent_metric",
            operator="lt",
            threshold=100.0,
        )


@pytest.mark.asyncio
async def test_create_slo_validates_operator(store):
    """create_slo raises ValueError for an unknown operator."""
    with pytest.raises(ValueError, match="Invalid operator"):
        await slo.create_slo(
            store,
            org_id=_ORG,
            name="bad-operator",
            metric="p99_latency",
            operator="bad_op",
            threshold=100.0,
        )


@pytest.mark.asyncio
async def test_create_slo_round_trip(store):
    """create_slo persists all fields and returns a StoredSloDefinition."""
    created = await slo.create_slo(
        store,
        org_id=_ORG,
        name="rt-slo",
        metric="hit_rate",
        operator="gte",
        threshold=0.9,
        window_minutes=30,
        enabled=True,
    )

    assert created.id.startswith("slo_")
    assert created.org_id == _ORG
    assert created.name == "rt-slo"
    assert created.metric == "hit_rate"
    assert created.operator == "gte"
    assert created.threshold == 0.9
    assert created.window_minutes == 30
    assert created.enabled is True

    # Round-trip via store
    fetched = await store.get_slo_definition(created.id, _ORG)
    assert fetched is not None
    assert fetched.id == created.id


# ── update_slo ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_slo_404(store):
    """update_slo raises StoreNotFoundError for an unknown SLO."""
    with pytest.raises(StoreNotFoundError):
        await slo.update_slo(
            store,
            slo_id="slo_ghost",
            org_id=_ORG,
            patch=SloDefinitionPatch(name="ghost"),
        )


@pytest.mark.asyncio
async def test_update_slo_empty_patch_raises(store):
    """update_slo raises ValueError when patch has no fields set."""
    created = await _create_slo(store, name="empty-patch-slo")

    with pytest.raises(ValueError, match="No fields to update"):
        await slo.update_slo(
            store,
            slo_id=created.id,
            org_id=_ORG,
            patch=SloDefinitionPatch(),
        )


@pytest.mark.asyncio
async def test_update_slo_validates_metric_when_set(store):
    """update_slo raises ValueError when patch.metric is invalid."""
    created = await _create_slo(store, name="upd-metric-slo")

    with pytest.raises(ValueError, match="Invalid metric"):
        await slo.update_slo(
            store,
            slo_id=created.id,
            org_id=_ORG,
            patch=SloDefinitionPatch(metric="bad_metric"),
        )


# ── delete_slo ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_slo_404(store):
    """delete_slo raises StoreNotFoundError for an unknown SLO."""
    with pytest.raises(StoreNotFoundError):
        await slo.delete_slo(store, slo_id="slo_ghost", org_id=_ORG)


# ── slo_status ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slo_status_computes_values_and_passing(store):
    """slo_status returns a status dict per enabled SLO with passing flag."""
    org = "svc-slo-status-org"
    # Seed events so compute_metric_value returns data
    await _seed_retrieval_events(store, org_id=org, latencies=[100.0, 150.0, 200.0])

    # SLO: p95_latency < 500 — should be passing
    created = await slo.create_slo(
        store,
        org_id=org,
        name="status-slo",
        metric="p95_latency",
        operator="lt",
        threshold=500.0,
        enabled=True,
    )

    statuses = await slo.slo_status(store)

    # Find our specific SLO in the result
    our = next((s for s in statuses if s["id"] == created.id), None)
    assert our is not None
    assert our["metric"] == "p95_latency"
    assert our["threshold"] == 500.0
    assert our["operator"] == "lt"
    assert our["current_value"] is not None
    assert our["passing"] is True


@pytest.mark.asyncio
async def test_slo_status_skips_disabled(store):
    """slo_status does not include disabled SLOs."""
    org = "svc-slo-disabled-org"
    created = await slo.create_slo(
        store,
        org_id=org,
        name="disabled-slo",
        metric="p99_latency",
        operator="lt",
        threshold=500.0,
        enabled=False,
    )

    statuses = await slo.slo_status(store)

    ids = [s["id"] for s in statuses]
    assert created.id not in ids


# ── list_alerts ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_alert_creates_alert(store):
    """test_alert inserts a firing alert and returns the StoredSloAlert."""
    from lore.persistence.types import StoredSloAlert

    created = await _create_slo(store, name="alert-slo")

    alert = await slo.test_alert(store, slo_id=created.id, org_id=_ORG)

    assert isinstance(alert, StoredSloAlert)
    assert alert.slo_id == created.id
    assert alert.status == "firing"
    assert alert.metric_value == 0.0
    assert alert.threshold == created.threshold
    assert len(alert.dispatched_to) == 1
    assert alert.dispatched_to[0]["channel"] == "test"


@pytest.mark.asyncio
async def test_test_alert_404_when_slo_missing(store):
    """test_alert raises StoreNotFoundError when SLO does not exist."""
    with pytest.raises(StoreNotFoundError):
        await slo.test_alert(store, slo_id="slo_ghost", org_id=_ORG)


# ── slo_timeseries ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slo_timeseries_validates_metric(store):
    """slo_timeseries raises ValueError for an unknown metric."""
    with pytest.raises(ValueError, match="Invalid metric"):
        await slo.slo_timeseries(
            store,
            org_id=_ORG,
            metric="bad_metric",
            window_hours=24,
            bucket_minutes=60,
        )


@pytest.mark.asyncio
async def test_slo_timeseries_returns_data_dict(store):
    """slo_timeseries returns a dict with metric/window/bucket_minutes/data keys."""
    org = "svc-slo-ts-org"
    await _seed_retrieval_events(store, org_id=org, latencies=[100.0, 200.0, 300.0])

    result = await slo.slo_timeseries(
        store,
        org_id=org,
        metric="p95_latency",
        window_hours=1,
        bucket_minutes=60,
    )

    assert result["metric"] == "p95_latency"
    assert result["window_hours"] == 1
    assert result["bucket_minutes"] == 60
    assert isinstance(result["data"], list)
    assert len(result["data"]) >= 1
    first = result["data"][0]
    assert "timestamp" in first
    assert "value" in first
