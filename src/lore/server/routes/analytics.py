"""Retrieval analytics endpoint — GET /v1/analytics/retrieval."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


# ── Response Models ────────────────────────────────────────────────


class ScoreDistribution(BaseModel):
    bucket: str
    count: int
    percentage: float


class DailyStat(BaseModel):
    date: str
    queries: int
    avg_score: Optional[float]
    hit_rate: float


class TopQuery(BaseModel):
    query: str
    count: int
    avg_score: Optional[float]


class RetrievalAnalytics(BaseModel):
    total_queries: int
    queries_with_results: int
    queries_empty: int
    hit_rate: float
    avg_results_per_query: float
    avg_score: Optional[float]
    avg_max_score: Optional[float]
    avg_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    score_distribution: List[ScoreDistribution]
    top_queries: List[TopQuery]
    memory_utilization: Optional[float]
    unique_memories_retrieved: int
    total_memories: int
    daily_stats: List[DailyStat]
    lookback_days: int


# ── Route ──────────────────────────────────────────────────────────


@router.get("/retrieval", response_model=RetrievalAnalytics)
async def retrieval_analytics(
    days: int = Query(7, ge=1, le=365, description="Lookback window in days"),
    project: Optional[str] = Query(None, description="Filter by project"),
    auth: AuthContext = Depends(get_auth_context),
) -> RetrievalAnalytics:
    """Retrieval analytics dashboard — measure memory effectiveness."""

    pool = await get_pool()

    # Build WHERE clause
    where_parts = ["org_id = $1", "created_at >= now() - make_interval(days => $2)"]
    params: list[Any] = [auth.org_id, days]

    effective_project = project
    if auth.project is not None:
        effective_project = auth.project
    if effective_project is not None:
        params.append(effective_project)
        where_parts.append(f"project = ${len(params)}")

    where_sql = " AND ".join(where_parts)

    async with pool.acquire() as conn:
        # ── Summary stats ──────────────────────────────────────
        summary = await conn.fetchrow(f"""
            SELECT
                COUNT(*)::int AS total_queries,
                COUNT(*) FILTER (WHERE results_count > 0)::int AS queries_with_results,
                COUNT(*) FILTER (WHERE results_count = 0)::int AS queries_empty,
                AVG(results_count)::float AS avg_results,
                AVG(avg_score)::float AS avg_score,
                AVG(max_score)::float AS avg_max_score,
                AVG(query_time_ms)::float AS avg_latency_ms
            FROM retrieval_events
            WHERE {where_sql}
        """, *params)

        total = summary["total_queries"] or 0
        with_results = summary["queries_with_results"] or 0
        empty = summary["queries_empty"] or 0

        # ── P95 latency ────────────────────────────────────────
        p95_row = await conn.fetchrow(f"""
            SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY query_time_ms) AS p95
            FROM retrieval_events
            WHERE {where_sql}
        """, *params)
        p95 = round(float(p95_row["p95"]), 2) if p95_row and p95_row["p95"] is not None else None

        # ── Score distribution ─────────────────────────────────
        score_dist_rows = await conn.fetch(f"""
            SELECT bucket, COUNT(*)::int AS cnt
            FROM (
                SELECT
                    CASE
                        WHEN s::float < 0.3 THEN '0.0-0.3'
                        WHEN s::float < 0.5 THEN '0.3-0.5'
                        WHEN s::float < 0.7 THEN '0.5-0.7'
                        WHEN s::float < 0.9 THEN '0.7-0.9'
                        ELSE '0.9-1.0'
                    END AS bucket
                FROM retrieval_events,
                     jsonb_array_elements_text(scores) AS s
                WHERE {where_sql}
            ) sub
            GROUP BY bucket
            ORDER BY bucket
        """, *params)

        total_scores = sum(r["cnt"] for r in score_dist_rows) or 1
        buckets_order = ["0.0-0.3", "0.3-0.5", "0.5-0.7", "0.7-0.9", "0.9-1.0"]
        bucket_counts: Dict[str, int] = {r["bucket"]: r["cnt"] for r in score_dist_rows}
        score_distribution = [
            ScoreDistribution(
                bucket=b,
                count=bucket_counts.get(b, 0),
                percentage=round(bucket_counts.get(b, 0) / total_scores * 100, 1),
            )
            for b in buckets_order
        ]

        # ── Top queries ────────────────────────────────────────
        top_rows = await conn.fetch(f"""
            SELECT query, COUNT(*)::int AS cnt, AVG(avg_score)::float AS avg_s
            FROM retrieval_events
            WHERE {where_sql}
            GROUP BY query
            ORDER BY cnt DESC
            LIMIT 10
        """, *params)
        top_queries = [
            TopQuery(query=r["query"], count=r["cnt"], avg_score=round(r["avg_s"], 4) if r["avg_s"] else None)
            for r in top_rows
        ]

        # ── Memory utilization ─────────────────────────────────
        unique_row = await conn.fetchrow(f"""
            SELECT COUNT(DISTINCT mid)::int AS unique_count
            FROM retrieval_events,
                 jsonb_array_elements_text(memory_ids) AS mid
            WHERE {where_sql}
        """, *params)
        unique_memories = unique_row["unique_count"] if unique_row else 0

        total_memories_row = await conn.fetchrow(
            "SELECT COUNT(*)::int AS total FROM memories WHERE org_id = $1",
            auth.org_id,
        )
        total_memories = total_memories_row["total"] if total_memories_row else 0

        utilization = round(unique_memories / total_memories * 100, 1) if total_memories > 0 else None

        # ── Daily stats ────────────────────────────────────────
        daily_rows = await conn.fetch(f"""
            SELECT
                created_at::date AS day,
                COUNT(*)::int AS queries,
                AVG(avg_score)::float AS avg_s,
                (COUNT(*) FILTER (WHERE results_count > 0))::float / GREATEST(COUNT(*), 1) AS hit_rate
            FROM retrieval_events
            WHERE {where_sql}
            GROUP BY day
            ORDER BY day DESC
        """, *params)
        daily_stats = [
            DailyStat(
                date=str(r["day"]),
                queries=r["queries"],
                avg_score=round(r["avg_s"], 4) if r["avg_s"] else None,
                hit_rate=round(float(r["hit_rate"]), 4),
            )
            for r in daily_rows
        ]

    return RetrievalAnalytics(
        total_queries=total,
        queries_with_results=with_results,
        queries_empty=empty,
        hit_rate=round(with_results / total, 4) if total > 0 else 0.0,
        avg_results_per_query=round(float(summary["avg_results"] or 0), 2),
        avg_score=round(float(summary["avg_score"]), 4) if summary["avg_score"] else None,
        avg_max_score=round(float(summary["avg_max_score"]), 4) if summary["avg_max_score"] else None,
        avg_latency_ms=round(float(summary["avg_latency_ms"]), 2) if summary["avg_latency_ms"] else None,
        p95_latency_ms=p95,
        score_distribution=score_distribution,
        top_queries=top_queries,
        memory_utilization=utilization,
        unique_memories_retrieved=unique_memories,
        total_memories=total_memories,
        daily_stats=daily_stats,
        lookback_days=days,
    )
