"""Tests for F3: Temporal Recall Filters."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pytest

from lore import Lore
from lore.store.memory import MemoryStore
from lore.temporal import TemporalFilterResolver, parse_iso
from lore.types import VALID_WINDOWS, RecallConfig

_DIM = 384


def _fake_embed(text: str) -> List[float]:
    rng = np.random.RandomState(abs(hash(text)) % (2**31))
    vec = rng.randn(_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _make_lore() -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_fake_embed, importance_threshold=0.0)


def _utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


# -----------------------------------------------------------------------
# TemporalFilterResolver unit tests
# -----------------------------------------------------------------------


class TestTemporalFilterResolver:
    """Unit tests for TemporalFilterResolver.resolve()."""

    def test_no_filters_returns_none(self):
        config = RecallConfig()
        assert TemporalFilterResolver.resolve(config) == (None, None)

    def test_has_temporal_filters_false(self):
        assert not TemporalFilterResolver.has_temporal_filters(RecallConfig())

    def test_has_temporal_filters_true(self):
        assert TemporalFilterResolver.has_temporal_filters(
            RecallConfig(year=2024)
        )
        assert TemporalFilterResolver.has_temporal_filters(
            RecallConfig(window="today")
        )
        assert TemporalFilterResolver.has_temporal_filters(
            RecallConfig(days_ago=7)
        )

    # -- Preset windows --

    def test_window_today(self):
        now = _utc(2024, 3, 15, 14, 30)
        config = RecallConfig(window="today")
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == _utc(2024, 3, 15, 0, 0)
        assert t is None

    def test_window_last_hour(self):
        now = _utc(2024, 3, 15, 14, 30)
        config = RecallConfig(window="last_hour")
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == now - timedelta(hours=1)
        assert t is None

    def test_window_last_week(self):
        now = _utc(2024, 3, 15, 14, 30)
        config = RecallConfig(window="last_week")
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == now - timedelta(weeks=1)

    def test_window_last_month(self):
        now = _utc(2024, 3, 15)
        config = RecallConfig(window="last_month")
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == now - timedelta(days=30)

    def test_window_last_year(self):
        now = _utc(2024, 3, 15)
        config = RecallConfig(window="last_year")
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == now - timedelta(days=365)

    def test_window_invalid_raises(self):
        config = RecallConfig(window="bogus")
        with pytest.raises(ValueError, match="Invalid window"):
            TemporalFilterResolver.resolve(config)

    # -- Year / month / day --

    def test_year_only(self):
        config = RecallConfig(year=2024)
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 1, 1)
        assert t == _utc(2025, 1, 1)

    def test_year_month(self):
        config = RecallConfig(year=2024, month=3)
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 3, 1)
        # End should be just past March 31
        assert t.month == 4 or (t.month == 3 and t.day == 31)

    def test_year_month_day(self):
        config = RecallConfig(year=2024, month=3, day=6)
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 3, 6)
        assert t == _utc(2024, 3, 7)

    def test_leap_year_feb29(self):
        config = RecallConfig(year=2024, month=2, day=29)
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 2, 29)
        assert t == _utc(2024, 3, 1)

    def test_february_month_range(self):
        config = RecallConfig(year=2024, month=2)
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 2, 1)
        assert t > _utc(2024, 2, 29)  # leap year

    def test_invalid_month_raises(self):
        config = RecallConfig(year=2024, month=13)
        with pytest.raises(ValueError, match="Invalid month"):
            TemporalFilterResolver.resolve(config)

    def test_invalid_day_raises(self):
        config = RecallConfig(year=2024, month=3, day=32)
        with pytest.raises(ValueError, match="Invalid day"):
            TemporalFilterResolver.resolve(config)

    def test_month_only_raises(self):
        config = RecallConfig(month=12)
        with pytest.raises(ValueError, match="month-only"):
            TemporalFilterResolver.resolve(config)

    def test_day_without_month_raises(self):
        config = RecallConfig(day=15)
        with pytest.raises(ValueError, match="day requires month"):
            TemporalFilterResolver.resolve(config)

    # -- Relative times --

    def test_days_ago(self):
        now = _utc(2024, 3, 15, 12, 0)
        config = RecallConfig(days_ago=7)
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == now - timedelta(days=7)
        assert t is None

    def test_hours_ago(self):
        now = _utc(2024, 3, 15, 12, 0)
        config = RecallConfig(hours_ago=3)
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == now - timedelta(hours=3)
        assert t is None

    def test_days_and_hours_combined(self):
        now = _utc(2024, 3, 15, 12, 0)
        config = RecallConfig(days_ago=2, hours_ago=3)
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == now - timedelta(days=2, hours=3)

    def test_days_ago_zero_means_today(self):
        now = _utc(2024, 3, 15, 14, 30)
        config = RecallConfig(days_ago=0)
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == _utc(2024, 3, 15, 0, 0)
        assert t is None

    def test_negative_days_raises(self):
        config = RecallConfig(days_ago=-1)
        with pytest.raises(ValueError, match="non-negative"):
            TemporalFilterResolver.resolve(config)

    def test_negative_hours_raises(self):
        config = RecallConfig(hours_ago=-1)
        with pytest.raises(ValueError, match="non-negative"):
            TemporalFilterResolver.resolve(config)

    # -- Before / after --

    def test_before_only(self):
        config = RecallConfig(before="2024-06-01T00:00:00Z")
        f, t = TemporalFilterResolver.resolve(config)
        assert f is None
        assert t == _utc(2024, 6, 1)

    def test_after_only(self):
        config = RecallConfig(after="2024-01-01T00:00:00Z")
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 1, 1)
        assert t is None

    def test_before_and_after(self):
        config = RecallConfig(
            after="2024-01-01T00:00:00Z",
            before="2024-06-01T00:00:00Z",
        )
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 1, 1)
        assert t == _utc(2024, 6, 1)

    # -- Explicit dates --

    def test_date_from_only(self):
        config = RecallConfig(date_from="2024-03-01")
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 3, 1)
        assert t is None

    def test_date_to_only(self):
        config = RecallConfig(date_to="2024-03-31")
        f, t = TemporalFilterResolver.resolve(config)
        assert f is None
        assert t == _utc(2024, 3, 31)

    def test_date_from_and_to(self):
        config = RecallConfig(
            date_from="2024-03-01",
            date_to="2024-03-31",
        )
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 3, 1)
        assert t == _utc(2024, 3, 31)

    # -- Priority: explicit dates override lower layers --

    def test_explicit_overrides_window(self):
        now = _utc(2024, 3, 15)
        config = RecallConfig(
            window="last_week",
            date_from="2020-01-01",
        )
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == _utc(2020, 1, 1)

    def test_days_ago_overrides_ymd(self):
        now = _utc(2024, 3, 15, 12, 0)
        config = RecallConfig(year=2023, days_ago=3)
        f, t = TemporalFilterResolver.resolve(config, now=now)
        # days_ago is higher priority than year
        assert f == now - timedelta(days=3)

    def test_before_after_compose_with_days_ago(self):
        now = _utc(2024, 3, 15, 12, 0)
        config = RecallConfig(
            days_ago=7,
            before="2024-03-12T00:00:00Z",
        )
        f, t = TemporalFilterResolver.resolve(config, now=now)
        assert f == now - timedelta(days=7)
        assert t == _utc(2024, 3, 12)

    # -- Timezone handling --

    def test_naive_iso_defaults_to_utc(self):
        dt = parse_iso("2024-03-15T12:00:00")
        assert dt.tzinfo == timezone.utc

    def test_tz_aware_iso_preserved(self):
        dt = parse_iso("2024-03-15T12:00:00+05:00")
        assert dt.utcoffset() == timedelta(hours=5)


# -----------------------------------------------------------------------
# Integration tests: temporal filters with real recall queries
# -----------------------------------------------------------------------


class TestTemporalRecallIntegration:
    """Integration tests using Lore.recall() with temporal filters."""

    def _setup_memories(self) -> Lore:
        """Create a Lore instance with memories at different timestamps."""
        lore = _make_lore()
        # Store memories and then manually adjust their created_at
        m1 = lore.remember("python tip from 2023")
        m2 = lore.remember("python tip from march 2024")
        m3 = lore.remember("python tip from june 2024")

        # Patch created_at timestamps
        mem1 = lore._store.get(m1)
        mem1.created_at = "2023-06-15T10:00:00+00:00"
        lore._store.update(mem1)

        mem2 = lore._store.get(m2)
        mem2.created_at = "2024-03-10T10:00:00+00:00"
        lore._store.update(mem2)

        mem3 = lore._store.get(m3)
        mem3.created_at = "2024-06-20T10:00:00+00:00"
        lore._store.update(mem3)

        return lore

    def test_year_filter(self):
        lore = self._setup_memories()
        results = lore.recall("python", year=2024)
        assert len(results) == 2
        for r in results:
            assert "2024" in r.memory.created_at

    def test_year_month_filter(self):
        lore = self._setup_memories()
        results = lore.recall("python", year=2024, month=3)
        assert len(results) == 1
        assert "2024-03" in results[0].memory.created_at

    def test_date_from_filter(self):
        lore = self._setup_memories()
        results = lore.recall("python", date_from="2024-01-01")
        assert len(results) == 2

    def test_date_to_filter(self):
        lore = self._setup_memories()
        results = lore.recall("python", date_to="2024-01-01")
        assert len(results) == 1
        assert "2023" in results[0].memory.created_at

    def test_date_range(self):
        lore = self._setup_memories()
        results = lore.recall(
            "python",
            date_from="2024-03-01",
            date_to="2024-04-01",
        )
        assert len(results) == 1
        assert "2024-03" in results[0].memory.created_at

    def test_before_filter(self):
        lore = self._setup_memories()
        results = lore.recall("python", before="2024-06-01T00:00:00Z")
        assert len(results) == 2

    def test_after_filter(self):
        lore = self._setup_memories()
        results = lore.recall("python", after="2024-06-01T00:00:00Z")
        assert len(results) == 1
        assert "2024-06" in results[0].memory.created_at

    def test_no_matches_returns_empty(self):
        lore = self._setup_memories()
        results = lore.recall("python", year=2020)
        assert results == []

    def test_importance_ordering_preserved(self):
        lore = self._setup_memories()
        results = lore.recall("python", year=2024, limit=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_temporal_with_project_filter(self):
        lore = _make_lore()
        lore.project = "work"
        m1 = lore.remember("work tip")
        mem1 = lore._store.get(m1)
        mem1.created_at = "2024-03-10T10:00:00+00:00"
        lore._store.update(mem1)

        lore.project = "personal"
        m2 = lore.remember("personal tip")
        mem2 = lore._store.get(m2)
        mem2.created_at = "2024-03-10T10:00:00+00:00"
        lore._store.update(mem2)

        lore.project = "work"
        results = lore.recall("tip", year=2024, month=3)
        assert len(results) == 1
        assert results[0].memory.project == "work"

    def test_temporal_with_tier_filter(self):
        lore = _make_lore()
        m1 = lore.remember("short tier memory", tier="short")
        mem1 = lore._store.get(m1)
        mem1.created_at = "2024-03-10T10:00:00+00:00"
        lore._store.update(mem1)

        m2 = lore.remember("long tier memory", tier="long")
        mem2 = lore._store.get(m2)
        mem2.created_at = "2024-03-10T10:00:00+00:00"
        lore._store.update(mem2)

        results = lore.recall("memory", year=2024, tier="short")
        assert len(results) == 1
        assert results[0].memory.tier == "short"


# -----------------------------------------------------------------------
# CLI tests
# -----------------------------------------------------------------------


class TestTemporalCLI:
    """Test CLI flag parsing for temporal filters."""

    def test_temporal_flags_parse(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "recall", "python",
            "--year", "2024",
            "--month", "3",
            "--day", "6",
        ])
        assert args.year == 2024
        assert args.month == 3
        assert args.day == 6

    def test_days_ago_flag(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["recall", "test", "--days-ago", "7"])
        assert args.days_ago == 7

    def test_hours_ago_flag(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["recall", "test", "--hours-ago", "3"])
        assert args.hours_ago == 3

    def test_window_flag(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["recall", "test", "--window", "last_week"])
        assert args.window == "last_week"

    def test_before_after_flags(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "recall", "test",
            "--before", "2024-12-31T00:00:00Z",
            "--after", "2024-01-01T00:00:00Z",
        ])
        assert args.before == "2024-12-31T00:00:00Z"
        assert args.after == "2024-01-01T00:00:00Z"

    def test_date_from_to_flags(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "recall", "test",
            "--date-from", "2024-03-01",
            "--date-to", "2024-03-31",
        ])
        assert args.date_from == "2024-03-01"
        assert args.date_to == "2024-03-31"

    def test_window_invalid_rejected(self):
        from lore.cli import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["recall", "test", "--window", "bogus"])


# -----------------------------------------------------------------------
# MCP tests
# -----------------------------------------------------------------------


class TestTemporalMCP:
    """Test MCP tool parameter passing."""

    def test_recall_accepts_temporal_params(self):
        """Verify MCP recall function signature includes temporal params."""
        import inspect

        from lore.mcp.server import recall as mcp_recall

        sig = inspect.signature(mcp_recall)
        temporal_params = [
            "year", "month", "day", "days_ago", "hours_ago",
            "window", "before", "after", "date_from", "date_to",
        ]
        for param in temporal_params:
            assert param in sig.parameters, f"Missing MCP param: {param}"
            assert sig.parameters[param].default is None


# -----------------------------------------------------------------------
# parse_iso tests
# -----------------------------------------------------------------------


class TestParseISO:
    def test_naive_string(self):
        dt = parse_iso("2024-03-15")
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15

    def test_with_time(self):
        dt = parse_iso("2024-03-15T14:30:00")
        assert dt.hour == 14
        assert dt.minute == 30

    def test_with_timezone(self):
        dt = parse_iso("2024-03-15T14:30:00+05:00")
        assert dt.utcoffset() == timedelta(hours=5)

    def test_utc_suffix(self):
        dt = parse_iso("2024-03-15T14:30:00+00:00")
        assert dt.tzinfo is not None


# -----------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------


class TestTemporalEdgeCases:
    def test_all_valid_windows(self):
        for w in VALID_WINDOWS:
            config = RecallConfig(window=w)
            f, t = TemporalFilterResolver.resolve(config)
            assert f is not None

    def test_february_non_leap(self):
        config = RecallConfig(year=2023, month=2)
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2023, 2, 1)
        assert t <= _utc(2023, 3, 1)

    def test_december_boundary(self):
        config = RecallConfig(year=2024, month=12)
        f, t = TemporalFilterResolver.resolve(config)
        assert f == _utc(2024, 12, 1)
        assert t > _utc(2024, 12, 31)

    def test_verbatim_with_temporal(self):
        lore = _make_lore()
        m = lore.remember("verbatim test")
        mem = lore._store.get(m)
        mem.created_at = "2024-03-10T10:00:00+00:00"
        lore._store.update(mem)

        results = lore.recall("verbatim", year=2024, verbatim=True)
        assert len(results) == 1
        assert results[0].verbatim is True
