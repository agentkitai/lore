"""Tests for deduplication engine (F7-S5)."""

from unittest.mock import MagicMock

from lore.ingest.adapters.base import NormalizedMessage
from lore.ingest.dedup import Deduplicator
from lore.types import Memory, RecallResult


def _make_memory(id="mem-1", source_message_id=None, adapter=None, metadata=None):
    meta = metadata or {}
    if source_message_id and adapter:
        meta["source_info"] = {
            "source_message_id": source_message_id,
            "adapter": adapter,
        }
    return Memory(id=id, content="test", metadata=meta)


class TestExactIdDedup:
    def test_exact_match(self):
        mem = _make_memory("mem-1", source_message_id="ts-123", adapter="slack")
        store = MagicMock()
        store.list.return_value = [mem]
        embedder = MagicMock()

        dedup = Deduplicator(store, embedder)
        msg = NormalizedMessage(content="hello", source_message_id="ts-123")
        result = dedup.check(msg, "slack")

        assert result.is_duplicate is True
        assert result.duplicate_of == "mem-1"
        assert result.similarity == 1.0
        assert result.strategy == "exact_id"

    def test_cross_adapter_no_false_match(self):
        mem = _make_memory("mem-1", source_message_id="123", adapter="slack")
        store = MagicMock()
        store.list.return_value = [mem]
        embedder = MagicMock()

        dedup = Deduplicator(store, embedder)
        msg = NormalizedMessage(content="hello", source_message_id="123")
        result = dedup.check(msg, "telegram")

        # Should NOT match because adapters differ
        # Falls through to content similarity
        store.search.return_value = []
        result = dedup.check(msg, "telegram")
        assert result.is_duplicate is False


class TestContentSimilarityDedup:
    def test_above_threshold(self):
        store = MagicMock()
        store.list.return_value = []
        store.search.return_value = [
            RecallResult(memory=Memory(id="mem-2", content="similar"), score=0.96)
        ]
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 384

        dedup = Deduplicator(store, embedder, threshold=0.95)
        msg = NormalizedMessage(content="some text")
        result = dedup.check(msg, "raw")

        assert result.is_duplicate is True
        assert result.duplicate_of == "mem-2"
        assert result.similarity == 0.96
        assert result.strategy == "content_similarity"

    def test_below_threshold(self):
        store = MagicMock()
        store.list.return_value = []
        store.search.return_value = [
            RecallResult(memory=Memory(id="mem-2", content="different"), score=0.90)
        ]
        embedder = MagicMock()
        embedder.embed.return_value = [0.1] * 384

        dedup = Deduplicator(store, embedder, threshold=0.95)
        msg = NormalizedMessage(content="some text")
        result = dedup.check(msg, "raw")

        assert result.is_duplicate is False

    def test_empty_content_skips_similarity(self):
        store = MagicMock()
        store.list.return_value = []
        embedder = MagicMock()

        dedup = Deduplicator(store, embedder)
        msg = NormalizedMessage(content="   ")
        result = dedup.check(msg, "raw")

        assert result.is_duplicate is False
        embedder.embed.assert_not_called()
