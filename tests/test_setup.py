"""Tests for the lore setup CLI command."""

from __future__ import annotations

import json
import stat
from unittest.mock import patch


class TestSetupClaudeCode:
    def test_creates_hook_script(self, tmp_path):
        from lore.setup import setup_claude_code

        hooks_dir = tmp_path / ".claude" / "hooks"
        settings_path = tmp_path / ".claude" / "settings.json"

        with patch("lore.setup._claude_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._claude_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._claude_settings_path", return_value=settings_path):
            setup_claude_code(server_url="http://localhost:9999", api_key="test-key")

        hook = hooks_dir / "lore-retrieve.sh"
        assert hook.exists()
        content = hook.read_text()
        assert "http://localhost:9999" in content
        assert "test-key" in content
        # Check executable
        assert hook.stat().st_mode & stat.S_IEXEC

    def test_creates_settings_json(self, tmp_path):
        from lore.setup import setup_claude_code

        hooks_dir = tmp_path / ".claude" / "hooks"
        settings_path = tmp_path / ".claude" / "settings.json"

        with patch("lore.setup._claude_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._claude_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._claude_settings_path", return_value=settings_path):
            setup_claude_code()

        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        hooks = settings["hooks"]["UserPromptSubmit"]
        assert len(hooks) == 1
        assert str(hooks_dir / "lore-retrieve.sh") in hooks[0]["command"]

    def test_idempotent(self, tmp_path):
        from lore.setup import setup_claude_code

        hooks_dir = tmp_path / ".claude" / "hooks"
        settings_path = tmp_path / ".claude" / "settings.json"

        with patch("lore.setup._claude_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._claude_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._claude_settings_path", return_value=settings_path):
            setup_claude_code()
            setup_claude_code()  # second call

        settings = json.loads(settings_path.read_text())
        hooks = settings["hooks"]["UserPromptSubmit"]
        assert len(hooks) == 1  # not duplicated

    def test_preserves_existing_settings(self, tmp_path):
        from lore.setup import setup_claude_code

        hooks_dir = tmp_path / ".claude" / "hooks"
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({"other_setting": True}))

        with patch("lore.setup._claude_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._claude_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._claude_settings_path", return_value=settings_path):
            setup_claude_code()

        settings = json.loads(settings_path.read_text())
        assert settings["other_setting"] is True
        assert "hooks" in settings


class TestSetupOpenClaw:
    def test_creates_hook_script(self, tmp_path):
        from lore.setup import setup_openclaw

        hooks_dir = tmp_path / ".openclaw" / "hooks"

        with patch("lore.setup._openclaw_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._openclaw_hook_path", return_value=hooks_dir / "lore-retrieve.sh"):
            setup_openclaw(server_url="http://localhost:9999")

        hook = hooks_dir / "lore-retrieve.sh"
        assert hook.exists()
        content = hook.read_text()
        assert "http://localhost:9999" in content
        assert hook.stat().st_mode & stat.S_IEXEC


class TestSetupCursor:
    def test_creates_hook_script(self, tmp_path):
        from lore.setup import setup_cursor

        hooks_dir = tmp_path / ".cursor" / "hooks"
        config_path = hooks_dir / "config.json"

        with patch("lore.setup._cursor_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._cursor_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._cursor_config_path", return_value=config_path):
            setup_cursor(server_url="http://localhost:9999", api_key="test-key")

        hook = hooks_dir / "lore-retrieve.sh"
        assert hook.exists()
        content = hook.read_text()
        assert "http://localhost:9999" in content
        assert "test-key" in content
        assert "beforeSubmitPrompt" in content or "jq" in content
        assert hook.stat().st_mode & stat.S_IEXEC

    def test_creates_config(self, tmp_path):
        from lore.setup import setup_cursor

        hooks_dir = tmp_path / ".cursor" / "hooks"
        config_path = hooks_dir / "config.json"

        with patch("lore.setup._cursor_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._cursor_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._cursor_config_path", return_value=config_path):
            setup_cursor()

        assert config_path.exists()
        config = json.loads(config_path.read_text())
        hooks = config["hooks"]["beforeSubmitPrompt"]
        assert len(hooks) == 1
        assert str(hooks_dir / "lore-retrieve.sh") in hooks[0]["command"]

    def test_idempotent(self, tmp_path):
        from lore.setup import setup_cursor

        hooks_dir = tmp_path / ".cursor" / "hooks"
        config_path = hooks_dir / "config.json"

        with patch("lore.setup._cursor_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._cursor_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._cursor_config_path", return_value=config_path):
            setup_cursor()
            setup_cursor()  # second call

        config = json.loads(config_path.read_text())
        hooks = config["hooks"]["beforeSubmitPrompt"]
        assert len(hooks) == 1  # not duplicated

    def test_remove(self, tmp_path):
        from lore.setup import remove_runtime, setup_cursor

        hooks_dir = tmp_path / ".cursor" / "hooks"
        hook_path = hooks_dir / "lore-retrieve.sh"
        config_path = hooks_dir / "config.json"

        with patch("lore.setup._cursor_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._cursor_hook_path", return_value=hook_path), \
             patch("lore.setup._cursor_config_path", return_value=config_path):
            setup_cursor()
            assert hook_path.exists()

            remove_runtime("cursor")
            assert not hook_path.exists()

            config = json.loads(config_path.read_text())
            assert len(config["hooks"]["beforeSubmitPrompt"]) == 0


class TestSetupCodex:
    def test_creates_hook_script(self, tmp_path):
        from lore.setup import setup_codex

        hooks_dir = tmp_path / ".codex" / "hooks"
        config_path = tmp_path / "codex.yaml"

        with patch("lore.setup._codex_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._codex_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._codex_config_path", return_value=config_path):
            setup_codex(server_url="http://localhost:9999", api_key="test-key")

        hook = hooks_dir / "lore-retrieve.sh"
        assert hook.exists()
        content = hook.read_text()
        assert "http://localhost:9999" in content
        assert "test-key" in content
        assert hook.stat().st_mode & stat.S_IEXEC

    def test_creates_config(self, tmp_path):
        from lore.setup import setup_codex

        hooks_dir = tmp_path / ".codex" / "hooks"
        config_path = tmp_path / "codex.yaml"

        with patch("lore.setup._codex_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._codex_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._codex_config_path", return_value=config_path):
            setup_codex()

        assert config_path.exists()
        content = config_path.read_text()
        assert "hooks:" in content or "beforePlan" in content
        assert str(hooks_dir / "lore-retrieve.sh") in content

    def test_idempotent(self, tmp_path):
        from lore.setup import setup_codex

        hooks_dir = tmp_path / ".codex" / "hooks"
        config_path = tmp_path / "codex.yaml"

        with patch("lore.setup._codex_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._codex_hook_path", return_value=hooks_dir / "lore-retrieve.sh"), \
             patch("lore.setup._codex_config_path", return_value=config_path):
            setup_codex()
            setup_codex()  # second call

        content = config_path.read_text()
        # The hook path should appear only once
        hook_str = str(hooks_dir / "lore-retrieve.sh")
        assert content.count(hook_str) == 1

    def test_remove(self, tmp_path):
        from lore.setup import remove_runtime, setup_codex

        hooks_dir = tmp_path / ".codex" / "hooks"
        hook_path = hooks_dir / "lore-retrieve.sh"
        config_path = tmp_path / "codex.yaml"

        with patch("lore.setup._codex_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._codex_hook_path", return_value=hook_path), \
             patch("lore.setup._codex_config_path", return_value=config_path):
            setup_codex()
            assert hook_path.exists()

            remove_runtime("codex")
            assert not hook_path.exists()


class TestSetupStatus:
    def test_shows_status(self, tmp_path, capsys):
        from lore.setup import show_status

        with patch("lore.setup._claude_hook_path", return_value=tmp_path / "no-hook.sh"), \
             patch("lore.setup._claude_settings_path", return_value=tmp_path / "no-settings.json"), \
             patch("lore.setup._openclaw_hook_path", return_value=tmp_path / "no-hook.sh"), \
             patch("lore.setup._cursor_hook_path", return_value=tmp_path / "no-hook.sh"), \
             patch("lore.setup._cursor_config_path", return_value=tmp_path / "no-config.json"), \
             patch("lore.setup._codex_hook_path", return_value=tmp_path / "no-hook.sh"), \
             patch("lore.setup._codex_config_path", return_value=tmp_path / "no-codex.yaml"):
            show_status()

        output = capsys.readouterr().out
        assert "Claude Code" in output
        assert "OpenClaw" in output
        assert "Cursor" in output
        assert "Codex" in output
        assert "[not installed]" in output


class TestSetupRemove:
    def test_remove_claude_code(self, tmp_path):
        from lore.setup import remove_runtime, setup_claude_code

        hooks_dir = tmp_path / ".claude" / "hooks"
        settings_path = tmp_path / ".claude" / "settings.json"
        hook_path = hooks_dir / "lore-retrieve.sh"

        with patch("lore.setup._claude_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._claude_hook_path", return_value=hook_path), \
             patch("lore.setup._claude_settings_path", return_value=settings_path):
            setup_claude_code()
            assert hook_path.exists()

            remove_runtime("claude-code")
            assert not hook_path.exists()

            settings = json.loads(settings_path.read_text())
            assert len(settings["hooks"]["UserPromptSubmit"]) == 0

    def test_remove_openclaw(self, tmp_path):
        from lore.setup import remove_runtime, setup_openclaw

        hooks_dir = tmp_path / ".openclaw" / "hooks"
        hook_path = hooks_dir / "lore-retrieve.sh"

        with patch("lore.setup._openclaw_hooks_dir", return_value=hooks_dir), \
             patch("lore.setup._openclaw_hook_path", return_value=hook_path):
            setup_openclaw()
            assert hook_path.exists()

            remove_runtime("openclaw")
            assert not hook_path.exists()


class TestSetupCLI:
    def test_setup_help(self):
        from lore.cli import build_parser
        parser = build_parser()
        # Should parse setup subcommand
        args = parser.parse_args(["setup", "--status"])
        assert args.command == "setup"
        assert args.status is True

    def test_setup_claude_code_args(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup", "claude-code", "--server-url", "http://example.com"])
        assert args.command == "setup"
        assert args.runtime == "claude-code"
        assert args.server_url == "http://example.com"

    def test_setup_cursor_args(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup", "cursor", "--server-url", "http://example.com"])
        assert args.command == "setup"
        assert args.runtime == "cursor"
        assert args.server_url == "http://example.com"

    def test_setup_codex_args(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup", "codex", "--api-key", "my-key"])
        assert args.command == "setup"
        assert args.runtime == "codex"
        assert args.api_key == "my-key"

    def test_setup_remove_args(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup", "--remove", "openclaw"])
        assert args.command == "setup"
        assert args.remove == "openclaw"

    def test_setup_remove_cursor_args(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup", "--remove", "cursor"])
        assert args.command == "setup"
        assert args.remove == "cursor"

    def test_setup_remove_codex_args(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["setup", "--remove", "codex"])
        assert args.command == "setup"
        assert args.remove == "codex"
