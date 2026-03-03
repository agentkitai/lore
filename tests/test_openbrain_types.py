"""Tests for Open Brain core data types."""

from __future__ import annotations

from openbrain.types import Memory, SearchResult, StoreStats


class TestMemory:
    """Tests for the Memory dataclass."""

    def test_defaults(self) -> None:
        m = Memory(id="01", content="test content")
        assert m.type == "note"
        assert m.source is None
        assert m.project is None
        assert m.tags == []
        assert m.metadata == {}
        assert m.embedding is None
        assert m.created_at == ""
        assert m.updated_at == ""
        assert m.expires_at is None

    def test_all_fields(self) -> None:
        m = Memory(
            id="01",
            content="test",
            type="lesson",
            source="test_source",
            project="myproject",
            tags=["a", "b"],
            metadata={"key": "val"},
            embedding=b"\x00" * 16,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            expires_at="2027-01-01T00:00:00+00:00",
        )
        assert m.type == "lesson"
        assert m.tags == ["a", "b"]
        assert m.metadata == {"key": "val"}
        assert len(m.embedding) == 16

    def test_tags_are_independent(self) -> None:
        """Ensure default tags list is not shared between instances."""
        a = Memory(id="1", content="a")
        b = Memory(id="2", content="b")
        a.tags.append("x")
        assert b.tags == []


class TestSearchResult:
    def test_basic(self) -> None:
        m = Memory(id="01", content="test")
        sr = SearchResult(memory=m, score=0.95)
        assert sr.score == 0.95
        assert sr.memory.id == "01"


class TestStoreStats:
    def test_basic(self) -> None:
        s = StoreStats(
            total_count=10,
            count_by_type={"note": 8, "lesson": 2},
            count_by_project={"proj": 10},
            oldest_memory="2025-01-01T00:00:00+00:00",
            newest_memory="2026-01-01T00:00:00+00:00",
        )
        assert s.total_count == 10
        assert s.count_by_type["note"] == 8

    def test_empty(self) -> None:
        s = StoreStats(
            total_count=0,
            count_by_type={},
            count_by_project={},
            oldest_memory=None,
            newest_memory=None,
        )
        assert s.total_count == 0
        assert s.oldest_memory is None
