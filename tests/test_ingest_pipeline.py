"""Tests for ingestion pipeline orchestrator (F7-S6)."""

from unittest.mock import MagicMock, patch

import pytest

from lore.ingest.adapters.base import NormalizedMessage
from lore.ingest.adapters.raw import RawAdapter
from lore.ingest.dedup import DedupResult, Deduplicator
from lore.ingest.pipeline import IngestResult, IngestionPipeline
from lore.types import Memory


def _make_pipeline(
    remember_return="mem-123",
    dedup_result=None,
    default_dedup_mode="reject",
):
    lore = MagicMock()
    lore.remember.return_value = remember_return
    lore._enrichment_pipeline = None
    lore._store = MagicMock()

    deduplicator = MagicMock(spec=Deduplicator)
    deduplicator.check.return_value = dedup_result or DedupResult(is_duplicate=False)

    pipeline = IngestionPipeline(
        lore=lore,
        deduplicator=deduplicator,
        default_dedup_mode=default_dedup_mode,
    )
    return pipeline, lore, deduplicator


class TestSuccessfulIngestion:
    def test_basic_ingest(self):
        pipeline, lore, _ = _make_pipeline()
        adapter = RawAdapter()
        result = pipeline.ingest(adapter, {"content": "hello world"}, project="test")

        assert result.status == "ingested"
        assert result.memory_id == "mem-123"
        lore.remember.assert_called_once()
        call_kwargs = lore.remember.call_args[1]
        assert call_kwargs["content"] == "hello world"
        assert call_kwargs["tier"] == "long"
        assert call_kwargs["source"] == "raw"
        assert call_kwargs["project"] == "test"
        assert "source_info" in call_kwargs["metadata"]

    def test_source_metadata_stored(self):
        pipeline, lore, _ = _make_pipeline()
        adapter = RawAdapter()
        result = pipeline.ingest(
            adapter,
            {"content": "test", "user": "alice", "channel": "eng"},
        )
        call_kwargs = lore.remember.call_args[1]
        si = call_kwargs["metadata"]["source_info"]
        assert si["adapter"] == "raw"
        assert si["user"] == "alice"
        assert si["channel"] == "eng"
        assert "ingested_at" in si


class TestDedupModes:
    def test_reject_mode(self):
        dedup = DedupResult(is_duplicate=True, duplicate_of="existing-1", similarity=1.0, strategy="exact_id")
        pipeline, lore, _ = _make_pipeline(dedup_result=dedup, default_dedup_mode="reject")
        result = pipeline.ingest(RawAdapter(), {"content": "duplicate"})

        assert result.status == "duplicate_rejected"
        assert result.duplicate_of == "existing-1"
        lore.remember.assert_not_called()

    def test_skip_mode(self):
        dedup = DedupResult(is_duplicate=True, duplicate_of="existing-1", similarity=0.96, strategy="content_similarity")
        pipeline, lore, _ = _make_pipeline(dedup_result=dedup, default_dedup_mode="skip")
        result = pipeline.ingest(RawAdapter(), {"content": "duplicate"})

        assert result.status == "duplicate_skipped"
        lore.remember.assert_not_called()

    def test_merge_mode(self):
        dedup = DedupResult(is_duplicate=True, duplicate_of="existing-1", similarity=1.0, strategy="exact_id")
        pipeline, lore, _ = _make_pipeline(dedup_result=dedup, default_dedup_mode="merge")
        # Setup store.get to return a memory for merge
        lore._store.get.return_value = Memory(
            id="existing-1", content="orig",
            metadata={"source_info": {"adapter": "slack"}},
        )
        result = pipeline.ingest(RawAdapter(), {"content": "duplicate"})

        assert result.status == "duplicate_merged"
        assert result.duplicate_of == "existing-1"
        lore._store.update.assert_called_once()

    def test_allow_mode_skips_dedup(self):
        pipeline, lore, deduplicator = _make_pipeline()
        result = pipeline.ingest(
            RawAdapter(), {"content": "hello"}, dedup_mode="allow"
        )

        assert result.status == "ingested"
        deduplicator.check.assert_not_called()


class TestEdgeCases:
    def test_empty_content_rejected(self):
        pipeline, lore, _ = _make_pipeline()
        result = pipeline.ingest(RawAdapter(), {"content": ""})

        assert result.status == "failed"
        assert "empty" in result.error.lower()
        lore.remember.assert_not_called()

    def test_whitespace_only_rejected(self):
        pipeline, lore, _ = _make_pipeline()
        result = pipeline.ingest(RawAdapter(), {"content": "   \n  "})

        assert result.status == "failed"
        lore.remember.assert_not_called()

    def test_storage_failure_handling(self):
        pipeline, lore, _ = _make_pipeline()
        lore.remember.side_effect = RuntimeError("DB error")
        result = pipeline.ingest(RawAdapter(), {"content": "hello"})

        assert result.status == "failed"
        assert "DB error" in result.error


class TestBatchIngestion:
    def test_batch_returns_per_item_results(self):
        pipeline, lore, _ = _make_pipeline()
        items = [{"content": "A"}, {"content": "B"}, {"content": "C"}]
        results = pipeline.ingest_batch(items, RawAdapter(), project="p1")

        assert len(results) == 3
        assert all(r.status == "ingested" for r in results)

    def test_batch_partial_failure(self):
        pipeline, lore, _ = _make_pipeline()
        items = [{"content": "good"}, {"content": ""}, {"content": "also good"}]
        results = pipeline.ingest_batch(items, RawAdapter())

        assert results[0].status == "ingested"
        assert results[1].status == "failed"
        assert results[2].status == "ingested"
