"""Tests for `lore mcp` self-discovery and lazy server start.

The MCP bridge is launched by Claude Code as `lore mcp` with no env block
in `~/.claude.json` by default. Without help it can't reach the local
`lore serve` because `LORE_API_URL` / `LORE_API_KEY` aren't set. These
tests pin the self-discovery contract:

* `_resolve_mcp_env` defaults `LORE_API_URL` and reads the API key from
  `~/.lore/key.txt` when env vars are missing.
* `_lazy_start_server` probes `/health`; if the server is down it spawns
  `lore serve` (unless `LORE_NO_AUTOSTART=true`) and polls until reachable.
* `cmd_mcp` wires both helpers together before delegating to
  `lore.mcp.server.run_server`.
"""

from __future__ import annotations

import argparse
import os
import sys
from unittest.mock import patch

import pytest


# ── _resolve_mcp_env ───────────────────────────────────────────────


class TestResolveMcpEnv:
    def test_defaults_api_url_when_unset(self, monkeypatch):
        from lore.cli.commands.server import _resolve_mcp_env

        monkeypatch.delenv("LORE_API_URL", raising=False)
        _resolve_mcp_env()
        assert os.environ["LORE_API_URL"] == "http://127.0.0.1:8765"

    def test_preserves_explicit_api_url(self, monkeypatch):
        from lore.cli.commands.server import _resolve_mcp_env

        monkeypatch.setenv("LORE_API_URL", "http://example.test:9000")
        _resolve_mcp_env()
        assert os.environ["LORE_API_URL"] == "http://example.test:9000"

    def test_reads_api_key_from_key_txt(self, monkeypatch, tmp_path):
        from lore.cli.commands import server as server_mod

        key_file = tmp_path / "key.txt"
        key_file.write_text("lore_sk_unit_test\n")
        monkeypatch.delenv("LORE_API_KEY", raising=False)
        monkeypatch.setattr(server_mod, "DEFAULT_KEY_PATH", str(key_file))

        server_mod._resolve_mcp_env()
        assert os.environ["LORE_API_KEY"] == "lore_sk_unit_test"

    def test_preserves_explicit_api_key(self, monkeypatch, tmp_path):
        from lore.cli.commands import server as server_mod

        key_file = tmp_path / "key.txt"
        key_file.write_text("from_file")
        monkeypatch.setenv("LORE_API_KEY", "from_env")
        monkeypatch.setattr(server_mod, "DEFAULT_KEY_PATH", str(key_file))

        server_mod._resolve_mcp_env()
        assert os.environ["LORE_API_KEY"] == "from_env"

    def test_missing_key_file_is_silent(self, monkeypatch, tmp_path):
        from lore.cli.commands import server as server_mod

        monkeypatch.delenv("LORE_API_KEY", raising=False)
        monkeypatch.setattr(
            server_mod, "DEFAULT_KEY_PATH", str(tmp_path / "no-such-file")
        )
        server_mod._resolve_mcp_env()
        assert "LORE_API_KEY" not in os.environ

    def test_blank_key_file_not_set(self, monkeypatch, tmp_path):
        from lore.cli.commands import server as server_mod

        key_file = tmp_path / "key.txt"
        key_file.write_text("   \n")
        monkeypatch.delenv("LORE_API_KEY", raising=False)
        monkeypatch.setattr(server_mod, "DEFAULT_KEY_PATH", str(key_file))

        server_mod._resolve_mcp_env()
        assert "LORE_API_KEY" not in os.environ


# ── _lazy_start_server ─────────────────────────────────────────────


class TestLazyStartServer:
    def test_no_spawn_when_health_ok(self, monkeypatch):
        from lore.cli.commands.server import _lazy_start_server

        spawn_calls = []
        ok = _lazy_start_server(
            "http://127.0.0.1:8765",
            health_probe=lambda: True,
            spawn_fn=lambda: spawn_calls.append(1),
            sleep_fn=lambda _s: None,
        )
        assert ok is True
        assert spawn_calls == []

    def test_no_spawn_when_autostart_disabled(self, monkeypatch):
        from lore.cli.commands.server import _lazy_start_server

        monkeypatch.setenv("LORE_NO_AUTOSTART", "true")
        spawn_calls = []
        ok = _lazy_start_server(
            "http://127.0.0.1:8765",
            health_probe=lambda: False,
            spawn_fn=lambda: spawn_calls.append(1),
            sleep_fn=lambda _s: None,
        )
        assert ok is False
        assert spawn_calls == []

    def test_spawn_then_health_recovers(self, monkeypatch):
        from lore.cli.commands.server import _lazy_start_server

        monkeypatch.delenv("LORE_NO_AUTOSTART", raising=False)
        # Probe sequence: down (initial), down, up.
        probes = iter([False, False, True])
        spawn_calls = []
        ok = _lazy_start_server(
            "http://127.0.0.1:8765",
            health_probe=lambda: next(probes),
            spawn_fn=lambda: spawn_calls.append(1),
            sleep_fn=lambda _s: None,
        )
        assert ok is True
        assert spawn_calls == [1]

    def test_spawn_but_never_recovers(self, monkeypatch):
        from lore.cli.commands.server import _lazy_start_server

        monkeypatch.delenv("LORE_NO_AUTOSTART", raising=False)
        spawn_calls = []
        ok = _lazy_start_server(
            "http://127.0.0.1:8765",
            health_probe=lambda: False,
            spawn_fn=lambda: spawn_calls.append(1),
            sleep_fn=lambda _s: None,
        )
        assert ok is False
        assert spawn_calls == [1]

    def test_spawn_oserror_returns_false(self, monkeypatch):
        from lore.cli.commands.server import _lazy_start_server

        monkeypatch.delenv("LORE_NO_AUTOSTART", raising=False)

        def boom():
            raise OSError("nope")

        ok = _lazy_start_server(
            "http://127.0.0.1:8765",
            health_probe=lambda: False,
            spawn_fn=boom,
            sleep_fn=lambda _s: None,
        )
        assert ok is False


# ── cmd_mcp wiring ─────────────────────────────────────────────────


class TestCmdMcp:
    def test_cmd_mcp_resolves_env_and_starts_server_before_run(
        self, monkeypatch, tmp_path
    ):
        """`cmd_mcp` must populate LORE_API_URL/_KEY and probe health
        BEFORE delegating to run_server, so the MCP tools see usable env."""
        from lore.cli.commands import server as server_mod

        # Arrange: blank env, key file present, fake mcp.server.run_server.
        monkeypatch.delenv("LORE_API_URL", raising=False)
        monkeypatch.delenv("LORE_API_KEY", raising=False)
        key_file = tmp_path / "key.txt"
        key_file.write_text("lore_sk_test_key\n")
        monkeypatch.setattr(server_mod, "DEFAULT_KEY_PATH", str(key_file))

        events = []

        def fake_lazy(api_url, **kwargs):
            events.append(("lazy", api_url, os.environ.get("LORE_API_KEY")))
            return True

        monkeypatch.setattr(server_mod, "_lazy_start_server", fake_lazy)

        fake_mod = type(sys)("lore.mcp.server")

        def fake_run_server():
            events.append(
                (
                    "run",
                    os.environ.get("LORE_API_URL"),
                    os.environ.get("LORE_API_KEY"),
                )
            )

        fake_mod.run_server = fake_run_server
        monkeypatch.setitem(sys.modules, "lore.mcp.server", fake_mod)

        # Act
        server_mod.cmd_mcp(argparse.Namespace())

        # Assert: lazy_start ran first with env populated, run_server saw same env.
        assert events == [
            ("lazy", "http://127.0.0.1:8765", "lore_sk_test_key"),
            ("run", "http://127.0.0.1:8765", "lore_sk_test_key"),
        ]
