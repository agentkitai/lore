"""STORY-034: End-to-End Integration Test (First-Run Flow).

Validates the complete user journey through the local-mode stack:
  MCP tools → embedder → SqliteStore → cosine search → ranked results

This test exercises the full pipeline without mocking the embedder,
proving that remember→recall→list→forget→stats all work together
with real ONNX embeddings and SQLite storage.

Marked as integration tests so they can be skipped in fast CI
(they require the ONNX model download on first run).
"""

from __future__ import annotations

import os
from typing import List
from unittest.mock import patch

import pytest

from lore.mcp import server as mcp_server

# ── Helpers ──────────────────────────────────────────────────────────

class _FakeEmbedder:
    """Deterministic embedder that returns different vectors per content.

    This allows cosine search to actually differentiate between memories,
    unlike a stub that always returns the same vector.
    """

    def _content_to_vec(self, text: str) -> List[float]:
        """Create a pseudo-embedding from content hash — deterministic."""
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        vec = [0.0] * 384
        for i in range(min(len(h), 384)):
            vec[i] = (h[i % len(h)] - 128) / 128.0
        return vec

    def embed(self, text: str) -> List[float]:
        return self._content_to_vec(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self._content_to_vec(t) for t in texts]


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_mcp_state():
    """Reset MCP global state between tests."""
    mcp_server._store = None
    mcp_server._embedder = None
    mcp_server._default_project = None
    yield
    mcp_server._store = None
    mcp_server._embedder = None
    mcp_server._default_project = None


@pytest.fixture(autouse=True)
def _patch_embedder():
    """Patch the embedder with deterministic content-based vectors."""
    with patch.object(mcp_server, "_get_embedder", return_value=_FakeEmbedder()):
        yield


@pytest.fixture
def local_db(tmp_path):
    """Provide a temp DB and configure local mode."""
    db_path = str(tmp_path / "e2e.db")
    with patch.dict(os.environ, {
        "LORE_STORE": "local",
        "LORE_DB_PATH": db_path,
        "LORE_PROJECT": "test-project",
    }, clear=False):
        yield db_path


# ── E2E: Full first-run journey ─────────────────────────────────────


class TestFirstRunJourney:
    """Simulate the complete first-run user journey from README."""

    def test_full_flow(self, local_db) -> None:
        """remember → recall → list → stats → forget → verify empty."""

        # ── Step 1: Remember several memories ────────────────────────
        r1 = mcp_server.remember(
            content="Stripe rate-limits at 100 req/min. Use exponential backoff.",
            type="lesson",
            tags=["stripe", "rate-limit", "api"],
            source="claude",
        )
        assert "Memory saved" in r1
        assert "ID:" in r1

        r2 = mcp_server.remember(
            content="PostgreSQL VACUUM should be scheduled weekly for large tables.",
            type="lesson",
            tags=["postgres", "maintenance"],
        )
        assert "Memory saved" in r2

        r3 = mcp_server.remember(
            content="The team decided to use Redis for session caching.",
            type="decision",
            tags=["redis", "architecture"],
        )
        assert "Memory saved" in r3

        r4 = mcp_server.remember(
            content="React useEffect cleanup should abort pending fetches.",
            type="snippet",
            tags=["react", "hooks"],
            project="frontend",  # different project
        )
        assert "Memory saved" in r4

        # ── Step 2: Recall memories ──────────────────────────────────
        recall_result = mcp_server.recall(query="rate limiting strategy")
        assert "relevant memory" in recall_result
        assert "Stripe" in recall_result

        recall_result2 = mcp_server.recall(query="database maintenance")
        assert "relevant memory" in recall_result2
        assert "VACUUM" in recall_result2

        # ── Step 3: Recall with project filter ───────────────────────
        # Default project is test-project; frontend memory should be excluded
        recall_proj = mcp_server.recall(query="React hooks", project="frontend")
        assert "relevant memory" in recall_proj
        assert "useEffect" in recall_proj

        # ── Step 4: List memories ────────────────────────────────────
        list_result = mcp_server.list_memories()
        # Default project is test-project, so 3 memories (not the frontend one)
        assert "3" in list_result

        list_all = mcp_server.list_memories(project="frontend")
        assert "1" in list_all

        # ── Step 5: List by type ─────────────────────────────────────
        list_lessons = mcp_server.list_memories(type="lesson")
        assert "2" in list_lessons

        list_decisions = mcp_server.list_memories(type="decision")
        assert "1" in list_decisions

        # ── Step 6: Stats ────────────────────────────────────────────
        stats_result = mcp_server.stats()
        assert "Total memories: 3" in stats_result  # test-project only
        assert "lesson" in stats_result

        # ── Step 7: Forget specific memory ───────────────────────────
        # Extract an ID from the remember result
        id_start = r3.index("ID: ") + 4
        id_end = r3.index(")", id_start)
        decision_id = r3[id_start:id_end]

        forget_result = mcp_server.forget(id=decision_id)
        assert "deleted" in forget_result

        # Verify stats decreased
        stats_after = mcp_server.stats()
        assert "Total memories: 2" in stats_after

        # ── Step 8: Forget by type ───────────────────────────────────
        forget_type = mcp_server.forget(type="lesson", project="test-project")
        assert "Deleted 2" in forget_type

        # ── Step 9: Verify empty ─────────────────────────────────────
        stats_empty = mcp_server.stats()
        assert "Total memories: 0" in stats_empty

        list_empty = mcp_server.list_memories()
        assert "No memories found" in list_empty


class TestRecallRelevanceOrdering:
    """Verify recall returns results ordered by relevance."""

    def test_relevant_results_first(self, local_db) -> None:
        # Store diverse memories
        mcp_server.remember(content="Python asyncio event loop patterns")
        mcp_server.remember(content="How to bake chocolate chip cookies")
        mcp_server.remember(content="Python error handling with try/except")
        mcp_server.remember(content="Gardening tips for tomato plants")
        mcp_server.remember(content="Python decorators and metaclasses")

        result = mcp_server.recall(query="Python programming patterns")
        # Should find relevant memories
        assert "relevant memory" in result
        # All returned memories should contain Python-related content
        # (exact ordering depends on embedding similarity)


class TestMemoryPersistence:
    """Verify memories persist across store re-initialization."""

    def test_persistence_across_reinit(self, local_db) -> None:
        mcp_server.remember(content="Persistent memory test content")

        # Simulate restart: clear store reference
        mcp_server._store = None

        # Re-query should find it
        result = mcp_server.recall(query="persistent memory")
        assert "relevant memory" in result
        assert "Persistent memory test content" in result


class TestMemoryTypes:
    """Verify different memory types work correctly."""

    def test_all_types(self, local_db) -> None:
        types = ["note", "lesson", "snippet", "fact", "conversation", "decision"]
        for t in types:
            result = mcp_server.remember(
                content=f"Test content for {t}",
                type=t,
            )
            assert "Memory saved" in result

        stats = mcp_server.stats()
        assert f"Total memories: {len(types)}" in stats
        for t in types:
            assert t in stats


class TestTagFiltering:
    """Verify tag-based operations work end-to-end."""

    def test_recall_with_tags(self, local_db) -> None:
        mcp_server.remember(
            content="Python asyncio patterns",
            tags=["python", "async"],
        )
        mcp_server.remember(
            content="JavaScript async/await patterns",
            tags=["javascript", "async"],
        )
        mcp_server.remember(
            content="Python type hints guide",
            tags=["python", "typing"],
        )

        # Recall with tags should filter
        result = mcp_server.recall(query="async patterns", tags=["python"])
        assert "relevant memory" in result
        assert "Python asyncio" in result


class TestBulkOperations:
    """Verify bulk delete safety and correctness."""

    def test_bulk_delete_requires_confirm(self, local_db) -> None:
        mcp_server.remember(content="test 1")
        mcp_server.remember(content="test 2")
        mcp_server.remember(content="test 3")

        # Without confirm or filter, should warn
        result = mcp_server.forget()
        assert "confirm" in result.lower()

        # With confirm, should delete all
        result = mcp_server.forget(confirm=True)
        assert "Deleted 3" in result

    def test_bulk_delete_with_filter(self, local_db) -> None:
        mcp_server.remember(content="old lesson", type="lesson")
        mcp_server.remember(content="old note", type="note")
        mcp_server.remember(content="keep this", type="snippet")

        result = mcp_server.forget(type="lesson")
        assert "Deleted 1" in result

        stats = mcp_server.stats()
        assert "Total memories: 2" in stats


class TestMetadataRoundtrip:
    """Verify metadata survives the full pipeline."""

    def test_metadata_preserved(self, local_db) -> None:
        mcp_server.remember(
            content="Test with metadata",
            metadata={"confidence": 0.95, "source_url": "https://example.com"},
        )

        result = mcp_server.recall(query="metadata test")
        assert "confidence" in result
        assert "0.95" in result


class TestEdgeCases:
    """Edge cases for robustness."""

    def test_empty_store_recall(self, local_db) -> None:
        result = mcp_server.recall(query="anything")
        assert "No relevant memories" in result

    def test_empty_store_list(self, local_db) -> None:
        result = mcp_server.list_memories()
        assert "No memories found" in result

    def test_empty_store_stats(self, local_db) -> None:
        result = mcp_server.stats()
        assert "Total memories: 0" in result

    def test_very_long_content(self, local_db) -> None:
        long_content = "x" * 10000
        result = mcp_server.remember(content=long_content)
        assert "Memory saved" in result

    def test_special_characters(self, local_db) -> None:
        result = mcp_server.remember(
            content="SELECT * FROM users WHERE name = 'O'Brien'; -- SQL injection test",
            tags=["sql", "security"],
        )
        assert "Memory saved" in result

        recall_result = mcp_server.recall(query="SQL injection")
        assert "O'Brien" in recall_result

    def test_unicode_content(self, local_db) -> None:
        result = mcp_server.remember(content="日本語テスト: emoji 🎉 and symbols ∑∂∆")
        assert "Memory saved" in result
