"""Tests for E2 S5: Lore.recent_activity() SDK method."""

from __future__ import annotations

import os
import struct
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import patch

import pytest

from lore.lore import Lore
from lore.store.memory import MemoryStore
from lore.types import Memory, RecentActivityResult


def _stub_embed(text: str) -> List[float]:
    return [0.1] * 384


def _make_memory(
    id: str,
    content: str = "test content",
    project: str | None = "lore",
    type: str = "general",
    tier: str = "long",
    hours_ago: float = 1,
    expires_at: str | None = None,
) -> Memory:
    created = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return Memory(
        id=id,
        content=content,
        type=type,
        tier=tier,
        project=project,
        created_at=created,
        updated_at=created,
        expires_at=expires_at,
        embedding=struct.pack("384f", *([0.1] * 384)),
    )


@pytest.fixture
def lore_with_memories():
    """Create a Lore instance with pre-populated memories."""
    store = MemoryStore()
    store.save(_make_memory("m1", "Decision: use FastMCP", project="lore", hours_ago=2))
    store.save(_make_memory("m2", "Fixed ONNX loading", project="lore", hours_ago=5))
    store.save(_make_memory("m3", "Deployed v2.3", project="app", hours_ago=3))
    store.save(_make_memory("m4", "Old memory", project="lore", hours_ago=48))

    lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
    lore._store = store
    return lore


class TestRecentActivityDefaults:
    def test_returns_result(self, lore_with_memories):
        result = lore_with_memories.recent_activity()
        assert isinstance(result, RecentActivityResult)
        assert result.hours == 24

    def test_default_24h_window(self, lore_with_memories):
        result = lore_with_memories.recent_activity()
        # m4 is 48h ago, should be excluded
        assert result.total_count == 3

    def test_groups_by_project(self, lore_with_memories):
        result = lore_with_memories.recent_activity()
        projects = {g.project for g in result.groups}
        assert "lore" in projects
        assert "app" in projects

    def test_generated_at_set(self, lore_with_memories):
        result = lore_with_memories.recent_activity()
        assert result.generated_at != ""
        # Should be parseable ISO
        datetime.fromisoformat(result.generated_at)

    def test_query_time_recorded(self, lore_with_memories):
        result = lore_with_memories.recent_activity()
        assert result.query_time_ms > 0


class TestRecentActivityParams:
    def test_custom_hours(self, lore_with_memories):
        result = lore_with_memories.recent_activity(hours=72)
        assert result.hours == 72
        assert result.total_count == 4  # All memories within 72h

    def test_hours_clamped_low(self, lore_with_memories):
        result = lore_with_memories.recent_activity(hours=0)
        assert result.hours == 1

    def test_hours_clamped_high(self, lore_with_memories):
        result = lore_with_memories.recent_activity(hours=500)
        assert result.hours == 168

    def test_max_memories_clamped_low(self, lore_with_memories):
        result = lore_with_memories.recent_activity(max_memories=0)
        # Should still work (clamped to 1)
        assert isinstance(result, RecentActivityResult)

    def test_max_memories_clamped_high(self, lore_with_memories):
        result = lore_with_memories.recent_activity(max_memories=999)
        assert isinstance(result, RecentActivityResult)

    def test_project_filter(self, lore_with_memories):
        result = lore_with_memories.recent_activity(project="app")
        assert result.total_count == 1
        assert result.groups[0].project == "app"

    def test_max_memories_respected(self, lore_with_memories):
        result = lore_with_memories.recent_activity(max_memories=2)
        assert result.total_count <= 2


class TestRecentActivityEdgeCases:
    def test_empty_store(self):
        store = MemoryStore()
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore._store = store
        result = lore.recent_activity()
        assert result.total_count == 0
        assert result.groups == []

    def test_excludes_expired(self):
        store = MemoryStore()
        expired_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        store.save(_make_memory("m1", "expired", hours_ago=2, expires_at=expired_at))
        store.save(_make_memory("m2", "valid", hours_ago=2))

        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore._store = store
        result = lore.recent_activity()
        assert result.total_count == 1

    def test_includes_all_tiers(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "working", tier="working", hours_ago=0.5))
        store.save(_make_memory("m2", "short", tier="short", hours_ago=0.5))
        store.save(_make_memory("m3", "long", tier="long", hours_ago=0.5))

        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore._store = store
        result = lore.recent_activity()
        tiers = {m.tier for g in result.groups for m in g.memories}
        assert tiers == {"working", "short", "long"}

    def test_store_error_returns_empty(self):
        """Store errors should return empty result, not crash."""
        store = MemoryStore()
        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore._store = store

        # Monkey-patch store to raise
        def bad_list(**kwargs):
            raise RuntimeError("DB down")
        store.list = bad_list

        result = lore.recent_activity()
        assert result.total_count == 0
        assert result.groups == []

    def test_project_env_fallback(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "in lore", project="lore", hours_ago=1))
        store.save(_make_memory("m2", "in other", project="other", hours_ago=1))

        lore = Lore(store=MemoryStore(), embedding_fn=_stub_embed)
        lore._store = store

        with patch.dict(os.environ, {"LORE_PROJECT": "lore"}):
            result = lore.recent_activity()
            assert result.total_count == 1
            assert result.groups[0].project == "lore"
