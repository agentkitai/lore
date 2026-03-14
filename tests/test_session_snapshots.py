"""Tests for E3 — Session Snapshots (Context Rescue)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from lore import Lore
from lore.store.memory import MemoryStore
from lore.types import VALID_MEMORY_TYPES, TIER_DECAY_HALF_LIVES


def _stub_embed(text: str):
    return [0.0] * 384


def _make_lore(**kwargs) -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_stub_embed, **kwargs)


# ── E3-S1: session_snapshot type + decay config ───────────────────


class TestSessionSnapshotType:
    def test_session_snapshot_in_valid_types(self):
        assert "session_snapshot" in VALID_MEMORY_TYPES

    def test_decay_working_tier(self):
        assert TIER_DECAY_HALF_LIVES["working"]["session_snapshot"] == 0.5

    def test_decay_short_tier(self):
        assert TIER_DECAY_HALF_LIVES["short"]["session_snapshot"] == 3

    def test_decay_long_tier(self):
        assert TIER_DECAY_HALF_LIVES["long"]["session_snapshot"] == 7

    def test_remember_with_session_snapshot_type(self):
        lore = _make_lore()
        mid = lore.remember("test snapshot", type="session_snapshot")
        mem = lore._store.get(mid)
        assert mem is not None
        assert mem.type == "session_snapshot"

    def test_existing_types_still_valid(self):
        for t in ["general", "code", "note", "lesson", "convention", "fact", "preference", "debug", "pattern"]:
            assert t in VALID_MEMORY_TYPES


# ── E3-S2: Lore.save_snapshot() raw path ─────────────────────────


class TestSaveSnapshotRaw:
    def test_save_with_content_only(self):
        lore = _make_lore()
        mem = lore.save_snapshot("Key decision: use PostgreSQL for storage")
        assert mem is not None
        assert mem.type == "session_snapshot"
        assert mem.tier == "long"
        assert mem.importance_score == 0.95
        assert "session_snapshot" in mem.tags
        assert mem.metadata is not None
        assert mem.metadata["extraction_method"] == "raw"
        assert len(mem.metadata["session_id"]) == 12
        assert mem.metadata["title"] == "Key decision: use PostgreSQL for storage"

    def test_save_with_explicit_fields(self):
        lore = _make_lore()
        mem = lore.save_snapshot(
            "decided auth approach",
            title="Auth decision",
            session_id="abc123def456",
            tags=["auth", "decision"],
        )
        assert mem.metadata["title"] == "Auth decision"
        assert mem.metadata["session_id"] == "abc123def456"
        assert "auth" in mem.tags
        assert "decision" in mem.tags
        assert "session_snapshot" in mem.tags
        assert "abc123def456" in mem.tags

    def test_save_empty_content_raises(self):
        lore = _make_lore()
        with pytest.raises(ValueError, match="non-empty"):
            lore.save_snapshot("")

    def test_save_whitespace_content_raises(self):
        lore = _make_lore()
        with pytest.raises(ValueError, match="non-empty"):
            lore.save_snapshot("   ")

    def test_importance_score_is_0_95(self):
        lore = _make_lore()
        mem = lore.save_snapshot("important context")
        assert mem.importance_score == 0.95

    def test_tags_include_session_id_and_type(self):
        lore = _make_lore()
        mem = lore.save_snapshot("test", session_id="mysession")
        assert "session_snapshot" in mem.tags
        assert "mysession" in mem.tags

    def test_title_auto_generated_from_content(self):
        lore = _make_lore()
        content = "x" * 200
        mem = lore.save_snapshot(content)
        assert mem.metadata["title"] == content[:80]

    def test_returns_memory_object(self):
        lore = _make_lore()
        mem = lore.save_snapshot("test")
        assert hasattr(mem, "id")
        assert hasattr(mem, "content")
        assert mem.content == "test"

    def test_round_trip_recall(self):
        lore = _make_lore()
        lore.save_snapshot("always use exponential backoff for retries")
        results = lore.recall("exponential backoff")
        assert len(results) > 0
        assert results[0].memory.type == "session_snapshot"


# ── E3-S3: LLM extraction for snapshots ──────────────────────────


class TestSnapshotLLMExtraction:
    def test_short_content_skips_extraction(self):
        lore = _make_lore()
        # Mock enrichment pipeline
        mock_pipeline = MagicMock()
        lore._enrichment_pipeline = mock_pipeline
        # Content <= 500 chars should skip extraction
        mem = lore.save_snapshot("short content")
        assert mem.metadata["extraction_method"] == "raw"
        mock_pipeline._llm.complete.assert_not_called()

    def test_no_enrichment_saves_raw(self):
        lore = _make_lore()
        long_content = "x" * 600
        mem = lore.save_snapshot(long_content)
        assert mem.metadata["extraction_method"] == "raw"

    def test_llm_extraction_runs_on_long_content(self):
        lore = _make_lore()
        # Mock enrichment pipeline
        mock_pipeline = MagicMock()
        mock_pipeline._llm.complete.return_value = "- Key decision: use Postgres\n- Next step: migrate data"
        lore._enrichment_pipeline = mock_pipeline

        long_content = "x" * 600
        mem = lore.save_snapshot(long_content)
        assert mem.metadata["extraction_method"] == "llm"
        assert "Key decision" in mem.content
        assert mem.context == long_content  # original preserved

    def test_llm_failure_falls_back_to_raw(self):
        lore = _make_lore()
        mock_pipeline = MagicMock()
        mock_pipeline._llm.complete.side_effect = RuntimeError("LLM down")
        lore._enrichment_pipeline = mock_pipeline

        long_content = "x" * 600
        mem = lore.save_snapshot(long_content)
        assert mem.metadata["extraction_method"] == "raw"
        assert mem.content == long_content


# ── E3-S4: Snapshot surfacing in recent_activity ──────────────────


class TestSnapshotSurfacing:
    def test_snapshot_appears_in_recent_activity(self):
        lore = _make_lore()
        lore.save_snapshot("session context here")
        result = lore.recent_activity(hours=24)
        assert result.total_count > 0
        # Find snapshot in groups
        found = False
        for group in result.groups:
            for m in group.memories:
                if m.type == "session_snapshot":
                    found = True
                    break
        assert found

    def test_snapshot_prefix_in_brief_format(self):
        from lore.recent import format_brief
        lore = _make_lore()
        lore.save_snapshot("test snapshot content")
        result = lore.recent_activity(hours=24)
        output = format_brief(result)
        assert "[Session Snapshot]" in output

    def test_snapshot_prefix_in_detailed_format(self):
        from lore.recent import format_detailed
        lore = _make_lore()
        lore.save_snapshot("test snapshot content")
        result = lore.recent_activity(hours=24)
        output = format_detailed(result)
        assert "[Session Snapshot]" in output

    def test_snapshot_prefix_in_cli_format(self):
        from lore.recent import format_cli
        lore = _make_lore()
        lore.save_snapshot("test snapshot content")
        result = lore.recent_activity(hours=24)
        output = format_cli(result)
        assert "[Session Snapshot]" in output

    def test_snapshot_has_high_importance(self):
        lore = _make_lore()
        lore.save_snapshot("important snapshot")
        result = lore.recent_activity(hours=24)
        for group in result.groups:
            snapshots = [m for m in group.memories if m.type == "session_snapshot"]
            for s in snapshots:
                assert s.importance_score == 0.95


# ── E3-S5: save_snapshot MCP tool ─────────────────────────────────


class TestSaveSnapshotMCP:
    @pytest.fixture
    def mock_lore(self):
        lore = _make_lore()
        with patch("lore.mcp.server._get_lore", return_value=lore):
            yield lore

    def test_mcp_save_snapshot(self, mock_lore):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import save_snapshot
        result = save_snapshot("key decisions made today")
        assert "Snapshot saved" in result
        assert "session=" in result
        assert "method=raw" in result

    def test_mcp_save_snapshot_with_params(self, mock_lore):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import save_snapshot
        result = save_snapshot(
            "auth decision",
            title="Auth",
            session_id="test123",
            tags=["auth"],
        )
        assert "Snapshot saved" in result
        assert "test123" in result

    def test_mcp_save_snapshot_empty_content(self, mock_lore):
        pytest.importorskip("mcp", reason="mcp not installed")
        from lore.mcp.server import save_snapshot
        result = save_snapshot("")
        assert "Error" in result or "non-empty" in result


# ── E3-S7: CLI snapshot save ──────────────────────────────────────


class TestSnapshotSaveCLI:
    def test_cli_snapshot_save(self, capsys):
        from lore.cli import main
        with patch("lore.cli._get_lore", return_value=_make_lore()):
            main(["snapshot-save", "key decisions"])
        captured = capsys.readouterr()
        assert "Snapshot saved:" in captured.out


# ── E3-S8: Snapshot management via existing tools ─────────────────


class TestSnapshotManagement:
    def test_list_memories_type_filter(self):
        lore = _make_lore()
        lore.save_snapshot("snapshot 1")
        lore.save_snapshot("snapshot 2")
        lore.remember("regular memory")
        snapshots = lore.list_memories(type="session_snapshot")
        assert len(snapshots) == 2
        for s in snapshots:
            assert s.type == "session_snapshot"

    def test_forget_snapshot(self):
        lore = _make_lore()
        mem = lore.save_snapshot("to be deleted")
        assert lore.forget(mem.id)
        assert lore._store.get(mem.id) is None
