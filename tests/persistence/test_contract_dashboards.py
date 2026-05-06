"""Contract tests for the AuditOps and AnalyticsOps slices of Store.

Covers query_audit_log with org isolation, workspace/action/actor/since
filters, ordering, and limit; and compute_retrieval_analytics.
These tests run against every Store implementation (Phase 1I: Postgres only).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from lore.persistence import Store
from lore.persistence.types import NewRetrievalEvent

# ── helpers ────────────────────────────────────────────────────────────────────


async def _ensure_org(store, org_id: str) -> None:
    """Insert an org row if it doesn't already exist (required by FK in other tables)."""
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


async def _insert_audit_entry(
    store,
    *,
    org_id: str = "org-a",
    workspace_id: str | None = None,
    actor_id: str = "actor-1",
    actor_type: str = "user",
    action: str = "memories.create",
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict | None = None,
    created_at: datetime | None = None,
) -> int:
    metadata_json = json.dumps(dict(metadata or {}))
    if created_at is None:
        row = await store._conn.fetchrow(
            """INSERT INTO audit_log (org_id, workspace_id, actor_id, actor_type, action, resource_type, resource_id, metadata)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb) RETURNING id""",
            org_id,
            workspace_id,
            actor_id,
            actor_type,
            action,
            resource_type,
            resource_id,
            metadata_json,
        )
    else:
        row = await store._conn.fetchrow(
            """INSERT INTO audit_log (org_id, workspace_id, actor_id, actor_type, action, resource_type, resource_id, metadata, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9) RETURNING id""",
            org_id,
            workspace_id,
            actor_id,
            actor_type,
            action,
            resource_type,
            resource_id,
            metadata_json,
            created_at,
        )
    return row["id"]


# ── query_audit_log tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_audit_log_returns_org_only(store: Store):
    """Entries from org_b must not appear when querying org_a."""
    id_a = await _insert_audit_entry(store, org_id="org-a")
    await _insert_audit_entry(store, org_id="org-b")

    results = await store.query_audit_log(org_id="org-a")

    ids = {r.id for r in results}
    assert id_a in ids
    # org-b entry must be absent
    assert all(r.org_id == "org-a" for r in results)


@pytest.mark.asyncio
async def test_query_audit_log_workspace_filter(store: Store):
    """workspace_id filter returns only matching entries."""
    id_ws1 = await _insert_audit_entry(store, org_id="org-wf", workspace_id="ws-1")
    id_ws2 = await _insert_audit_entry(store, org_id="org-wf", workspace_id="ws-2")
    id_none = await _insert_audit_entry(store, org_id="org-wf", workspace_id=None)

    results = await store.query_audit_log(org_id="org-wf", workspace_id="ws-1")

    ids = {r.id for r in results}
    assert id_ws1 in ids
    assert id_ws2 not in ids
    assert id_none not in ids


@pytest.mark.asyncio
async def test_query_audit_log_action_filter(store: Store):
    """action filter returns only entries with that action."""
    id_create = await _insert_audit_entry(store, org_id="org-af", action="memories.create")
    id_delete = await _insert_audit_entry(store, org_id="org-af", action="memories.delete")

    results = await store.query_audit_log(org_id="org-af", action="memories.create")

    ids = {r.id for r in results}
    assert id_create in ids
    assert id_delete not in ids


@pytest.mark.asyncio
async def test_query_audit_log_since_filter(store: Store):
    """since filter excludes entries older than the given timestamp."""
    old_dt = datetime.now(timezone.utc) - timedelta(hours=2)
    recent_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    id_old = await _insert_audit_entry(store, org_id="org-sf", created_at=old_dt)
    id_recent = await _insert_audit_entry(store, org_id="org-sf", created_at=recent_dt)

    results = await store.query_audit_log(org_id="org-sf", since=cutoff_ts)

    ids = {r.id for r in results}
    assert id_recent in ids
    assert id_old not in ids


@pytest.mark.asyncio
async def test_query_audit_log_orders_by_created_at_desc(store: Store):
    """Results must be ordered newest-first."""
    early_dt = datetime.now(timezone.utc) - timedelta(minutes=30)
    late_dt = datetime.now(timezone.utc) - timedelta(minutes=5)

    id_early = await _insert_audit_entry(store, org_id="org-ord", created_at=early_dt)
    id_late = await _insert_audit_entry(store, org_id="org-ord", created_at=late_dt)

    results = await store.query_audit_log(org_id="org-ord")

    assert len(results) >= 2
    ids = [r.id for r in results]
    assert ids.index(id_late) < ids.index(id_early)


@pytest.mark.asyncio
async def test_query_audit_log_respects_limit(store: Store):
    """limit parameter caps the number of returned rows."""
    for i in range(5):
        await _insert_audit_entry(store, org_id="org-lim", actor_id=f"actor-{i}")

    results = await store.query_audit_log(org_id="org-lim", limit=3)

    assert len(results) == 3


# ── compute_retrieval_analytics helpers ───────────────────────────────────────


async def _ensure_org_analytics(store, org_id: str) -> None:
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


def _event(
    org_id: str,
    query: str = "test query",
    results_count: int = 3,
    scores: list[float] | None = None,
    memory_ids: list[str] | None = None,
    avg_score: float | None = 0.8,
    max_score: float | None = 0.9,
    query_time_ms: float = 50.0,
    project: str | None = None,
) -> NewRetrievalEvent:
    if scores is None:
        scores = [0.9, 0.8, 0.7]
    if memory_ids is None:
        memory_ids = ["m1", "m2", "m3"]
    return NewRetrievalEvent(
        org_id=org_id,
        query=query,
        results_count=results_count,
        scores=scores,
        memory_ids=memory_ids,
        avg_score=avg_score,
        max_score=max_score,
        min_score_threshold=0.3,
        query_time_ms=query_time_ms,
        project=project,
    )


async def _insert_event_at(store, ev: NewRetrievalEvent, created_at: datetime) -> None:
    """Insert a retrieval event with a specific created_at timestamp."""
    import json as _json

    await store._conn.execute(
        """
        INSERT INTO retrieval_events
            (org_id, query, results_count, scores, memory_ids,
             avg_score, max_score, min_score_threshold, query_time_ms,
             project, format, created_at)
        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10, $11, $12)
        """,
        ev.org_id,
        ev.query,
        ev.results_count,
        _json.dumps(list(ev.scores)),
        _json.dumps(list(ev.memory_ids)),
        ev.avg_score,
        ev.max_score,
        ev.min_score_threshold,
        ev.query_time_ms,
        ev.project,
        ev.format,
        created_at,
    )


# ── compute_retrieval_analytics tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_compute_analytics_zero_events_returns_zeros(store: Store):
    """Fresh org with no events should return all-zero counts and None averages."""
    await _ensure_org_analytics(store, "org-ana-zero")

    result = await store.compute_retrieval_analytics(org_id="org-ana-zero", days=7)

    assert result.total_queries == 0
    assert result.queries_with_results == 0
    assert result.queries_empty == 0
    assert result.avg_results_per_query == 0.0
    assert result.avg_score is None
    assert result.avg_max_score is None
    assert result.avg_latency_ms is None
    assert result.p95_latency_ms is None
    assert result.unique_memories_retrieved == 0
    assert result.total_memories == 0
    assert result.daily_stats == []
    assert result.top_queries == []
    # All buckets should exist and be 0
    assert len(result.score_distribution) == 5
    assert all(b.count == 0 for b in result.score_distribution)


@pytest.mark.asyncio
async def test_compute_analytics_with_events(store: Store):
    """Record several events; verify summary stats match expected values."""
    await _ensure_org_analytics(store, "org-ana-ev")

    await store.record_retrieval_event(
        _event("org-ana-ev", query="q1", results_count=3, avg_score=0.8, query_time_ms=40.0)
    )
    await store.record_retrieval_event(
        _event("org-ana-ev", query="q2", results_count=0, scores=[], memory_ids=[], avg_score=None, max_score=None, query_time_ms=10.0)
    )
    await store.record_retrieval_event(
        _event("org-ana-ev", query="q3", results_count=2, scores=[0.7, 0.6], memory_ids=["m4", "m5"], avg_score=0.65, query_time_ms=60.0)
    )

    result = await store.compute_retrieval_analytics(org_id="org-ana-ev", days=7)

    assert result.total_queries == 3
    assert result.queries_with_results == 2
    assert result.queries_empty == 1
    assert result.avg_latency_ms is not None
    assert abs(result.avg_latency_ms - round((40.0 + 10.0 + 60.0) / 3, 2)) < 0.1


@pytest.mark.asyncio
async def test_compute_analytics_filters_by_project(store: Store):
    """Events with different projects; project filter constrains results."""
    await _ensure_org_analytics(store, "org-ana-proj")

    await store.record_retrieval_event(_event("org-ana-proj", project="proj-a", query="pq-a"))
    await store.record_retrieval_event(_event("org-ana-proj", project="proj-a", query="pq-a2"))
    await store.record_retrieval_event(_event("org-ana-proj", project="proj-b", query="pq-b"))

    result_a = await store.compute_retrieval_analytics(
        org_id="org-ana-proj", days=7, project="proj-a"
    )
    result_b = await store.compute_retrieval_analytics(
        org_id="org-ana-proj", days=7, project="proj-b"
    )
    result_all = await store.compute_retrieval_analytics(org_id="org-ana-proj", days=7)

    assert result_a.total_queries == 2
    assert result_b.total_queries == 1
    assert result_all.total_queries == 3


@pytest.mark.asyncio
async def test_compute_analytics_respects_days_window(store: Store):
    """An event from 10 days ago should not appear when days=5."""
    await _ensure_org_analytics(store, "org-ana-days")

    old_dt = datetime.now(timezone.utc) - timedelta(days=10)
    recent_dt = datetime.now(timezone.utc) - timedelta(hours=1)

    ev_old = _event("org-ana-days", query="old-query")
    ev_recent = _event("org-ana-days", query="recent-query")

    await _insert_event_at(store, ev_old, old_dt)
    await _insert_event_at(store, ev_recent, recent_dt)

    result = await store.compute_retrieval_analytics(org_id="org-ana-days", days=5)

    assert result.total_queries == 1
    assert result.top_queries[0].query == "recent-query"


@pytest.mark.asyncio
async def test_compute_analytics_p95_latency(store: Store):
    """Record 20 events with known latencies; verify p95 is within tolerance."""
    await _ensure_org_analytics(store, "org-ana-p95")

    latencies = [float(i * 10) for i in range(1, 21)]  # 10, 20, ..., 200
    for lat in latencies:
        await store.record_retrieval_event(
            _event("org-ana-p95", query="latency-test", query_time_ms=lat)
        )

    result = await store.compute_retrieval_analytics(org_id="org-ana-p95", days=7)

    assert result.p95_latency_ms is not None
    # PostgreSQL percentile_cont(0.95) on 20 values [10..200] step 10
    # p95 = 190 + 0.95*(200-190) = 190 + 9.5 = 199.5? Actually:
    # sorted: 10,20,...,200 (20 items). p95 = value at 0.95*(20-1)+1 = 19.05th position
    # = 190 + 0.05*(200-190) = 190 + 0.5 = 190.5
    # Accept a 2ms tolerance
    assert abs(result.p95_latency_ms - 190.5) < 2.0


@pytest.mark.asyncio
async def test_compute_analytics_score_distribution_buckets(store: Store):
    """Record events with scores in different buckets; verify counts."""
    await _ensure_org_analytics(store, "org-ana-dist")

    # 2 scores in 0.0-0.3, 1 in 0.3-0.5, 3 in 0.5-0.7, 0 in 0.7-0.9, 2 in 0.9-1.0
    await store.record_retrieval_event(
        _event(
            "org-ana-dist",
            scores=[0.1, 0.2, 0.4, 0.6, 0.55, 0.65, 0.95, 0.92],
            memory_ids=["m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8"],
            results_count=8,
            avg_score=0.55,
            max_score=0.95,
        )
    )

    result = await store.compute_retrieval_analytics(org_id="org-ana-dist", days=7)

    bucket_map = {b.bucket: b.count for b in result.score_distribution}
    assert bucket_map["0.0-0.3"] == 2
    assert bucket_map["0.3-0.5"] == 1
    assert bucket_map["0.5-0.7"] == 3
    assert bucket_map["0.7-0.9"] == 0
    assert bucket_map["0.9-1.0"] == 2


@pytest.mark.asyncio
async def test_compute_analytics_top_queries(store: Store):
    """Top queries are ordered by frequency, limited to 10."""
    await _ensure_org_analytics(store, "org-ana-top")

    # "hot" query appears 5 times, "cold" query 1 time
    for _ in range(5):
        await store.record_retrieval_event(_event("org-ana-top", query="hot query"))
    await store.record_retrieval_event(_event("org-ana-top", query="cold query"))

    result = await store.compute_retrieval_analytics(org_id="org-ana-top", days=7)

    assert len(result.top_queries) >= 2
    assert result.top_queries[0].query == "hot query"
    assert result.top_queries[0].count == 5
    assert result.top_queries[1].query == "cold query"
    assert result.top_queries[1].count == 1


@pytest.mark.asyncio
async def test_compute_analytics_unique_memories(store: Store):
    """Unique memories retrieved counts distinct memory IDs across all events."""
    await _ensure_org_analytics(store, "org-ana-uniq")

    # event 1: m1, m2, m3; event 2: m2, m3, m4 → 4 unique
    await store.record_retrieval_event(
        _event("org-ana-uniq", memory_ids=["m1", "m2", "m3"], results_count=3)
    )
    await store.record_retrieval_event(
        _event("org-ana-uniq", memory_ids=["m2", "m3", "m4"], results_count=3)
    )

    result = await store.compute_retrieval_analytics(org_id="org-ana-uniq", days=7)

    assert result.unique_memories_retrieved == 4


@pytest.mark.asyncio
async def test_compute_analytics_daily_stats(store: Store):
    """Daily stats group events by date with correct query counts and hit rates."""
    await _ensure_org_analytics(store, "org-ana-daily")

    today = datetime.now(timezone.utc)
    yesterday = today - timedelta(days=1)

    # today: 2 events (1 with results, 1 empty) → hit_rate=0.5
    await _insert_event_at(
        store,
        _event("org-ana-daily", results_count=3, memory_ids=["m1", "m2", "m3"]),
        today,
    )
    await _insert_event_at(
        store,
        _event("org-ana-daily", results_count=0, scores=[], memory_ids=[], avg_score=None, max_score=None),
        today,
    )
    # yesterday: 1 event with results → hit_rate=1.0
    await _insert_event_at(
        store,
        _event("org-ana-daily", results_count=2, memory_ids=["m3", "m4"]),
        yesterday,
    )

    result = await store.compute_retrieval_analytics(org_id="org-ana-daily", days=7)

    assert len(result.daily_stats) == 2
    # Results are DESC ordered by day, so today first
    today_stat = result.daily_stats[0]
    yesterday_stat = result.daily_stats[1]

    assert today_stat.queries == 2
    assert abs(today_stat.hit_rate - 0.5) < 0.001

    assert yesterday_stat.queries == 1
    assert abs(yesterday_stat.hit_rate - 1.0) < 0.001
