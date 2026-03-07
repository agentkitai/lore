"""Tests for F2: Verbatim Recall mode."""

from __future__ import annotations

from typing import List
from unittest.mock import patch

import numpy as np
import pytest

from lore import Lore, RecallResult
from lore.prompt.formatter import PromptFormatter
from lore.store.memory import MemoryStore
from lore.types import Memory, RecallConfig

_DIM = 384


def _fake_embed(text: str) -> List[float]:
    rng = np.random.RandomState(abs(hash(text)) % (2**31))
    vec = rng.randn(_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _make_lore(**kwargs) -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_fake_embed, **kwargs)


def _make_result(content: str, score: float = 0.9, **mem_kwargs) -> RecallResult:
    mem = Memory(id="test-id", content=content, created_at="2026-03-01T12:00:00+00:00", **mem_kwargs)
    return RecallResult(memory=mem, score=score)


# ---------------------------------------------------------------
# S1: RecallConfig + RecallResult extended
# ---------------------------------------------------------------
class TestRecallConfigExtended:
    def test_verbatim_default_false(self):
        config = RecallConfig()
        assert config.verbatim is False

    def test_verbatim_set_true(self):
        config = RecallConfig(verbatim=True)
        assert config.verbatim is True

    def test_recall_result_verbatim_default_false(self):
        r = _make_result("test")
        assert r.verbatim is False

    def test_recall_result_verbatim_set_true(self):
        r = _make_result("test")
        r.verbatim = True
        assert r.verbatim is True

    def test_existing_fields_preserved(self):
        config = RecallConfig(query="test", date_from="2026-01-01")
        assert config.query == "test"
        assert config.date_from == "2026-01-01"


# ---------------------------------------------------------------
# S2: Recall pipeline conditional formatting
# ---------------------------------------------------------------
class TestRecallPipelineVerbatim:
    def test_verbatim_returns_raw_content(self):
        lore = _make_lore()
        lore.remember("exact original content here")
        results = lore.recall("content", verbatim=True)
        assert len(results) >= 1
        assert results[0].verbatim is True
        assert results[0].memory.content == "exact original content here"

    def test_non_verbatim_default_behavior(self):
        lore = _make_lore()
        lore.remember("some content")
        results = lore.recall("content")
        assert len(results) >= 1
        assert results[0].verbatim is False

    def test_verbatim_no_breaking_changes(self):
        lore = _make_lore()
        lore.remember("test")
        results_default = lore.recall("test")
        results_explicit = lore.recall("test", verbatim=False)
        assert len(results_default) == len(results_explicit)
        assert results_default[0].verbatim is False
        assert results_explicit[0].verbatim is False


# ---------------------------------------------------------------
# S3: CLI --verbatim flag
# ---------------------------------------------------------------
class TestCLIVerbatim:
    def test_verbatim_flag_parsed(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["recall", "test query", "--verbatim"])
        assert args.verbatim is True

    def test_short_flag_parsed(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["recall", "test query", "-v"])
        assert args.verbatim is True

    def test_default_no_verbatim(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["recall", "test query"])
        assert args.verbatim is False

    def test_verbatim_with_limit_and_offset(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["recall", "test", "--verbatim", "--limit", "20", "--offset", "5"])
        assert args.verbatim is True
        assert args.limit == 20
        assert args.offset == 5

    def test_verbatim_output_format(self, tmp_path, capsys):
        from lore.cli import main
        db = str(tmp_path / "test.db")
        main(["--db", db, "remember", "exact words here", "--source", "chat"])
        capsys.readouterr()
        main(["--db", db, "recall", "exact words", "--verbatim"])
        out = capsys.readouterr().out
        assert "exact words here" in out
        assert "---" in out

    def test_verbatim_shows_metadata(self, tmp_path, capsys):
        from lore.cli import main
        db = str(tmp_path / "test.db")
        main(["--db", db, "remember", "verbatim test"])
        capsys.readouterr()
        main(["--db", db, "recall", "verbatim test", "--verbatim"])
        out = capsys.readouterr().out
        # Should show created_at timestamp
        assert "[" in out
        assert "]" in out


# ---------------------------------------------------------------
# S4: MCP tool verbatim parameter
# ---------------------------------------------------------------
class TestMCPVerbatim:
    @pytest.fixture
    def mock_lore(self):
        lore = _make_lore()
        mcp_mod = pytest.importorskip("mcp", reason="mcp not installed")  # noqa: F841
        with patch("lore.mcp.server._get_lore", return_value=lore):
            yield lore

    def test_recall_verbatim(self, mock_lore):
        from lore.mcp.server import recall, remember
        remember("original content for verbatim test")
        result = recall("original content", verbatim=True)
        assert "verbatim" in result.lower()
        assert "original content for verbatim test" in result

    def test_recall_verbatim_false_default(self, mock_lore):
        from lore.mcp.server import recall, remember
        remember("test content")
        result = recall("test content")
        # Default behavior should not say "verbatim"
        assert "Found" in result

    def test_recall_verbatim_with_filters(self, mock_lore):
        from lore.mcp.server import recall, remember
        remember("filtered verbatim", type="lesson")
        result = recall("filtered", verbatim=True, type="lesson")
        assert "filtered verbatim" in result

    def test_recall_verbatim_shows_metadata(self, mock_lore):
        from lore.mcp.server import recall, remember
        remember("test memory")
        result = recall("test", verbatim=True)
        assert "created:" in result or "source:" in result

    def test_as_prompt_verbatim(self, mock_lore):
        from lore.mcp.server import as_prompt, remember
        remember("raw prompt content")
        result = as_prompt("raw prompt", verbatim=True)
        assert "original words" in result.lower()
        assert "raw prompt content" in result


# ---------------------------------------------------------------
# S5: SDK method with verbatim
# ---------------------------------------------------------------
class TestSDKVerbatim:
    def test_recall_signature_accepts_verbatim(self):
        lore = _make_lore()
        lore.remember("sdk test")
        results = lore.recall("sdk test", verbatim=True)
        assert isinstance(results, list)
        assert all(isinstance(r, RecallResult) for r in results)

    def test_verbatim_results_have_raw_content(self):
        lore = _make_lore()
        original = "The exact original content with special chars: <>&"
        lore.remember(original)
        results = lore.recall("exact original", verbatim=True)
        assert len(results) >= 1
        assert results[0].memory.content == original

    def test_verbatim_backward_compatible(self):
        lore = _make_lore()
        lore.remember("compat test")
        # Call without verbatim — should work as before
        results = lore.recall("compat test")
        assert len(results) >= 1
        assert results[0].verbatim is False


# ---------------------------------------------------------------
# S6: Verbatim + all existing filters
# ---------------------------------------------------------------
class TestVerbatimFilters:
    def test_verbatim_with_type_filter(self):
        lore = _make_lore()
        lore.remember("lesson content", type="lesson")
        lore.remember("fact content", type="fact")
        results = lore.recall("content", type="lesson", verbatim=True)
        assert len(results) == 1
        assert results[0].memory.type == "lesson"
        assert results[0].verbatim is True

    def test_verbatim_with_tier_filter(self):
        lore = _make_lore()
        lore.remember("short tier", tier="short")
        lore.remember("long tier", tier="long")
        results = lore.recall("tier", tier="short", verbatim=True)
        assert len(results) == 1
        assert results[0].memory.tier == "short"

    def test_verbatim_with_tags_filter(self):
        lore = _make_lore()
        lore.remember("tagged memory", tags=["python"])
        lore.remember("untagged memory")
        results = lore.recall("memory", tags=["python"], verbatim=True)
        assert len(results) == 1
        assert "python" in results[0].memory.tags

    def test_verbatim_with_limit(self):
        lore = _make_lore()
        for i in range(10):
            lore.remember(f"memory {i}")
        results = lore.recall("memory", limit=3, verbatim=True)
        assert len(results) == 3
        assert all(r.verbatim for r in results)

    def test_verbatim_preserves_scoring(self):
        lore = _make_lore()
        for i in range(5):
            lore.remember(f"memory {i}")
        results = lore.recall("memory", verbatim=True)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------
# S7: As prompt integration with verbatim
# ---------------------------------------------------------------
class TestAsPromptVerbatim:
    def test_as_prompt_verbatim_includes_header(self):
        lore = _make_lore()
        lore.remember("original words")
        output = lore.as_prompt("original", verbatim=True)
        assert "original words" in output.lower()

    def test_as_prompt_verbatim_budget_enforcement(self):
        lore = _make_lore()
        for i in range(10):
            lore.remember(f"memory content number {i} " + "x" * 100)
        output = lore.as_prompt("memory", verbatim=True, max_chars=300)
        assert len(output) <= 500  # Allow some overhead beyond budget

    def test_as_prompt_verbatim_false_uses_standard(self):
        lore = _make_lore()
        lore.remember("test content")
        output_default = lore.as_prompt("test")
        output_explicit = lore.as_prompt("test", verbatim=False)
        # Both should use standard formatting (XML by default)
        assert "<memories" in output_default
        assert "<memories" in output_explicit

    def test_as_prompt_verbatim_empty_results(self):
        lore = _make_lore()
        output = lore.as_prompt("nonexistent", verbatim=True)
        assert output == ""

    def test_formatter_verbatim_output(self):
        formatter = PromptFormatter()
        results = [
            RecallResult(
                memory=Memory(
                    id="id1", content="original text here",
                    created_at="2026-03-01T12:00:00+00:00",
                    source="chat",
                ),
                score=0.9,
            ),
        ]
        output = formatter.format("test", results, verbatim=True)
        assert "original words" in output.lower()
        assert "original text here" in output
        assert "2026-03-01" in output
        assert "chat" in output

    def test_formatter_verbatim_budget(self):
        formatter = PromptFormatter()
        results = [
            RecallResult(
                memory=Memory(id=f"id{i}", content="x" * 200, created_at="2026-03-01T00:00:00"),
                score=0.9 - i * 0.1,
            )
            for i in range(10)
        ]
        output = formatter.format("q", results, verbatim=True, max_chars=400)
        # Should include fewer than 10 memories
        assert output.count("x" * 200) < 10


# ---------------------------------------------------------------
# S8: Metadata display with verbatim
# ---------------------------------------------------------------
class TestMetadataDisplay:
    def test_verbatim_includes_created_at(self):
        lore = _make_lore()
        lore.remember("metadata test")
        results = lore.recall("metadata test", verbatim=True)
        assert results[0].memory.created_at != ""

    def test_verbatim_includes_source(self):
        lore = _make_lore()
        lore.remember("source test", source="chat-session")
        results = lore.recall("source test", verbatim=True)
        assert results[0].memory.source == "chat-session"

    def test_verbatim_includes_project(self):
        lore = _make_lore(project="my-project")
        lore.remember("project test")
        results = lore.recall("project test", verbatim=True)
        assert results[0].memory.project == "my-project"

    def test_verbatim_includes_tier(self):
        lore = _make_lore()
        lore.remember("tier test", tier="short")
        results = lore.recall("tier test", verbatim=True)
        assert results[0].memory.tier == "short"


# ---------------------------------------------------------------
# S9: Pagination for verbatim
# ---------------------------------------------------------------
class TestVerbatimPagination:
    def test_offset_skips_results(self):
        lore = _make_lore()
        for i in range(10):
            lore.remember(f"memory {i}")
        all_results = lore.recall("memory", limit=10, verbatim=True)
        offset_results = lore.recall("memory", limit=5, offset=5, verbatim=True)
        # Offset results should be a subset of all results starting at position 5
        assert len(offset_results) == 5
        all_ids = [r.memory.id for r in all_results]
        offset_ids = [r.memory.id for r in offset_results]
        assert offset_ids == all_ids[5:10]

    def test_offset_beyond_results(self):
        lore = _make_lore()
        lore.remember("single memory")
        results = lore.recall("single", offset=10, verbatim=True)
        assert len(results) == 0

    def test_default_limit(self):
        lore = _make_lore()
        for i in range(10):
            lore.remember(f"memory {i}")
        results = lore.recall("memory", verbatim=True)
        assert len(results) == 5  # default limit

    def test_pagination_with_cli(self, tmp_path, capsys):
        from lore.cli import main
        db = str(tmp_path / "test.db")
        for i in range(5):
            main(["--db", db, "remember", f"paginated memory {i}"])
        capsys.readouterr()
        main(["--db", db, "recall", "paginated", "--verbatim", "--limit", "2", "--offset", "0"])
        out1 = capsys.readouterr().out
        main(["--db", db, "recall", "paginated", "--verbatim", "--limit", "2", "--offset", "2"])
        out2 = capsys.readouterr().out
        # Both pages should have content
        assert "---" in out1
        assert "---" in out2


# ---------------------------------------------------------------
# Integration: verbatim end-to-end
# ---------------------------------------------------------------
class TestVerbatimIntegration:
    def test_full_flow_remember_and_verbatim_recall(self):
        lore = _make_lore()
        original = "Always use exponential backoff for rate limits"
        lore.remember(original, type="lesson", tags=["api"], source="debugging-session")
        results = lore.recall("backoff rate limit", verbatim=True)
        assert len(results) >= 1
        r = results[0]
        assert r.verbatim is True
        assert r.memory.content == original
        assert r.memory.type == "lesson"
        assert r.memory.source == "debugging-session"
        assert "api" in r.memory.tags

    def test_verbatim_and_non_verbatim_same_results(self):
        lore = _make_lore()
        lore.remember("shared content")
        v_results = lore.recall("shared", verbatim=True)
        n_results = lore.recall("shared", verbatim=False)
        assert len(v_results) == len(n_results)
        # Same memory IDs
        v_ids = {r.memory.id for r in v_results}
        n_ids = {r.memory.id for r in n_results}
        assert v_ids == n_ids
