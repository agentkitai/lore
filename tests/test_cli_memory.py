"""Tests for Lore CLI memory commands."""

from __future__ import annotations

import json
from typing import List
from unittest.mock import patch

import pytest

from lore.cli import main


class _FakeEmbedder:
    def embed(self, text: str) -> List[float]:
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        return [(h[i % len(h)] - 128) / 128.0 for i in range(384)]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture(autouse=True)
def _patch_embedder():
    with patch("lore.lore.LocalEmbedder", return_value=_FakeEmbedder()):
        yield


class TestRememberCommand:
    def test_basic(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "Test content"])
        out = capsys.readouterr().out
        assert "Memory saved" in out

    def test_with_options(self, db_path, capsys) -> None:
        main([
            "--db", db_path,
            "remember", "API rate limits",
            "--type", "lesson",
            "--tags", "api,reliability",
            "--source", "claude",
        ])
        out = capsys.readouterr().out
        assert "Memory saved" in out

    def test_json_output(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "Test", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "id" in data


class TestRecallCommand:
    def test_empty(self, db_path, capsys) -> None:
        main(["--db", db_path, "recall", "anything"])
        out = capsys.readouterr().out
        assert "No relevant memories" in out

    def test_finds_stored(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "Stripe rate limits"])
        capsys.readouterr()  # clear

        main(["--db", db_path, "recall", "rate limiting"])
        out = capsys.readouterr().out
        assert "Stripe" in out

    def test_json_output(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "Test content"])
        capsys.readouterr()

        main(["--db", db_path, "recall", "test", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "memory" in data[0]
        assert "score" in data[0]


class TestForgetCommand:
    def test_by_id(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "to delete", "--json"])
        mid = json.loads(capsys.readouterr().out)["id"]

        main(["--db", db_path, "forget", mid])
        out = capsys.readouterr().out
        assert "Deleted 1" in out

    def test_by_type(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "a lesson", "--type", "lesson"])
        main(["--db", db_path, "remember", "a note", "--type", "note"])
        capsys.readouterr()

        main(["--db", db_path, "forget", "--type", "lesson"])
        out = capsys.readouterr().out
        assert "Deleted 1" in out

    def test_json_output(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "temp"])
        capsys.readouterr()

        main(["--db", db_path, "forget", "--type", "note", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "deleted" in data


class TestMemoriesCommand:
    def test_empty(self, db_path, capsys) -> None:
        main(["--db", db_path, "memories"])
        out = capsys.readouterr().out
        assert "No memories" in out

    def test_lists_stored(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "First memory"])
        main(["--db", db_path, "remember", "Second memory"])
        capsys.readouterr()

        main(["--db", db_path, "memories"])
        out = capsys.readouterr().out
        assert "2" in out

    def test_json_output(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "Test"])
        capsys.readouterr()

        main(["--db", db_path, "memories", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "memories" in data
        assert "total" in data


class TestStatsCommand:
    def test_empty(self, db_path, capsys) -> None:
        main(["--db", db_path, "stats"])
        out = capsys.readouterr().out
        assert "Total memories: 0" in out

    def test_with_data(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "one", "--type", "note"])
        main(["--db", db_path, "remember", "two", "--type", "lesson"])
        capsys.readouterr()

        main(["--db", db_path, "stats"])
        out = capsys.readouterr().out
        assert "Total memories: 2" in out
        assert "note" in out
        assert "lesson" in out

    def test_json_output(self, db_path, capsys) -> None:
        main(["--db", db_path, "remember", "Test"])
        capsys.readouterr()

        main(["--db", db_path, "stats", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "total_count" in data


class TestProjectFlag:
    def test_project_scoping(self, db_path, capsys) -> None:
        main(["--db", db_path, "--project", "alpha", "remember", "alpha memory"])
        main(["--db", db_path, "--project", "beta", "remember", "beta memory"])
        capsys.readouterr()

        main(["--db", db_path, "--project", "alpha", "memories"])
        out = capsys.readouterr().out
        assert "1" in out
