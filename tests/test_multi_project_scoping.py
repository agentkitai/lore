"""STORY-031: Multi-Project Scoping (FR-007) End-to-End Validation.

Explicit acceptance tests ensuring project scoping works end-to-end
across MCP tools and the SQLite store.

Test matrix: project=None, project="default-proj", project="custom-proj"
against all tools: remember, recall, forget, list, stats.
"""

from __future__ import annotations

import os
from typing import List
from unittest.mock import patch

import pytest

from lore.mcp import server as mcp_server
from lore.memory_store.sqlite import SqliteStore
from lore.types import Memory


# ── Helpers ──────────────────────────────────────────────────────────


class _FakeEmbedder:
    """Stub embedder that avoids loading the real ONNX model."""

    def embed(self, text: str) -> List[float]:
        return [0.1] * 384

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [[0.1] * 384 for _ in texts]


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
    """Patch the embedder getter to avoid needing the ONNX model."""
    with patch.object(mcp_server, "_get_embedder", return_value=_FakeEmbedder()):
        yield


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temp DB path and configure env for local mode."""
    db_path = str(tmp_path / "test.db")
    with patch.dict(os.environ, {
        "LORE_STORE": "local",
        "LORE_DB_PATH": db_path,
    }, clear=False):
        yield db_path


@pytest.fixture
def tmp_db_with_default_project(tmp_path):
    """Provide a temp DB path with LORE_PROJECT set."""
    db_path = str(tmp_path / "test.db")
    with patch.dict(os.environ, {
        "LORE_STORE": "local",
        "LORE_DB_PATH": db_path,
        "LORE_PROJECT": "default-proj",
    }, clear=False):
        yield db_path


# ── AC1: Default project from LORE_PROJECT env var ──────────────────


class TestDefaultProjectFromEnv:
    """Memory saved without explicit project uses LORE_PROJECT env var."""

    def test_remember_uses_env_default(self, tmp_db_with_default_project) -> None:
        mcp_server.remember(content="Auto-project memory")

        store = mcp_server._get_store()
        memories, _ = store.list()
        assert len(memories) == 1
        assert memories[0].project == "default-proj"

    def test_remember_overrides_env_default(self, tmp_db_with_default_project) -> None:
        mcp_server.remember(content="Custom project memory", project="custom-proj")

        store = mcp_server._get_store()
        memories, _ = store.list()
        assert len(memories) == 1
        assert memories[0].project == "custom-proj"

    def test_no_env_default_yields_none(self, tmp_db) -> None:
        mcp_server.remember(content="No project memory")

        store = mcp_server._get_store()
        memories, _ = store.list()
        assert len(memories) == 1
        assert memories[0].project is None


# ── AC2: Explicit project parameter ─────────────────────────────────


class TestExplicitProject:
    """Memory saved with explicit project stores that value."""

    def test_explicit_project_stored(self, tmp_db) -> None:
        mcp_server.remember(content="payments memory", project="payments")
        mcp_server.remember(content="infra memory", project="infra")

        store = mcp_server._get_store()
        payments, _ = store.list(project="payments")
        infra, _ = store.list(project="infra")
        assert len(payments) == 1
        assert payments[0].content == "payments memory"
        assert len(infra) == 1
        assert infra[0].content == "infra memory"


# ── AC3: recall respects project filter ──────────────────────────────


class TestRecallProjectFilter:
    """recall with project returns only memories from that project."""

    def test_recall_filters_by_project(self, tmp_db) -> None:
        mcp_server.remember(content="rate limiting in payments", project="payments")
        mcp_server.remember(content="rate limiting in infra", project="infra")

        result = mcp_server.recall(query="rate limiting", project="payments")
        assert "payments" in result
        assert "infra" not in result

    def test_recall_unscoped_returns_all(self, tmp_db) -> None:
        mcp_server.remember(content="memory in payments", project="payments")
        mcp_server.remember(content="memory in infra", project="infra")

        result = mcp_server.recall(query="memory")
        assert "2 relevant" in result


# ── AC4: list respects project filter ────────────────────────────────


class TestListProjectFilter:
    """list with project returns only memories from that project."""

    def test_list_filters_by_project(self, tmp_db) -> None:
        mcp_server.remember(content="payments memory", project="payments")
        mcp_server.remember(content="infra memory", project="infra")
        mcp_server.remember(content="no project memory")

        result = mcp_server.list_memories(project="payments")
        assert "1" in result
        assert "payments" in result

    def test_list_unscoped_returns_all(self, tmp_db) -> None:
        mcp_server.remember(content="payments memory", project="payments")
        mcp_server.remember(content="infra memory", project="infra")
        mcp_server.remember(content="no project memory")

        result = mcp_server.list_memories()
        assert "3" in result


# ── AC5: forget respects project filter ──────────────────────────────


class TestForgetProjectFilter:
    """forget with project deletes only memories from that project."""

    def test_forget_by_project(self, tmp_db) -> None:
        mcp_server.remember(content="keep this", project="keep")
        mcp_server.remember(content="delete this", project="delete-me")

        result = mcp_server.forget(project="delete-me")
        assert "Deleted 1" in result

        # Verify the other project is untouched
        store = mcp_server._get_store()
        memories, total = store.list()
        assert total == 1
        assert memories[0].project == "keep"

    def test_forget_with_project_and_type(self, tmp_db) -> None:
        mcp_server.remember(content="lesson in proj", type="lesson", project="proj")
        mcp_server.remember(content="note in proj", type="note", project="proj")
        mcp_server.remember(content="lesson elsewhere", type="lesson", project="other")

        result = mcp_server.forget(type="lesson", project="proj")
        assert "Deleted 1" in result

        store = mcp_server._get_store()
        _, total = store.list()
        assert total == 2


# ── AC6: stats respects project filter ───────────────────────────────


class TestStatsProjectFilter:
    """stats with project counts only memories from that project."""

    def test_stats_by_project(self, tmp_db) -> None:
        mcp_server.remember(content="a", project="alpha")
        mcp_server.remember(content="b", project="alpha")
        mcp_server.remember(content="c", project="beta")

        result = mcp_server.stats(project="alpha")
        assert "Total memories: 2" in result

    def test_stats_unscoped(self, tmp_db) -> None:
        mcp_server.remember(content="a", project="alpha")
        mcp_server.remember(content="b", project="beta")

        result = mcp_server.stats()
        assert "Total memories: 2" in result


# ── AC7: Unscoped queries return all projects ────────────────────────


class TestUnscopedQueries:
    """Unscoped queries (no project filter) return all projects."""

    def test_unscoped_recall(self, tmp_db) -> None:
        for proj in ["alpha", "beta", "gamma"]:
            mcp_server.remember(content=f"memory in {proj}", project=proj)

        result = mcp_server.recall(query="memory")
        assert "3 relevant" in result

    def test_unscoped_list(self, tmp_db) -> None:
        for proj in ["alpha", "beta", "gamma"]:
            mcp_server.remember(content=f"memory in {proj}", project=proj)

        result = mcp_server.list_memories()
        assert "3" in result

    def test_unscoped_stats(self, tmp_db) -> None:
        for proj in ["alpha", "beta", "gamma"]:
            mcp_server.remember(content=f"memory in {proj}", project=proj)

        result = mcp_server.stats()
        assert "Total memories: 3" in result
        assert "alpha" in result
        assert "beta" in result
        assert "gamma" in result


# ── SqliteStore direct project isolation tests ───────────────────────


class TestSqliteStoreProjectIsolation:
    """Direct SqliteStore tests for project scoping."""

    @pytest.fixture
    def store(self, tmp_path):
        s = SqliteStore(str(tmp_path / "test.db"))
        yield s
        s.close()

    def _make_memory(self, id: str, content: str, project: str | None = None) -> Memory:
        import struct
        return Memory(
            id=id,
            content=content,
            project=project,
            embedding=struct.pack("384f", *([0.1] * 384)),
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )

    def test_search_filters_by_project(self, store) -> None:
        store.save(self._make_memory("a", "hello world", project="proj1"))
        store.save(self._make_memory("b", "hello world", project="proj2"))

        results = store.search(embedding=[0.1] * 384, project="proj1")
        assert len(results) == 1
        assert results[0].memory.id == "a"

    def test_list_filters_by_project(self, store) -> None:
        store.save(self._make_memory("a", "hello", project="proj1"))
        store.save(self._make_memory("b", "world", project="proj2"))

        memories, total = store.list(project="proj1")
        assert total == 1
        assert memories[0].id == "a"

    def test_delete_by_filter_respects_project(self, store) -> None:
        store.save(self._make_memory("a", "hello", project="proj1"))
        store.save(self._make_memory("b", "world", project="proj2"))

        deleted = store.delete_by_filter(project="proj1")
        assert deleted == 1
        assert store.get("a") is None
        assert store.get("b") is not None

    def test_stats_filters_by_project(self, store) -> None:
        store.save(self._make_memory("a", "hello", project="proj1"))
        store.save(self._make_memory("b", "world", project="proj1"))
        store.save(self._make_memory("c", "test", project="proj2"))

        s = store.stats(project="proj1")
        assert s.total_count == 2

    def test_search_no_project_filter_returns_all(self, store) -> None:
        store.save(self._make_memory("a", "hello", project="proj1"))
        store.save(self._make_memory("b", "hello", project="proj2"))
        store.save(self._make_memory("c", "hello", project=None))

        results = store.search(embedding=[0.1] * 384)
        assert len(results) == 3
