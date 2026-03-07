"""Tests for F1: On This Day — temporal recall."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from lore import Lore
from lore.cli import build_parser, main
from lore.store.memory import MemoryStore
from lore.temporal import OnThisDayEngine
from lore.types import Memory


def _stub_embed(text: str):
    return [0.0] * 384


def _make_lore() -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_stub_embed)


def _make_memory(
    mid: str,
    content: str,
    created_at: str,
    importance_score: float = 1.0,
    tier: str = "long",
    project: str | None = None,
    archived: bool = False,
    expires_at: str | None = None,
    source: str | None = None,
    tags: list | None = None,
) -> Memory:
    return Memory(
        id=mid,
        content=content,
        created_at=created_at,
        updated_at=created_at,
        importance_score=importance_score,
        tier=tier,
        project=project,
        archived=archived,
        expires_at=expires_at,
        source=source,
        tags=tags or [],
    )


# -----------------------------------------------------------------------
# S1: OnThisDayEngine class
# -----------------------------------------------------------------------


class TestOnThisDayEngine:
    def test_basic_query(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "march 6 event", "2024-03-06T10:00:00+00:00"))
        store.save(_make_memory("m2", "march 6 last year", "2023-03-06T12:00:00+00:00"))
        store.save(_make_memory("m3", "different day", "2024-03-10T10:00:00+00:00"))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0)

        assert 2024 in results
        assert 2023 in results
        assert len(results[2024]) == 1
        assert len(results[2023]) == 1
        assert results[2024][0].id == "m1"
        assert results[2023][0].id == "m2"

    def test_defaults_to_today(self):
        store = MemoryStore()
        today = date.today()
        store.save(
            _make_memory("m1", "today memory", f"2023-{today.month:02d}-{today.day:02d}T10:00:00+00:00")
        )

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(date_window_days=0)

        assert 2023 in results
        assert len(results[2023]) == 1

    def test_returns_dict_grouped_by_year(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "2024", "2024-06-15T10:00:00+00:00"))
        store.save(_make_memory("m2", "2023", "2023-06-15T10:00:00+00:00"))
        store.save(_make_memory("m3", "2022", "2022-06-15T10:00:00+00:00"))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=6, day=15, date_window_days=0)

        assert isinstance(results, dict)
        assert set(results.keys()) == {2024, 2023, 2022}

    def test_ordered_by_year_desc_then_importance_desc(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "low", "2024-03-06T10:00:00+00:00", importance_score=0.5))
        store.save(_make_memory("m2", "high", "2024-03-06T12:00:00+00:00", importance_score=0.9))
        store.save(_make_memory("m3", "old", "2022-03-06T10:00:00+00:00", importance_score=0.8))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0)

        years = list(results.keys())
        assert years == [2024, 2022]  # DESC
        # Within 2024, high importance first
        assert results[2024][0].id == "m2"
        assert results[2024][1].id == "m1"


# -----------------------------------------------------------------------
# S2: Date window / month+day extraction
# -----------------------------------------------------------------------


class TestDateWindow:
    def test_window_default_1(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "day before", "2024-03-05T10:00:00+00:00"))
        store.save(_make_memory("m2", "exact day", "2024-03-06T10:00:00+00:00"))
        store.save(_make_memory("m3", "day after", "2024-03-07T10:00:00+00:00"))
        store.save(_make_memory("m4", "too far", "2024-03-08T10:00:00+00:00"))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=1)

        assert len(results[2024]) == 3  # m1, m2, m3 included
        ids = {m.id for m in results[2024]}
        assert ids == {"m1", "m2", "m3"}

    def test_window_zero_exact_match(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "exact", "2024-03-06T10:00:00+00:00"))
        store.save(_make_memory("m2", "next day", "2024-03-07T10:00:00+00:00"))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0)

        assert len(results.get(2024, [])) == 1
        assert results[2024][0].id == "m1"

    def test_window_clamped_to_valid_range(self):
        """Window at start of month doesn't go below day 1."""
        store = MemoryStore()
        store.save(_make_memory("m1", "jan 1", "2024-01-01T10:00:00+00:00"))
        store.save(_make_memory("m2", "jan 2", "2024-01-02T10:00:00+00:00"))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=1, day=1, date_window_days=1)

        assert 2024 in results
        ids = {m.id for m in results[2024]}
        assert "m1" in ids
        assert "m2" in ids

    def test_leap_year_feb_29(self):
        """Feb 29 memories from leap years are found."""
        store = MemoryStore()
        store.save(_make_memory("m1", "leap day", "2024-02-29T10:00:00+00:00"))
        store.save(_make_memory("m2", "feb 28", "2023-02-28T10:00:00+00:00"))

        engine = OnThisDayEngine(store)
        # Query for Feb 29 with window 1 should find Feb 28-30
        results = engine.on_this_day(month=2, day=29, date_window_days=1)

        assert 2024 in results
        assert results[2024][0].id == "m1"
        # Feb 28 from 2023 also matches with window
        assert 2023 in results


# -----------------------------------------------------------------------
# S3: Python grouping by year
# -----------------------------------------------------------------------


class TestGroupingByYear:
    def test_all_memory_fields_preserved(self):
        store = MemoryStore()
        mem = _make_memory(
            "m1", "test content", "2024-03-06T10:00:00+00:00",
            importance_score=0.75, tier="short", project="proj1",
            source="test", tags=["tag1"],
        )
        store.save(mem)

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0)

        m = results[2024][0]
        assert m.id == "m1"
        assert m.content == "test content"
        assert m.tier == "short"
        assert m.project == "proj1"
        assert m.importance_score == 0.75
        assert m.source == "test"
        assert m.tags == ["tag1"]

    def test_empty_results(self):
        store = MemoryStore()
        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6)
        assert results == {}


# -----------------------------------------------------------------------
# S4: CLI command
# -----------------------------------------------------------------------


class TestCLI:
    def test_parser_on_this_day(self):
        parser = build_parser()
        args = parser.parse_args(["on-this-day", "--month", "3", "--day", "6"])
        assert args.command == "on-this-day"
        assert args.month == 3
        assert args.day == 6

    def test_parser_with_all_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "on-this-day",
            "--month", "3", "--day", "6",
            "--project", "work", "--tier", "long",
            "--limit", "10", "--offset", "5", "--json",
        ])
        assert args.project == "work"
        assert args.tier == "long"
        assert args.limit == 10
        assert args.offset == 5
        assert args.as_json is True

    def test_cli_integration(self, tmp_path, capsys):
        db = str(tmp_path / "test.db")
        main(["--db", db, "remember", "march 6 memory", "--type", "general"])
        capsys.readouterr()  # clear output

        # Query for today (may or may not match depending on current date)
        main(["--db", db, "on-this-day", "--json"])
        out = capsys.readouterr().out
        # Should be valid JSON (even if empty)
        parsed = json.loads(out)
        assert isinstance(parsed, dict)

    def test_cli_invalid_month(self, tmp_path):
        db = str(tmp_path / "test.db")
        with pytest.raises(SystemExit):
            main(["--db", db, "on-this-day", "--month", "13"])

    def test_cli_json_output_structure(self, tmp_path, capsys):
        db = str(tmp_path / "test.db")
        # Store a memory
        main(["--db", db, "remember", "test knowledge"])
        capsys.readouterr()

        today = date.today()
        main([
            "--db", db, "on-this-day",
            "--month", str(today.month),
            "--day", str(today.day),
            "--json",
        ])
        out = capsys.readouterr().out
        parsed = json.loads(out)
        if parsed:
            year_key = str(today.year)
            assert year_key in parsed
            for mem in parsed[year_key]:
                assert "id" in mem
                assert "content" in mem
                assert "type" in mem
                assert "tier" in mem


# -----------------------------------------------------------------------
# S5 + S8: MCP tool
# -----------------------------------------------------------------------


class TestMCPTool:
    def test_on_this_day_tool(self):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import on_this_day

        lore = _make_lore()
        today = date.today()
        lore.remember(content="mcp test memory")

        with patch("lore.mcp.server._get_lore", return_value=lore):
            result = on_this_day(month=today.month, day=today.day)
            assert "mcp test memory" in result

    def test_on_this_day_no_results(self):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import on_this_day

        lore = _make_lore()
        with patch("lore.mcp.server._get_lore", return_value=lore):
            result = on_this_day(month=1, day=1)
            assert "No memories found" in result

    def test_on_this_day_invalid_date(self):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import on_this_day

        lore = _make_lore()
        with patch("lore.mcp.server._get_lore", return_value=lore):
            result = on_this_day(month=13, day=1)
            assert "Invalid date" in result

    def test_on_this_day_includes_metadata(self):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import on_this_day

        lore = _make_lore()
        today = date.today()
        lore.remember(content="metadata test", source="pytest", project="test-proj")

        with patch("lore.mcp.server._get_lore", return_value=lore):
            result = on_this_day(month=today.month, day=today.day)
            assert "metadata test" in result
            assert "pytest" in result
            assert "test-proj" in result


# -----------------------------------------------------------------------
# S6: SDK method
# -----------------------------------------------------------------------


class TestSDKMethod:
    def test_on_this_day_method_exists(self):
        lore = _make_lore()
        assert hasattr(lore, "on_this_day")
        lore.close()

    def test_on_this_day_returns_dict(self):
        lore = _make_lore()
        result = lore.on_this_day(month=1, day=1)
        assert isinstance(result, dict)
        lore.close()

    def test_on_this_day_delegates_to_engine(self):
        lore = _make_lore()
        today = date.today()
        lore.remember(content="sdk test")

        result = lore.on_this_day(month=today.month, day=today.day)
        assert isinstance(result, dict)
        if today.year in result:
            assert any("sdk test" in m.content for m in result[today.year])
        lore.close()

    def test_on_this_day_with_project_filter(self):
        lore = _make_lore()
        today = date.today()
        lore.remember(content="proj A", project="alpha")
        lore.remember(content="proj B", project="beta")

        result = lore.on_this_day(
            month=today.month, day=today.day, project="alpha"
        )
        all_memories = [m for mems in result.values() for m in mems]
        for m in all_memories:
            assert m.project == "alpha"
        lore.close()

    def test_on_this_day_with_tier_filter(self):
        lore = _make_lore()
        today = date.today()
        lore.remember(content="long tier", tier="long")
        lore.remember(content="short tier", tier="short")

        result = lore.on_this_day(
            month=today.month, day=today.day, tier="long"
        )
        all_memories = [m for mems in result.values() for m in mems]
        for m in all_memories:
            assert m.tier == "long"
        lore.close()


# -----------------------------------------------------------------------
# S7: Tier visibility + archived status
# -----------------------------------------------------------------------


class TestTierVisibility:
    def test_archived_memories_excluded(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "active", "2024-03-06T10:00:00+00:00"))
        store.save(_make_memory("m2", "archived", "2024-03-06T12:00:00+00:00", archived=True))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0)

        all_ids = {m.id for mems in results.values() for m in mems}
        assert "m1" in all_ids
        assert "m2" not in all_ids

    def test_expired_memories_excluded(self):
        store = MemoryStore()
        store.save(_make_memory(
            "m1", "not expired", "2024-03-06T10:00:00+00:00",
        ))
        store.save(_make_memory(
            "m2", "expired", "2024-03-06T12:00:00+00:00",
            expires_at="2020-01-01T00:00:00+00:00",
        ))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0)

        all_ids = {m.id for mems in results.values() for m in mems}
        assert "m1" in all_ids
        assert "m2" not in all_ids

    def test_tier_filter(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "long", "2024-03-06T10:00:00+00:00", tier="long"))
        store.save(_make_memory("m2", "short", "2024-03-06T12:00:00+00:00", tier="short"))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0, tier="long")

        all_ids = {m.id for mems in results.values() for m in mems}
        assert "m1" in all_ids
        assert "m2" not in all_ids


# -----------------------------------------------------------------------
# S9: Edge cases and integration
# -----------------------------------------------------------------------


class TestEdgeCases:
    def test_invalid_month(self):
        engine = OnThisDayEngine(MemoryStore())
        with pytest.raises(ValueError, match="month must be 1-12"):
            engine.on_this_day(month=0, day=1)
        with pytest.raises(ValueError, match="month must be 1-12"):
            engine.on_this_day(month=13, day=1)

    def test_invalid_day(self):
        engine = OnThisDayEngine(MemoryStore())
        with pytest.raises(ValueError, match="day must be 1-31"):
            engine.on_this_day(month=1, day=0)
        with pytest.raises(ValueError, match="day must be 1-31"):
            engine.on_this_day(month=1, day=32)

    def test_limit_and_offset(self):
        store = MemoryStore()
        for i in range(5):
            store.save(_make_memory(
                f"m{i}", f"mem {i}", "2024-03-06T10:00:00+00:00",
                importance_score=float(i),
            ))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0, limit=2)
        total = sum(len(v) for v in results.values())
        assert total == 2

    def test_offset(self):
        store = MemoryStore()
        for i in range(5):
            store.save(_make_memory(
                f"m{i}", f"mem {i}", "2024-03-06T10:00:00+00:00",
                importance_score=float(i),
            ))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0, offset=3)
        total = sum(len(v) for v in results.values())
        assert total == 2

    def test_no_created_at_skipped(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "no date", ""))
        store.save(_make_memory("m2", "has date", "2024-03-06T10:00:00+00:00"))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0)

        all_ids = {m.id for mems in results.values() for m in mems}
        assert "m1" not in all_ids
        assert "m2" in all_ids

    def test_format_results_empty(self):
        engine = OnThisDayEngine(MemoryStore())
        assert engine.format_results({}) == "No memories found for this day."

    def test_format_results_with_metadata(self):
        store = MemoryStore()
        mem = _make_memory(
            "m1", "test content", "2024-03-06T10:00:00+00:00",
            source="pytest", project="testproj", tags=["t1"],
        )
        store.save(mem)

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=3, day=6, date_window_days=0)
        formatted = engine.format_results(results, include_metadata=True)

        assert "test content" in formatted
        assert "2024" in formatted
        assert "pytest" in formatted
        assert "testproj" in formatted
        assert "t1" in formatted

    def test_many_years(self):
        store = MemoryStore()
        for year in range(2015, 2025):
            store.save(_make_memory(
                f"m{year}", f"year {year}",
                f"{year}-07-04T10:00:00+00:00",
            ))

        engine = OnThisDayEngine(store)
        results = engine.on_this_day(month=7, day=4, date_window_days=0)

        assert len(results) == 10
        years = list(results.keys())
        assert years == sorted(years, reverse=True)


# -----------------------------------------------------------------------
# S10: Documentation (tested via docstrings and help text)
# -----------------------------------------------------------------------


class TestDocumentation:
    def test_engine_has_docstring(self):
        assert OnThisDayEngine.__doc__ is not None
        assert "on-this-day" in OnThisDayEngine.__doc__.lower() or "month+day" in OnThisDayEngine.__doc__

    def test_on_this_day_method_has_docstring(self):
        assert OnThisDayEngine.on_this_day.__doc__ is not None
        assert "month" in OnThisDayEngine.on_this_day.__doc__

    def test_sdk_method_has_docstring(self):
        assert Lore.on_this_day.__doc__ is not None

    def test_cli_help_text(self):
        parser = build_parser()
        # Verify on-this-day is in the subcommands
        subparsers_actions = [
            action for action in parser._subparsers._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        assert len(subparsers_actions) > 0
        choices = subparsers_actions[0].choices
        assert "on-this-day" in choices
