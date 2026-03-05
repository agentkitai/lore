"""Tests for MCP server tools."""

from __future__ import annotations

import pytest
mcp = pytest.importorskip("mcp", reason="mcp not installed")

from unittest.mock import patch

import pytest

from lore import Lore
from lore.store.memory import MemoryStore


def _stub_embed(text: str):
    return [0.0] * 384


def _make_lore() -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_stub_embed)


@pytest.fixture
def mock_lore():
    """Patch _get_lore in MCP server to return test Lore instance."""
    lore = _make_lore()
    with patch("lore.mcp.server._get_lore", return_value=lore):
        yield lore


class TestMCPTools:
    def test_remember(self, mock_lore) -> None:
        from lore.mcp.server import remember
        result = remember("test knowledge")
        assert "Memory saved" in result
        assert "ID:" in result

    def test_recall_empty(self, mock_lore) -> None:
        from lore.mcp.server import recall
        result = recall("anything")
        assert "No relevant memories" in result

    def test_recall_with_results(self, mock_lore) -> None:
        from lore.mcp.server import recall, remember
        remember("rate limiting requires exponential backoff")
        result = recall("rate limit")
        assert "Found" in result
        assert "rate limiting" in result

    def test_forget(self, mock_lore) -> None:
        from lore.mcp.server import forget, remember
        result = remember("ephemeral")
        # Extract ID from result
        mid = result.split("ID: ")[1].rstrip(")")
        result = forget(mid)
        assert "forgotten" in result

    def test_forget_nonexistent(self, mock_lore) -> None:
        from lore.mcp.server import forget
        result = forget("nonexistent")
        assert "not found" in result

    def test_list_memories_empty(self, mock_lore) -> None:
        from lore.mcp.server import list_memories
        result = list_memories()
        assert "No memories" in result

    def test_list_memories_with_data(self, mock_lore) -> None:
        from lore.mcp.server import list_memories, remember
        remember("test item")
        result = list_memories()
        assert "test item" in result

    def test_stats(self, mock_lore) -> None:
        from lore.mcp.server import remember, stats
        remember("test1")
        remember("test2", type="lesson")
        result = stats()
        assert "Total memories: 2" in result

    def test_upvote(self, mock_lore) -> None:
        from lore.mcp.server import remember, upvote_memory
        result = remember("test")
        mid = result.split("ID: ")[1].rstrip(")")
        result = upvote_memory(mid)
        assert "Upvoted" in result

    def test_downvote(self, mock_lore) -> None:
        from lore.mcp.server import downvote_memory, remember
        result = remember("test")
        mid = result.split("ID: ")[1].rstrip(")")
        result = downvote_memory(mid)
        assert "Downvoted" in result
