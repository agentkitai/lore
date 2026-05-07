"""Contract tests for the SloOps slice of Store — SLO definition CRUD + alerts + metrics.

These tests run against every Store implementation (Phase 1K: Postgres only).
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from lore.persistence import (
    NewSloAlert,
    NewSloDefinition,
    SloDefinitionPatch,
    Store,
    StoredSloAlert,
    StoredSloDefinition,
)
from lore.persistence.types import NewRetrievalEvent
from tests.persistence.conftest import _is_sqlite

# ── helpers ────────────────────────────────────────────────────────────────────


async def _insert_slo(
    store,
    *,
    org_id: str = "test-org",
    name: str = "test-slo",
    metric: str = "p99_latency",
    operator: str = "gt",
    threshold: float = 200.0,
    window_minutes: int = 60,
    enabled: bool = True,
    alert_channels: list | None = None,
) -> str:
    """Insert a slo_definitions row via raw SQL and return its id."""
    from ulid import ULID

    slo_id = f"slo_{ULID()}"
    ac = alert_channels or []
    if _is_sqlite(store):
        await store._conn.execute(
            """
            INSERT INTO slo_definitions
                (id, org_id, name, metric, operator, threshold,
                 window_minutes, enabled, alert_channels)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slo_id,
                org_id,
                name,
                metric,
                operator,
                threshold,
                window_minutes,
                1 if enabled else 0,
                json.dumps(ac),
            ),
        )
        await store._conn.commit()
    else:
        await store._conn.execute(
            """
            INSERT INTO slo_definitions
                (id, org_id, name, metric, operator, threshold,
                 window_minutes, enabled, alert_channels)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            """,
            slo_id,
            org_id,
            name,
            metric,
            operator,
            threshold,
            window_minutes,
            enabled,
            json.dumps(ac),
        )
    return slo_id


# ── list_slo_definitions ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_org_only_when_filter_set(store: Store):
    await _insert_slo(store, org_id="org-list", name="slo-a")
    await _insert_slo(store, org_id="org-list", name="slo-b")
    await _insert_slo(store, org_id="other-org", name="slo-c")

    results = await store.list_slo_definitions("org-list")

    assert len(results) == 2
    for r in results:
        assert r.org_id == "org-list"
        assert isinstance(r, StoredSloDefinition)


@pytest.mark.asyncio
async def test_list_returns_all_when_org_filter_none(store: Store):
    await _insert_slo(store, org_id="org-all-a", name="slo-x")
    await _insert_slo(store, org_id="org-all-b", name="slo-y")

    results = await store.list_slo_definitions(None)

    # Should include rows from both orgs (no WHERE clause — preserves multi-tenancy quirk)
    org_ids = {r.org_id for r in results}
    assert "org-all-a" in org_ids
    assert "org-all-b" in org_ids


# ── get_slo_definition ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(store: Store):
    result = await store.get_slo_definition("slo_does_not_exist", "org-x")
    assert result is None


@pytest.mark.asyncio
async def test_get_org_isolation(store: Store):
    slo_id = await _insert_slo(store, org_id="org-iso-a", name="slo-iso")

    # Correct org → found
    result = await store.get_slo_definition(slo_id, "org-iso-a")
    assert result is not None
    assert result.id == slo_id

    # Wrong org → None
    result_wrong = await store.get_slo_definition(slo_id, "org-iso-b")
    assert result_wrong is None


# ── create_slo_definition ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_round_trip(store: Store):
    channels = [{"type": "email", "address": "ops@example.com"}]
    slo = NewSloDefinition(
        org_id="org-create",
        name="my-slo",
        metric="error_rate",
        operator="gt",
        threshold=0.05,
        window_minutes=30,
        enabled=True,
        alert_channels=channels,
    )

    created = await store.create_slo_definition(slo)

    assert created.id.startswith("slo_")
    assert created.org_id == "org-create"
    assert created.name == "my-slo"
    assert created.metric == "error_rate"
    assert created.operator == "gt"
    assert created.threshold == 0.05
    assert created.window_minutes == 30
    assert created.enabled is True
    # alert_channels round-trips through JSONB
    assert len(created.alert_channels) == 1
    assert created.alert_channels[0]["type"] == "email"
    assert created.alert_channels[0]["address"] == "ops@example.com"
    assert isinstance(created.created_at, datetime)
    assert isinstance(created.updated_at, datetime)

    # Round-trip via get
    fetched = await store.get_slo_definition(created.id, "org-create")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.alert_channels == created.alert_channels


# ── update_slo_definition ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_changes_field(store: Store):
    slo_id = await _insert_slo(
        store, org_id="org-upd", name="before-slo", threshold=100.0
    )

    patch = SloDefinitionPatch(name="after-slo", threshold=250.0)
    result = await store.update_slo_definition(slo_id, "org-upd", patch)

    assert result is not None
    assert result.name == "after-slo"
    assert result.threshold == 250.0
    assert result.id == slo_id

    # Verify persisted
    fetched = await store.get_slo_definition(slo_id, "org-upd")
    assert fetched is not None
    assert fetched.name == "after-slo"
    assert fetched.threshold == 250.0


@pytest.mark.asyncio
async def test_update_returns_none_when_missing(store: Store):
    patch = SloDefinitionPatch(name="ghost-slo")
    result = await store.update_slo_definition("slo_nonexistent", "org-x", patch)
    assert result is None


@pytest.mark.asyncio
async def test_update_empty_patch_raises_value_error(store: Store):
    slo_id = await _insert_slo(store, org_id="org-empty", name="empty-patch-slo")

    with pytest.raises(ValueError, match="empty patch"):
        await store.update_slo_definition(slo_id, "org-empty", SloDefinitionPatch())


# ── delete_slo_definition ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_returns_true_when_existed(store: Store):
    slo_id = await _insert_slo(store, org_id="org-del", name="to-delete-slo")

    result = await store.delete_slo_definition(slo_id, "org-del")

    assert result is True

    # Confirm gone
    fetched = await store.get_slo_definition(slo_id, "org-del")
    assert fetched is None


@pytest.mark.asyncio
async def test_delete_returns_false_when_missing(store: Store):
    result = await store.delete_slo_definition("slo_ghost", "org-ghost")
    assert result is False


# ── SLO alerts helpers ─────────────────────────────────────────────────────────


async def _insert_alert(
    store,
    *,
    org_id: str = "test-org",
    slo_id: str = "slo_test",
    metric_value: float = 250.0,
    threshold: float = 200.0,
    status: str = "firing",
    dispatched_to: list | None = None,
) -> int:
    """Insert a slo_alerts row via raw SQL and return its id."""
    dt = dispatched_to or []
    if _is_sqlite(store):
        cursor = await store._conn.execute(
            """
            INSERT INTO slo_alerts
                (org_id, slo_id, metric_value, threshold, status, dispatched_to)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                org_id,
                slo_id,
                metric_value,
                threshold,
                status,
                json.dumps(dt),
            ),
        )
        new_id = cursor.lastrowid
        await cursor.close()
        await store._conn.commit()
        return int(new_id)
    row = await store._conn.fetchrow(
        """
        INSERT INTO slo_alerts
            (org_id, slo_id, metric_value, threshold, status, dispatched_to)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING id
        """,
        org_id,
        slo_id,
        metric_value,
        threshold,
        status,
        json.dumps(dt),
    )
    return int(row["id"])


# ── list_slo_alerts ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_slo_alerts_returns_all_when_no_filter(store: Store):
    slo_id_a = await _insert_slo(store, org_id="org-al1", name="slo-al-a")
    slo_id_b = await _insert_slo(store, org_id="org-al1", name="slo-al-b")
    await _insert_alert(store, org_id="org-al1", slo_id=slo_id_a)
    await _insert_alert(store, org_id="org-al1", slo_id=slo_id_b)

    results = await store.list_slo_alerts()

    # No filter — must include both alerts (may include rows from other tests,
    # but our two must be present within the transaction)
    returned_slo_ids = {r.slo_id for r in results}
    assert slo_id_a in returned_slo_ids
    assert slo_id_b in returned_slo_ids


@pytest.mark.asyncio
async def test_list_slo_alerts_filters_by_slo_id(store: Store):
    slo_id_a = await _insert_slo(store, org_id="org-al2", name="slo-flt-a")
    slo_id_b = await _insert_slo(store, org_id="org-al2", name="slo-flt-b")
    await _insert_alert(store, org_id="org-al2", slo_id=slo_id_a)
    await _insert_alert(store, org_id="org-al2", slo_id=slo_id_b)

    results = await store.list_slo_alerts(slo_id=slo_id_a)

    assert len(results) == 1
    assert results[0].slo_id == slo_id_a
    assert isinstance(results[0], StoredSloAlert)


@pytest.mark.asyncio
async def test_list_slo_alerts_respects_limit(store: Store):
    slo_id = await _insert_slo(store, org_id="org-al3", name="slo-limit")
    for i in range(5):
        await _insert_alert(
            store, org_id="org-al3", slo_id=slo_id, metric_value=float(200 + i)
        )

    results = await store.list_slo_alerts(slo_id=slo_id, limit=3)

    assert len(results) == 3


# ── record_slo_alert ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_slo_alert_round_trip(store: Store):
    slo_id = await _insert_slo(store, org_id="org-ra1", name="slo-ra")
    channels = [{"type": "email", "address": "ops@example.com"}]
    alert = NewSloAlert(
        org_id="org-ra1",
        slo_id=slo_id,
        metric_value=300.0,
        threshold=200.0,
        status="firing",
        dispatched_to=channels,
    )

    stored = await store.record_slo_alert(alert)

    assert isinstance(stored, StoredSloAlert)
    assert stored.id > 0
    assert stored.org_id == "org-ra1"
    assert stored.slo_id == slo_id
    assert stored.metric_value == 300.0
    assert stored.threshold == 200.0
    assert stored.status == "firing"
    # JSONB roundtrip on dispatched_to
    assert len(stored.dispatched_to) == 1
    assert stored.dispatched_to[0]["type"] == "email"
    assert stored.dispatched_to[0]["address"] == "ops@example.com"
    assert isinstance(stored.created_at, datetime)


# ── compute_metric_value ───────────────────────────────────────────────────────


async def _seed_retrieval_events(store, *, org_id: str, latencies: list[float]) -> None:
    """Seed retrieval_events rows with given query_time_ms values."""
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
                query_time_ms=ms,
            )
        )


@pytest.mark.asyncio
async def test_compute_metric_value_returns_none_when_no_events(store: Store):
    result = await store.compute_metric_value(
        org_id="org-mv-empty",
        metric="p95_latency",
        window_minutes=60,
    )
    assert result is None


@pytest.mark.asyncio
async def test_compute_metric_value_p95_latency(store: Store):
    # 20 events: latencies 10..200ms (step 10).  p95 of sorted [10,20,...,200] ≈ 190.
    latencies = [float(i * 10) for i in range(1, 21)]
    await _seed_retrieval_events(store, org_id="org-mv-p95", latencies=latencies)

    result = await store.compute_metric_value(
        org_id="org-mv-p95",
        metric="p95_latency",
        window_minutes=60,
    )

    assert result is not None
    # PostgreSQL percentile_cont(0.95) on 20 values [10..200] = 191.5 (interpolated)
    assert 180.0 <= result <= 200.0


@pytest.mark.asyncio
async def test_compute_metric_value_hit_rate(store: Store):
    # Insert 8 events with results, 2 without → hit_rate = 0.8
    for _ in range(8):
        await store.record_retrieval_event(
            NewRetrievalEvent(
                org_id="org-mv-hr",
                query="hit",
                results_count=3,
                scores=[0.9, 0.8, 0.7],
                memory_ids=["m1", "m2", "m3"],
                avg_score=0.8,
                max_score=0.9,
                min_score_threshold=0.3,
                query_time_ms=50.0,
            )
        )
    for _ in range(2):
        await store.record_retrieval_event(
            NewRetrievalEvent(
                org_id="org-mv-hr",
                query="miss",
                results_count=0,
                scores=[],
                memory_ids=[],
                avg_score=None,
                max_score=None,
                min_score_threshold=0.3,
                query_time_ms=20.0,
            )
        )

    result = await store.compute_metric_value(
        org_id="org-mv-hr",
        metric="hit_rate",
        window_minutes=60,
    )

    assert result is not None
    assert abs(result - 0.8) < 0.05


@pytest.mark.asyncio
async def test_compute_metric_value_unknown_metric_raises_value_error(store: Store):
    with pytest.raises(ValueError, match="Unknown metric"):
        await store.compute_metric_value(
            org_id="org-mv-bad",
            metric="nonexistent_metric",
            window_minutes=60,
        )


# ── compute_metric_timeseries ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compute_metric_timeseries_returns_buckets(store: Store):
    latencies = [100.0, 150.0, 200.0]
    await _seed_retrieval_events(store, org_id="org-ts1", latencies=latencies)

    results = await store.compute_metric_timeseries(
        org_id="org-ts1",
        metric="p95_latency",
        window_hours=1,
        bucket_minutes=60,
    )

    assert len(results) >= 1
    first = results[0]
    assert hasattr(first, "timestamp")
    assert hasattr(first, "value")
    assert first.value is not None
    assert isinstance(first.value, float)


@pytest.mark.asyncio
async def test_compute_metric_timeseries_unknown_metric_raises(store: Store):
    with pytest.raises(ValueError, match="Unknown metric"):
        await store.compute_metric_timeseries(
            org_id="org-ts-bad",
            metric="bad_metric",
            window_hours=1,
            bucket_minutes=15,
        )
