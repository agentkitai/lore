"""Unit tests for conversation extraction pipeline and parsing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from lore.conversation.extractor import _TYPE_MAP, ConversationExtractor
from lore.types import ConversationMessage


def _make_extractor(with_llm=True):
    """Create a ConversationExtractor with a mocked Lore instance."""
    mock_lore = MagicMock()
    mock_lore.project = None
    mock_lore.recall.return_value = []

    if with_llm:
        mock_pipeline = MagicMock()
        mock_pipeline.llm.model = "gpt-4o-mini"
        mock_lore._enrichment_pipeline = mock_pipeline
    else:
        mock_lore._enrichment_pipeline = None

    return ConversationExtractor(mock_lore), mock_lore


class TestFormatTranscript:
    def test_format_transcript(self):
        extractor, _ = _make_extractor()
        messages = [
            ConversationMessage(role="user", content="How do I deploy?"),
            ConversationMessage(role="assistant", content="Use copilot deploy."),
        ]
        result = extractor._format_transcript(messages)
        assert "[user]: How do I deploy?" in result
        assert "[assistant]: Use copilot deploy." in result
        assert "\n\n" in result

    def test_format_transcript_single(self):
        extractor, _ = _make_extractor()
        messages = [ConversationMessage(role="user", content="Hello")]
        result = extractor._format_transcript(messages)
        assert result == "[user]: Hello"


class TestParseExtractionResponse:
    def test_parse_extraction_valid(self):
        extractor, _ = _make_extractor()
        response = json.dumps({
            "memories": [
                {
                    "content": "ECS memory limit is 512MB",
                    "type": "fact",
                    "confidence": 0.9,
                    "tags": ["ecs", "deployment"],
                }
            ]
        })
        result = extractor._parse_extraction_response(response)
        assert len(result) == 1
        assert result[0]["content"] == "ECS memory limit is 512MB"
        assert result[0]["type"] == "fact"
        assert result[0]["confidence"] == 0.9
        assert result[0]["tags"] == ["ecs", "deployment"]

    def test_parse_extraction_malformed(self):
        extractor, _ = _make_extractor()
        result = extractor._parse_extraction_response("not json at all {{{")
        assert result == []

    def test_parse_extraction_markdown_wrapped(self):
        extractor, _ = _make_extractor()
        response = '```json\n{"memories": [{"content": "test fact", "type": "fact"}]}\n```'
        result = extractor._parse_extraction_response(response)
        assert len(result) == 1
        assert result[0]["content"] == "test fact"

    def test_parse_extraction_empty_content_skipped(self):
        extractor, _ = _make_extractor()
        response = json.dumps({
            "memories": [
                {"content": "", "type": "fact"},
                {"content": "valid memory", "type": "fact"},
            ]
        })
        result = extractor._parse_extraction_response(response)
        assert len(result) == 1
        assert result[0]["content"] == "valid memory"

    def test_parse_extraction_non_dict_items_skipped(self):
        extractor, _ = _make_extractor()
        response = json.dumps({
            "memories": ["not a dict", {"content": "valid", "type": "fact"}]
        })
        result = extractor._parse_extraction_response(response)
        assert len(result) == 1

    def test_parse_extraction_memories_not_list(self):
        extractor, _ = _make_extractor()
        response = json.dumps({"memories": "not a list"})
        result = extractor._parse_extraction_response(response)
        assert result == []


class TestTypeMapping:
    def test_all_types_mapped(self):
        for llm_type in ["fact", "decision", "preference", "lesson", "correction"]:
            assert llm_type in _TYPE_MAP

    def test_type_mapping_values(self):
        assert ConversationExtractor._map_type("fact") == "fact"
        assert ConversationExtractor._map_type("decision") == "general"
        assert ConversationExtractor._map_type("preference") == "preference"
        assert ConversationExtractor._map_type("lesson") == "lesson"
        assert ConversationExtractor._map_type("correction") == "general"
        assert ConversationExtractor._map_type("unknown") == "general"


class TestConfidenceClamping:
    def test_confidence_clamping_high(self):
        extractor, _ = _make_extractor()
        response = json.dumps({
            "memories": [{"content": "test", "confidence": 1.5}]
        })
        result = extractor._parse_extraction_response(response)
        assert result[0]["confidence"] == 1.0

    def test_confidence_clamping_low(self):
        extractor, _ = _make_extractor()
        response = json.dumps({
            "memories": [{"content": "test", "confidence": -0.5}]
        })
        result = extractor._parse_extraction_response(response)
        assert result[0]["confidence"] == 0.0

    def test_confidence_default(self):
        extractor, _ = _make_extractor()
        response = json.dumps({
            "memories": [{"content": "test"}]
        })
        result = extractor._parse_extraction_response(response)
        assert result[0]["confidence"] == 0.8


class TestTagLimits:
    def test_tags_limited_to_five(self):
        extractor, _ = _make_extractor()
        response = json.dumps({
            "memories": [{
                "content": "test",
                "tags": ["a", "b", "c", "d", "e", "f", "g"],
            }]
        })
        result = extractor._parse_extraction_response(response)
        assert len(result[0]["tags"]) == 5

    def test_tags_lowercased(self):
        extractor, _ = _make_extractor()
        response = json.dumps({
            "memories": [{"content": "test", "tags": ["ECS", "Deploy"]}]
        })
        result = extractor._parse_extraction_response(response)
        assert result[0]["tags"] == ["ecs", "deploy"]


class TestValidation:
    def test_empty_messages_raises(self):
        extractor, _ = _make_extractor()
        with pytest.raises(ValueError, match="non-empty"):
            extractor.extract([])

    def test_no_llm_raises(self):
        extractor, _ = _make_extractor(with_llm=False)
        messages = [ConversationMessage(role="user", content="test")]
        with pytest.raises(RuntimeError, match="LLM"):
            extractor.extract(messages)


class TestExtractPipeline:
    def test_extract_stores_memories(self):
        extractor, mock_lore = _make_extractor()
        mock_lore.remember.return_value = "mem-001"
        mock_lore._enrichment_pipeline.llm.complete.return_value = json.dumps({
            "memories": [
                {"content": "ECS needs 512MB", "type": "fact", "confidence": 0.9, "tags": ["ecs"]},
            ]
        })

        messages = [
            ConversationMessage(role="user", content="How much memory for ECS?"),
            ConversationMessage(role="assistant", content="512MB is recommended."),
        ]
        result = extractor.extract(messages)

        assert result.status == "completed"
        assert result.memories_extracted == 1
        assert result.memory_ids == ["mem-001"]
        assert result.message_count == 2
        mock_lore.remember.assert_called_once()

    def test_extract_dedup_skips_similar(self):
        extractor, mock_lore = _make_extractor()

        # Simulate existing similar memory
        mock_recall_result = MagicMock()
        mock_recall_result.score = 0.95
        mock_lore.recall.return_value = [mock_recall_result]

        mock_lore._enrichment_pipeline.llm.complete.return_value = json.dumps({
            "memories": [
                {"content": "Already stored", "type": "fact"},
            ]
        })

        messages = [ConversationMessage(role="user", content="test")]
        result = extractor.extract(messages)

        assert result.memories_extracted == 0
        assert result.duplicates_skipped == 1
        mock_lore.remember.assert_not_called()

    def test_extract_metadata(self):
        extractor, mock_lore = _make_extractor()
        mock_lore.remember.return_value = "mem-001"
        mock_lore._enrichment_pipeline.llm.complete.return_value = json.dumps({
            "memories": [{"content": "fact", "type": "fact"}]
        })

        messages = [ConversationMessage(role="user", content="test")]
        extractor.extract(messages, user_id="alice", session_id="sess-1")

        call_kwargs = mock_lore.remember.call_args[1]
        assert call_kwargs["source"] == "conversation"
        metadata = call_kwargs["metadata"]
        assert metadata["source"] == "conversation"
        assert metadata["user_id"] == "alice"
        assert metadata["session_id"] == "sess-1"
        assert "extracted_at" in metadata
        assert metadata["extraction_model"] == "gpt-4o-mini"
        assert metadata["conversation_length"] == 1

    def test_partial_extraction_on_chunk_failure(self):
        """S19: multi-chunk with one failing chunk still stores others."""
        from lore.conversation.chunker import ConversationChunker

        extractor, mock_lore = _make_extractor()
        mock_lore.remember.return_value = "mem-001"

        # Create enough messages to force 3 chunks
        long_msg = "word " * 500  # ~667 tokens per message
        messages = [
            ConversationMessage(role="user", content=long_msg)
            for _ in range(20)
        ]

        # Verify chunking produces multiple chunks
        chunker = ConversationChunker(max_tokens=8000, overlap_messages=2)
        chunks = chunker.chunk(messages)
        assert len(chunks) >= 2, f"Expected >=2 chunks, got {len(chunks)}"

        # Make LLM succeed for first call, fail for second, succeed for rest
        call_count = [0]
        def side_effect(prompt):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("LLM timeout")
            return json.dumps({
                "memories": [{"content": f"Memory from chunk {call_count[0]}", "type": "fact"}]
            })

        mock_lore._enrichment_pipeline.llm.complete.side_effect = side_effect

        result = extractor.extract(messages)

        # Should still complete with partial results
        assert result.status == "completed"
        assert result.memories_extracted >= 1
        assert result.error is not None
        assert "Chunk 1 failed" in result.error

    def test_single_chunk_failure_raises(self):
        """Single-chunk failure should raise RuntimeError."""
        extractor, mock_lore = _make_extractor()
        mock_lore._enrichment_pipeline.llm.complete.side_effect = RuntimeError("API down")

        messages = [ConversationMessage(role="user", content="short message")]
        with pytest.raises(RuntimeError, match="Extraction failed"):
            extractor.extract(messages)
