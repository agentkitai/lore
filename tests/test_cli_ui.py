"""Tests for the lore ui CLI command."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from lore.cli import build_parser, cmd_ui


class TestUICommandParser:
    def test_ui_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["ui", "--no-open"])
        assert args.command == "ui"
        assert args.no_open is True

    def test_default_port(self):
        parser = build_parser()
        args = parser.parse_args(["ui", "--no-open"])
        assert args.port == 8766

    def test_custom_port(self):
        parser = build_parser()
        args = parser.parse_args(["ui", "--port", "3333", "--no-open"])
        assert args.port == 3333

    def test_default_host(self):
        parser = build_parser()
        args = parser.parse_args(["ui", "--no-open"])
        assert args.host == "127.0.0.1"

    def test_custom_host(self):
        parser = build_parser()
        args = parser.parse_args(["ui", "--host", "0.0.0.0", "--no-open"])
        assert args.host == "0.0.0.0"


class TestUICommand:
    def test_security_warning_on_0000(self, capsys):
        mock_uvicorn = MagicMock()
        mock_lore = MagicMock()
        mock_lore._store = MagicMock()

        parser = build_parser()
        args = parser.parse_args(["ui", "--host", "0.0.0.0", "--no-open"])

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}), \
             patch("lore.cli._get_lore", return_value=mock_lore):
            cmd_ui(args)

        captured = capsys.readouterr()
        assert "Security warning" in captured.err

    def test_no_browser_with_no_open(self):
        mock_uvicorn = MagicMock()
        mock_lore = MagicMock()
        mock_lore._store = MagicMock()

        parser = build_parser()
        args = parser.parse_args(["ui", "--no-open"])

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}), \
             patch("lore.cli._get_lore", return_value=mock_lore), \
             patch("webbrowser.open") as mock_open:
            cmd_ui(args)
            mock_open.assert_not_called()
