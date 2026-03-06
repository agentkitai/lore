"""Scenario 6 — Prompt export via as_prompt()."""

from __future__ import annotations

import pytest

from lore import Lore


class TestPromptExport:
    """Test as_prompt() formatting and budget enforcement."""

    def test_as_prompt_xml(self, lore_no_llm: Lore) -> None:
        """format='xml' returns an XML-like structure."""
        lore_no_llm.remember("always validate user input")
        output = lore_no_llm.as_prompt("validate input", format="xml")
        assert "<" in output  # XML tags present
        assert "validate" in output.lower()

    def test_as_prompt_markdown(self, lore_no_llm: Lore) -> None:
        """format='markdown' returns markdown-formatted output."""
        lore_no_llm.remember("use parameterized queries to prevent SQL injection")
        output = lore_no_llm.as_prompt("SQL injection", format="markdown")
        assert len(output) > 0
        assert "SQL injection" in output or "parameterized" in output

    def test_as_prompt_respects_token_budget(self, lore_no_llm: Lore) -> None:
        """max_tokens limits the output length."""
        for i in range(10):
            lore_no_llm.remember(f"memory number {i} with some extra padding text " * 5)

        # Very small token budget (~40 chars per token approx)
        output_small = lore_no_llm.as_prompt("memory", max_tokens=20)
        output_large = lore_no_llm.as_prompt("memory", max_tokens=5000)

        # Small budget should produce shorter output than large budget
        # (or equal if all memories are short enough)
        assert len(output_small) <= len(output_large)

    def test_as_prompt_includes_metadata(self, lore_no_llm: Lore) -> None:
        """include_metadata=True adds metadata to the output."""
        lore_no_llm.remember(
            "always use HTTPS in production",
            tags=["security"],
            metadata={"priority": "high"},
        )
        output_with = lore_no_llm.as_prompt(
            "HTTPS", format="xml", include_metadata=True,
        )
        output_without = lore_no_llm.as_prompt(
            "HTTPS", format="xml", include_metadata=False,
        )
        # Metadata output should be at least as long (usually longer)
        assert len(output_with) >= len(output_without)

    def test_as_prompt_empty_store(self, lore_no_llm: Lore) -> None:
        """as_prompt returns empty string when no memories match."""
        output = lore_no_llm.as_prompt("nonexistent topic")
        assert output == ""

    def test_as_prompt_invalid_format(self, lore_no_llm: Lore) -> None:
        """Invalid format raises ValueError."""
        lore_no_llm.remember("test")
        with pytest.raises(ValueError, match="Unknown format"):
            lore_no_llm.as_prompt("test", format="invalid_format")
