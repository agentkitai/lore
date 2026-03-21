"""Tests for enhanced setup wizard (F2)."""

from __future__ import annotations

import stat
from unittest.mock import MagicMock, patch


class TestBackupConfig:
    def test_creates_backup(self, tmp_path):
        from lore.setup import _backup_config
        config = tmp_path / "config.json"
        config.write_text('{"hooks": {}}')

        backup = _backup_config(config)
        assert backup is not None
        assert backup.exists()
        assert "lore-backup" in backup.name
        assert backup.read_text() == '{"hooks": {}}'

    def test_returns_none_for_missing_file(self, tmp_path):
        from lore.setup import _backup_config
        config = tmp_path / "nonexistent.json"
        assert _backup_config(config) is None

    def test_prunes_old_backups(self, tmp_path):
        from lore.setup import _backup_config
        config = tmp_path / "config.json"
        config.write_text("v1")

        # Create 4 backups
        for i in range(4):
            config.write_text(f"v{i+2}")
            _backup_config(config)

        backups = list(tmp_path.glob("config.json.lore-backup.*"))
        assert len(backups) <= 3


class TestValidateHook:
    def test_validates_existing_hook(self, tmp_path):
        from lore.setup import _validate_hook
        hook = tmp_path / "test-hook.sh"
        hook.write_text("#!/bin/bash\necho hello\n")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)

        errors = _validate_hook(hook)
        assert len(errors) == 0

    def test_reports_missing_hook(self, tmp_path):
        from lore.setup import _validate_hook
        hook = tmp_path / "missing.sh"
        errors = _validate_hook(hook)
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_reports_not_executable(self, tmp_path):
        from lore.setup import _validate_hook
        hook = tmp_path / "no-exec.sh"
        hook.write_text("#!/bin/bash\necho hello\n")
        hook.chmod(0o644)  # no execute permission

        errors = _validate_hook(hook)
        assert any("not executable" in e for e in errors)


class TestValidateConfig:
    def test_valid_json_config(self, tmp_path):
        from lore.setup import _validate_config
        config = tmp_path / "config.json"
        config.write_text('{"hooks": {"test": []}}')

        errors = _validate_config(config, "claude-code")
        assert len(errors) == 0

    def test_invalid_json(self, tmp_path):
        from lore.setup import _validate_config
        config = tmp_path / "config.json"
        config.write_text("{bad json")

        errors = _validate_config(config, "claude-code")
        assert any("Invalid JSON" in e for e in errors)

    def test_missing_hooks_key(self, tmp_path):
        from lore.setup import _validate_config
        config = tmp_path / "config.json"
        config.write_text('{"other": "value"}')

        errors = _validate_config(config, "claude-code")
        assert any("hooks" in e for e in errors)


class TestTestConnection:
    def test_unreachable_server(self):
        from lore.setup import _test_connection
        result = _test_connection("http://localhost:19999")
        assert result["status"] == "unreachable"
        assert result["health"] is False

    def test_successful_connection(self):
        from lore.setup import _test_connection

        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            result = _test_connection("http://localhost:8765", "lore_sk_test")
            assert result["status"] == "ok"
            assert result["health"] is True


class TestSetupCLI:
    def test_setup_dry_run(self, capsys):
        from lore.cli import build_parser, cmd_setup
        parser = build_parser()
        args = parser.parse_args(["setup", "claude-code", "--dry-run"])
        cmd_setup(args)
        captured = capsys.readouterr()
        assert "dry-run" in captured.out.lower()

    def test_setup_parser_has_new_flags(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "setup", "claude-code",
            "--validate", "--test-connection", "--dry-run",
        ])
        assert args.validate is True
        assert args.test_connection is True
        assert args.setup_dry_run is True
