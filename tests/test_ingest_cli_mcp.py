"""Tests for CLI ingest subcommand and MCP ingest tool (F7-S9)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from lore.cli import main as cli_main


def _mock_lore():
    """Create a MagicMock Lore with store/embedder stubs for pipeline usage."""
    lore = MagicMock()
    lore.remember.return_value = "mem-001"
    lore._store.list.return_value = []
    lore._store.search.return_value = []
    lore._embedder.embed.return_value = [0.0] * 384
    return lore


class TestCLIIngest:
    def test_single_item(self, tmp_path):
        db = str(tmp_path / "test.db")
        with patch("lore.cli._helpers._get_lore") as mock_get:
            lore = _mock_lore()
            mock_get.return_value = lore

            cli_main(["--db", db, "ingest", "Some knowledge", "--source", "manual", "--user", "alice", "--project", "p1"])

        lore.remember.assert_called_once()
        call_kwargs = lore.remember.call_args[1]
        assert call_kwargs["content"] == "Some knowledge"
        assert call_kwargs["source"] == "manual"
        assert call_kwargs["project"] == "p1"
        assert call_kwargs["metadata"]["source_info"]["user"] == "alice"

    def test_file_import_json(self, tmp_path):
        data = [{"content": "A", "user": "alice"}, {"content": "B", "user": "bob"}]
        f = tmp_path / "data.json"
        f.write_text(json.dumps(data))

        db = str(tmp_path / "test.db")
        with patch("lore.cli._helpers._get_lore") as mock_get:
            lore = _mock_lore()
            mock_get.return_value = lore

            cli_main(["--db", db, "ingest", "--source", "raw", "--file", str(f)])

        assert lore.remember.call_count == 2

    def test_file_import_text(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("line one\nline two\n\nline three\n")

        db = str(tmp_path / "test.db")
        with patch("lore.cli._helpers._get_lore") as mock_get:
            lore = _mock_lore()
            mock_get.return_value = lore

            cli_main(["--db", db, "ingest", "--source", "raw", "--file", str(f)])

        assert lore.remember.call_count == 3  # 3 non-empty lines

    def test_file_not_found(self, tmp_path):
        db = str(tmp_path / "test.db")
        with patch("lore.cli._helpers._get_lore") as mock_get:
            lore = _mock_lore()
            mock_get.return_value = lore

            with pytest.raises(SystemExit):
                cli_main(["--db", db, "ingest", "--file", "/nonexistent.json"])

    def test_no_content_or_file(self, tmp_path):
        db = str(tmp_path / "test.db")
        with patch("lore.cli._helpers._get_lore") as mock_get:
            lore = _mock_lore()
            mock_get.return_value = lore

            with pytest.raises(SystemExit):
                cli_main(["--db", db, "ingest"])

    def test_dedup_mode_option(self, tmp_path):
        db = str(tmp_path / "test.db")
        with patch("lore.cli._helpers._get_lore") as mock_get:
            lore = _mock_lore()
            mock_get.return_value = lore

            cli_main(["--db", db, "ingest", "content", "--dedup-mode", "skip"])

        lore.remember.assert_called_once()

    def test_dedup_mode_allow_skips_dedup(self, tmp_path):
        """--dedup-mode allow skips dedup check entirely."""
        db = str(tmp_path / "test.db")
        with patch("lore.cli._helpers._get_lore") as mock_get:
            lore = _mock_lore()
            mock_get.return_value = lore

            cli_main(["--db", db, "ingest", "content", "--dedup-mode", "allow"])

        lore.remember.assert_called_once()
        # store.list and store.search should NOT have been called (dedup skipped)
        lore._store.list.assert_not_called()

    def test_no_enrich_flag(self, tmp_path):
        """--no-enrich flag is wired through the pipeline."""
        db = str(tmp_path / "test.db")
        with patch("lore.cli._helpers._get_lore") as mock_get:
            lore = _mock_lore()
            mock_get.return_value = lore

            cli_main(["--db", db, "ingest", "content", "--no-enrich"])

        lore.remember.assert_called_once()

    def test_tags_option(self, tmp_path):
        db = str(tmp_path / "test.db")
        with patch("lore.cli._helpers._get_lore") as mock_get:
            lore = _mock_lore()
            mock_get.return_value = lore

            cli_main(["--db", db, "ingest", "content", "--tags", "a,b,c"])

        call_kwargs = lore.remember.call_args[1]
        assert "a" in call_kwargs["tags"]
        assert "b" in call_kwargs["tags"]
        assert "c" in call_kwargs["tags"]


class TestMCPIngestTool:
    def test_ingest_tool_basic(self):
        from lore.mcp.server import ingest

        with patch("lore.mcp.server._get_lore") as mock_get:
            lore = MagicMock()
            lore.remember.return_value = "mem-001"
            mock_get.return_value = lore

            result = ingest(content="lesson learned", source="mcp", user="agent")

        assert "mem-001" in result
        assert "mcp" in result
        lore.remember.assert_called_once()
        call_kwargs = lore.remember.call_args[1]
        assert call_kwargs["source"] == "mcp"
        assert call_kwargs["metadata"]["source_info"]["user"] == "agent"

    def test_ingest_tool_with_tags(self):
        from lore.mcp.server import ingest

        with patch("lore.mcp.server._get_lore") as mock_get:
            lore = MagicMock()
            lore.remember.return_value = "mem-002"
            mock_get.return_value = lore

            ingest(content="test", tags="a,b")

        call_kwargs = lore.remember.call_args[1]
        assert call_kwargs["tags"] == ["a", "b"]

    def test_ingest_tool_error_handling(self):
        from lore.mcp.server import ingest

        with patch("lore.mcp.server._get_lore") as mock_get:
            lore = MagicMock()
            lore.remember.side_effect = RuntimeError("DB down")
            mock_get.return_value = lore

            result = ingest(content="test")

        assert "failed" in result.lower()
