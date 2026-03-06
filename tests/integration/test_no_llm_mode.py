"""Scenario 10 — Baseline operations without LLM."""

from __future__ import annotations

import pytest

from lore import Lore


class TestNoLLMMode:
    """Verify core CRUD operations work without any LLM provider."""

    def test_remember_without_llm(self, lore_no_llm: Lore) -> None:
        """remember() stores content and returns a ULID string."""
        mid = lore_no_llm.remember("always use exponential backoff")
        assert isinstance(mid, str)
        assert len(mid) > 0

    def test_recall_without_llm(self, lore_no_llm: Lore) -> None:
        """recall() returns vector-similarity results for stored memories."""
        lore_no_llm.remember("rate limiting requires exponential backoff")
        results = lore_no_llm.recall("rate limit backoff")
        assert len(results) >= 1
        assert results[0].memory.content == "rate limiting requires exponential backoff"
        assert results[0].score > 0

    def test_forget_without_llm(self, lore_no_llm: Lore) -> None:
        """forget() deletes a memory and returns True."""
        mid = lore_no_llm.remember("ephemeral note")
        assert lore_no_llm.forget(mid) is True
        # Second forget returns False — already gone
        assert lore_no_llm.forget(mid) is False

    def test_list_without_llm(self, lore_no_llm: Lore) -> None:
        """list_memories() returns all stored memories."""
        lore_no_llm.remember("first")
        lore_no_llm.remember("second")
        memories = lore_no_llm.list_memories()
        assert len(memories) == 2
        contents = {m.content for m in memories}
        assert contents == {"first", "second"}

    def test_stats_without_llm(self, lore_no_llm: Lore) -> None:
        """stats() returns correct aggregate counts."""
        lore_no_llm.remember("note one")
        lore_no_llm.remember("note two", type="lesson")
        s = lore_no_llm.stats()
        assert s.total == 2
        assert s.by_type.get("general") == 1
        assert s.by_type.get("lesson") == 1

    def test_remember_with_tiers(self, lore_no_llm: Lore) -> None:
        """All three tiers (working, short, long) are accepted."""
        mid_w = lore_no_llm.remember("working note", tier="working")
        mid_s = lore_no_llm.remember("short note", tier="short")
        mid_l = lore_no_llm.remember("long note", tier="long")
        assert all(isinstance(m, str) for m in [mid_w, mid_s, mid_l])

        memories = lore_no_llm.list_memories()
        tiers = {m.tier for m in memories}
        assert tiers == {"working", "short", "long"}

    def test_remember_with_tags(self, lore_no_llm: Lore) -> None:
        """Tags are stored and can be used for filtering in recall."""
        lore_no_llm.remember("api design guideline", tags=["api", "design"])
        lore_no_llm.remember("database migration tips", tags=["db"])

        # Recall filtering by tag
        results = lore_no_llm.recall("guideline", tags=["api"])
        assert len(results) >= 1
        assert all("api" in r.memory.tags for r in results)

    def test_upvote_downvote(self, lore_no_llm: Lore) -> None:
        """upvote() and downvote() update vote counts on the memory."""
        mid = lore_no_llm.remember("a useful pattern")
        lore_no_llm.upvote(mid)
        lore_no_llm.upvote(mid)
        lore_no_llm.downvote(mid)

        mem = lore_no_llm.get(mid)
        assert mem is not None
        assert mem.upvotes == 2
        assert mem.downvotes == 1
