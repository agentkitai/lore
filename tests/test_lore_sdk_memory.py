"""STORY-019: Tests for Lore SDK memory methods (remember/recall/forget/list/stats)."""

from __future__ import annotations

from typing import List

import pytest

from lore.lore import Lore
from lore.types import Memory, SearchResult, StoreStats


class _FakeEmbedder:
    """Deterministic embedder for tests."""

    def embed(self, text: str) -> List[float]:
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        return [(h[i % len(h)] - 128) / 128.0 for i in range(384)]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


@pytest.fixture
def client(tmp_path):
    """Create a Lore instance with local DB and fake embedder."""
    db_path = str(tmp_path / "test.db")
    lore = Lore(
        db_path=db_path,
        embedder=_FakeEmbedder(),
        redact=False,
    )
    yield lore
    lore.close()


@pytest.fixture
def client_with_project(tmp_path):
    """Create a Lore instance with a default project."""
    db_path = str(tmp_path / "test.db")
    lore = Lore(
        db_path=db_path,
        project="default-proj",
        embedder=_FakeEmbedder(),
        redact=False,
    )
    yield lore
    lore.close()


class TestRemember:
    def test_returns_memory_object(self, client: Lore) -> None:
        memory = client.remember(content="Test content")
        assert isinstance(memory, Memory)
        assert len(memory.id) > 0
        assert memory.content == "Test content"
        assert memory.embedding is None  # embedding stripped from return

    def test_stored_and_retrievable(self, client: Lore) -> None:
        memory = client.remember(content="Important fact")
        fetched = client.get_memory(memory.id)
        assert fetched is not None
        assert fetched.content == "Important fact"
        assert fetched.type == "note"

    def test_all_fields(self, client: Lore) -> None:
        memory = client.remember(
            content="Use exponential backoff for rate limits",
            type="lesson",
            tags=["api", "reliability"],
            metadata={"confidence": 0.9},
            project="backend",
            source="claude",
        )
        assert memory.type == "lesson"
        assert memory.tags == ["api", "reliability"]
        assert memory.metadata == {"confidence": 0.9}
        assert memory.project == "backend"
        assert memory.source == "claude"
        # Verify it was persisted correctly
        fetched = client.get_memory(memory.id)
        assert fetched is not None
        assert fetched.content == memory.content

    def test_default_project(self, client_with_project: Lore) -> None:
        memory = client_with_project.remember(content="test")
        assert memory.project == "default-proj"

    def test_override_default_project(self, client_with_project: Lore) -> None:
        memory = client_with_project.remember(content="test", project="other")
        assert memory.project == "other"

    def test_ttl(self, client: Lore) -> None:
        memory = client.remember(content="temp memory", ttl="7d")
        assert memory.expires_at is not None


class TestRecall:
    def test_empty_returns_empty(self, client: Lore) -> None:
        results = client.recall("anything")
        assert results == []

    def test_finds_stored(self, client: Lore) -> None:
        client.remember(content="Stripe rate-limits at 100 req/min")
        results = client.recall("stripe rate limiting")
        assert len(results) >= 1
        assert isinstance(results[0], SearchResult)
        assert "Stripe" in results[0].memory.content

    def test_limit(self, client: Lore) -> None:
        for i in range(10):
            client.remember(content=f"Memory number {i}")
        results = client.recall("memory", limit=3)
        assert len(results) == 3

    def test_type_filter(self, client: Lore) -> None:
        client.remember(content="a lesson", type="lesson")
        client.remember(content="a note", type="note")
        results = client.recall("content", type="lesson")
        assert all(r.memory.type == "lesson" for r in results)

    def test_project_filter(self, client: Lore) -> None:
        client.remember(content="memory in alpha", project="alpha")
        client.remember(content="memory in beta", project="beta")
        results = client.recall("memory", project="alpha")
        assert len(results) == 1
        assert results[0].memory.project == "alpha"


class TestForget:
    def test_by_id(self, client: Lore) -> None:
        memory = client.remember(content="to delete")
        count = client.forget(id=memory.id)
        assert count == 1
        assert client.get_memory(memory.id) is None

    def test_by_id_nonexistent(self, client: Lore) -> None:
        count = client.forget(id="nonexistent")
        assert count == 0

    def test_by_type(self, client: Lore) -> None:
        client.remember(content="lesson 1", type="lesson")
        client.remember(content="note 1", type="note")
        count = client.forget(type="lesson")
        assert count == 1

    def test_by_project(self, client: Lore) -> None:
        client.remember(content="keep", project="keep")
        client.remember(content="delete", project="delete-me")
        count = client.forget(project="delete-me")
        assert count == 1


class TestListMemories:
    def test_empty(self, client: Lore) -> None:
        memories, total = client.list_memories()
        assert memories == []
        assert total == 0

    def test_returns_stored(self, client: Lore) -> None:
        client.remember(content="first")
        client.remember(content="second")
        memories, total = client.list_memories()
        assert total == 2
        assert len(memories) == 2

    def test_filter_by_type(self, client: Lore) -> None:
        client.remember(content="lesson", type="lesson")
        client.remember(content="note", type="note")
        memories, total = client.list_memories(type="lesson")
        assert total == 1

    def test_pagination(self, client: Lore) -> None:
        for i in range(5):
            client.remember(content=f"mem {i}")
        memories, total = client.list_memories(limit=2, offset=0)
        assert len(memories) == 2
        assert total == 5


class TestMemoryStats:
    def test_empty(self, client: Lore) -> None:
        stats = client.memory_stats()
        assert isinstance(stats, StoreStats)
        assert stats.total_count == 0

    def test_with_data(self, client: Lore) -> None:
        client.remember(content="note 1", type="note")
        client.remember(content="lesson 1", type="lesson")
        client.remember(content="note 2", type="note")
        stats = client.memory_stats()
        assert stats.total_count == 3
        assert stats.count_by_type["note"] == 2
        assert stats.count_by_type["lesson"] == 1

    def test_filter_by_project(self, client: Lore) -> None:
        client.remember(content="a", project="alpha")
        client.remember(content="b", project="beta")
        stats = client.memory_stats(project="alpha")
        assert stats.total_count == 1


class TestStatsAlias:
    """stats() is a convenience alias for memory_stats()."""

    def test_stats_returns_store_stats(self, client: Lore) -> None:
        client.remember(content="a note", type="note")
        result = client.stats()
        assert isinstance(result, StoreStats)
        assert result.total_count == 1

    def test_stats_matches_memory_stats(self, client: Lore) -> None:
        client.remember(content="one", type="note")
        client.remember(content="two", type="lesson")
        assert client.stats() == client.memory_stats()

    def test_stats_project_filter(self, client: Lore) -> None:
        client.remember(content="a", project="alpha")
        client.remember(content="b", project="beta")
        stats = client.stats(project="alpha")
        assert stats.total_count == 1


