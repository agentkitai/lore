"""Retrieval analytics endpoint — GET /v1/analytics/retrieval."""

from __future__ import annotations

import logging
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

from lore.persistence import Store
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services import analytics as analytics_service

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
    store: Store = Depends(get_store),
) -> RetrievalAnalytics:
    """Retrieval analytics dashboard — measure memory effectiveness."""
    effective_project = auth.project if auth.project is not None else project

    result = await analytics_service.get_retrieval_analytics(
        store,
        org_id=auth.org_id,
        days=days,
        project=effective_project,
    )

    return RetrievalAnalytics(
        **{
            **result,
            "score_distribution": [ScoreDistribution(**b) for b in result["score_distribution"]],
            "top_queries": [TopQuery(**q) for q in result["top_queries"]],
            "daily_stats": [DailyStat(**d) for d in result["daily_stats"]],
        }
    )
