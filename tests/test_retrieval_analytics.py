"""Tests for retrieval analytics endpoint and event logging."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRetrievalAnalyticsEndpoint:
    """Test GET /v1/analytics/retrieval response structure."""

    @pytest.mark.asyncio
    async def test_empty_state_returns_zeros(self):
        """Analytics endpoint should return zeros when no events exist."""
        from lore.server.routes.analytics import retrieval_analytics

        # Mock DB pool that returns empty results
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            # summary query
            {"total_queries": 0, "queries_with_results": 0, "queries_empty": 0,
             "avg_results": None, "avg_score": None, "avg_max_score": None,
             "avg_latency_ms": None},
            # p95 query
            {"p95": None},
            # unique memories
            {"unique_count": 0},
            # total memories
            {"total": 100},
        ])
        mock_conn.fetch = AsyncMock(side_effect=[
            [],  # score distribution
            [],  # top queries
            [],  # daily stats
        ])
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=mock_conn)

        mock_auth = MagicMock()
        mock_auth.org_id = "test-org"
        mock_auth.project = None

        with patch("lore.server.routes.analytics.get_pool", return_value=mock_pool):
            result = await retrieval_analytics(days=7, project=None, auth=mock_auth)

        assert result.total_queries == 0
        assert result.queries_with_results == 0
        assert result.queries_empty == 0
        assert result.hit_rate == 0.0
        assert result.avg_results_per_query == 0.0
        assert result.avg_score is None
        assert result.memory_utilization == 0.0
        assert result.total_memories == 100
        assert result.unique_memories_retrieved == 0
        assert len(result.score_distribution) == 5
        assert len(result.top_queries) == 0
        assert len(result.daily_stats) == 0

    @pytest.mark.asyncio
    async def test_score_distribution_buckets(self):
        """Score distribution should have exactly 5 buckets."""
        from lore.server.routes.analytics import retrieval_analytics

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[
            {"total_queries": 10, "queries_with_results": 8, "queries_empty": 2,
             "avg_results": 3.5, "avg_score": 0.55, "avg_max_score": 0.72,
             "avg_latency_ms": 15.5},
            {"p95": 25.3},
            {"unique_count": 15},
            {"total": 100},
        ])
        mock_conn.fetch = AsyncMock(side_effect=[
            [{"bucket": "0.3-0.5", "cnt": 20}, {"bucket": "0.5-0.7", "cnt": 10}],
            [{"query": "test query", "cnt": 5, "avg_s": 0.6}],
            [{"day": "2026-03-09", "queries": 10, "avg_s": 0.55, "hit_rate": 0.8}],
        ])
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=mock_conn)

        mock_auth = MagicMock()
        mock_auth.org_id = "test-org"
        mock_auth.project = None

        with patch("lore.server.routes.analytics.get_pool", return_value=mock_pool):
            result = await retrieval_analytics(days=7, project=None, auth=mock_auth)

        assert result.total_queries == 10
        assert result.hit_rate == 0.8
        assert len(result.score_distribution) == 5
        buckets = [d.bucket for d in result.score_distribution]
        assert buckets == ["0.0-0.3", "0.3-0.5", "0.5-0.7", "0.7-0.9", "0.9-1.0"]
        assert result.memory_utilization == 15.0
        assert result.p95_latency_ms == 25.3


class TestRetrievalEventLogging:
    """Test that retrieve calls create analytics events."""

    @pytest.mark.asyncio
    async def test_log_event_writes_to_db(self):
        """_record_retrieval_event should INSERT into retrieval_events."""
        from lore.server.routes.retrieve import RetrieveMemory, _record_retrieval_event

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=mock_conn)

        mock_auth = MagicMock()
        mock_auth.org_id = "test-org"

        memories = [
            RetrieveMemory(
                id="mem1", content="test content", type="fact",
                tier="long", score=0.85, created_at="2026-03-09",
            ),
            RetrieveMemory(
                id="mem2", content="another", type="lesson",
                tier="long", score=0.62, created_at="2026-03-09",
            ),
        ]

        with patch("lore.server.routes.retrieve.get_pool", return_value=mock_pool), \
             patch("lore.server.metrics.retrieve_queries_total", MagicMock()), \
             patch("lore.server.metrics.retrieve_results_total", MagicMock()), \
             patch("lore.server.metrics.retrieve_empty_total", MagicMock()), \
             patch("lore.server.metrics.retrieve_latency", MagicMock()), \
             patch("lore.server.metrics.retrieve_max_score", MagicMock()):
            await _record_retrieval_event(
                auth=mock_auth,
                query_text="test query",
                memories=memories,
                min_score=0.3,
                elapsed_ms=15.5,
                fmt="xml",
                effective_project=None,
            )

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "INSERT INTO retrieval_events" in call_args[0][0]
        assert call_args[0][1] == "test-org"
        assert call_args[0][2] == "test query"
        assert call_args[0][3] == 2  # results_count
        assert json.loads(call_args[0][4]) == [0.85, 0.62]  # scores

    @pytest.mark.asyncio
    async def test_log_event_handles_errors_gracefully(self):
        """_record_retrieval_event should not raise on DB errors."""
        from lore.server.routes.retrieve import _record_retrieval_event

        mock_auth = MagicMock()
        mock_auth.org_id = "test-org"

        with patch("lore.server.routes.retrieve.get_pool", side_effect=Exception("DB down")):
            # Should not raise
            await _record_retrieval_event(
                auth=mock_auth,
                query_text="test",
                memories=[],
                min_score=0.3,
                elapsed_ms=5.0,
                fmt="xml",
                effective_project=None,
            )


class TestPrometheusMetrics:
    """Test that Prometheus metrics are properly defined."""

    def test_retrieve_metrics_exist(self):
        from lore.server.metrics import (
            ALL_METRICS,
            retrieve_empty_total,
            retrieve_latency,
            retrieve_max_score,
            retrieve_queries_total,
            retrieve_results_total,
        )

        assert retrieve_queries_total is not None
        assert retrieve_results_total is not None
        assert retrieve_empty_total is not None
        assert retrieve_latency is not None
        assert retrieve_max_score is not None

        # All should be in ALL_METRICS registry
        assert retrieve_queries_total in ALL_METRICS
        assert retrieve_max_score in ALL_METRICS

    def test_retrieve_metrics_collect(self):
        """Metrics should produce valid Prometheus text output."""
        from lore.server.metrics import retrieve_queries_total

        retrieve_queries_total.inc()
        output = retrieve_queries_total.collect()
        assert "lore_retrieve_queries_total" in output


class TestAnalyticsResponseModel:
    """Test Pydantic models for analytics responses."""

    def test_score_distribution_model(self):
        from lore.server.routes.analytics import ScoreDistribution

        sd = ScoreDistribution(bucket="0.3-0.5", count=10, percentage=33.3)
        assert sd.bucket == "0.3-0.5"
        assert sd.count == 10

    def test_daily_stat_model(self):
        from lore.server.routes.analytics import DailyStat

        ds = DailyStat(date="2026-03-09", queries=25, avg_score=0.65, hit_rate=0.85)
        assert ds.queries == 25

    def test_top_query_model(self):
        from lore.server.routes.analytics import TopQuery

        tq = TopQuery(query="how to debug", count=5, avg_score=0.72)
        assert tq.count == 5

    def test_full_analytics_model(self):
        from lore.server.routes.analytics import RetrievalAnalytics

        analytics = RetrievalAnalytics(
            total_queries=100,
            queries_with_results=80,
            queries_empty=20,
            hit_rate=0.8,
            avg_results_per_query=3.5,
            avg_score=0.55,
            avg_max_score=0.72,
            avg_latency_ms=15.5,
            p95_latency_ms=25.3,
            score_distribution=[],
            top_queries=[],
            memory_utilization=15.0,
            unique_memories_retrieved=15,
            total_memories=100,
            daily_stats=[],
            lookback_days=7,
        )
        assert analytics.total_queries == 100
        assert analytics.hit_rate == 0.8
