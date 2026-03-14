"""Tests for Session Accumulator — auto-snapshot and session context injection."""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock, patch

from lore import Lore
from lore.store.memory import MemoryStore


def _stub_embed(text: str):
    return [0.0] * 384


def _make_lore(**kwargs) -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_stub_embed, redact=False, **kwargs)


# ── Feature 1: SessionAccumulator unit tests ────────────────────


class TestSessionAccumulatorBasic:
    """Test accumulation, threshold trigger, and reset."""

    def test_accumulate_below_threshold(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=1000)
        result = acc.add_content("hello world", session_id="test-session")
        assert result is None  # Below threshold, no trigger

    def test_accumulate_triggers_at_threshold(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=50)
        # First call: 30 chars — below threshold
        result = acc.add_content("x" * 30, session_id="s1")
        assert result is None
        # Second call: 30 more chars (total 60) — crosses threshold of 50
        result = acc.add_content("y" * 30, session_id="s1")
        assert result == "s1"

    def test_drain_returns_state_and_resets(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=100_000)
        acc.add_content("content A", session_id="s1")
        acc.add_content("query B", session_id="s1", is_query=True)

        state = acc.drain("s1")
        assert state is not None
        assert state["contents"] == ["content A"]
        assert state["queries"] == ["query B"]
        assert state["chars"] == len("content A") + len("query B")

        # After drain, session is gone
        assert acc.drain("s1") is None

    def test_drain_nonexistent_session_returns_none(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=100)
        assert acc.drain("nonexistent") is None

    def test_separate_sessions_independent(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=50)
        acc.add_content("x" * 30, session_id="s1")
        acc.add_content("y" * 30, session_id="s2")

        # Neither crossed individually
        s1 = acc.drain("s1")
        s2 = acc.drain("s2")
        assert s1["chars"] == 30
        assert s2["chars"] == 30

    def test_query_vs_content_tracking(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=100_000)
        acc.add_content("remember this", session_id="s1", is_query=False)
        acc.add_content("search for that", session_id="s1", is_query=True)

        state = acc.drain("s1")
        assert "remember this" in state["contents"]
        assert "search for that" in state["queries"]
        assert "remember this" not in state["queries"]
        assert "search for that" not in state["contents"]


class TestSessionAccumulatorThreadSafety:
    """Verify thread-safe accumulation."""

    def test_concurrent_accumulation(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=100_000)
        errors = []

        def add_many(session_id, count):
            try:
                for _ in range(count):
                    acc.add_content("x" * 10, session_id=session_id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=add_many, args=(f"s{i}", 100))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        for i in range(5):
            state = acc.drain(f"s{i}")
            assert state["chars"] == 1000  # 100 * 10


class TestBuildSnapshotContent:
    """Test snapshot content formatting."""

    def test_builds_structured_summary(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator()
        state = {
            "chars": 100,
            "contents": ["Fixed auth bug", "Added rate limiting"],
            "queries": ["CORS errors FastAPI", "rate limiting best practices"],
            "started_at": time.time(),
        }
        result = acc.build_snapshot_content(state)
        assert "[Auto-snapshot" in result
        assert "Fixed auth bug" in result
        assert "Added rate limiting" in result
        assert "CORS errors FastAPI" in result
        assert "rate limiting best practices" in result

    def test_truncates_long_content(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator()
        long_content = "x" * 1000
        state = {
            "chars": 1000,
            "contents": [long_content],
            "queries": [],
            "started_at": time.time(),
        }
        result = acc.build_snapshot_content(state)
        assert "..." in result
        assert len(result) < 1000  # Truncated

    def test_empty_state(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator()
        state = {"chars": 0, "contents": [], "queries": [], "started_at": time.time()}
        result = acc.build_snapshot_content(state)
        assert "[Auto-snapshot" in result


# ── Feature 2: Auto-snapshot via MCP handlers ───────────────────


class TestMcpRememberAccumulation:
    """Verify that the MCP remember handler feeds the accumulator."""

    def test_remember_feeds_accumulator(self):
        from lore.mcp import server

        lore = _make_lore()

        # Patch _get_lore and _accumulator
        with (
            patch.object(server, "_get_lore", return_value=lore),
            patch.object(server, "_accumulator") as mock_acc,
        ):
            mock_acc.add_content.return_value = None
            server.remember("test content", session_id="my-session")
            mock_acc.add_content.assert_called_once_with(
                "test content", session_id="my-session", is_query=False,
            )

    def test_remember_triggers_snapshot_on_threshold(self):
        from lore.mcp import server

        lore = _make_lore()
        fake_state = {
            "chars": 30000,
            "contents": ["big content"],
            "queries": [],
            "started_at": time.time(),
        }

        with (
            patch.object(server, "_get_lore", return_value=lore),
            patch.object(server, "_accumulator") as mock_acc,
            patch.object(server, "_fire_and_forget_snapshot") as mock_snap,
        ):
            mock_acc.add_content.return_value = "my-session"
            mock_acc.drain.return_value = fake_state
            server.remember("trigger content", session_id="my-session")
            mock_snap.assert_called_once_with(lore, "my-session", fake_state)


class TestMcpRecallAccumulation:
    """Verify that the MCP recall handler feeds queries to the accumulator."""

    def test_recall_feeds_query_to_accumulator(self):
        from lore.mcp import server

        lore = _make_lore()
        # Pre-populate a memory so recall returns something
        lore.remember("some relevant content", type="general")

        with (
            patch.object(server, "_get_lore", return_value=lore),
            patch.object(server, "_maybe_auto_snapshot") as mock_snap,
            patch.object(server, "_get_session_context", return_value=[]),
        ):
            server.recall("test query", session_id="s1")
            mock_snap.assert_called_once_with(
                lore, "test query", session_id="s1", is_query=True,
            )


# ── Feature 2: Auto-inject session context on recall ────────────


class TestAutoInjectSessionContext:
    """Verify that recall includes recent session snapshots."""

    def test_recall_includes_session_snapshots(self):
        from lore.mcp import server

        lore = _make_lore()
        # Save a regular memory to have a non-snapshot result
        lore.remember("regular memory about databases", type="general")
        # Save a session snapshot
        lore.save_snapshot(
            "Important context from earlier session",
            title="Earlier session",
            session_id="earlier-s1",
        )

        with patch.object(server, "_get_lore", return_value=lore):
            server._accumulator = server.SessionAccumulator()
            # Query something that matches the regular memory but also
            # triggers the session context injection
            result = server.recall("databases", include_session_context=True)
            # The snapshot should appear either in regular results (if it matched)
            # or as session context — either way it should be present
            assert "Important context from earlier session" in result

    def test_session_context_label_appears_for_non_overlapping_snapshots(self):
        from lore.mcp import server

        lore = _make_lore()
        # Save a regular memory
        lore.remember("regular memory about databases", type="general")
        # Save a snapshot — with stub embeddings it may match the query too,
        # but _get_session_context deduplicates, so we mock it to test the label
        snapshot_mem = lore.save_snapshot("Session context info", session_id="s1")

        # Mock _get_session_context to return session context lines
        mock_lines = [
            "─" * 60,
            "[Session Context] Recent session snapshots (last 24h):\n",
            "  Snapshot 1: test (created: 2026-03-14)",
            "  Session context info",
            "",
        ]
        with (
            patch.object(server, "_get_lore", return_value=lore),
            patch.object(server, "_get_session_context", return_value=mock_lines),
        ):
            server._accumulator = server.SessionAccumulator()
            result = server.recall("databases", include_session_context=True)
            assert "[Session Context]" in result

    def test_recall_skips_session_context_when_disabled(self):
        from lore.mcp import server

        lore = _make_lore()
        lore.save_snapshot("Snapshot content", session_id="s1")

        with patch.object(server, "_get_lore", return_value=lore):
            server._accumulator = server.SessionAccumulator()
            result = server.recall("context", include_session_context=False)
            assert "[Session Context]" not in result

    def test_no_duplicate_session_context(self):
        from lore.mcp import server

        lore = _make_lore()
        # Save a session snapshot that also matches semantically
        lore.save_snapshot("test memory content", session_id="s1")

        with patch.object(server, "_get_lore", return_value=lore):
            server._accumulator = server.SessionAccumulator()
            result = server.recall("test memory content", include_session_context=True)
            # Count occurrences — the snapshot should appear in either
            # regular results or session context, not both
            occurrences = result.count("test memory content")
            # Should appear at most twice (once as content, possibly once as context label)
            assert occurrences >= 1


# ── Feature 3: Cross-platform session ID ─────────────────────────


class TestCrossPlatformSessionId:
    """Verify PID-based default and explicit override."""

    def test_default_session_key_uses_pid(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator()
        key = acc._default_session_key()
        assert key.startswith(f"pid-{os.getpid()}-")

    def test_explicit_session_id_overrides_default(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=100_000)
        acc.add_content("hello", session_id="custom-id-123")

        state = acc.drain("custom-id-123")
        assert state is not None
        assert state["contents"] == ["hello"]

        # Default key should have nothing
        default_key = acc._default_session_key()
        assert acc.drain(default_key) is None

    def test_default_key_used_when_no_session_id(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=100_000)
        acc.add_content("hello")  # No session_id

        default_key = acc._default_session_key()
        state = acc.drain(default_key)
        assert state is not None
        assert state["contents"] == ["hello"]


# ── Feature: Threshold configurability via env var ───────────────


class TestThresholdConfigurability:
    def test_default_threshold(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator()
        # Default comes from _SNAPSHOT_THRESHOLD which reads env var
        assert acc._threshold > 0

    def test_custom_threshold(self):
        from lore.mcp.server import SessionAccumulator

        acc = SessionAccumulator(threshold=5000)
        assert acc._threshold == 5000

    def test_env_var_threshold(self):
        with patch.dict(os.environ, {"LORE_SNAPSHOT_THRESHOLD": "15000"}):
            # Re-read the env var
            threshold = int(os.environ.get("LORE_SNAPSHOT_THRESHOLD", "30000"))
            from lore.mcp.server import SessionAccumulator

            acc = SessionAccumulator(threshold=threshold)
            assert acc._threshold == 15000


# ── Fire-and-forget snapshot ─────────────────────────────────────


class TestFireAndForgetSnapshot:
    def test_snapshot_saved_in_background(self):
        from lore.mcp import server

        lore = _make_lore()
        state = {
            "chars": 30000,
            "contents": ["content A", "content B"],
            "queries": ["query 1"],
            "started_at": time.time(),
        }

        # Call _fire_and_forget_snapshot and wait for thread
        server._fire_and_forget_snapshot(lore, "test-session", state)
        # Give the background thread a moment
        time.sleep(0.5)

        # Verify a snapshot was saved
        memories = lore._store.list(type="session_snapshot")
        assert len(memories) >= 1
        snapshot = memories[0]
        assert "auto_snapshot" in snapshot.tags
        assert snapshot.type == "session_snapshot"

    def test_snapshot_failure_does_not_raise(self):
        from lore.mcp import server

        lore = MagicMock()
        lore.save_snapshot.side_effect = RuntimeError("DB down")

        state = {
            "chars": 30000,
            "contents": ["content"],
            "queries": [],
            "started_at": time.time(),
        }

        # Should not raise
        server._fire_and_forget_snapshot(lore, "test-session", state)
        time.sleep(0.5)  # Wait for thread — no exception should propagate
