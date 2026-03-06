"""Tests for the as_prompt MCP tool."""

from __future__ import annotations

import pytest

mcp = pytest.importorskip("mcp", reason="mcp not installed")

from unittest.mock import patch  # noqa: E402

from lore import Lore  # noqa: E402
from lore.store.memory import MemoryStore  # noqa: E402


def _stub_embed(text: str):
    return [0.0] * 384


def _make_lore() -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_stub_embed)


@pytest.fixture
def mock_lore():
    lore = _make_lore()
    with patch("lore.mcp.server._get_lore", return_value=lore):
        yield lore


class TestMCPAsPrompt:
    def test_as_prompt_tool_exists(self, mock_lore):
        from lore.mcp.server import as_prompt
        assert callable(as_prompt)

    def test_as_prompt_returns_formatted(self, mock_lore):
        from lore.mcp.server import as_prompt, remember
        remember("Always use CI for deployments")
        result = as_prompt("deployment", format="xml")
        assert "<memories" in result
        assert "Always use CI" in result

    def test_as_prompt_empty_results(self, mock_lore):
        from lore.mcp.server import as_prompt
        result = as_prompt("nonexistent topic")
        assert result == ""

    def test_as_prompt_error_handling(self, mock_lore):
        from lore.mcp.server import as_prompt
        # Invalid format should be caught and returned as error string
        result = as_prompt("test", format="invalid_format")
        assert "Failed to format memories" in result

    def test_as_prompt_markdown_format(self, mock_lore):
        from lore.mcp.server import as_prompt, remember
        remember("test memory")
        result = as_prompt("test", format="markdown")
        assert "## Relevant Memories" in result

    def test_as_prompt_no_wrapping(self, mock_lore):
        from lore.mcp.server import as_prompt, remember
        remember("test memory")
        result = as_prompt("test", format="xml")
        # Should NOT have wrapping status text like "Found N memory(ies):"
        assert "Found" not in result
        assert "memory(ies)" not in result
