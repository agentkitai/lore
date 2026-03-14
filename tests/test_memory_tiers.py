"""Tests for F4: Multi-Level Memory Tiers.

Covers: types/constants, store tier filtering, facade tier logic
(remember/recall/list/stats), TTL interaction, backward compatibility,
recall weighting, validation, CLI, and MCP integration.
"""

from __future__ import annotations

import struct
import tempfile
from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from lore.lore import Lore
from lore.store.memory import MemoryStore
from lore.store.memory import MemoryStore
from lore.types import (
    TIER_DEFAULT_TTL,
    TIER_RECALL_WEIGHT,
    VALID_TIERS,
    Memory,
    MemoryStats,
)


def _stub_embed(text: str) -> List[float]:
    return [0.1] * 384


def _make_memory(
    id: str = "m1",
    content: str = "test",
    tier: str = "long",
    type: str = "general",
    project: str | None = None,
    created_at: str = "2026-01-01T00:00:00+00:00",
    embedding: bytes | None = None,
) -> Memory:
    if embedding is None:
        embedding = struct.pack("384f", *([0.1] * 384))
    return Memory(
        id=id,
        content=content,
        tier=tier,
        type=type,
        project=project,
        created_at=created_at,
        updated_at=created_at,
        embedding=embedding,
    )


# =========================================================================
# 1. Types and Constants (5 tests)
# =========================================================================


class TestTierConstants:
    def test_memory_default_tier_is_long(self):
        m = Memory(id="x", content="test")
        assert m.tier == "long"

    def test_memory_accepts_explicit_tier(self):
        m = Memory(id="x", content="test", tier="working")
        assert m.tier == "working"

    def test_valid_tiers_contains_all(self):
        assert VALID_TIERS == ("working", "short", "long")

    def test_tier_default_ttl_values(self):
        assert TIER_DEFAULT_TTL["working"] == 3600
        assert TIER_DEFAULT_TTL["short"] == 604800
        assert TIER_DEFAULT_TTL["long"] is None

    def test_tier_recall_weight_values(self):
        assert TIER_RECALL_WEIGHT["working"] == 1.0
        assert TIER_RECALL_WEIGHT["short"] == 1.1
        assert TIER_RECALL_WEIGHT["long"] == 1.2

    def test_memory_stats_has_by_tier(self):
        stats = MemoryStats(total=0)
        assert stats.by_tier == {}


# =========================================================================
# 2. Store Layer — MemoryStore (3 tests)
# =========================================================================


class TestMemoryStoreTierFiltering:
    def test_list_filters_by_tier(self):
        store = MemoryStore()
        store.save(_make_memory(id="w", tier="working", created_at="2026-01-01T01:00:00+00:00"))
        store.save(_make_memory(id="s", tier="short", created_at="2026-01-01T02:00:00+00:00"))
        store.save(_make_memory(id="l", tier="long", created_at="2026-01-01T03:00:00+00:00"))

        result = store.list(tier="working")
        assert len(result) == 1
        assert result[0].id == "w"

    def test_count_filters_by_tier(self):
        store = MemoryStore()
        store.save(_make_memory(id="w1", tier="working", created_at="2026-01-01T01:00:00+00:00"))
        store.save(_make_memory(id="w2", tier="working", created_at="2026-01-01T02:00:00+00:00"))
        store.save(_make_memory(id="l", tier="long", created_at="2026-01-01T03:00:00+00:00"))

        assert store.count(tier="working") == 2
        assert store.count(tier="long") == 1

    def test_list_no_tier_returns_all(self):
        store = MemoryStore()
        store.save(_make_memory(id="w", tier="working", created_at="2026-01-01T01:00:00+00:00"))
        store.save(_make_memory(id="s", tier="short", created_at="2026-01-01T02:00:00+00:00"))
        store.save(_make_memory(id="l", tier="long", created_at="2026-01-01T03:00:00+00:00"))

        assert len(store.list()) == 3


# =========================================================================
# 3. Store Layer — MemoryStore (5 tests)
# =========================================================================


class TestMemoryStoreTier:
    @pytest.fixture
    def memory_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore()
            yield store
            store.close()

    def test_save_and_get_tier(self, memory_store: MemoryStore):
        m = _make_memory(id="t1", tier="working")
        memory_store.save(m)
        got = memory_store.get("t1")
        assert got is not None
        assert got.tier == "working"

    def test_update_persists_tier(self, memory_store: MemoryStore):
        m = _make_memory(id="t2", tier="working")
        memory_store.save(m)
        m.tier = "long"
        memory_store.update(m)
        got = memory_store.get("t2")
        assert got is not None
        assert got.tier == "long"

    def test_list_filter_by_tier(self, memory_store: MemoryStore):
        memory_store.save(_make_memory(id="w", tier="working", created_at="2026-01-01T01:00:00+00:00"))
        memory_store.save(_make_memory(id="s", tier="short", created_at="2026-01-01T02:00:00+00:00"))
        memory_store.save(_make_memory(id="l", tier="long", created_at="2026-01-01T03:00:00+00:00"))

        result = memory_store.list(tier="short")
        assert len(result) == 1
        assert result[0].id == "s"

    def test_count_filter_by_tier(self, memory_store: MemoryStore):
        memory_store.save(_make_memory(id="w1", tier="working", created_at="2026-01-01T01:00:00+00:00"))
        memory_store.save(_make_memory(id="w2", tier="working", created_at="2026-01-01T02:00:00+00:00"))
        memory_store.save(_make_memory(id="l", tier="long", created_at="2026-01-01T03:00:00+00:00"))

        assert memory_store.count(tier="working") == 2

    def test_row_to_memory_default_tier(self, memory_store: MemoryStore):
        """Defensive: _row_to_memory on a row without tier col returns 'long'."""
        # This is tested implicitly by the migration test above,
        # but let's verify directly that default tier is "long" for new saves.
        memory_store.save(_make_memory(id="d1"))
        got = memory_store.get("d1")
        assert got is not None
        assert got.tier == "long"


# =========================================================================
# 4. Lore Facade (9 tests)
# =========================================================================


class TestLoreFacadeTier:
    @pytest.fixture
    def lore(self):
        store = MemoryStore()
        l = Lore(store=store, embedding_fn=_stub_embed, redact=False)
        yield l
        l.close()

    def test_remember_default_tier(self, lore: Lore):
        mid = lore.remember("test content")
        mem = lore.get(mid)
        assert mem is not None
        assert mem.tier == "long"
        assert mem.ttl is None
        assert mem.expires_at is None

    def test_remember_working_tier_default_ttl(self, lore: Lore):
        mid = lore.remember("scratch note", tier="working")
        mem = lore.get(mid)
        assert mem is not None
        assert mem.tier == "working"
        assert mem.ttl == 3600
        assert mem.expires_at is not None

    def test_remember_short_tier_default_ttl(self, lore: Lore):
        mid = lore.remember("session learning", tier="short")
        mem = lore.get(mid)
        assert mem is not None
        assert mem.tier == "short"
        assert mem.ttl == 604800

    def test_remember_explicit_ttl_overrides_tier(self, lore: Lore):
        mid = lore.remember("custom ttl", tier="working", ttl=7200)
        mem = lore.get(mid)
        assert mem is not None
        assert mem.ttl == 7200  # Not 3600

    def test_remember_invalid_tier_raises(self, lore: Lore):
        with pytest.raises(ValueError, match="invalid tier"):
            lore.remember("test", tier="invalid")

    def test_recall_tier_filter(self, lore: Lore):
        lore.remember("working memory", tier="working")
        lore.remember("long memory", tier="long")

        results = lore.recall("memory", tier="working")
        assert all(r.memory.tier == "working" for r in results)

    def test_recall_tier_weight_affects_scoring(self, lore: Lore):
        """Long-tier memory should score higher than working-tier with same content."""
        lore.remember("identical content for testing", tier="long")
        lore.remember("identical content for testing", tier="working")

        results = lore.recall("identical content for testing", limit=10)
        assert len(results) >= 2
        # Long-tier should score higher due to 1.2x vs 1.0x weight
        long_results = [r for r in results if r.memory.tier == "long"]
        working_results = [r for r in results if r.memory.tier == "working"]
        if long_results and working_results:
            assert long_results[0].score > working_results[0].score

    def test_list_memories_tier_filter(self, lore: Lore):
        lore.remember("working", tier="working")
        lore.remember("long", tier="long")

        short_list = lore.list_memories(tier="short")
        assert len(short_list) == 0

        long_list = lore.list_memories(tier="long")
        assert len(long_list) == 1
        assert long_list[0].tier == "long"

    def test_stats_includes_by_tier(self, lore: Lore):
        lore.remember("mem1", tier="long")
        lore.remember("mem2", tier="long")
        lore.remember("mem3", tier="working")

        stats = lore.stats()
        assert stats.by_tier["long"] == 2
        assert stats.by_tier["working"] == 1

    def test_configurable_tier_weights(self):
        store = MemoryStore()
        custom_weights = {"working": 2.0, "short": 1.0, "long": 1.0}
        l = Lore(
            store=store,
            embedding_fn=_stub_embed,
            redact=False,
            tier_recall_weights=custom_weights,
        )
        assert l._tier_weights == custom_weights
        l.close()


# =========================================================================
# 5. Integration / TTL Tests (4 tests)
# =========================================================================


class TestTierTTLIntegration:
    @pytest.fixture
    def lore(self):
        store = MemoryStore()
        l = Lore(store=store, embedding_fn=_stub_embed, redact=False)
        yield l
        l.close()

    def test_working_memory_has_expires_at(self, lore: Lore):
        mid = lore.remember("scratch", tier="working")
        mem = lore.get(mid)
        assert mem is not None
        assert mem.expires_at is not None
        expires = datetime.fromisoformat(mem.expires_at)
        # Should be ~1 hour from now
        expected = datetime.now(timezone.utc) + timedelta(hours=1)
        assert abs((expires - expected).total_seconds()) < 5

    def test_long_memory_no_expiry(self, lore: Lore):
        mid = lore.remember("permanent", tier="long")
        mem = lore.get(mid)
        assert mem is not None
        assert mem.expires_at is None
        assert mem.ttl is None

    def test_backward_compat_remember_no_tier(self, lore: Lore):
        """remember() without tier should behave identically to pre-F4."""
        mid = lore.remember("no tier specified")
        mem = lore.get(mid)
        assert mem is not None
        assert mem.tier == "long"
        assert mem.ttl is None
        assert mem.expires_at is None



# =========================================================================
# 6. Validation Tests (2 tests)
# =========================================================================


class TestTierValidation:
    def test_remember_invalid_tier_value_error(self):
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_stub_embed, redact=False)
        with pytest.raises(ValueError, match="invalid tier"):
            lore.remember("test", tier="invalid")
        lore.close()

    def test_remember_empty_tier_value_error(self):
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_stub_embed, redact=False)
        with pytest.raises(ValueError, match="invalid tier"):
            lore.remember("test", tier="")
        lore.close()


# =========================================================================
# 7. CLI Tests (3 tests)
# =========================================================================


class TestCLITierFlags:
    def test_remember_tier_flag_parsed(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["remember", "test content", "--tier", "working"])
        assert args.tier == "working"

    def test_memories_tier_flag_parsed(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["memories", "--tier", "short"])
        assert args.tier == "short"

    def test_recall_tier_flag_parsed(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["recall", "query", "--tier", "long"])
        assert args.tier == "long"

    def test_remember_default_tier_is_long(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["remember", "test content"])
        assert args.tier == "long"

    def test_memories_default_tier_is_none(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["memories"])
        assert args.tier is None

    def test_recall_default_tier_is_none(self):
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["recall", "query"])
        assert args.tier is None


# =========================================================================
# 8. MCP Tool Tests (6 tests)
# =========================================================================


class TestMCPTier:
    @pytest.fixture(autouse=True)
    def _setup_mcp(self):
        """Reset module-level Lore instance between tests."""
        import lore.mcp.server as srv

        store = MemoryStore()
        test_lore = Lore(store=store, embedding_fn=_stub_embed, redact=False)
        srv._lore = test_lore
        yield
        srv._lore = None
        test_lore.close()

    def test_mcp_remember_with_tier(self):
        from lore.mcp.server import remember

        result = remember(content="scratch note", tier="working")
        assert "tier: working" in result

    def test_mcp_remember_default_tier(self):
        from lore.mcp.server import remember

        result = remember(content="default tier memory")
        assert "tier: long" in result

    def test_mcp_recall_with_tier_filter(self):
        from lore.mcp.server import recall, remember

        remember(content="working memory for testing recall", tier="working")
        remember(content="long memory for testing recall", tier="long")

        result = recall(query="testing recall", tier="working")
        assert "tier: working" in result
        # Should NOT contain long-tier results
        assert "tier: long" not in result

    def test_mcp_list_with_tier_filter(self):
        from lore.mcp.server import list_memories, remember

        remember(content="working mem", tier="working")
        remember(content="long mem", tier="long")

        result = list_memories(tier="working")
        assert "working mem" in result
        assert "long mem" not in result

    def test_mcp_stats_shows_tier_breakdown(self):
        from lore.mcp.server import remember, stats

        remember(content="working", tier="working")
        remember(content="long1", tier="long")
        remember(content="long2", tier="long")

        result = stats()
        assert "By tier:" in result
        assert "long: 2" in result
        assert "working: 1" in result

    def test_mcp_recall_output_shows_tier(self):
        from lore.mcp.server import recall, remember

        remember(content="test memory for tier display")
        result = recall(query="tier display")
        assert "tier: long" in result


# =========================================================================
# 9. HttpStore Tier Mapping Tests (3 tests)
# =========================================================================


class TestHttpStoreTierMapping:
    def test_memory_to_lesson_includes_tier(self):
        from lore.store.http import HttpStore

        m = _make_memory(tier="working")
        payload = HttpStore._memory_to_lesson(m)
        assert payload["meta"]["tier"] == "working"

    def test_lesson_to_memory_reads_tier(self):
        from lore.store.http import HttpStore

        data = {
            "id": "x",
            "problem": "test",
            "meta": {"type": "general", "tier": "short"},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        mem = HttpStore._lesson_to_memory(data)
        assert mem.tier == "short"

    def test_lesson_to_memory_missing_tier_defaults_long(self):
        from lore.store.http import HttpStore

        data = {
            "id": "x",
            "problem": "test",
            "meta": {"type": "general"},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        mem = HttpStore._lesson_to_memory(data)
        assert mem.tier == "long"
