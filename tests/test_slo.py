"""Tests for SLO Dashboard (F3) — threshold evaluation and alert dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestThresholdEvaluation:
    def test_lt_passing(self):
        from lore.server.routes.slo import _check_threshold
        assert _check_threshold(45.0, "lt", 50.0) is True

    def test_lt_failing(self):
        from lore.server.routes.slo import _check_threshold
        assert _check_threshold(55.0, "lt", 50.0) is False

    def test_gt_passing(self):
        from lore.server.routes.slo import _check_threshold
        assert _check_threshold(0.95, "gt", 0.90) is True

    def test_gt_failing(self):
        from lore.server.routes.slo import _check_threshold
        assert _check_threshold(0.85, "gt", 0.90) is False

    def test_none_value_passes(self):
        from lore.server.routes.slo import _check_threshold
        assert _check_threshold(None, "lt", 50.0) is True

    def test_equal_value_lt(self):
        from lore.server.routes.slo import _check_threshold
        # Equal is NOT less than, so it should fail
        assert _check_threshold(50.0, "lt", 50.0) is False

    def test_equal_value_gt(self):
        from lore.server.routes.slo import _check_threshold
        # Equal is NOT greater than, so it should fail
        assert _check_threshold(50.0, "gt", 50.0) is False


class TestMetricSql:
    def test_p50_latency(self):
        from lore.server.routes.slo import _metric_sql
        sql = _metric_sql("p50_latency")
        assert "percentile_cont(0.50)" in sql
        assert "AS value" in sql

    def test_p99_latency(self):
        from lore.server.routes.slo import _metric_sql
        sql = _metric_sql("p99_latency")
        assert "percentile_cont(0.99)" in sql

    def test_hit_rate(self):
        from lore.server.routes.slo import _metric_sql
        sql = _metric_sql("hit_rate")
        assert "results_count > 0" in sql

    def test_invalid_metric_raises(self):
        from lore.server.routes.slo import _metric_sql
        with pytest.raises(KeyError):
            _metric_sql("invalid_metric")


class TestValidMetrics:
    def test_valid_metrics(self):
        from lore.server.routes.slo import VALID_METRICS
        assert "p50_latency" in VALID_METRICS
        assert "p95_latency" in VALID_METRICS
        assert "p99_latency" in VALID_METRICS
        assert "hit_rate" in VALID_METRICS

    def test_valid_operators(self):
        from lore.server.routes.slo import VALID_OPERATORS
        assert "lt" in VALID_OPERATORS
        assert "gt" in VALID_OPERATORS


class TestAlertChannels:
    def test_webhook_channel_init(self):
        from lore.server.alerting import WebhookChannel
        ch = WebhookChannel("https://example.com/webhook")
        assert ch.url == "https://example.com/webhook"

    def test_email_channel_init(self):
        from lore.server.alerting import EmailChannel
        ch = EmailChannel("test@example.com")
        assert ch.to_addr == "test@example.com"

    @pytest.mark.asyncio
    async def test_webhook_send_with_mock(self):
        from lore.server.alerting import WebhookChannel

        ch = WebhookChannel("https://example.com/webhook")
        alert = {"slo_name": "test", "metric": "p99_latency", "value": 55.0}

        # Mock urllib since httpx may not be installed
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            # Force ImportError for httpx to use urllib fallback
            import sys
            _orig = sys.modules.get("httpx")
            sys.modules["httpx"] = None
            try:
                result = await ch.send(alert)
            finally:
                if _orig is not None:
                    sys.modules["httpx"] = _orig
                else:
                    sys.modules.pop("httpx", None)
            assert result is True

    @pytest.mark.asyncio
    async def test_email_send_without_smtp_config(self):
        from lore.server.alerting import EmailChannel

        ch = EmailChannel("test@example.com", smtp_host="")
        alert = {"slo_name": "test"}
        result = await ch.send(alert)
        assert result is False


class TestSloResponse:
    def test_slo_response_model(self):
        from lore.server.routes.slo import SloResponse
        slo = SloResponse(
            id="test-id",
            org_id="org-1",
            name="P99 < 50ms",
            metric="p99_latency",
            operator="lt",
            threshold=50.0,
            window_minutes=60,
            enabled=True,
        )
        assert slo.name == "P99 < 50ms"
        assert slo.threshold == 50.0

    def test_slo_status_response(self):
        from lore.server.routes.slo import SloStatusResponse
        status = SloStatusResponse(
            id="test-id",
            name="P99 < 50ms",
            metric="p99_latency",
            threshold=50.0,
            operator="lt",
            current_value=45.0,
            passing=True,
        )
        assert status.passing is True

    def test_slo_create_request(self):
        from lore.server.routes.slo import SloCreateRequest
        req = SloCreateRequest(
            name="Hit Rate > 90%",
            metric="hit_rate",
            operator="gt",
            threshold=0.90,
        )
        assert req.metric == "hit_rate"
        assert req.window_minutes == 60  # default


class TestSloCLI:
    def test_slo_subparser_exists(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["slo", "list"])
        assert args.command == "slo"
        assert args.slo_command == "list"

    def test_slo_create_args(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "slo", "create",
            "--name", "P99 < 50ms",
            "--metric", "p99_latency",
            "--threshold", "50",
            "--operator", "lt",
        ])
        assert args.slo_name == "P99 < 50ms"
        assert args.metric == "p99_latency"
        assert args.threshold == 50.0
