"""Tests for CLI."""

from __future__ import annotations

import pytest

from lore.cli import build_parser, main


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


class TestCLIParsing:
    def test_remember_args(self):
        parser = build_parser()
        args = parser.parse_args(["remember", "some knowledge"])
        assert args.command == "remember"
        assert args.content == "some knowledge"

    def test_remember_with_type(self):
        parser = build_parser()
        args = parser.parse_args(["remember", "test", "--type", "lesson"])
        assert args.type == "lesson"

    def test_recall_args(self):
        parser = build_parser()
        args = parser.parse_args(["recall", "search text"])
        assert args.command == "recall"
        assert args.query == "search text"

    def test_forget_args(self):
        parser = build_parser()
        args = parser.parse_args(["forget", "abc123"])
        assert args.command == "forget"
        assert args.id == "abc123"

    def test_memories_args(self):
        parser = build_parser()
        args = parser.parse_args(["memories", "--limit", "10"])
        assert args.command == "memories"
        assert args.limit == 10

    def test_stats_args(self):
        parser = build_parser()
        args = parser.parse_args(["stats"])
        assert args.command == "stats"

    def test_db_override(self):
        parser = build_parser()
        args = parser.parse_args(["--db", "/tmp/x.db", "memories"])
        assert args.db == "/tmp/x.db"


class TestCLIIntegration:
    def test_remember_and_memories(self, db_path, capsys):
        main(["--db", db_path, "remember", "test knowledge"])
        out = capsys.readouterr().out.strip()
        assert len(out) == 26  # ULID length

        main(["--db", db_path, "memories"])
        out = capsys.readouterr().out
        assert "test knowledge" in out

    def test_recall(self, db_path, capsys):
        main(["--db", db_path, "remember", "rate limiting requires backoff"])
        capsys.readouterr()
        main(["--db", db_path, "recall", "rate limit"])
        out = capsys.readouterr().out
        assert "rate limiting" in out

    def test_forget(self, db_path, capsys):
        main(["--db", db_path, "remember", "to forget"])
        mid = capsys.readouterr().out.strip()
        main(["--db", db_path, "forget", mid])
        out = capsys.readouterr().out
        assert "Forgotten" in out

    def test_stats(self, db_path, capsys):
        main(["--db", db_path, "remember", "test1"])
        main(["--db", db_path, "remember", "test2", "--type", "lesson"])
        capsys.readouterr()
        main(["--db", db_path, "stats"])
        out = capsys.readouterr().out
        assert "Total: 2" in out

    def test_no_command_exits(self):
        with pytest.raises(SystemExit):
            main([])

    def test_memories_empty(self, db_path, capsys):
        main(["--db", db_path, "memories"])
        out = capsys.readouterr().out
        assert "No memories" in out

    def test_remember_with_tags(self, db_path, capsys):
        main(["--db", db_path, "remember", "tagged memory", "--tags", "api,stripe"])
        mid = capsys.readouterr().out.strip()
        assert len(mid) == 26
