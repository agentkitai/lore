"""Lazy-server tests: ``--idle-timeout`` flag + ``_lore_ensure_server`` helper.

Two layers:

* **Unit:** ``cmd_serve`` parses ``--idle-timeout`` and pushes it into
  ``LORE_IDLE_TIMEOUT`` before invoking uvicorn. ``LastRequestTracker``
  middleware updates the sentinel on every request. The bash preamble
  rendered into hook templates passes ``bash -n`` and short-circuits when
  ``/health`` returns 200 (no spawn).
* **Integration:** ``idle_watcher_loop`` exits via the injected
  ``exit_fn`` once the monotonic clock advances past the timeout. The
  end-to-end FastAPI client path: hit ``/health``, time-travel monotonic
  clock past the timeout, trigger one watcher tick, assert the mock
  ``exit_fn`` was called.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

# ── Unit: cmd_serve idle-timeout plumbing ─────────────────────────


class TestCmdServeIdleTimeout:
    def _make_args(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 18765,
        require_auth: bool = False,
        idle_timeout: int | None = None,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            host=host,
            port=port,
            require_auth=require_auth,
            idle_timeout=idle_timeout,
        )

    def test_idle_timeout_none_defaults_to_zero_in_env(self, monkeypatch):
        """With no flag and no env var, LORE_IDLE_TIMEOUT is set to '0'."""
        from lore.cli.commands.server import cmd_serve

        monkeypatch.delenv("LORE_IDLE_TIMEOUT", raising=False)
        called = {}

        class _FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                called["env"] = os.environ.get("LORE_IDLE_TIMEOUT")

        monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn)
        cmd_serve(self._make_args(idle_timeout=None))
        assert called["env"] == "0"

    def test_idle_timeout_flag_sets_env_var(self, monkeypatch):
        """--idle-timeout 3600 propagates into LORE_IDLE_TIMEOUT before uvicorn."""
        from lore.cli.commands.server import cmd_serve

        monkeypatch.delenv("LORE_IDLE_TIMEOUT", raising=False)
        called = {}

        class _FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                called["env"] = os.environ.get("LORE_IDLE_TIMEOUT")
                called["host"] = host
                called["port"] = port

        monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn)
        cmd_serve(self._make_args(idle_timeout=3600))
        assert called["env"] == "3600"
        assert called["host"] == "127.0.0.1"
        assert called["port"] == 18765

    def test_idle_timeout_env_fallback(self, monkeypatch):
        """If --idle-timeout omitted, LORE_IDLE_TIMEOUT env var is honored."""
        from lore.cli.commands.server import cmd_serve

        monkeypatch.setenv("LORE_IDLE_TIMEOUT", "1800")
        called = {}

        class _FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                called["env"] = os.environ.get("LORE_IDLE_TIMEOUT")

        monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn)
        cmd_serve(self._make_args(idle_timeout=None))
        assert called["env"] == "1800"

    def test_negative_idle_timeout_clamped_to_zero(self, monkeypatch):
        from lore.cli.commands.server import cmd_serve

        called = {}

        class _FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                called["env"] = os.environ.get("LORE_IDLE_TIMEOUT")

        monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn)
        cmd_serve(self._make_args(idle_timeout=-5))
        assert called["env"] == "0"

    def test_parser_registers_idle_timeout_flag(self):
        """The CLI parser exposes --idle-timeout with the expected attr name."""
        from lore.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["serve", "--idle-timeout", "300"])
        assert args.idle_timeout == 300
        # Default is None so cmd_serve falls back to env / 0.
        args = parser.parse_args(["serve"])
        assert args.idle_timeout is None


# ── Unit: idle module internals ───────────────────────────────────


class TestIdleModule:
    def test_get_configured_timeout_default_zero(self, monkeypatch):
        from lore.server.idle import get_configured_timeout

        monkeypatch.delenv("LORE_IDLE_TIMEOUT", raising=False)
        assert get_configured_timeout() == 0

    def test_get_configured_timeout_parses(self, monkeypatch):
        from lore.server.idle import get_configured_timeout

        monkeypatch.setenv("LORE_IDLE_TIMEOUT", "1234")
        assert get_configured_timeout() == 1234

    def test_get_configured_timeout_invalid_returns_zero(self, monkeypatch):
        from lore.server.idle import get_configured_timeout

        monkeypatch.setenv("LORE_IDLE_TIMEOUT", "not-a-number")
        assert get_configured_timeout() == 0

    def test_touch_updates_sentinel(self):
        """`_touch()` should advance the sentinel forward."""
        from lore.server import idle

        idle._touch()
        first = idle._last_request_at
        # Patch the monotonic symbol on the idle module specifically so we
        # don't perturb asyncio's own time source.
        with patch.object(idle, "_monotonic", return_value=first + 5):
            idle._touch()
        assert idle._last_request_at == first + 5

    @pytest.mark.asyncio
    async def test_idle_watcher_loop_exits_when_idle(self):
        """Watcher fires exit_fn once the elapsed time exceeds the timeout."""
        from lore.server import idle

        # Snap sentinel forward "in the past" relative to the next monotonic
        # reading inside the loop, simulating a stale server.
        idle._touch()
        anchor = idle._last_request_at

        exits: list[int] = []

        def fake_exit(code: int) -> None:
            exits.append(code)

        # Patch ONLY the idle module's view of the monotonic clock; the
        # asyncio event loop keeps its own real clock so `await
        # asyncio.sleep(0.01)` still returns. The patched monotonic
        # advances 1000s past _last_request_at, well past the 10s limit.
        with patch.object(idle, "_monotonic", return_value=anchor + 1000):
            await idle.idle_watcher_loop(
                idle_timeout=10, check_interval=0.01, exit_fn=fake_exit
            )

        assert exits == [0]

    @pytest.mark.asyncio
    async def test_idle_watcher_loop_keeps_running_when_active(self):
        """If the sentinel is fresh, the watcher should not call exit_fn."""
        from lore.server import idle

        idle._touch()
        anchor = idle._last_request_at
        exits: list[int] = []

        def fake_exit(code: int) -> None:
            exits.append(code)

        async def runner() -> None:
            # Patch only idle.time.monotonic; asyncio's clock stays real.
            with patch.object(idle, "_monotonic", return_value=anchor + 1):
                await idle.idle_watcher_loop(
                    idle_timeout=60, check_interval=0.01, exit_fn=fake_exit
                )

        task = asyncio.create_task(runner())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert exits == []  # never tripped


# ── Unit: LastRequestTracker middleware ───────────────────────────


class TestLastRequestTracker:
    def test_middleware_touches_sentinel_per_request(self, monkeypatch):
        """A request through the FastAPI test client should bump the sentinel."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from lore.server import idle

        # Force the lazy-server middleware install (it's gated on >0 timeout
        # at app-creation time in the real app).
        monkeypatch.setenv("LORE_IDLE_TIMEOUT", "60")

        app = FastAPI()
        app.add_middleware(idle.LastRequestTracker)

        @app.get("/health")
        async def _health():
            return {"status": "ok"}

        # Snap the sentinel back, then advance the monotonic clock by 5s on
        # the next read so the middleware sees a clear delta.
        idle.reset_for_tests()
        baseline = idle._last_request_at

        with patch.object(idle, "_monotonic", return_value=baseline + 5):
            with TestClient(app) as client:
                r = client.get("/health")
        assert r.status_code == 200
        # Middleware ran _touch() during the request — sentinel should have moved.
        assert idle._last_request_at >= baseline + 5

    def test_install_middleware_skips_tracker_when_disabled(self, monkeypatch):
        """When LORE_IDLE_TIMEOUT=0, install_middleware does not register the tracker."""
        from fastapi import FastAPI

        from lore.server.middleware import install_middleware

        monkeypatch.delenv("LORE_IDLE_TIMEOUT", raising=False)
        app = FastAPI()
        install_middleware(app)
        # The middleware list lives on app.user_middleware; check no
        # LastRequestTracker entry.
        assert not any(
            "LastRequestTracker" in repr(mw) for mw in app.user_middleware
        )

    def test_install_middleware_registers_tracker_when_enabled(self, monkeypatch):
        from fastapi import FastAPI

        from lore.server.middleware import install_middleware

        monkeypatch.setenv("LORE_IDLE_TIMEOUT", "60")
        app = FastAPI()
        install_middleware(app)
        assert any(
            "LastRequestTracker" in repr(mw) for mw in app.user_middleware
        )


# ── Unit: rendered ensure-server bash preamble ────────────────────


class TestEnsureServerBash:
    def test_render_produces_plain_bash(self):
        """The pre-rendered helper has no unresolved `{` placeholders."""
        from lore.setup import _render_ensure_server_bash

        rendered = _render_ensure_server_bash()
        # No leftover format placeholders; doubled braces must have been
        # collapsed during the format() pass.
        assert "{{" not in rendered
        assert "}}" not in rendered
        # Sanity: the helper definition is present.
        assert "_lore_ensure_server() {" in rendered
        assert "lore serve --port 8765 --idle-timeout" in rendered

    def test_rendered_helper_passes_bash_n(self, tmp_path):
        """`bash -n` validates the rendered helper as a standalone script."""
        from lore.setup import _render_ensure_server_bash

        script = "#!/usr/bin/env bash\nset -e\n" + _render_ensure_server_bash()
        p = tmp_path / "ensure.sh"
        p.write_text(script)
        result = subprocess.run(
            ["bash", "-n", str(p)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_rendered_helper_no_spawn_when_health_ok(self, tmp_path):
        """If `curl /health` returns success, no `lore serve` is spawned."""
        from lore.setup import _render_ensure_server_bash

        # Stub curl to always succeed (rc=0). Stub `lore` and `nohup` to a
        # tracer that records its args. The tracer should NEVER fire.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "curl").write_text("#!/usr/bin/env bash\nexit 0\n")
        (bin_dir / "curl").chmod(0o755)
        marker = tmp_path / "spawn-ran"
        (bin_dir / "nohup").write_text(
            f"#!/usr/bin/env bash\necho \"$@\" > {marker}\nexit 0\n"
        )
        (bin_dir / "nohup").chmod(0o755)
        (bin_dir / "lore").write_text("#!/usr/bin/env bash\nexit 0\n")
        (bin_dir / "lore").chmod(0o755)

        script_body = (
            "#!/usr/bin/env bash\nset +e\n"
            + _render_ensure_server_bash()
            + "\n_lore_ensure_server\necho rc=$?\n"
        )
        p = tmp_path / "ensure.sh"
        p.write_text(script_body)
        p.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["LORE_API_URL"] = "http://127.0.0.1:65535"  # ignored by stub curl
        result = subprocess.run(
            ["bash", str(p)], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0
        assert "rc=0" in result.stdout
        assert not marker.exists(), "nohup should not have been called when /health succeeds"

    def test_rendered_helper_spawns_when_health_fails(self, tmp_path):
        """If curl fails, the helper spawns `nohup lore serve --idle-timeout ...`."""
        from lore.setup import _render_ensure_server_bash

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        # First curl fails (server not up); the polling-loop curls also fail
        # so the helper falls through to return 1. We just want to assert the
        # spawn cmdline was constructed correctly.
        (bin_dir / "curl").write_text("#!/usr/bin/env bash\nexit 22\n")
        (bin_dir / "curl").chmod(0o755)
        marker = tmp_path / "spawn-args"
        (bin_dir / "nohup").write_text(
            f"#!/usr/bin/env bash\necho \"$@\" > {marker}\nexit 0\n"
        )
        (bin_dir / "nohup").chmod(0o755)
        (bin_dir / "lore").write_text("#!/usr/bin/env bash\nexit 0\n")
        (bin_dir / "lore").chmod(0o755)
        # Make `sleep` a no-op so the polling loop runs fast.
        (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n")
        (bin_dir / "sleep").chmod(0o755)

        script_body = (
            "#!/usr/bin/env bash\nset +e\n"
            + _render_ensure_server_bash()
            + "\n_lore_ensure_server\necho rc=$?\n"
        )
        p = tmp_path / "ensure.sh"
        p.write_text(script_body)
        p.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["LORE_IDLE_TIMEOUT"] = "1800"
        result = subprocess.run(
            ["bash", str(p)], capture_output=True, text=True, env=env, timeout=10
        )
        # rc=1 because curl never succeeds in the polling loop either.
        assert "rc=1" in result.stdout
        assert marker.exists(), "spawn args marker should be written"
        spawned = marker.read_text()
        assert "lore" in spawned
        assert "serve" in spawned
        assert "--port" in spawned and "8765" in spawned
        assert "--idle-timeout" in spawned and "1800" in spawned

    def test_rendered_helper_skips_spawn_when_no_autostart(self, tmp_path):
        """LORE_NO_AUTOSTART=true short-circuits to return 1 without spawning."""
        from lore.setup import _render_ensure_server_bash

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "curl").write_text("#!/usr/bin/env bash\nexit 22\n")
        (bin_dir / "curl").chmod(0o755)
        marker = tmp_path / "spawn-ran"
        (bin_dir / "nohup").write_text(
            f"#!/usr/bin/env bash\ntouch {marker}\nexit 0\n"
        )
        (bin_dir / "nohup").chmod(0o755)
        (bin_dir / "lore").write_text("#!/usr/bin/env bash\nexit 0\n")
        (bin_dir / "lore").chmod(0o755)

        script_body = (
            "#!/usr/bin/env bash\nset +e\n"
            + _render_ensure_server_bash()
            + "\n_lore_ensure_server\necho rc=$?\n"
        )
        p = tmp_path / "ensure.sh"
        p.write_text(script_body)
        p.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["LORE_NO_AUTOSTART"] = "true"
        result = subprocess.run(
            ["bash", str(p)], capture_output=True, text=True, env=env
        )
        assert "rc=1" in result.stdout
        assert not marker.exists()


# ── Unit: hooks render with ensure_server_bash placeholder ────────


class TestHookTemplatesEmbed:
    def test_all_four_hooks_render(self, tmp_path):
        """Every hook template renders + survives bash/python syntax check."""
        from lore.setup import (
            CLAUDE_CODE_HOOK_SCRIPT,
            LORE_CAPTURE_STOP_HOOK_SCRIPT,
            LORE_CAPTURE_TOOL_HOOK_SCRIPT,
            LORE_DREAM_TRIGGER_HOOK_SCRIPT,
            _render_ensure_server_bash,
        )

        ensure = _render_ensure_server_bash()
        retrieve = CLAUDE_CODE_HOOK_SCRIPT.format(
            server_url="http://127.0.0.1:8765", api_key="k",
        )
        capture_tool = LORE_CAPTURE_TOOL_HOOK_SCRIPT.format(
            server_url="http://127.0.0.1:8765",
            api_key="k",
            ensure_server_bash=ensure,
        )
        capture_stop = LORE_CAPTURE_STOP_HOOK_SCRIPT.format(
            server_url="http://127.0.0.1:8765",
            api_key="k",
            ensure_server_bash=ensure,
        )
        dream_trigger = LORE_DREAM_TRIGGER_HOOK_SCRIPT.format(
            ensure_server_bash=ensure,
        )

        # Bash hooks: `bash -n` syntax check.
        for name, content in (
            ("capture-tool.sh", capture_tool),
            ("capture-stop.sh", capture_stop),
            ("dream-trigger.sh", dream_trigger),
        ):
            p = tmp_path / name
            p.write_text(content)
            r = subprocess.run(
                ["bash", "-n", str(p)], capture_output=True, text=True
            )
            assert r.returncode == 0, f"{name} bash -n failed: {r.stderr}"
            # Each contains the helper invocation.
            assert "_lore_ensure_server" in content

        # Python hook: py_compile.
        p = tmp_path / "retrieve.py"
        p.write_text(retrieve)
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(p)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        # The python hook calls subprocess.run on the embedded bash.
        assert "_lore_ensure_server" in retrieve
        assert "subprocess" in retrieve


# ── Integration: setup_claude_code installs all four hooks ────────


class TestSetupClaudeCodeWithEnsureServer:
    def test_setup_writes_executable_hooks_with_helper(self, tmp_path, monkeypatch):
        from lore.setup import setup_claude_code

        monkeypatch.setenv("HOME", str(tmp_path))
        # cmd_serve writes ~/.lore/key.txt; our test uses a stubbed key.
        setup_claude_code(
            server_url="http://127.0.0.1:8765",
            api_key="test-key",
        )

        hooks_dir = tmp_path / ".claude" / "hooks"
        retrieve = hooks_dir / "lore-retrieve.sh"
        capture_tool = hooks_dir / "lore-capture-tool.sh"
        capture_stop = hooks_dir / "lore-capture-stop.sh"
        dream_trigger = hooks_dir / "lore-dream-trigger.sh"

        for hook in (retrieve, capture_tool, capture_stop, dream_trigger):
            assert hook.exists(), f"missing {hook}"
            assert os.access(hook, os.X_OK), f"not executable: {hook}"
            content = hook.read_text()
            assert "_lore_ensure_server" in content, (
                f"ensure-server helper missing from {hook}"
            )

        # Bash syntax-check the three bash hooks (skip the python one — we
        # already exercise it with py_compile above).
        for hook in (capture_tool, capture_stop, dream_trigger):
            r = subprocess.run(
                ["bash", "-n", str(hook)], capture_output=True, text=True
            )
            assert r.returncode == 0, f"{hook}: {r.stderr}"
