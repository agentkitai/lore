"""Tests for the lore ui CLI command."""

from __future__ import annotations

from unittest.mock import patch

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
    def test_opens_correct_url(self, capsys):
        parser = build_parser()
        args = parser.parse_args(["ui", "--no-open"])

        cmd_ui(args)

        captured = capsys.readouterr()
        assert "http://127.0.0.1:8766/ui" in captured.out

    def test_no_browser_with_no_open(self):
        parser = build_parser()
        args = parser.parse_args(["ui", "--no-open"])

        with patch("webbrowser.open") as mock_open:
            cmd_ui(args)
            mock_open.assert_not_called()

    def test_browser_opens_without_no_open(self):
        parser = build_parser()
        args = parser.parse_args(["ui"])

        with patch("webbrowser.open") as mock_open:
            cmd_ui(args)
            mock_open.assert_called_once()
