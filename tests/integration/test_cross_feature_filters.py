"""Scenario 9 — Cross-feature recall filters."""

from __future__ import annotations

import pytest

from lore import Lore


class TestCrossFeatureFilters:
    """Test recall filtering by tier, type, tags, and combinations."""

    def _seed_memories(self, lore: Lore) -> None:
        """Seed a variety of memories for filter testing."""
        lore.remember("API rate limiting patterns", type="lesson", tier="long", tags=["api", "patterns"])
        lore.remember("database connection pooling", type="general", tier="long", tags=["db", "patterns"])
        lore.remember("quick debug note for auth", type="debug", tier="working", tags=["auth"])
        lore.remember("convention: use snake_case in Python", type="convention", tier="long", tags=["python", "style"])
        lore.remember("weekly standup notes", type="note", tier="short", tags=["meetings"])

    def test_filter_by_tier(self, lore_no_llm: Lore) -> None:
        """recall(tier='long') only returns long-tier memories."""
        self._seed_memories(lore_no_llm)
        results = lore_no_llm.recall("patterns", tier="long")
        for r in results:
            assert r.memory.tier == "long"

    def test_filter_by_type(self, lore_no_llm: Lore) -> None:
        """recall(type='lesson') only returns lessons."""
        self._seed_memories(lore_no_llm)
        results = lore_no_llm.recall("patterns", type="lesson")
        for r in results:
            assert r.memory.type == "lesson"

    def test_filter_by_tags(self, lore_no_llm: Lore) -> None:
        """recall(tags=['api']) only returns memories with the 'api' tag."""
        self._seed_memories(lore_no_llm)
        results = lore_no_llm.recall("patterns", tags=["api"])
        assert len(results) >= 1
        for r in results:
            assert "api" in r.memory.tags

    def test_combined_tier_and_type(self, lore_no_llm: Lore) -> None:
        """recall(tier='long', type='lesson') combines both filters."""
        self._seed_memories(lore_no_llm)
        results = lore_no_llm.recall("patterns", tier="long", type="lesson")
        for r in results:
            assert r.memory.tier == "long"
            assert r.memory.type == "lesson"

    def test_combined_tier_type_tags(self, lore_no_llm: Lore) -> None:
        """All three filters applied simultaneously."""
        self._seed_memories(lore_no_llm)
        results = lore_no_llm.recall(
            "patterns", tier="long", type="lesson", tags=["api"],
        )
        for r in results:
            assert r.memory.tier == "long"
            assert r.memory.type == "lesson"
            assert "api" in r.memory.tags

    def test_no_results_for_impossible_filter(self, lore_no_llm: Lore) -> None:
        """Filters that match nothing return an empty list."""
        self._seed_memories(lore_no_llm)
        results = lore_no_llm.recall(
            "patterns", tier="working", type="lesson",
        )
        # No lessons exist in working tier
        assert len(results) == 0

    def test_list_memories_filters(self, lore_no_llm: Lore) -> None:
        """list_memories also supports tier and type filters."""
        self._seed_memories(lore_no_llm)
        long_lessons = lore_no_llm.list_memories(tier="long", type="lesson")
        assert len(long_lessons) == 1
        assert long_lessons[0].type == "lesson"
        assert long_lessons[0].tier == "long"
