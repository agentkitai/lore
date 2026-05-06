"""Service tests for lore.services.analytics."""

from __future__ import annotations

import json

import pytest

from lore.persistence.types import NewRetrievalEvent
from lore.services import analytics

# ── helpers ───────────────────────────────────────────────────────────────────


async def _ensure_org(store, org_id: str) -> None:
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


async def _insert_event(
    store,
    org_id: str,
    *,
    query: str = "test query",
    results_count: int = 3,
    scores: list[float] | None = None,
    memory_ids: list[str] | None = None,
    avg_score: float | None = 0.8,
    max_score: float | None = 0.9,
    query_time_ms: float = 50.0,
) -> None:
    if scores is None:
        scores = [0.9, 0.8, 0.7]
    if memory_ids is None:
        memory_ids = ["m1", "m2", "m3"]
    ev = NewRetrievalEvent(
        org_id=org_id,
        query=query,
        results_count=results_count,
        scores=scores,
        memory_ids=memory_ids,
        avg_score=avg_score,
        max_score=max_score,
        min_score_threshold=0.3,
        query_time_ms=query_time_ms,
    )
    await store._conn.execute(
        """
        INSERT INTO retrieval_events
            (org_id, query, results_count, scores, memory_ids,
             avg_score, max_score, min_score_threshold, query_time_ms,
             project, format)
        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10, $11)
        """,
        ev.org_id,
        ev.query,
        ev.results_count,
        json.dumps(list(ev.scores)),
        json.dumps(list(ev.memory_ids)),
        ev.avg_score,
        ev.max_score,
        ev.min_score_threshold,
        ev.query_time_ms,
        ev.project,
        ev.format,
    )


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_returns_zeros_when_no_events(store):
    """Fresh org with no events returns all-zero shaped dict."""
    org = "ana-t1"
    await _ensure_org(store, org)

    result = await analytics.get_retrieval_analytics(store, org_id=org, days=7)

    assert result["total_queries"] == 0
    assert result["queries_with_results"] == 0
    assert result["hit_rate"] == 0.0
    assert result["top_queries"] == []
    assert result["lookback_days"] == 7
    # score_distribution may include zero-count buckets; all counts must be 0
    assert all(b["count"] == 0 for b in result["score_distribution"])


@pytest.mark.asyncio
async def test_hit_rate_computed_from_total(store):
    """hit_rate = queries_with_results / total_queries."""
    org = "ana-t2"
    await _ensure_org(store, org)

    # 2 events with results, 1 empty
    await _insert_event(store, org, results_count=3, scores=[0.9, 0.8, 0.7])
    await _insert_event(store, org, results_count=2, scores=[0.7, 0.6], memory_ids=["m4", "m5"])
    await _insert_event(
        store,
        org,
        results_count=0,
        scores=[],
        memory_ids=[],
        avg_score=None,
        max_score=None,
    )

    result = await analytics.get_retrieval_analytics(store, org_id=org, days=7)

    assert result["total_queries"] == 3
    assert result["queries_with_results"] == 2
    expected_hit_rate = round(2 / 3, 4)
    assert result["hit_rate"] == expected_hit_rate


@pytest.mark.asyncio
async def test_score_distribution_percentages_sum_close_to_100(store):
    """score_distribution percentages should sum to ~100% when events exist."""
    org = "ana-t3"
    await _ensure_org(store, org)

    # Insert multiple events to get a distribution
    for i in range(4):
        await _insert_event(
            store,
            org,
            results_count=2,
            scores=[0.9, 0.4],
            avg_score=0.65,
        )

    result = await analytics.get_retrieval_analytics(store, org_id=org, days=7)

    dist = result["score_distribution"]
    if dist:
        total_pct = sum(b["percentage"] for b in dist)
        # Allow small rounding error
        assert abs(total_pct - 100.0) < 1.0
        assert all("bucket" in b and "count" in b and "percentage" in b for b in dist)
