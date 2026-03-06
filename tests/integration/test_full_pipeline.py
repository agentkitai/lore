"""Scenario 1 — Full pipeline: remember, recall, forget lifecycle."""

from __future__ import annotations

import pytest

from lore import Lore
from lore.types import TIER_DEFAULT_TTL


class TestFullPipeline:
    """Test remember with various features (no real LLM)."""

    def test_remember_assigns_tier(self, lore_no_llm: Lore) -> None:
        """Tier parameter is persisted on the memory."""
        mid = lore_no_llm.remember("short-lived note", tier="short")
        mem = lore_no_llm.get(mid)
        assert mem is not None
        assert mem.tier == "short"

    def test_remember_with_metadata(self, lore_no_llm: Lore) -> None:
        """Custom metadata is stored and retrievable."""
        meta = {"author": "test", "version": 3}
        mid = lore_no_llm.remember("metadata note", metadata=meta)
        mem = lore_no_llm.get(mid)
        assert mem is not None
        assert mem.metadata["author"] == "test"
        assert mem.metadata["version"] == 3

    def test_remember_computes_ttl_from_tier(self, lore_no_llm: Lore) -> None:
        """Tier provides default TTL: working=3600, short=604800, long=None."""
        mid_w = lore_no_llm.remember("w", tier="working")
        mid_s = lore_no_llm.remember("s", tier="short")
        mid_l = lore_no_llm.remember("l", tier="long")

        mem_w = lore_no_llm.get(mid_w)
        mem_s = lore_no_llm.get(mid_s)
        mem_l = lore_no_llm.get(mid_l)

        assert mem_w.ttl == TIER_DEFAULT_TTL["working"]  # 3600
        assert mem_s.ttl == TIER_DEFAULT_TTL["short"]     # 604800
        assert mem_l.ttl == TIER_DEFAULT_TTL["long"]      # None

    def test_recall_returns_scored_results(self, lore_no_llm: Lore) -> None:
        """Recall results have score > 0."""
        lore_no_llm.remember("Python async await patterns")
        results = lore_no_llm.recall("async await")
        assert len(results) >= 1
        for r in results:
            assert r.score > 0

    def test_recall_filters_by_type(self, lore_no_llm: Lore) -> None:
        """recall(type='lesson') only returns lessons."""
        lore_no_llm.remember("general note", type="general")
        lore_no_llm.remember("learned to use pytest fixtures", type="lesson")

        results = lore_no_llm.recall("pytest", type="lesson")
        for r in results:
            assert r.memory.type == "lesson"

    def test_recall_filters_by_tier(self, lore_no_llm: Lore) -> None:
        """recall(tier='long') excludes working/short memories."""
        lore_no_llm.remember("ephemeral scratch", tier="working")
        lore_no_llm.remember("persistent knowledge", tier="long")

        results = lore_no_llm.recall("knowledge", tier="long")
        for r in results:
            assert r.memory.tier == "long"

    def test_pipeline_remember_recall_forget(self, lore_no_llm: Lore) -> None:
        """Full lifecycle: remember -> recall -> forget -> recall empty."""
        mid = lore_no_llm.remember("unique canary string for lifecycle test")

        # Should be recallable
        results = lore_no_llm.recall("unique canary string")
        assert any(r.memory.id == mid for r in results)

        # Forget
        assert lore_no_llm.forget(mid) is True

        # Should no longer appear (store is empty)
        results = lore_no_llm.recall("unique canary string")
        assert not any(r.memory.id == mid for r in results)
