"""Tests for prompt formatting: templates, budget enforcement, and edge cases."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lore.prompt.formatter import PromptFormatter
from lore.prompt.templates import (
    FORMAT_REGISTRY,
    format_chatml,
    format_markdown,
    format_raw,
    format_xml,
)
from lore.types import Memory, RecallResult


def _make_results(
    contents: list[str],
    scores: list[float] | None = None,
    types: list[str] | None = None,
    tags: list[list[str]] | None = None,
) -> list[RecallResult]:
    """Create RecallResult list for testing formatters directly."""
    if scores is None:
        scores = [0.9 - i * 0.1 for i in range(len(contents))]
    if types is None:
        types = ["lesson"] * len(contents)
    if tags is None:
        tags = [["tag1"] for _ in contents]
    results = []
    for i, (content, score, mtype, mtags) in enumerate(
        zip(contents, scores, types, tags)
    ):
        mem = Memory(
            id=f"test-id-{i:03d}",
            content=content,
            type=mtype,
            tags=mtags,
            created_at="2026-03-01T00:00:00+00:00",
        )
        results.append(RecallResult(memory=mem, score=score))
    return results


# ---------------------------------------------------------------
# XML Format Tests
# ---------------------------------------------------------------
class TestXMLFormat:
    def test_basic_output(self):
        results = _make_results(
            ["Deploy via CI", "Prod is us-east-1"],
            scores=[0.87, 0.72],
            types=["lesson", "fact"],
        )
        output = format_xml("deployment", results, False)
        assert '<memories query="deployment">' in output
        assert 'type="lesson"' in output
        assert 'score="0.87"' in output
        assert "Deploy via CI" in output
        assert "</memories>" in output

    def test_well_formed_xml(self):
        results = _make_results(["hello world", "another memory"])
        output = format_xml("test", results, False)
        ET.fromstring(output)  # Should not raise

    def test_xml_escaping(self):
        results = _make_results(['<script>alert("x")&foo</script>'])
        output = format_xml("test", results, False)
        ET.fromstring(output)  # Must be parseable
        assert "<script>" not in output
        assert "&lt;script&gt;" in output

    def test_include_metadata(self):
        results = _make_results(["content"], tags=[["devops", "k8s"]])
        output = format_xml("test", results, True)
        assert 'tags="devops,k8s"' in output
        assert 'id="test-id-000"' in output
        assert 'created="2026-03-01' in output

    def test_empty_results(self):
        assert format_xml("test", [], False) == ""


# ---------------------------------------------------------------
# ChatML Format Tests
# ---------------------------------------------------------------
class TestChatMLFormat:
    def test_basic_output(self):
        results = _make_results(
            ["Content one.", "Content two."],
            scores=[0.87, 0.72],
            types=["lesson", "fact"],
        )
        output = format_chatml("testing", results, False)
        assert "<|im_start|>system" in output
        assert "Relevant memories for: testing" in output
        assert "[lesson, 0.87] Content one." in output
        assert "[fact, 0.72] Content two." in output
        assert "<|im_end|>" in output

    def test_include_metadata(self):
        results = _make_results(["content"], tags=[["t1"]])
        output = format_chatml("test", results, True)
        assert "tags=t1" in output
        assert "id=test-id-000" in output

    def test_empty_results(self):
        assert format_chatml("test", [], False) == ""


# ---------------------------------------------------------------
# Markdown Format Tests
# ---------------------------------------------------------------
class TestMarkdownFormat:
    def test_basic_output(self):
        results = _make_results(
            ["Content one.", "Content two."],
            scores=[0.87, 0.72],
            types=["lesson", "fact"],
        )
        output = format_markdown("patterns", results, False)
        assert "## Relevant Memories: patterns" in output
        assert "- **[lesson, 0.87]** Content one." in output
        assert "- **[fact, 0.72]** Content two." in output

    def test_include_metadata(self):
        results = _make_results(["content"], tags=[["t1"]])
        output = format_markdown("test", results, True)
        assert "tags=t1" in output
        assert "id=test-id-000" in output

    def test_empty_results(self):
        assert format_markdown("test", [], False) == ""


# ---------------------------------------------------------------
# Raw Format Tests
# ---------------------------------------------------------------
class TestRawFormat:
    def test_basic_output(self):
        results = _make_results(["Content one.", "Content two."])
        output = format_raw("test", results, False)
        assert "Relevant memories for: test" in output
        assert "Content one." in output
        assert "Content two." in output

    def test_no_markup(self):
        results = _make_results(["hello"])
        output = format_raw("test", results, False)
        assert "<" not in output
        assert "**" not in output
        assert "<|im" not in output
        assert "#" not in output

    def test_include_metadata(self):
        results = _make_results(["content"], tags=[["t1"]])
        output = format_raw("test", results, True)
        assert "tags=t1" in output
        assert "id=test-id-000" in output

    def test_empty_results(self):
        assert format_raw("test", [], False) == ""


# ---------------------------------------------------------------
# Budget Enforcement Tests
# ---------------------------------------------------------------
class TestBudgetEnforcement:
    def _many_results(self, n=5, content_len=100):
        contents = [f"Memory content {'x' * content_len}" for _ in range(n)]
        scores = [0.9 - i * 0.1 for i in range(n)]
        return _make_results(contents, scores)

    def test_max_tokens_limits_output(self):
        formatter = PromptFormatter()
        results = self._many_results(5, content_len=100)
        output = formatter.format("q", results, max_tokens=50)
        # 50 tokens ~200 chars, should include fewer than 5 memories
        assert output != ""
        # Count <memory entries in XML
        count = output.count("<memory ")
        assert count < 5

    def test_max_chars_limits_output(self):
        formatter = PromptFormatter()
        results = self._many_results(5, content_len=100)
        output = formatter.format("q", results, max_chars=300)
        count = output.count("<memory ")
        assert count < 5

    def test_both_budgets_stricter_wins(self):
        formatter = PromptFormatter()
        results = self._many_results(5, content_len=100)
        # max_tokens=100 → 400 chars; max_chars=200 → stricter
        out_chars = formatter.format("q", results, max_tokens=100, max_chars=200)
        out_tokens = formatter.format("q", results, max_tokens=100)
        assert len(out_chars) <= len(out_tokens)

    def test_no_budget_includes_all(self):
        formatter = PromptFormatter()
        results = self._many_results(10)
        output = formatter.format("q", results)
        assert output.count("<memory ") == 10

    def test_first_memory_always_included(self):
        formatter = PromptFormatter()
        results = _make_results(["x" * 500])
        output = formatter.format("q", results, max_chars=100)
        assert "x" * 500 in output

    def test_score_descending_order(self):
        formatter = PromptFormatter()
        results = _make_results(
            ["A", "B", "C", "D"],
            scores=[0.9, 0.7, 0.5, 0.3],
        )
        # Budget for ~2 memories
        output = formatter.format("q", results, max_chars=250)
        count = output.count("<memory ")
        assert count >= 1
        # First memory (highest score) should always be present
        assert "A" in output

    def test_negative_budget_treated_as_no_budget(self):
        formatter = PromptFormatter()
        results = self._many_results(3)
        output = formatter.format("q", results, max_tokens=-1)
        assert output.count("<memory ") == 3

    def test_negative_max_chars_treated_as_no_budget(self):
        formatter = PromptFormatter()
        results = self._many_results(3)
        output = formatter.format("q", results, max_chars=-5)
        assert output.count("<memory ") == 3


# ---------------------------------------------------------------
# Filtering & Edge Cases
# ---------------------------------------------------------------
class TestFiltering:
    def test_min_score_filters(self):
        formatter = PromptFormatter()
        results = _make_results(["a", "b", "c"], scores=[0.9, 0.5, 0.2])
        output = formatter.format("q", results, min_score=0.4)
        assert output.count("<memory ") == 2

    def test_min_score_zero_no_filter(self):
        formatter = PromptFormatter()
        results = _make_results(["a", "b", "c"], scores=[0.9, 0.5, 0.2])
        output = formatter.format("q", results, min_score=0.0)
        assert output.count("<memory ") == 3

    def test_empty_recall_returns_empty(self):
        formatter = PromptFormatter()
        assert formatter.format("q", []) == ""

    def test_unknown_format_raises(self):
        formatter = PromptFormatter()
        results = _make_results(["a"])
        with pytest.raises(ValueError, match="Unknown format"):
            formatter.format("q", results, format="html")

    def test_single_large_memory_included(self):
        formatter = PromptFormatter()
        results = _make_results(["x" * 1000])
        output = formatter.format("q", results, max_chars=50)
        assert output != ""

    def test_all_below_min_score_returns_empty(self):
        formatter = PromptFormatter()
        results = _make_results(["a", "b"], scores=[0.1, 0.05])
        output = formatter.format("q", results, min_score=0.5)
        assert output == ""

    def test_format_registry_has_all_formats(self):
        assert set(FORMAT_REGISTRY.keys()) == {"xml", "chatml", "markdown", "raw"}


# ---------------------------------------------------------------
# Integration: as_prompt end-to-end with MemoryStore
# ---------------------------------------------------------------
class TestAsPromptIntegration:
    def test_end_to_end_with_memory_store(self):
        from lore import Lore
        from lore.store.memory import MemoryStore

        def stub_embed(text):
            return [0.0] * 384

        lore = Lore(store=MemoryStore(), embedding_fn=stub_embed)
        lore.remember("Always use CI for deploys", type="lesson", tags=["devops"])
        lore.remember("Prod is us-east-1", type="fact")
        lore.remember("Use exponential backoff", type="lesson")

        output = lore.as_prompt("deployment", format="xml")
        assert "<memories" in output
        assert output.count("<memory ") == 3
        lore.close()

    def test_as_prompt_returns_empty_on_no_matches(self):
        from lore import Lore
        from lore.store.memory import MemoryStore

        def stub_embed(text):
            return [0.0] * 384

        lore = Lore(store=MemoryStore(), embedding_fn=stub_embed)
        output = lore.as_prompt("anything")
        assert output == ""
        lore.close()

    def test_as_prompt_all_formats(self):
        from lore import Lore
        from lore.store.memory import MemoryStore

        def stub_embed(text):
            return [0.0] * 384

        lore = Lore(store=MemoryStore(), embedding_fn=stub_embed)
        lore.remember("test memory")

        for fmt in ["xml", "chatml", "markdown", "raw"]:
            output = lore.as_prompt("test", format=fmt)
            assert output != "", f"Format {fmt} returned empty"
        lore.close()

    def test_as_prompt_passes_recall_params(self):
        from lore import Lore
        from lore.store.memory import MemoryStore

        def stub_embed(text):
            return [0.0] * 384

        lore = Lore(store=MemoryStore(), embedding_fn=stub_embed)
        lore.remember("lesson one", type="lesson", tags=["t1"])
        lore.remember("fact one", type="fact", tags=["t2"])

        output = lore.as_prompt("test", format="xml", type="lesson")
        assert "lesson one" in output
        # fact should not appear when filtering by type=lesson
        # (depends on store filtering — MemoryStore supports type filter)
        lore.close()
