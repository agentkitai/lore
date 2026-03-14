"""Tests for E2 Batch 3: MCP tool, CLI command, REST endpoint."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import patch

from lore.lore import Lore
from lore.store.memory import MemoryStore
from lore.types import Memory


def _stub_embed(text: str) -> List[float]:
    return [0.1] * 384


def _make_memory(
    id: str,
    content: str = "test content",
    project: str | None = "lore",
    hours_ago: float = 1,
) -> Memory:
    created = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return Memory(
        id=id,
        content=content,
        project=project,
        created_at=created,
        updated_at=created,
        embedding=struct.pack("384f", *([0.1] * 384)),
    )


# =========================================================================
# S6: MCP Tool Tests
# =========================================================================


class TestMcpRecentActivityTool:
    def test_tool_registered(self):
        """recent_activity tool should be registered in the MCP server."""
        from lore.mcp.server import mcp
        tools = mcp._tool_manager.list_tools()
        tool_names = [t.name for t in tools]
        assert "recent_activity" in tool_names

    def test_instructions_mention_recent_activity(self):
        """MCP instructions should mention recent_activity."""
        from lore.mcp.server import mcp
        assert "recent_activity" in mcp.instructions

    def test_brief_returns_string(self):
        """MCP tool should return a formatted string for brief format."""
        from lore.mcp import server as mcp_server

        store = MemoryStore()
        store.save(_make_memory("m1", "Test memory"))
        lore = Lore(embedding_fn=_stub_embed)
        lore._store = store

        old = mcp_server._lore
        mcp_server._lore = lore
        try:
            result = mcp_server.recent_activity(hours=24, format="brief")
            assert isinstance(result, str)
            assert "Recent Activity" in result or "No recent activity" in result
        finally:
            mcp_server._lore = old

    def test_structured_returns_json(self):
        """MCP tool should return valid JSON for structured format."""
        from lore.mcp import server as mcp_server

        store = MemoryStore()
        store.save(_make_memory("m1", "Test memory"))
        lore = Lore(embedding_fn=_stub_embed)
        lore._store = store

        old = mcp_server._lore
        mcp_server._lore = lore
        try:
            result = mcp_server.recent_activity(hours=24, format="structured")
            parsed = json.loads(result)
            assert "groups" in parsed
            assert "total_count" in parsed
        finally:
            mcp_server._lore = old

    def test_error_handling(self):
        """MCP tool should return error message string on exception."""
        from lore.mcp import server as mcp_server

        old = mcp_server._lore
        mcp_server._lore = None  # Force re-init
        try:
            # This should not crash, just return an error string
            with patch.dict("os.environ", {"LORE_STORE": "invalid_type"}, clear=False):
                mcp_server._lore = None
                result = mcp_server.recent_activity()
                assert isinstance(result, str)
                assert "Failed" in result or "Invalid" in result or "error" in result.lower()
        finally:
            mcp_server._lore = old


# =========================================================================
# S7: CLI Tests
# =========================================================================


class TestCliRecent:
    def test_cli_recent_runs(self, tmp_path):
        """lore recent should run and exit 0."""
        db = str(tmp_path / "test.db")
        result = subprocess.run(
            [sys.executable, "-m", "lore", "--db", db, "recent"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0

    def test_cli_recent_default_output(self, tmp_path):
        """Should show 'Recent Activity' or 'No recent activity'."""
        db = str(tmp_path / "test.db")
        result = subprocess.run(
            [sys.executable, "-m", "lore", "--db", db, "recent"],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout
        assert "recent activity" in output.lower()

    def test_cli_recent_custom_hours(self, tmp_path):
        """--hours flag should be respected."""
        db = str(tmp_path / "test.db")
        result = subprocess.run(
            [sys.executable, "-m", "lore", "--db", db, "recent", "--hours", "72"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "72h" in result.stdout

    def test_cli_recent_format_detailed(self, tmp_path):
        """--format detailed should work."""
        db = str(tmp_path / "test.db")
        result = subprocess.run(
            [sys.executable, "-m", "lore", "--db", db, "recent", "--format", "detailed"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
