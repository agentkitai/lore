"""Integration tests for conversation extraction — end-to-end pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

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


class TestRecallUserIdFiltering:
    def test_recall_with_user_id_filters(self):
        """recall(query, user_id='alice') returns only Alice's memories."""
        lore, mock_pipeline = _make_lore_with_mock_llm()

        # Alice's extraction
        alice_response = json.dumps({
            "memories": [{"content": "Alice prefers dark mode", "type": "preference", "confidence": 0.9, "tags": ["ui"]}]
        })
        mock_pipeline.llm.complete.return_value = alice_response
        lore.add_conversation(
            [{"role": "user", "content": "I prefer dark mode"}],
            user_id="alice",
        )

        # Bob's extraction
        bob_response = json.dumps({
            "memories": [{"content": "Bob uses light theme", "type": "preference", "confidence": 0.9, "tags": ["ui"]}]
        })
        mock_pipeline.llm.complete.return_value = bob_response
        lore.add_conversation(
            [{"role": "user", "content": "I use light theme"}],
            user_id="bob",
        )

        # Recall with user_id filter
        alice_results = lore.recall("theme preference", user_id="alice")
        assert len(alice_results) > 0
        for r in alice_results:
            assert (r.memory.metadata or {}).get("user_id") == "alice"

        bob_results = lore.recall("theme preference", user_id="bob")
        assert len(bob_results) > 0
        for r in bob_results:
            assert (r.memory.metadata or {}).get("user_id") == "bob"

        lore.close()

    def test_recall_without_user_id_returns_all(self):
        """recall(query) without user_id returns all memories."""
        lore, mock_pipeline = _make_lore_with_mock_llm()

        alice_response = json.dumps({
            "memories": [{"content": "Alice prefers dark mode", "type": "preference"}]
        })
        mock_pipeline.llm.complete.return_value = alice_response
        lore.add_conversation(
            [{"role": "user", "content": "test"}],
            user_id="alice",
        )

        bob_response = json.dumps({
            "memories": [{"content": "Bob uses light theme", "type": "preference"}]
        })
        mock_pipeline.llm.complete.return_value = bob_response
        lore.add_conversation(
            [{"role": "user", "content": "test"}],
            user_id="bob",
        )

        # Recall without user_id should return all
        all_results = lore.recall("theme preference")
        assert len(all_results) >= 2
        lore.close()
