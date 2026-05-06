"""Retrieval analytics service — wraps AnalyticsOps.compute_retrieval_analytics with response shaping."""

from __future__ import annotations

from typing import Any, Optional

from lore.persistence import Store


async def get_retrieval_analytics(
    store: Store,
    *,
    org_id: str,
    days: int,
    project: Optional[str] = None,
) -> dict[str, Any]:
    """Compute retrieval analytics and shape into a wire-ready dict.

    Computes derived fields (hit_rate, memory_utilization, score_distribution percentages)
    that the wire response needs but the persistence layer doesn't compute.
    """
    result = await store.compute_retrieval_analytics(org_id=org_id, days=days, project=project)

    total = result.total_queries
    hit_rate = (result.queries_with_results / total) if total else 0.0
    memory_utilization = (
        result.unique_memories_retrieved / result.total_memories
        if result.total_memories
        else None
    )

    total_scored = sum(b.count for b in result.score_distribution) or 1
    score_dist = [
        {
            "bucket": b.bucket,
            "count": b.count,
            "percentage": round(100 * b.count / total_scored, 1),
        }
        for b in result.score_distribution
    ]

    return {
        "total_queries": result.total_queries,
        "queries_with_results": result.queries_with_results,
        "queries_empty": result.queries_empty,
        "hit_rate": round(hit_rate, 4),
        "avg_results_per_query": round(result.avg_results_per_query or 0.0, 2),
        "avg_score": result.avg_score,
        "avg_max_score": result.avg_max_score,
        "avg_latency_ms": result.avg_latency_ms,
        "p95_latency_ms": result.p95_latency_ms,
        "score_distribution": score_dist,
        "top_queries": [
            {"query": q.query, "count": q.count, "avg_score": q.avg_score}
            for q in result.top_queries
        ],
        "memory_utilization": memory_utilization,
        "unique_memories_retrieved": result.unique_memories_retrieved,
        "total_memories": result.total_memories,
        "daily_stats": [
            {
                "date": d.date,
                "queries": d.queries,
                "avg_score": d.avg_score,
                "hit_rate": d.hit_rate,
            }
            for d in result.daily_stats
        ],
        "lookback_days": days,
    }
