"""Tests for TTL / Expiration support (STORY-024).

Verifies:
  - remember accepts ttl parameter and sets expires_at
  - Expired memories excluded from recall
  - Expired memories excluded from list (unless include_expired=True)
  - Expired memories excluded from stats
  - delete_expired cleans up expired memories
  - TTL parsing edge cases
"""

from __future__ import annotations

import json
import struct
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import patch

import pytest

from lore.memory_store.sqlite import SqliteStore
from lore.types import Memory


class _FakeEmbedder:
    def embed(self, text: str) -> List[float]:
        import hashlib

        h = hashlib.sha256(text.encode()).digest()
        return [(h[i % len(h)] - 128) / 128.0 for i in range(384)]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


def _embed(text: str) -> List[float]:
    return _FakeEmbedder().embed(text)


def _embed_bytes(text: str) -> bytes:
    vec = _embed(text)
    return struct.pack(f"{len(vec)}f", *vec)


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "test.db"))
    yield s
    s.close()


def _make_memory(
    content: str,
    expires_at: str | None = None,
    project: str | None = None,
    mem_type: str = "note",
) -> Memory:
    from ulid import ULID

    now = datetime.now(timezone.utc).isoformat()
    return Memory(
        id=str(ULID()),
        content=content,
        type=mem_type,
        project=project,
        tags=[],
        metadata={},
        embedding=_embed_bytes(content),
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
    )


class TestTTLParsing:
    """Test _parse_ttl helper from MCP and SDK."""

    def test_parse_seconds(self) -> None:
        from lore.mcp.server import _parse_ttl

        result = _parse_ttl("30s")
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt > datetime.now(timezone.utc)

    def test_parse_minutes(self) -> None:
        from lore.mcp.server import _parse_ttl

        result = _parse_ttl("5m")
        assert result is not None

    def test_parse_hours(self) -> None:
        from lore.mcp.server import _parse_ttl

        result = _parse_ttl("2h")
        assert result is not None

    def test_parse_days(self) -> None:
        from lore.mcp.server import _parse_ttl

        result = _parse_ttl("7d")
        assert result is not None

    def test_parse_weeks(self) -> None:
        from lore.mcp.server import _parse_ttl

        result = _parse_ttl("2w")
        assert result is not None

    def test_parse_none(self) -> None:
        from lore.mcp.server import _parse_ttl

        assert _parse_ttl(None) is None
        assert _parse_ttl("") is None

    def test_parse_invalid(self) -> None:
        from lore.mcp.server import _parse_ttl

        assert _parse_ttl("abc") is None
        assert _parse_ttl("7x") is None
        assert _parse_ttl("forever") is None


class TestExpiredExcludedFromSearch:
    """Expired memories should not appear in search results."""

    def test_expired_excluded_from_recall(self, store) -> None:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        store.save(_make_memory("expired memory about cats", expires_at=past))
        store.save(_make_memory("valid memory about cats", expires_at=future))
        store.save(_make_memory("no-ttl memory about cats"))

        results = store.search(embedding=_embed("cats"), limit=10)
        contents = [r.memory.content for r in results]
        assert "expired memory about cats" not in contents
        assert "valid memory about cats" in contents
        assert "no-ttl memory about cats" in contents

    def test_all_expired_returns_empty(self, store) -> None:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        store.save(_make_memory("expired only", expires_at=past))

        results = store.search(embedding=_embed("expired"), limit=10)
        assert results == []


class TestExpiredExcludedFromList:
    """Expired memories should not appear in list by default."""

    def test_expired_excluded_by_default(self, store) -> None:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        store.save(_make_memory("expired item", expires_at=past))
        store.save(_make_memory("valid item", expires_at=future))
        store.save(_make_memory("permanent item"))

        memories, total = store.list()
        contents = [m.content for m in memories]
        assert "expired item" not in contents
        assert "valid item" in contents
        assert "permanent item" in contents

    def test_include_expired_flag(self, store) -> None:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        store.save(_make_memory("expired item", expires_at=past))
        store.save(_make_memory("permanent item"))

        # Without include_expired
        memories, _ = store.list()
        assert len(memories) == 1
        assert memories[0].content == "permanent item"

        # With include_expired
        memories, _ = store.list(include_expired=True)
        assert len(memories) == 2
        contents = [m.content for m in memories]
        assert "expired item" in contents
        assert "permanent item" in contents


class TestExpiredExcludedFromStats:
    """Stats should not count expired memories."""

    def test_expired_not_counted(self, store) -> None:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        store.save(_make_memory("expired", expires_at=past, mem_type="lesson"))
        store.save(_make_memory("valid", expires_at=future, mem_type="note"))
        store.save(_make_memory("permanent", mem_type="note"))

        stats = store.stats()
        assert stats.total_count == 2
        assert stats.count_by_type.get("lesson", 0) == 0
        assert stats.count_by_type["note"] == 2


class TestDeleteExpired:
    """delete_expired should remove all expired memories."""

    def test_cleanup(self, store) -> None:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        store.save(_make_memory("expired 1", expires_at=past))
        store.save(_make_memory("expired 2", expires_at=past))
        store.save(_make_memory("valid", expires_at=future))
        store.save(_make_memory("permanent"))

        deleted = store.delete_expired()
        assert deleted == 2

        # Verify only valid ones remain (use include_expired to see everything)
        memories, total = store.list(include_expired=True)
        assert len(memories) == 2
        contents = [m.content for m in memories]
        assert "valid" in contents
        assert "permanent" in contents

    def test_no_expired(self, store) -> None:
        store.save(_make_memory("permanent"))
        deleted = store.delete_expired()
        assert deleted == 0


class TestTTLViaCLI:
    """TTL via the CLI remember command."""

    @pytest.fixture(autouse=True)
    def _patch_embedder(self):
        with patch("lore.lore.LocalEmbedder", return_value=_FakeEmbedder()):
            yield

    def test_remember_with_ttl(self, tmp_path, capsys) -> None:
        from lore.cli import main

        db = str(tmp_path / "cli_ttl.db")
        main(["--db", db, "remember", "temp note", "--ttl", "7d", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "id" in data

        # Verify the memory has an expires_at set
        s = SqliteStore(db)
        m = s.get(data["id"])
        assert m is not None
        assert m.expires_at is not None
        exp = datetime.fromisoformat(m.expires_at)
        assert exp > datetime.now(timezone.utc)
        s.close()

    def test_list_include_expired(self, tmp_path, capsys) -> None:
        from lore.cli import main

        db = str(tmp_path / "cli_expired.db")

        # Store a memory with a past expiration directly
        s = SqliteStore(db)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        s.save(_make_memory("old expired", expires_at=past))
        s.save(_make_memory("still valid"))
        s.close()

        # Without --include-expired
        main(["--db", db, "memories", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data["memories"]) == 1
        assert data["memories"][0]["content"] == "still valid"

        # With --include-expired
        main(["--db", db, "memories", "--include-expired", "--json"])
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data["memories"]) == 2


class TestTTLViaMCP:
    """TTL via MCP tools."""

    @pytest.fixture(autouse=True)
    def _patch_embedder(self):
        with patch("lore.lore.LocalEmbedder", return_value=_FakeEmbedder()):
            yield

    def test_remember_with_ttl(self, tmp_path) -> None:
        import os

        from lore.mcp import server as mcp_server

        mcp_server._store = None
        mcp_server._embedder = None

        os.environ["LORE_DB_PATH"] = str(tmp_path / "mcp_ttl.db")
        os.environ["LORE_STORE"] = "local"

        # Inject fake embedder directly
        mcp_server._embedder = _FakeEmbedder()
        try:
            result = mcp_server.remember(content="temp note", ttl="7d")
            assert "Memory saved" in result
        finally:
            mcp_server._store = None
            mcp_server._embedder = None
            os.environ.pop("LORE_DB_PATH", None)

    def test_list_include_expired(self, tmp_path) -> None:
        import os

        from lore.mcp import server as mcp_server

        mcp_server._store = None
        mcp_server._embedder = None

        db_path = str(tmp_path / "mcp_expired.db")
        os.environ["LORE_DB_PATH"] = db_path
        os.environ["LORE_STORE"] = "local"

        # Insert an expired memory directly
        s = SqliteStore(db_path)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        s.save(_make_memory("expired via mcp", expires_at=past))
        s.save(_make_memory("valid via mcp"))
        s.close()

        # Inject fake embedder directly
        mcp_server._embedder = _FakeEmbedder()
        try:
            # Without include_expired
            result_normal = mcp_server.list_memories()
            # With include_expired
            result_with_expired = mcp_server.list_memories(include_expired=True)

            assert "expired via mcp" not in result_normal
            assert "valid via mcp" in result_normal
            assert "expired via mcp" in result_with_expired
        finally:
            mcp_server._store = None
            mcp_server._embedder = None
            os.environ.pop("LORE_DB_PATH", None)
