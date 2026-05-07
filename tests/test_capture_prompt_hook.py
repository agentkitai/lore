"""Phase 6G — UserPromptSubmit hook (``hooks/lore-capture-prompt.sh``) tests.

Coverage:

* Sanity: the script exists at ``hooks/lore-capture-prompt.sh`` and is
  executable.
* Smoke: piping a fake UserPromptSubmit JSON event into the hook with
  ``LORE_HOME=tmp_path`` produces a single ``buffer.jsonl`` line with
  ``kind="prompt"``, the right session id directory, and the next
  available ``seq``.
* ``<private>`` end-to-end: a balanced ``<private>SECRET</private>``
  block never reaches the buffer; the surrounding text does.
* Master kill switch: ``LORE_AUTO_SAVE=false`` short-circuits the hook
  before it touches disk.
* Empty / whitespace-only prompts after stripping no-op cleanly.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

# Hook lives at <repo-root>/hooks/lore-capture-prompt.sh.
HOOK_PATH = (
    Path(__file__).resolve().parent.parent
    / "hooks"
    / "lore-capture-prompt.sh"
)


def _run_hook(event: dict, lore_home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LORE_HOME"] = str(lore_home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def _read_buffer(lore_home: Path, sid: str) -> list[dict]:
    safe = sid  # Tests use safe ids.
    path = lore_home / "sessions" / safe / "buffer.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── Sanity ────────────────────────────────────────────────────────────


class TestHookFileSanity:
    def test_hook_exists(self):
        assert HOOK_PATH.exists(), f"hook missing: {HOOK_PATH}"

    def test_hook_is_executable(self):
        mode = HOOK_PATH.stat().st_mode
        assert mode & stat.S_IXUSR, "hook is not user-executable"

    def test_hook_passes_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(HOOK_PATH)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr


# ── Smoke ─────────────────────────────────────────────────────────────


class TestSmoke:
    def test_basic_prompt_appended(self, tmp_path):
        result = _run_hook(
            {"session_id": "sid-1", "prompt": "hello there"},
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        entries = _read_buffer(tmp_path, "sid-1")
        assert len(entries) == 1
        e = entries[0]
        assert e["kind"] == "prompt"
        assert e["text"] == "hello there"
        assert e["seq"] == 1
        assert "ts" in e

    def test_seq_increments_against_existing_buffer(self, tmp_path):
        # Pre-seed a tool entry at seq=5 so the next prompt should be 6.
        sd = tmp_path / "sessions" / "sid-2"
        sd.mkdir(parents=True)
        with (sd / "buffer.jsonl").open("w", encoding="utf-8") as f:
            f.write(json.dumps({"seq": 5, "kind": "tool", "tool": "Edit"}) + "\n")
        _run_hook({"session_id": "sid-2", "prompt": "next"}, tmp_path)
        entries = _read_buffer(tmp_path, "sid-2")
        assert len(entries) == 2
        assert entries[1]["seq"] == 6
        assert entries[1]["kind"] == "prompt"

    def test_no_session_id_noops(self, tmp_path):
        result = _run_hook({"prompt": "no sid"}, tmp_path)
        assert result.returncode == 0
        # Nothing should have been created.
        assert not (tmp_path / "sessions").exists()


# ── <private> end-to-end ─────────────────────────────────────────────


class TestPrivateStripping:
    def test_balanced_private_block_stripped(self, tmp_path):
        _run_hook(
            {
                "session_id": "sid-priv",
                "prompt": "<private>SECRET</private> debug CORS",
            },
            tmp_path,
        )
        entries = _read_buffer(tmp_path, "sid-priv")
        assert len(entries) == 1
        assert "SECRET" not in entries[0]["text"]
        assert "debug CORS" in entries[0]["text"]

    def test_unbalanced_private_strips_to_eos(self, tmp_path):
        _run_hook(
            {
                "session_id": "sid-priv2",
                "prompt": "before <private>oops never closed",
            },
            tmp_path,
        )
        entries = _read_buffer(tmp_path, "sid-priv2")
        assert len(entries) == 1
        assert "oops" not in entries[0]["text"]
        # Surviving prefix.
        assert entries[0]["text"].startswith("before")

    def test_empty_after_strip_noops(self, tmp_path):
        _run_hook(
            {
                "session_id": "sid-empty",
                "prompt": "<private>nothing else</private>",
            },
            tmp_path,
        )
        # The session dir exists (we mkdir'd before checking text), but
        # no buffer.jsonl line should have been written. Reading via the
        # helper returns [] either way.
        entries = _read_buffer(tmp_path, "sid-empty")
        assert entries == []


# ── Master kill switch ────────────────────────────────────────────────


class TestKillSwitch:
    def test_lore_auto_save_false_skips_hook(self, tmp_path):
        result = _run_hook(
            {"session_id": "sid-off", "prompt": "would be captured"},
            tmp_path,
            extra_env={"LORE_AUTO_SAVE": "false"},
        )
        assert result.returncode == 0
        # No session dir created at all.
        assert not (tmp_path / "sessions" / "sid-off").exists()


# ── Truncation ────────────────────────────────────────────────────────


class TestTruncation:
    def test_long_prompt_truncated_to_max_bytes(self, tmp_path):
        big = "A" * 20000
        _run_hook(
            {"session_id": "sid-long", "prompt": big},
            tmp_path,
            extra_env={"LORE_PROMPT_MAX_BYTES": "100"},
        )
        entries = _read_buffer(tmp_path, "sid-long")
        assert len(entries) == 1
        # Capped at 100 bytes (ASCII so == chars).
        assert len(entries[0]["text"].encode("utf-8")) <= 100
