"""Phase 6A — auto-capture pipeline tests.

Two layers of coverage:

* **Unit:** truncation rules, cursor read/write atomicity, buffer reader,
  prompt builder, sanitization helpers, recursion guard.
* **Integration:** ``cmd_capture_extract`` against a fixture buffer +
  fake transcript + mocked ``subprocess.Popen`` and ``shutil.which``.
  Tests assert what the subagent *would* have been launched with — we
  never invoke the real ``claude -p`` here.

The PostToolUse hook script itself is rendered with a stub
``server_url`` / ``api_key`` and exercised via ``bash``: this catches
``.format()`` substitution bugs that would ship a broken hook to
users. We create the hook in a temp ``HOME`` and feed it synthetic
PostToolUse JSON on stdin.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from lore.cli.commands import capture as cap
from lore.setup import (
    LORE_CAPTURE_STOP_HOOK_SCRIPT,
    LORE_CAPTURE_TOOL_HOOK_SCRIPT,
    _render_ensure_server_bash,
)

# ── Unit tests ────────────────────────────────────────────────────


class TestTruncate:
    def test_short_string_passes_through(self):
        assert cap._truncate("hi") == "hi"

    def test_exactly_threshold_passes_through(self):
        s = "x" * 200
        assert cap._truncate(s) == s

    def test_long_string_head_tail(self):
        s = "A" * 100 + "B" * 100 + "C" * 100  # 300 chars
        out = cap._truncate(s)
        # head 100 + ellipsis + tail 80 = 181 (counting the ellipsis as 1 codepoint)
        assert out.startswith("A" * 100)
        assert out.endswith("C" * 80)
        assert "…" in out
        assert len(out) == 181

    def test_dict_serialized_then_truncated(self):
        big = {"k": "v" * 500}
        out = cap._truncate(big)
        assert "…" in out
        # JSON encoding of small dict still happens deterministically
        small = {"a": 1, "b": 2}
        assert cap._truncate(small) == json.dumps(small, ensure_ascii=False, sort_keys=True)

    def test_none_becomes_empty(self):
        assert cap._truncate(None) == ""

    def test_non_serializable_falls_back_to_str(self):
        class Weird:
            def __repr__(self):
                return "<weird>"

        out = cap._truncate(Weird())
        assert "<weird>" in out


class TestSanitizeSessionId:
    def test_simple_session_id_passes_through(self):
        assert cap._sanitize_session_id("abc-123") == "abc-123"

    def test_special_chars_replaced(self):
        assert cap._sanitize_session_id("a/b\\c") == "a_b_c"

    def test_long_id_truncated(self):
        long = "x" * 200
        assert cap._sanitize_session_id(long) == "x" * 64

    def test_empty_falls_back(self):
        assert cap._sanitize_session_id("") == "unknown"


class TestCursorIO:
    def test_read_missing_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert cap._read_cursor("sess1") == 0

    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cap._write_cursor("sess1", 42)
        assert cap._read_cursor("sess1") == 42
        # Atomicity: tmp file should be cleaned up.
        cursor_dir = tmp_path / ".lore" / "sessions" / "sess1"
        leftover = list(cursor_dir.glob("*.tmp"))
        assert leftover == []

    def test_corrupt_cursor_yields_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = cap._cursor_path("sess1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-an-integer")
        assert cap._read_cursor("sess1") == 0


class TestBufferRead:
    def test_missing_buffer_yields_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert cap._read_buffer("sess1") == []

    def test_skips_malformed_lines(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = cap._buffer_path("sess1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"seq": 1, "tool": "Edit"}) + "\n"
            + "not-json\n"
            + json.dumps({"seq": 2, "tool": "Bash"}) + "\n"
        )
        out = cap._read_buffer("sess1")
        assert [e["seq"] for e in out] == [1, 2]

    def test_skips_non_dict_entries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = cap._buffer_path("sess1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1,2,3]\n" + json.dumps({"seq": 5}) + "\n")
        out = cap._read_buffer("sess1")
        assert [e["seq"] for e in out] == [5]


class TestPromptBuilder:
    def test_includes_buffer_transcript_titles(self):
        prompt = cap._build_prompt(
            buffer_lines=['{"seq":1,"tool":"Edit"}'],
            transcript_tail="[user] Hi\n[assistant] Hello",
            recent_titles=["Already saved memory"],
        )
        assert '"tool":"Edit"' in prompt
        assert "[user] Hi" in prompt
        assert "Already saved memory" in prompt
        assert "PROCESSED_THROUGH_SEQ=" in prompt

    def test_handles_empty_inputs(self):
        prompt = cap._build_prompt(
            buffer_lines=[],
            transcript_tail="",
            recent_titles=[],
        )
        assert "(empty)" in prompt
        assert "(no transcript available)" in prompt
        assert "(none)" in prompt


class TestScanLogForProcessedSeq:
    def test_missing_log_returns_none(self, tmp_path):
        assert cap._scan_log_for_processed_seq(tmp_path / "missing.log") is None

    def test_no_marker_returns_none(self, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text("nothing useful here\n")
        assert cap._scan_log_for_processed_seq(log) is None

    def test_latest_marker_wins(self, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text(
            "PROCESSED_THROUGH_SEQ=3\nPROCESSED_THROUGH_SEQ=10\nstale=junk\n"
        )
        assert cap._scan_log_for_processed_seq(log) == 10


class TestTranscriptTail:
    def test_missing_transcript_returns_empty(self):
        assert cap._read_transcript_tail("/no/such/file.jsonl", 10) == ""

    def test_filters_to_user_assistant(self, tmp_path):
        path = tmp_path / "transcript.jsonl"
        path.write_text(
            json.dumps({"type": "user", "message": {"content": "first"}}) + "\n"
            + json.dumps({"type": "system", "message": {"content": "ignore"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": [{"text": "reply"}]}}) + "\n"
        )
        out = cap._read_transcript_tail(str(path), 10)
        assert "[user] first" in out
        assert "[assistant] reply" in out
        assert "ignore" not in out

    def test_max_turns_limits_output(self, tmp_path):
        path = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": f"u{i}"}})
            for i in range(20)
        ]
        path.write_text("\n".join(lines) + "\n")
        out = cap._read_transcript_tail(str(path), 3)
        assert out.count("[user]") == 3
        # Newest 3 are u17/u18/u19 — last in the file.
        assert "u19" in out
        assert "u17" in out
        assert "u0" not in out


# ── Integration: cmd_capture_extract ──────────────────────────────


class TestCaptureExtractIntegration:
    """End-to-end ``cmd_capture_extract`` exercising buffer slicing,
    prompt construction, and subprocess.Popen at the boundary."""

    def _seed_session(self, tmp_path: Path, session_id: str, n: int) -> Path:
        sess_dir = tmp_path / ".lore" / "sessions" / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        buf = sess_dir / "buffer.jsonl"
        with buf.open("w", encoding="utf-8") as f:
            for i in range(1, n + 1):
                f.write(json.dumps({
                    "seq": i, "ts": "now", "tool": "Edit",
                    "input_summary": f"file=src/mod{i}.py",
                    "output_summary": "Updated 1 line",
                }) + "\n")
        return sess_dir

    def test_spawn_called_with_prompt_containing_buffer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Disable recent-memory fetch (would otherwise hit the network).
        monkeypatch.setattr(
            cap, "_fetch_recent_memory_titles",
            lambda *a, **kw: ["Already saved A"],
        )
        # Pretend `claude` is on PATH.
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/claude" if name == "claude" else None)

        # Seed a transcript file.
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps({"type": "user", "message": {"content": "Help me refactor"}}) + "\n"
        )

        self._seed_session(tmp_path, "sess1", 5)

        captured: dict = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs
                self.pid = 12345

        monkeypatch.setattr(subprocess, "Popen", FakePopen)

        ns = type("Args", (), {
            "session_id": "sess1",
            "transcript_path": str(transcript),
        })()
        rc = cap.cmd_capture_extract(ns)

        assert rc == 0
        assert "cmd" in captured, "Popen was never called"
        cmd = captured["cmd"]
        assert cmd[0] == "claude"
        assert cmd[1] == "-p"
        prompt = cmd[2]
        # Prompt must contain buffer entries, transcript context, recent titles.
        assert "src/mod1.py" in prompt
        assert "src/mod5.py" in prompt
        assert "Help me refactor" in prompt
        assert "Already saved A" in prompt
        assert "PROCESSED_THROUGH_SEQ=" in prompt
        # Detached invocation hygiene.
        assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
        assert captured["kwargs"]["start_new_session"] is True

    def test_no_buffer_means_no_spawn(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/claude")

        called: dict = {"count": 0}

        def boom(*a, **kw):
            called["count"] += 1
            raise AssertionError("Popen should not be called")

        monkeypatch.setattr(subprocess, "Popen", boom)
        ns = type("Args", (), {"session_id": "no-buffer", "transcript_path": None})()
        assert cap.cmd_capture_extract(ns) == 0
        assert called["count"] == 0

    def test_cursor_skips_already_processed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(cap, "_fetch_recent_memory_titles", lambda *a, **kw: [])
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/claude")

        self._seed_session(tmp_path, "sess2", 5)
        # Mark seq 3 as processed.
        cap._write_cursor("sess2", 3)

        captured: dict = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured["prompt"] = cmd[2]

        monkeypatch.setattr(subprocess, "Popen", FakePopen)

        ns = type("Args", (), {"session_id": "sess2", "transcript_path": None})()
        cap.cmd_capture_extract(ns)
        assert "src/mod3.py" not in captured.get("prompt", ""), "should not include seq=3 (already processed)"
        assert "src/mod4.py" in captured["prompt"]
        assert "src/mod5.py" in captured["prompt"]

    def test_missing_claude_binary_logs_and_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(cap, "_fetch_recent_memory_titles", lambda *a, **kw: [])
        monkeypatch.setattr(shutil, "which", lambda name: None)

        # subprocess.Popen must not be invoked.
        def boom(*a, **kw):
            raise AssertionError("Popen called even though claude is missing")

        monkeypatch.setattr(subprocess, "Popen", boom)

        self._seed_session(tmp_path, "sess3", 3)
        ns = type("Args", (), {"session_id": "sess3", "transcript_path": None})()
        assert cap.cmd_capture_extract(ns) == 0

        errors_log = cap._errors_log("sess3")
        assert errors_log.exists()
        assert "claude binary not found" in errors_log.read_text()

    def test_concurrent_invocation_skipped_via_flock(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(cap, "_fetch_recent_memory_titles", lambda *a, **kw: [])
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/claude")

        self._seed_session(tmp_path, "sess4", 3)

        spawn_calls: list = []

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                spawn_calls.append(cmd)

        monkeypatch.setattr(subprocess, "Popen", FakePopen)

        # Hold the lock manually, then call cmd_capture_extract.
        import fcntl
        lock = cap._lock_path("sess4")
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ns = type("Args", (), {"session_id": "sess4", "transcript_path": None})()
            assert cap.cmd_capture_extract(ns) == 0
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        # Popen must NOT have been called: the second invocation no-oped.
        assert spawn_calls == []
        errors = cap._errors_log("sess4").read_text()
        assert "another instance holds the lock" in errors

    def test_previous_processed_marker_advances_cursor(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(cap, "_fetch_recent_memory_titles", lambda *a, **kw: [])
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/claude")

        self._seed_session(tmp_path, "sess5", 5)
        # Pretend a previous detached run wrote a marker into extract.log.
        elog = cap._extract_log("sess5")
        elog.parent.mkdir(parents=True, exist_ok=True)
        elog.write_text("memory saved\nPROCESSED_THROUGH_SEQ=3\n")

        class FakePopen:
            def __init__(self, *a, **kw):
                pass

        monkeypatch.setattr(subprocess, "Popen", FakePopen)

        ns = type("Args", (), {"session_id": "sess5", "transcript_path": None})()
        cap.cmd_capture_extract(ns)
        assert cap._read_cursor("sess5") == 3

    def test_no_session_id_noops(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ns = type("Args", (), {"session_id": "", "transcript_path": None})()
        assert cap.cmd_capture_extract(ns) == 0

    def test_dispatcher_routes_extract(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ns = type("Args", (), {"session_id": "", "transcript_path": None})()
        assert cap.cmd_capture(ns) == 0


# ── Hook script template tests ────────────────────────────────────


def _render(template: str) -> str:
    return template.format(
        server_url="http://localhost:8765",
        api_key="test-key",
        ensure_server_bash=_render_ensure_server_bash(),
    )


def _hook_env(env: dict) -> dict:
    """Test helper: bake LORE_NO_AUTOSTART so the rendered ensure-server
    helper short-circuits without spawning a background `lore serve`.
    Tests that want to exercise the spawn path explicitly should clear it."""
    env.setdefault("LORE_NO_AUTOSTART", "true")
    return env


class TestHookTemplates:
    def test_tool_template_renders_to_valid_bash(self, tmp_path):
        rendered = _render(LORE_CAPTURE_TOOL_HOOK_SCRIPT)
        path = tmp_path / "tool.sh"
        path.write_text(rendered)
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_stop_template_renders_to_valid_bash(self, tmp_path):
        rendered = _render(LORE_CAPTURE_STOP_HOOK_SCRIPT)
        path = tmp_path / "stop.sh"
        path.write_text(rendered)
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_tool_hook_appends_buffer_line(self, tmp_path):
        rendered = _render(LORE_CAPTURE_TOOL_HOOK_SCRIPT)
        hook_path = tmp_path / "lore-capture-tool.sh"
        hook_path.write_text(rendered)
        hook_path.chmod(0o755)

        env = _hook_env(os.environ.copy())
        env["HOME"] = str(tmp_path)
        env["LORE_CAPTURE_N"] = "999"  # don't try to spawn capture-extract

        payload = json.dumps({
            "session_id": "hooktest",
            "tool_name": "Edit",
            "tool_input": {"file": "x.py", "edit": "y" * 300},  # forces truncation
            "tool_response": {"ok": True},
            "transcript_path": "",
        })
        result = subprocess.run(
            ["bash", str(hook_path)],
            input=payload, capture_output=True, text=True, env=env, timeout=10,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        buf = tmp_path / ".lore" / "sessions" / "hooktest" / "buffer.jsonl"
        assert buf.exists(), f"buffer.jsonl missing under {tmp_path}"
        line = json.loads(buf.read_text().strip())
        assert line["seq"] == 1
        assert line["tool"] == "Edit"
        assert "…" in line["input_summary"], "long input should be truncated with ellipsis"

    def test_tool_hook_skips_mcp_lore_recursion(self, tmp_path):
        rendered = _render(LORE_CAPTURE_TOOL_HOOK_SCRIPT)
        hook_path = tmp_path / "lore-capture-tool.sh"
        hook_path.write_text(rendered)
        hook_path.chmod(0o755)
        env = _hook_env(os.environ.copy())
        env["HOME"] = str(tmp_path)

        payload = json.dumps({
            "session_id": "hooktest",
            "tool_name": "mcp__lore__remember",
            "tool_input": {}, "tool_response": {},
        })
        result = subprocess.run(
            ["bash", str(hook_path)],
            input=payload, capture_output=True, text=True, env=env, timeout=10,
        )
        assert result.returncode == 0
        # Buffer must not have been created at all.
        buf = tmp_path / ".lore" / "sessions" / "hooktest" / "buffer.jsonl"
        assert not buf.exists()

    def test_tool_hook_honors_lore_auto_save_false(self, tmp_path):
        rendered = _render(LORE_CAPTURE_TOOL_HOOK_SCRIPT)
        hook_path = tmp_path / "lore-capture-tool.sh"
        hook_path.write_text(rendered)
        hook_path.chmod(0o755)
        env = _hook_env(os.environ.copy())
        env["HOME"] = str(tmp_path)
        env["LORE_AUTO_SAVE"] = "false"

        payload = json.dumps({
            "session_id": "hooktest",
            "tool_name": "Edit",
            "tool_input": {}, "tool_response": {},
        })
        result = subprocess.run(
            ["bash", str(hook_path)],
            input=payload, capture_output=True, text=True, env=env, timeout=10,
        )
        assert result.returncode == 0
        buf = tmp_path / ".lore" / "sessions" / "hooktest" / "buffer.jsonl"
        assert not buf.exists()

    def test_tool_hook_skip_list_filters_read(self, tmp_path):
        rendered = _render(LORE_CAPTURE_TOOL_HOOK_SCRIPT)
        hook_path = tmp_path / "lore-capture-tool.sh"
        hook_path.write_text(rendered)
        hook_path.chmod(0o755)
        env = _hook_env(os.environ.copy())
        env["HOME"] = str(tmp_path)
        # Default skip list includes Read.
        payload = json.dumps({
            "session_id": "hooktest",
            "tool_name": "Read",
            "tool_input": {}, "tool_response": {},
        })
        result = subprocess.run(
            ["bash", str(hook_path)],
            input=payload, capture_output=True, text=True, env=env, timeout=10,
        )
        assert result.returncode == 0
        buf = tmp_path / ".lore" / "sessions" / "hooktest" / "buffer.jsonl"
        assert not buf.exists()
