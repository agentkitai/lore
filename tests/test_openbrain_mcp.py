"""Tests for Open Brain MCP tools."""

from __future__ import annotations

import os
from typing import List
from unittest.mock import patch

import pytest

# We test the tool functions directly, not via MCP transport
from openbrain.mcp import server as mcp_server


def _stub_embed(text: str) -> List[float]:
    """Deterministic stub embedding for tests."""
    return [0.1] * 384


@pytest.fixture(autouse=True)
def _clean_mcp_state():
    """Reset global state between tests."""
    mcp_server._store = None
    mcp_server._embedder = None
    mcp_server._default_project = None
    yield
    mcp_server._store = None
    mcp_server._embedder = None
    mcp_server._default_project = None


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temp DB path and configure env for local mode."""
    db_path = str(tmp_path / "test.db")
    with patch.dict(os.environ, {
        "OPENBRAIN_STORE": "local",
        "OPENBRAIN_DB_PATH": db_path,
    }, clear=False):
        yield db_path


class _FakeEmbedder:
    """Stub embedder that avoids loading the real ONNX model."""

    def embed(self, text: str) -> List[float]:
        return [0.1] * 384

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [[0.1] * 384 for _ in texts]


@pytest.fixture(autouse=True)
def _patch_embedder():
    """Patch the embedder getter to avoid needing the ONNX model."""
    with patch.object(mcp_server, "_get_embedder", return_value=_FakeEmbedder()):
        yield


class TestRemember:
    def test_remember_basic(self, tmp_db) -> None:
        result = mcp_server.remember(content="Test memory content")
        assert "Memory saved" in result
        assert "ID:" in result

    def test_remember_with_all_fields(self, tmp_db) -> None:
        result = mcp_server.remember(
            content="API rate limiting strategy",
            type="lesson",
            tags=["api", "reliability"],
            metadata={"confidence": 0.9},
            project="backend",
            source="claude",
        )
        assert "Memory saved" in result

    def test_remember_uses_default_project(self, tmp_db) -> None:
        with patch.dict(os.environ, {"OPENBRAIN_PROJECT": "default-proj"}, clear=False):
            # Reset store so it picks up new env
            mcp_server._store = None
            mcp_server._default_project = None
            result = mcp_server.remember(content="test")
            assert "Memory saved" in result

            # Verify memory has the default project
            store = mcp_server._get_store()
            memories, _ = store.list()
            assert len(memories) == 1
            assert memories[0].project == "default-proj"


class TestRecall:
    def test_recall_no_results(self, tmp_db) -> None:
        result = mcp_server.recall(query="something")
        assert "No relevant memories" in result

    def test_recall_finds_stored(self, tmp_db) -> None:
        mcp_server.remember(content="Python error handling best practices")
        result = mcp_server.recall(query="error handling")
        assert "relevant memory" in result
        assert "Python error handling" in result

    def test_recall_limit(self, tmp_db) -> None:
        for i in range(5):
            mcp_server.remember(content=f"Memory number {i}")
        result = mcp_server.recall(query="memory", limit=2)
        assert "2 relevant" in result


class TestForget:
    def test_forget_by_id(self, tmp_db) -> None:
        result = mcp_server.remember(content="to be deleted")
        # Extract ID from result
        id_start = result.index("ID: ") + 4
        id_end = result.index(")", id_start)
        memory_id = result[id_start:id_end]

        result = mcp_server.forget(id=memory_id)
        assert "deleted" in result

    def test_forget_nonexistent(self, tmp_db) -> None:
        result = mcp_server.forget(id="nonexistent_id")
        assert "not found" in result

    def test_forget_bulk_needs_confirm(self, tmp_db) -> None:
        mcp_server.remember(content="test")
        result = mcp_server.forget()
        assert "confirm" in result.lower()

    def test_forget_bulk_with_confirm(self, tmp_db) -> None:
        mcp_server.remember(content="test")
        result = mcp_server.forget(confirm=True)
        assert "Deleted" in result

    def test_forget_by_type(self, tmp_db) -> None:
        mcp_server.remember(content="lesson one", type="lesson")
        mcp_server.remember(content="note one", type="note")
        result = mcp_server.forget(type="lesson")
        assert "Deleted 1" in result


class TestListMemories:
    def test_list_empty(self, tmp_db) -> None:
        result = mcp_server.list_memories()
        assert "No memories found" in result

    def test_list_returns_stored(self, tmp_db) -> None:
        mcp_server.remember(content="First memory")
        mcp_server.remember(content="Second memory")
        result = mcp_server.list_memories()
        assert "2" in result

    def test_list_filter_by_type(self, tmp_db) -> None:
        mcp_server.remember(content="a lesson", type="lesson")
        mcp_server.remember(content="a note", type="note")
        result = mcp_server.list_memories(type="lesson")
        assert "1" in result


class TestStats:
    def test_stats_empty(self, tmp_db) -> None:
        result = mcp_server.stats()
        assert "Total memories: 0" in result

    def test_stats_with_data(self, tmp_db) -> None:
        mcp_server.remember(content="one", type="note")
        mcp_server.remember(content="two", type="lesson")
        result = mcp_server.stats()
        assert "Total memories: 2" in result
        assert "note" in result
        assert "lesson" in result
