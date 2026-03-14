"""Tests for E2: Recent Activity — data types and formatting module."""

from __future__ import annotations

import pytest

from lore.recent import (
    _format_time,
    format_brief,
    format_cli,
    format_detailed,
    format_structured,
    group_memories_by_project,
)
from lore.types import Memory, ProjectGroup, RecentActivityResult


def _make_memory(
    id: str = "m1",
    content: str = "test memory content",
    type: str = "general",
    tier: str = "long",
    project: str | None = "lore",
    created_at: str = "2026-03-14T14:30:00+00:00",
    tags: list | None = None,
    importance_score: float = 1.0,
) -> Memory:
    return Memory(
        id=id,
        content=content,
        type=type,
        tier=tier,
        project=project,
        created_at=created_at,
        updated_at=created_at,
        tags=tags or [],
        importance_score=importance_score,
    )


# =========================================================================
# S1: Data type tests
# =========================================================================


class TestProjectGroup:
    def test_creation(self):
        m = _make_memory()
        g = ProjectGroup(project="lore", memories=[m], count=1)
        assert g.project == "lore"
        assert len(g.memories) == 1
        assert g.count == 1

    def test_defaults(self):
        g = ProjectGroup(project="test")
        assert g.summary is None
        assert g.memories == []
        assert g.count == 0

    def test_with_summary(self):
        g = ProjectGroup(project="test", memories=[], count=0, summary="Key points")
        assert g.summary == "Key points"


class TestRecentActivityResult:
    def test_creation(self):
        g = ProjectGroup(project="lore", memories=[_make_memory()], count=1)
        r = RecentActivityResult(groups=[g], total_count=1, hours=24)
        assert r.total_count == 1
        assert r.hours == 24
        assert len(r.groups) == 1

    def test_defaults(self):
        r = RecentActivityResult()
        assert r.has_llm_summary is False
        assert r.query_time_ms == 0.0
        assert r.generated_at == ""
        assert r.groups == []
        assert r.total_count == 0


# =========================================================================
# S4: Grouping tests
# =========================================================================


class TestGroupMemoriesByProject:
    def test_empty_list(self):
        assert group_memories_by_project([]) == []

    def test_single_project(self):
        mems = [
            _make_memory(id="m1", project="lore", created_at="2026-03-14T10:00:00+00:00"),
            _make_memory(id="m2", project="lore", created_at="2026-03-14T11:00:00+00:00"),
        ]
        groups = group_memories_by_project(mems)
        assert len(groups) == 1
        assert groups[0].project == "lore"
        assert groups[0].count == 2
        # Newest first
        assert groups[0].memories[0].id == "m2"

    def test_multiple_projects(self):
        mems = [
            _make_memory(id="m1", project="lore", created_at="2026-03-14T10:00:00+00:00"),
            _make_memory(id="m2", project="app", created_at="2026-03-14T12:00:00+00:00"),
            _make_memory(id="m3", project="lore", created_at="2026-03-14T11:00:00+00:00"),
        ]
        groups = group_memories_by_project(mems)
        assert len(groups) == 2
        projects = [g.project for g in groups]
        assert "lore" in projects
        assert "app" in projects

    def test_null_project_grouped_as_default(self):
        mems = [_make_memory(id="m1", project=None)]
        groups = group_memories_by_project(mems)
        assert len(groups) == 1
        assert groups[0].project == "default"

    def test_groups_sorted_by_newest(self):
        mems = [
            _make_memory(id="m1", project="old-proj", created_at="2026-03-14T08:00:00+00:00"),
            _make_memory(id="m2", project="new-proj", created_at="2026-03-14T16:00:00+00:00"),
        ]
        groups = group_memories_by_project(mems)
        assert groups[0].project == "new-proj"
        assert groups[1].project == "old-proj"


# =========================================================================
# S4: Format tests
# =========================================================================


class TestFormatBrief:
    def test_no_memories(self):
        r = RecentActivityResult(hours=24)
        assert format_brief(r) == "No recent activity in the last 24h."

    def test_basic(self):
        m = _make_memory(content="Fixed a critical bug in the authentication module")
        g = ProjectGroup(project="lore", memories=[m], count=1)
        r = RecentActivityResult(groups=[g], total_count=1, hours=24)
        text = format_brief(r)
        assert "## Recent Activity (last 24h)" in text
        assert "### lore (1)" in text
        assert "[14:30]" in text
        assert "general:" in text

    def test_truncation_at_100_chars(self):
        long_content = "x" * 150
        m = _make_memory(content=long_content)
        g = ProjectGroup(project="lore", memories=[m], count=1)
        r = RecentActivityResult(groups=[g], total_count=1, hours=24)
        text = format_brief(r)
        assert "x" * 100 + "..." in text

    def test_with_summary_replaces_listing(self):
        m = _make_memory()
        g = ProjectGroup(project="lore", memories=[m], count=1, summary="- Key point A\n- Key point B")
        r = RecentActivityResult(groups=[g], total_count=1, hours=24)
        text = format_brief(r)
        assert "Key point A" in text
        assert "[14:30]" not in text  # Raw listing replaced by summary

    def test_overflow(self):
        mems = [_make_memory(id=f"m{i}", created_at=f"2026-03-14T{10+i:02d}:00:00+00:00") for i in range(5)]
        g = ProjectGroup(project="lore", memories=mems, count=5)
        r = RecentActivityResult(groups=[g], total_count=5, hours=24)
        text = format_brief(r)
        assert "(2 more)" in text
        # Only 3 memory lines shown
        lines = [l for l in text.split("\n") if l.startswith("- [")]
        assert len(lines) == 3


class TestFormatDetailed:
    def test_metadata_included(self):
        m = _make_memory(
            tier="short",
            importance_score=0.85,
            tags=["architecture", "decision"],
        )
        g = ProjectGroup(project="lore", memories=[m], count=1)
        r = RecentActivityResult(groups=[g], total_count=1, hours=24)
        text = format_detailed(r)
        assert "tier: short" in text
        assert "importance: 0.85" in text
        assert "Tags: architecture, decision" in text

    def test_no_memories(self):
        r = RecentActivityResult(hours=48)
        assert "No recent activity in the last 48h." in format_detailed(r)


class TestFormatStructured:
    def test_all_fields_present(self):
        m = _make_memory(tags=["tag1"])
        g = ProjectGroup(project="lore", memories=[m], count=1)
        r = RecentActivityResult(
            groups=[g], total_count=1, hours=24,
            generated_at="2026-03-14T14:00:00Z",
            query_time_ms=15.2,
        )
        d = format_structured(r)
        assert d["total_count"] == 1
        assert d["hours"] == 24
        assert d["generated_at"] == "2026-03-14T14:00:00Z"
        assert d["has_llm_summary"] is False
        assert d["query_time_ms"] == 15.2
        assert len(d["groups"]) == 1
        group = d["groups"][0]
        assert group["project"] == "lore"
        assert group["count"] == 1
        mem = group["memories"][0]
        assert mem["id"] == "m1"
        assert mem["tags"] == ["tag1"]
        assert "importance_score" in mem


class TestFormatCli:
    def test_no_markdown(self):
        m = _make_memory()
        g = ProjectGroup(project="lore", memories=[m], count=1)
        r = RecentActivityResult(groups=[g], total_count=1, hours=24)
        text = format_cli(r)
        assert "##" not in text
        assert "**" not in text
        assert "Recent Activity (last 24h)" in text
        assert "lore (1 memories)" in text

    def test_empty(self):
        r = RecentActivityResult(hours=72)
        assert "No recent activity in the last 72h." in format_cli(r)


class TestFormatTime:
    def test_valid_iso(self):
        assert _format_time("2026-03-14T14:30:00+00:00") == "14:30"

    def test_invalid_short(self):
        assert _format_time("2026") == "??:??"

    def test_empty(self):
        assert _format_time("") == "??:??"

    def test_none_like(self):
        assert _format_time("") == "??:??"
