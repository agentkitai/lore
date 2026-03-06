"""Scenario 7 — Tier mechanics and lifecycle."""

from __future__ import annotations

import pytest

from lore import Lore


class TestTierLifecycle:
    """Test tier-specific TTL, expiry, and filtering."""

    def test_tier_default_ttl(self, lore_no_llm: Lore) -> None:
        """Each tier gets the correct default TTL."""
        mid_w = lore_no_llm.remember("working note", tier="working")
        mid_s = lore_no_llm.remember("short note", tier="short")
        mid_l = lore_no_llm.remember("long note", tier="long")

        assert lore_no_llm.get(mid_w).ttl == 3600
        assert lore_no_llm.get(mid_s).ttl == 604800
        assert lore_no_llm.get(mid_l).ttl is None

    def test_working_tier_has_expiry(self, lore_no_llm: Lore) -> None:
        """Working tier memories get an expires_at timestamp."""
        mid = lore_no_llm.remember("scratch pad", tier="working")
        mem = lore_no_llm.get(mid)
        assert mem.expires_at is not None

    def test_long_tier_no_expiry(self, lore_no_llm: Lore) -> None:
        """Long tier memories have no expires_at."""
        mid = lore_no_llm.remember("permanent knowledge", tier="long")
        mem = lore_no_llm.get(mid)
        assert mem.expires_at is None

    def test_short_tier_has_expiry(self, lore_no_llm: Lore) -> None:
        """Short tier memories get an expires_at timestamp."""
        mid = lore_no_llm.remember("weekly note", tier="short")
        mem = lore_no_llm.get(mid)
        assert mem.expires_at is not None

    def test_tier_filtering_in_recall(self, lore_no_llm: Lore) -> None:
        """recall(tier='long') excludes working and short memories."""
        lore_no_llm.remember("working scratch", tier="working")
        lore_no_llm.remember("short note", tier="short")
        lore_no_llm.remember("permanent fact", tier="long")

        results = lore_no_llm.recall("note", tier="long")
        for r in results:
            assert r.memory.tier == "long"

    def test_tier_filtering_in_list(self, lore_no_llm: Lore) -> None:
        """list_memories(tier='short') only returns short-tier memories."""
        lore_no_llm.remember("w", tier="working")
        lore_no_llm.remember("s", tier="short")
        lore_no_llm.remember("l", tier="long")

        short_mems = lore_no_llm.list_memories(tier="short")
        assert len(short_mems) == 1
        assert short_mems[0].tier == "short"

    def test_explicit_ttl_overrides_tier_default(self, lore_no_llm: Lore) -> None:
        """An explicit ttl parameter overrides the tier default."""
        mid = lore_no_llm.remember("custom ttl", tier="long", ttl=999)
        mem = lore_no_llm.get(mid)
        assert mem.ttl == 999
        assert mem.expires_at is not None  # TTL was explicitly set, so expiry exists

    def test_invalid_tier_raises(self, lore_no_llm: Lore) -> None:
        """An invalid tier raises ValueError."""
        with pytest.raises(ValueError, match="invalid tier"):
            lore_no_llm.remember("bad tier", tier="permanent")
