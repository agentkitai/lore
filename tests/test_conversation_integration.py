"""Integration tests for conversation extraction — end-to-end pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from lore import Lore
from lore.store.sqlite import SqliteStore


def _make_lore_with_mock_llm():
    """Create a Lore instance with real store/embedder but mocked LLM."""
    store = SqliteStore(":memory:")
    lore = Lore(store=store, enrichment=False)

    # Manually wire up a mock enrichment pipeline
    mock_pipeline = MagicMock()
    mock_pipeline.llm.model = "gpt-4o-mini"
    # enrich() must return a JSON-serializable dict (called during remember())
    mock_pipeline.enrich.return_value = {
        "topics": [], "sentiment": {"label": "neutral", "score": 0.0},
        "entities": [], "categories": [],
    }
    lore._enrichment_pipeline = mock_pipeline

    return lore, mock_pipeline


SAMPLE_LLM_RESPONSE = json.dumps({
    "memories": [
        {
            "content": "ECS task memory limit should be set to 512MB for the API service",
            "type": "fact",
            "confidence": 0.9,
            "tags": ["ecs", "deployment", "memory"],
        },
        {
            "content": "Using Fargate instead of EC2 for container deployment",
            "type": "decision",
            "confidence": 0.85,
            "tags": ["fargate", "deployment"],
        },
    ]
})


class TestEndToEndExtract:
    def test_end_to_end_extract(self):
        lore, mock_pipeline = _make_lore_with_mock_llm()
        mock_pipeline.llm.complete.return_value = SAMPLE_LLM_RESPONSE

        messages = [
            {"role": "user", "content": "How do I deploy to ECS?"},
            {"role": "assistant", "content": "Use Fargate. Set memory to 512MB."},
            {"role": "user", "content": "That worked, thanks!"},
        ]

        result = lore.add_conversation(messages)
        assert result.status == "completed"
        assert result.memories_extracted == 2
        assert len(result.memory_ids) == 2
        assert result.message_count == 3
        lore.close()

    def test_extracted_memories_recallable(self):
        lore, mock_pipeline = _make_lore_with_mock_llm()
        mock_pipeline.llm.complete.return_value = SAMPLE_LLM_RESPONSE

        messages = [
            {"role": "user", "content": "How do I deploy to ECS?"},
            {"role": "assistant", "content": "Use Fargate. Set memory to 512MB."},
        ]
        lore.add_conversation(messages)

        # Recall should find the extracted memories
        results = lore.recall("ECS memory limit")
        assert len(results) > 0
        contents = [r.memory.content for r in results]
        assert any("512MB" in c for c in contents)
        lore.close()

    def test_metadata_persisted(self):
        lore, mock_pipeline = _make_lore_with_mock_llm()
        mock_pipeline.llm.complete.return_value = SAMPLE_LLM_RESPONSE

        messages = [
            {"role": "user", "content": "Deploy to ECS with 512MB"},
        ]
        result = lore.add_conversation(
            messages, user_id="alice", session_id="sess-123"
        )

        # Check metadata on stored memories
        for mid in result.memory_ids:
            mem = lore._store.get(mid)
            assert mem is not None
            assert mem.source == "conversation"
            metadata = mem.metadata or {}
            assert metadata.get("source") == "conversation"
            assert metadata.get("user_id") == "alice"
            assert metadata.get("session_id") == "sess-123"
            assert "extracted_at" in metadata
            assert metadata.get("extraction_model") == "gpt-4o-mini"
        lore.close()

    def test_dedup_across_conversations(self):
        lore, mock_pipeline = _make_lore_with_mock_llm()
        mock_pipeline.llm.complete.return_value = SAMPLE_LLM_RESPONSE

        messages = [
            {"role": "user", "content": "Deploy to ECS with 512MB"},
        ]

        # First extraction
        result1 = lore.add_conversation(messages)
        assert result1.memories_extracted == 2

        # Second extraction with same content — should dedup
        result2 = lore.add_conversation(messages)
        assert result2.memories_extracted == 0
        assert result2.duplicates_skipped == 2
        lore.close()

    def test_empty_extraction(self):
        lore, mock_pipeline = _make_lore_with_mock_llm()
        mock_pipeline.llm.complete.return_value = json.dumps({"memories": []})

        messages = [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = lore.add_conversation(messages)
        assert result.status == "completed"
        assert result.memories_extracted == 0
        lore.close()
