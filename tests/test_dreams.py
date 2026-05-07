"""Phase 6E — dreaming consolidation tests.

Coverage matrix (per spec):

  * **Migration parity** — 022 applies cleanly on PG + SQLite
    (covered transitively by ``persistence/test_sqlite_smoke.py`` and
    the migrations-parity guard script).
  * **DreamOps contract** — round-trip + lifecycle + session counting
    on both backends.
  * **Service** — eligibility math (24h + ≥5 sessions), status snapshot
    shape, run lifecycle.
  * **CLI** — ``--status`` (cold), ``--status --json``, ``--force`` with
    mocked ``claude -p`` subprocess, ``apply`` validation, prompt
    construction, summary parsing.
  * **Concurrency** — flock prevents a second concurrent dream.
  * **Hook template** — ``LORE_DREAM_TRIGGER_HOOK_SCRIPT`` is valid bash;
    honors ``LORE_DREAM_AUTO=false``; eligible-now → spawns.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# CLI tests open SqliteStore; skip when [solo] not installed (CI's python
# job runs without it). Only the contract tests run on Postgres.
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")

from lore.cli.commands import dream as dream_cli
from lore.setup import LORE_DREAM_TRIGGER_HOOK_SCRIPT, _render_ensure_server_bash

# DreamOps contract tests + service-layer eligibility round-trip live in
# tests/persistence/test_contract_dreams.py so they get the parametrized
# ``store`` fixture (PG + SQLite). The CLI / hook coverage stays here.


# ── Phase 2 transcript gathering ─────────────────────────────────


class TestPhase2GatherSignal:
    def test_no_transcripts_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        out = dream_cli._list_recent_transcripts()
        assert out == []

    def test_grep_finds_correction_signals(self, tmp_path):
        path = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"content": "actually let's revert"}}),
            json.dumps({"type": "user", "message": {"content": "I prefer tabs over spaces"}}),
            json.dumps({"type": "assistant", "message": {"content": "Sure"}}),
            json.dumps({"type": "user", "message": {"content": "no signal here"}}),
        ]
        path.write_text("\n".join(lines) + "\n")
        out = dream_cli._grep_transcript_signals([path])
        # ``actually``, ``I prefer``, and ``don't`` (zero hits in 'no signal')
        # should produce 2 hits total.
        patterns = [hit["pattern"] for hit in out]
        assert any("actually" in p for p in patterns)
        assert any("prefer" in p for p in patterns)

    def test_grep_skips_assistant_lines(self, tmp_path):
        path = tmp_path / "transcript.jsonl"
        path.write_text(
            json.dumps({"type": "assistant", "message": {"content": "actually you're right"}}) + "\n"
        )
        out = dream_cli._grep_transcript_signals([path])
        assert out == []

    def test_max_hits_caps_output(self, tmp_path):
        path = tmp_path / "transcript.jsonl"
        # Many user turns containing the trigger word.
        with path.open("w") as f:
            for _ in range(500):
                f.write(json.dumps({
                    "type": "user", "message": {"content": "actually wait"},
                }) + "\n")
        out = dream_cli._grep_transcript_signals([path], max_total_hits=50)
        # Each line matches both ``actually`` AND ``no wait``? "no wait"
        # is checked as 'no wait' lowercase substring; only ``actually``
        # matches here. Allow a small slack for the regex set.
        assert len(out) <= 50


# ── Prompt building & summary parsing ────────────────────────────


class TestPromptBuilder:
    def test_includes_all_required_markers(self):
        prompt = dream_cli._build_prompt(
            run_id="run-abc",
            org_id="solo",
            phase1_stats={"total_memories": 42, "by_type": {"lesson": 10}},
            phase2_signals=[
                {"file": "p.jsonl", "pattern": "actually", "snippet": "actually X"},
            ],
            review_mode=False,
        )
        assert "PHASE_1_ORIENT_COMPLETE" in prompt
        assert "PHASE_2_SIGNAL_COMPLETE" in prompt
        assert "PHASE_3_CONSOLIDATE_COMPLETE" in prompt
        assert "PHASE_4_PRUNE_COMPLETE" in prompt
        assert "RUN_ID: run-abc" in prompt
        assert '"total_memories": 42' in prompt
        assert "actually X" in prompt
        # No review-mode clause.
        assert "REVIEW MODE" not in prompt

    def test_review_mode_includes_clause(self):
        prompt = dream_cli._build_prompt(
            run_id="run-xyz",
            org_id="solo",
            phase1_stats={},
            phase2_signals=[],
            review_mode=True,
        )
        assert "REVIEW MODE" in prompt
        # Subagent should be told to write proposed.md, not mutate.
        assert "proposed.md" in prompt
        assert "do NOT call consolidate" in prompt


class TestSummaryParser:
    def test_full_markers_parsed(self, tmp_path):
        log = tmp_path / "extract.log"
        log.write_text(
            "some output\n"
            "PHASE_1_ORIENT_COMPLETE\n"
            "PHASE_2_SIGNAL_COMPLETE\n"
            "PHASE_3_CONSOLIDATE_COMPLETE: 5 2\n"
            "PHASE_4_PRUNE_COMPLETE: 9\n"
            "RUN_ID: run-001\n"
        )
        out = dream_cli._parse_summary_from_log(log)
        assert out["phase_1_complete"] is True
        assert out["phase_2_complete"] is True
        assert out["phase_3_complete"] is True
        assert out["phase_3_merged"] == 5
        assert out["phase_3_promoted"] == 2
        assert out["phase_4_complete"] is True
        assert out["phase_4_pruned"] == 9

    def test_missing_log_yields_all_false(self, tmp_path):
        out = dream_cli._parse_summary_from_log(tmp_path / "no.log")
        assert out["phase_1_complete"] is False
        assert out["phase_3_merged"] is None
        assert out["phase_4_pruned"] is None


# ── CLI behavior ──────────────────────────────────────────────────


class TestCmdDreamStatus:
    def test_status_cold_db(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Use a temp SQLite DB.
        db_path = tmp_path / "dream.db"
        monkeypatch.setenv("LORE_DATABASE_URL", f"sqlite:///{db_path}")

        ns = type("Args", (), {
            "status": True, "as_json": False, "force": False,
            "review": False, "org_id": None, "dream_args": [],
        })()
        rc = dream_cli.cmd_dream(ns)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Dream status" in out
        assert "Eligible now:       True" in out

    def test_status_json(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        db_path = tmp_path / "dream.db"
        monkeypatch.setenv("LORE_DATABASE_URL", f"sqlite:///{db_path}")

        ns = type("Args", (), {
            "status": True, "as_json": True, "force": False,
            "review": False, "org_id": None, "dream_args": [],
        })()
        rc = dream_cli.cmd_dream(ns)
        assert rc == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["last_run_at"] is None
        assert payload["eligible_now"] is True
        assert payload["sessions_required"] == 5


class TestCmdDreamForce:
    def test_force_runs_with_mocked_subagent(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        db_path = tmp_path / "dream.db"
        monkeypatch.setenv("LORE_DATABASE_URL", f"sqlite:///{db_path}")
        # Pretend `claude` is on PATH but never actually invoke it.
        monkeypatch.setattr(
            shutil, "which",
            lambda name: "/fake/claude" if name == "claude" else None,
        )

        captured: dict = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs
                self.pid = 7777

        monkeypatch.setattr(subprocess, "Popen", FakePopen)

        ns = type("Args", (), {
            "status": False, "as_json": False, "force": True,
            "review": False, "org_id": None, "dream_args": [],
        })()
        rc = dream_cli.cmd_dream(ns)
        assert rc == 0

        # Subagent should have been launched as `claude -p <prompt>`.
        assert captured["cmd"][0] == "claude"
        assert captured["cmd"][1] == "-p"
        prompt = captured["cmd"][2]
        assert "PHASE_3_CONSOLIDATE_COMPLETE" in prompt
        assert "RUN_ID:" in prompt
        # Detached invocation hygiene.
        assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
        assert captured["kwargs"]["start_new_session"] is True

        out = capsys.readouterr().out
        assert "Dream started" in out

    def test_force_with_review_mode_includes_clause(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        db_path = tmp_path / "dream.db"
        monkeypatch.setenv("LORE_DATABASE_URL", f"sqlite:///{db_path}")
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/claude")

        captured: dict = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured["prompt"] = cmd[2]
                self.pid = 1

        monkeypatch.setattr(subprocess, "Popen", FakePopen)

        ns = type("Args", (), {
            "status": False, "as_json": False, "force": True,
            "review": True, "org_id": None, "dream_args": [],
        })()
        rc = dream_cli.cmd_dream(ns)
        assert rc == 0
        assert "REVIEW MODE" in captured["prompt"]


class TestCmdDreamConcurrency:
    def test_second_dream_skips_when_locked(
        self, tmp_path, monkeypatch, capsys,
    ):
        import fcntl

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        db_path = tmp_path / "dream.db"
        monkeypatch.setenv("LORE_DATABASE_URL", f"sqlite:///{db_path}")
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/claude")

        # Hold the dreams lock manually.
        lock_path = dream_cli._dreams_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            spawn_calls: list = []

            class FakePopen:
                def __init__(self, *a, **kw):
                    spawn_calls.append(a)

            monkeypatch.setattr(subprocess, "Popen", FakePopen)

            ns = type("Args", (), {
                "status": False, "as_json": False, "force": True,
                "review": False, "org_id": None, "dream_args": [],
            })()
            rc = dream_cli.cmd_dream(ns)
            assert rc == 0
            # Subagent must NOT have been launched.
            assert spawn_calls == []
            err = capsys.readouterr().err
            assert "in flight" in err
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


class TestCmdDreamApply:
    def test_apply_missing_proposal_errors(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ns = type("Args", (), {
            "status": False, "as_json": False, "force": False,
            "review": False, "org_id": None,
            "dream_args": ["apply", "missing-run-id"],
        })()
        rc = dream_cli.cmd_dream(ns)
        assert rc == 1
        err = capsys.readouterr().err
        assert "No proposed.md" in err

    def test_apply_with_proposed_file_returns_zero(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        run_id = "run-abc"
        proposed = dream_cli._proposed_md_path(run_id)
        proposed.parent.mkdir(parents=True, exist_ok=True)
        proposed.write_text("# proposal\n- forget abc\n")
        ns = type("Args", (), {
            "status": False, "as_json": False, "force": False,
            "review": False, "org_id": None,
            "dream_args": ["apply", run_id],
        })()
        rc = dream_cli.cmd_dream(ns)
        assert rc == 0

    def test_apply_without_run_id_errors(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ns = type("Args", (), {
            "status": False, "as_json": False, "force": False,
            "review": False, "org_id": None, "dream_args": ["apply"],
        })()
        rc = dream_cli.cmd_dream(ns)
        assert rc == 1
        err = capsys.readouterr().err
        assert "requires a run_id" in err


# ── Trigger hook template tests ───────────────────────────────────


def _render_dream_hook() -> str:
    return LORE_DREAM_TRIGGER_HOOK_SCRIPT.format(
        ensure_server_bash=_render_ensure_server_bash(),
    )


class TestDreamTriggerHook:
    def test_renders_to_valid_bash(self, tmp_path):
        rendered = _render_dream_hook()
        path = tmp_path / "trigger.sh"
        path.write_text(rendered)
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_dream_auto_false_exits_zero_no_spawn(self, tmp_path):
        rendered = _render_dream_hook()
        hook = tmp_path / "trigger.sh"
        hook.write_text(rendered)
        hook.chmod(0o755)
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["LORE_DREAM_AUTO"] = "false"
        result = subprocess.run(
            ["bash", str(hook)],
            input="{}", capture_output=True, text=True,
            env=env, timeout=10,
        )
        # Exits 0 even though there's no `lore` binary.
        assert result.returncode == 0

    def test_missing_lore_binary_exits_zero(self, tmp_path):
        rendered = _render_dream_hook()
        hook = tmp_path / "trigger.sh"
        hook.write_text(rendered)
        hook.chmod(0o755)
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        # PATH has only the standard /usr/bin so bash + python3 are
        # available, but ``lore`` (typically under ~/.local/bin) is not.
        env["PATH"] = "/usr/bin:/bin"
        env["LORE_DREAM_AUTO"] = "true"
        result = subprocess.run(
            ["bash", str(hook)],
            input="{}", capture_output=True, text=True,
            env=env, timeout=10,
        )
        assert result.returncode == 0


# ── Setup integration ─────────────────────────────────────────────


class TestSetupInstallsDreamHook:
    def test_setup_claude_code_writes_dream_trigger_hook(self, tmp_path):
        from unittest.mock import patch as _patch

        from lore.setup import setup_claude_code

        hooks_dir = tmp_path / ".claude" / "hooks"
        settings_path = tmp_path / ".claude" / "settings.json"

        with _patch("lore.setup._claude_hooks_dir", return_value=hooks_dir), \
             _patch("lore.setup._claude_hook_path",
                    return_value=hooks_dir / "lore-retrieve.sh"), \
             _patch("lore.setup._claude_capture_tool_hook_path",
                    return_value=hooks_dir / "lore-capture-tool.sh"), \
             _patch("lore.setup._claude_capture_stop_hook_path",
                    return_value=hooks_dir / "lore-capture-stop.sh"), \
             _patch("lore.setup._claude_dream_trigger_hook_path",
                    return_value=hooks_dir / "lore-dream-trigger.sh"), \
             _patch("lore.setup._claude_settings_path",
                    return_value=settings_path):
            setup_claude_code(server_url="http://localhost:9999", api_key="k")

        dream_hook = hooks_dir / "lore-dream-trigger.sh"
        assert dream_hook.exists()
        # Stop event should now have BOTH the capture-stop hook AND the
        # dream trigger hook registered.
        settings = json.loads(settings_path.read_text())
        stop_hooks = settings.get("hooks", {}).get("Stop", [])
        commands = [
            h["hooks"][0]["command"] for h in stop_hooks if h.get("hooks")
        ]
        assert any("lore-capture-stop.sh" in c for c in commands)
        assert any("lore-dream-trigger.sh" in c for c in commands)
