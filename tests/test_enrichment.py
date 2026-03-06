"""Unit tests for enrichment pipeline, prompts, and validation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from lore.enrichment.prompts import build_extraction_prompt


# ---------------------------------------------------------------
# Prompt tests
# ---------------------------------------------------------------

class TestBuildExtractionPrompt:
    def test_without_context(self):
        prompt = build_extraction_prompt("The Kubernetes deployment failed on AWS")
        assert 'The Kubernetes deployment failed on AWS' in prompt
        assert 'topics' in prompt
        assert 'sentiment' in prompt
        assert 'entities' in prompt
        assert 'categories' in prompt
        assert 'Additional context' not in prompt
        assert prompt.rstrip().endswith("Return ONLY valid JSON. No explanation.")

    def test_with_context(self):
        prompt = build_extraction_prompt(
            "The deployment failed",
            context="Production incident on 2026-03-01",
        )
        assert 'The deployment failed' in prompt
        assert 'Additional context' in prompt
        assert 'Production incident on 2026-03-01' in prompt

    def test_format_contains_valid_categories(self):
        prompt = build_extraction_prompt("test content")
        assert "infrastructure" in prompt
        assert "debugging" in prompt
        assert "security" in prompt

    def test_format_contains_valid_entity_types(self):
        prompt = build_extraction_prompt("test content")
        assert "person" in prompt
        assert "tool" in prompt
        assert "framework" in prompt


# ---------------------------------------------------------------
# Pipeline parse/validate tests
# ---------------------------------------------------------------

def _make_pipeline():
    """Create a pipeline with a mocked LLM client."""
    mock_llm = MagicMock()
    mock_llm.model = "gpt-4o-mini"
    mock_llm.check_api_key.return_value = True

    from lore.enrichment.pipeline import EnrichmentPipeline
    return EnrichmentPipeline(mock_llm), mock_llm


class TestPipelineParseValidate:
    def test_parse_valid_json(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": ["deployment", "kubernetes"],
            "sentiment": {"label": "negative", "score": -0.5},
            "entities": [{"name": "Kubernetes", "type": "tool"}],
            "categories": ["infrastructure"],
        })
        result = pipeline._parse_and_validate(response)
        assert result["topics"] == ["deployment", "kubernetes"]
        assert result["sentiment"]["label"] == "negative"
        assert result["sentiment"]["score"] == -0.5
        assert len(result["entities"]) == 1
        assert result["entities"][0]["name"] == "Kubernetes"
        assert result["categories"] == ["infrastructure"]

    def test_parse_json_with_code_fences(self):
        pipeline, _ = _make_pipeline()
        response = '```json\n{"topics": ["test"], "sentiment": {"label": "neutral", "score": 0.0}, "entities": [], "categories": []}\n```'
        result = pipeline._parse_and_validate(response)
        assert result["topics"] == ["test"]

    def test_parse_malformed_json(self, caplog):
        import logging
        pipeline, _ = _make_pipeline()
        with caplog.at_level(logging.WARNING):
            result = pipeline._parse_and_validate("this is not json")
        assert result["topics"] == []
        assert result["sentiment"] == {"label": "neutral", "score": 0.0}
        assert result["entities"] == []
        assert result["categories"] == []
        assert "malformed JSON" in caplog.text

    def test_parse_partial_json(self):
        """JSON with some valid fields and missing others."""
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": ["debugging"],
            # sentiment, entities, categories missing
        })
        result = pipeline._parse_and_validate(response)
        assert result["topics"] == ["debugging"]
        assert result["sentiment"] == {"label": "neutral", "score": 0.0}
        assert result["entities"] == []
        assert result["categories"] == []

    def test_topics_max_five(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": ["a", "b", "c", "d", "e", "f", "g", "h"],
            "sentiment": {"label": "neutral", "score": 0.0},
            "entities": [],
            "categories": [],
        })
        result = pipeline._parse_and_validate(response)
        assert len(result["topics"]) == 5

    def test_topics_lowercase(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": ["Kubernetes", "AWS"],
            "sentiment": {"label": "neutral", "score": 0.0},
            "entities": [],
            "categories": [],
        })
        result = pipeline._parse_and_validate(response)
        assert result["topics"] == ["kubernetes", "aws"]

    def test_sentiment_clamp_high(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": [],
            "sentiment": {"label": "positive", "score": 2.5},
            "entities": [],
            "categories": [],
        })
        result = pipeline._parse_and_validate(response)
        assert result["sentiment"]["score"] == 1.0

    def test_sentiment_clamp_low(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": [],
            "sentiment": {"label": "negative", "score": -3.0},
            "entities": [],
            "categories": [],
        })
        result = pipeline._parse_and_validate(response)
        assert result["sentiment"]["score"] == -1.0

    def test_sentiment_invalid_label(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": [],
            "sentiment": {"label": "amazing", "score": 0.5},
            "entities": [],
            "categories": [],
        })
        result = pipeline._parse_and_validate(response)
        assert result["sentiment"]["label"] == "neutral"

    def test_entity_invalid_type(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": [],
            "sentiment": {"label": "neutral", "score": 0.0},
            "entities": [{"name": "PostgreSQL", "type": "database"}],
            "categories": [],
        })
        result = pipeline._parse_and_validate(response)
        assert result["entities"][0]["type"] == "concept"

    def test_entity_empty_name_skipped(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": [],
            "sentiment": {"label": "neutral", "score": 0.0},
            "entities": [
                {"name": "", "type": "tool"},
                {"name": "Valid", "type": "tool"},
            ],
            "categories": [],
        })
        result = pipeline._parse_and_validate(response)
        assert len(result["entities"]) == 1
        assert result["entities"][0]["name"] == "Valid"

    def test_categories_from_fixed_set(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": [],
            "sentiment": {"label": "neutral", "score": 0.0},
            "entities": [],
            "categories": ["infrastructure", "banana", "debugging", "testing", "security"],
        })
        result = pipeline._parse_and_validate(response)
        # "banana" filtered out, remaining truncated to 3
        assert "banana" not in result["categories"]
        assert len(result["categories"]) <= 3
        assert "infrastructure" in result["categories"]

    def test_categories_max_three(self):
        pipeline, _ = _make_pipeline()
        response = json.dumps({
            "topics": [],
            "sentiment": {"label": "neutral", "score": 0.0},
            "entities": [],
            "categories": ["infrastructure", "debugging", "testing", "security"],
        })
        result = pipeline._parse_and_validate(response)
        assert len(result["categories"]) == 3


class TestPipelineEnrich:
    def test_enrich_success(self):
        pipeline, mock_llm = _make_pipeline()
        mock_llm.complete.return_value = json.dumps({
            "topics": ["deployment"],
            "sentiment": {"label": "negative", "score": -0.5},
            "entities": [{"name": "K8s", "type": "tool"}],
            "categories": ["infrastructure"],
        })

        result = pipeline.enrich("The K8s deployment failed")
        assert result["topics"] == ["deployment"]
        assert "enriched_at" in result
        assert result["enrichment_model"] == "gpt-4o-mini"

    def test_enrich_no_api_key_raises(self):
        pipeline, mock_llm = _make_pipeline()
        mock_llm.check_api_key.return_value = False

        with pytest.raises(RuntimeError, match="API key not configured"):
            pipeline.enrich("test content")
