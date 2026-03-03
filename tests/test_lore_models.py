"""Tests for Lore Pydantic API models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from lore.server.models import (
    BulkDeleteResponse,
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryResponse,
    MemorySearchResponse,
    MemorySearchResult,
    StatsResponse,
)


class TestMemoryCreateRequest:
    def test_minimal(self) -> None:
        req = MemoryCreateRequest(content="hello")
        assert req.content == "hello"
        assert req.type == "note"
        assert req.tags == []
        assert req.metadata == {}
        assert req.source is None
        assert req.project is None
        assert req.expires_at is None

    def test_full(self) -> None:
        req = MemoryCreateRequest(
            content="Use retry with exponential backoff",
            type="lesson",
            source="claude",
            project="api-server",
            tags=["reliability", "http"],
            metadata={"confidence": 0.9},
        )
        assert req.type == "lesson"
        assert req.tags == ["reliability", "http"]

    def test_empty_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryCreateRequest(content="")

    def test_no_embedding_field(self) -> None:
        """Server generates embeddings — clients should NOT pass them."""
        req = MemoryCreateRequest(content="test")
        assert not hasattr(req, "embedding")


class TestMemoryResponse:
    def test_roundtrip(self) -> None:
        now = datetime.now(timezone.utc)
        resp = MemoryResponse(
            id="01",
            content="hello",
            type="note",
            tags=["a"],
            metadata={},
            created_at=now,
            updated_at=now,
        )
        data = resp.model_dump()
        assert data["id"] == "01"
        assert data["tags"] == ["a"]


class TestMemorySearchResult:
    def test_includes_score(self) -> None:
        now = datetime.now(timezone.utc)
        sr = MemorySearchResult(
            id="01",
            content="hello",
            type="note",
            tags=[],
            metadata={},
            created_at=now,
            updated_at=now,
            score=0.87,
        )
        assert sr.score == 0.87


class TestMemorySearchResponse:
    def test_list(self) -> None:
        resp = MemorySearchResponse(memories=[])
        assert resp.memories == []


class TestMemoryListResponse:
    def test_pagination(self) -> None:
        resp = MemoryListResponse(memories=[], total=50, limit=20, offset=0)
        assert resp.total == 50
        assert resp.limit == 20


class TestStatsResponse:
    def test_empty_store(self) -> None:
        resp = StatsResponse(
            total_count=0,
            count_by_type={},
            count_by_project={},
        )
        assert resp.total_count == 0
        assert resp.oldest_memory is None

    def test_with_data(self) -> None:
        resp = StatsResponse(
            total_count=42,
            count_by_type={"note": 30, "lesson": 12},
            count_by_project={"api": 42},
            oldest_memory=datetime(2025, 1, 1, tzinfo=timezone.utc),
            newest_memory=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert resp.count_by_type["lesson"] == 12


class TestBulkDeleteResponse:
    def test_basic(self) -> None:
        resp = BulkDeleteResponse(deleted=5)
        assert resp.deleted == 5
