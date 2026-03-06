"""Unit tests for Classification dataclass, taxonomy validation, LLMClassifier with mocked provider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from lore.classify.base import Classification, Classifier, make_classification
from lore.classify.llm import CLASSIFY_PROMPT, LLMClassifier
from lore.classify.taxonomies import DOMAIN_LABELS, EMOTION_LABELS, INTENT_LABELS
from lore.llm.base import LLMProvider


# ── Taxonomy tests ──────────────────────────────────────────────────


class TestTaxonomies:
    def test_intent_labels_content(self):
        assert INTENT_LABELS == (
            "question", "statement", "instruction",
            "preference", "observation", "decision",
        )

    def test_domain_labels_content(self):
        assert DOMAIN_LABELS == (
            "technical", "personal", "business",
            "creative", "administrative",
        )

    def test_emotion_labels_content(self):
        assert EMOTION_LABELS == (
            "neutral", "frustrated", "excited",
            "curious", "confident", "uncertain",
        )

    def test_labels_are_tuples(self):
        assert isinstance(INTENT_LABELS, tuple)
        assert isinstance(DOMAIN_LABELS, tuple)
        assert isinstance(EMOTION_LABELS, tuple)


# ── Classification dataclass tests ──────────────────────────────────


class TestClassification:
    def test_basic_creation(self):
        c = Classification(
            intent="preference", domain="technical", emotion="confident",
            confidence={"intent": 0.9, "domain": 0.85, "emotion": 0.7},
        )
        assert c.intent == "preference"
        assert c.domain == "technical"
        assert c.emotion == "confident"
        assert c.confidence["intent"] == 0.9

    def test_to_dict(self):
        c = Classification(
            intent="question", domain="personal", emotion="curious",
            confidence={"intent": 0.8, "domain": 0.6, "emotion": 0.7},
        )
        d = c.to_dict()
        assert d["intent"] == "question"
        assert d["domain"] == "personal"
        assert d["emotion"] == "curious"
        assert d["confidence"]["intent"] == 0.8
        assert "low_confidence" not in d

    def test_to_dict_with_low_confidence(self):
        c = Classification(
            intent="statement", domain="personal", emotion="neutral",
            confidence={"intent": 0.3, "domain": 0.3, "emotion": 0.3},
            low_confidence=True,
        )
        d = c.to_dict()
        assert d["low_confidence"] is True

    def test_default_low_confidence_false(self):
        c = Classification(
            intent="statement", domain="personal", emotion="neutral",
            confidence={},
        )
        assert c.low_confidence is False


# ── make_classification tests ───────────────────────────────────────


class TestMakeClassification:
    def test_valid_labels(self):
        c = make_classification(
            intent="preference", domain="technical", emotion="confident",
            confidence={"intent": 0.9, "domain": 0.85, "emotion": 0.7},
        )
        assert c.intent == "preference"
        assert c.domain == "technical"

    def test_invalid_intent(self):
        with pytest.raises(ValueError, match="Unknown intent"):
            make_classification(
                intent="unknown", domain="technical", emotion="neutral",
                confidence={},
            )

    def test_invalid_domain(self):
        with pytest.raises(ValueError, match="Unknown domain"):
            make_classification(
                intent="statement", domain="invalid", emotion="neutral",
                confidence={},
            )

    def test_invalid_emotion(self):
        with pytest.raises(ValueError, match="Unknown emotion"):
            make_classification(
                intent="statement", domain="personal", emotion="angry",
                confidence={},
            )

    def test_confidence_clamping(self):
        c = make_classification(
            intent="statement", domain="personal", emotion="neutral",
            confidence={"intent": 1.5, "domain": -0.1, "emotion": 0.5},
        )
        assert c.confidence["intent"] == 1.0
        assert c.confidence["domain"] == 0.0
        assert c.confidence["emotion"] == 0.5


# ── Classifier ABC test ────────────────────────────────────────────


class TestClassifierABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            Classifier()  # type: ignore[abstract]


# ── LLMClassifier tests (mocked provider) ──────────────────────────


def _mock_provider(response: str) -> LLMProvider:
    """Create a mock LLMProvider returning a fixed response."""
    provider = MagicMock(spec=LLMProvider)
    provider.complete.return_value = response
    return provider


def _mock_error_provider(error: Exception) -> LLMProvider:
    provider = MagicMock(spec=LLMProvider)
    provider.complete.side_effect = error
    return provider


class TestLLMClassifier:
    def test_valid_json_response(self):
        response = json.dumps({
            "intent": "preference",
            "domain": "technical",
            "emotion": "confident",
            "confidence": {"intent": 0.92, "domain": 0.88, "emotion": 0.75},
        })
        clf = LLMClassifier(_mock_provider(response))
        result = clf.classify("I always use bun")
        assert result.intent == "preference"
        assert result.domain == "technical"
        assert result.emotion == "confident"
        assert result.confidence["intent"] == 0.92

    def test_json_with_markdown_fences(self):
        inner = json.dumps({
            "intent": "question",
            "domain": "technical",
            "emotion": "curious",
            "confidence": {"intent": 0.9, "domain": 0.8, "emotion": 0.7},
        })
        response = f"```json\n{inner}\n```"
        clf = LLMClassifier(_mock_provider(response))
        result = clf.classify("How do I deploy?")
        assert result.intent == "question"

    def test_malformed_json_fallback(self):
        clf = LLMClassifier(_mock_provider("this is not json"))
        result = clf.classify("How do I deploy to staging?")
        # Should fall back to rule-based
        assert result.intent == "question"  # rule-based catches the "?"
        assert result.confidence["intent"] <= 0.6

    def test_llm_exception_fallback(self):
        clf = LLMClassifier(_mock_error_provider(ConnectionError("timeout")))
        result = clf.classify("The deploy took 12 minutes today")
        # Should fall back to rule-based — no exception raised
        assert result.intent in INTENT_LABELS
        assert result.domain in DOMAIN_LABELS
        assert result.emotion in EMOTION_LABELS

    def test_invalid_intent_per_axis_fallback(self):
        response = json.dumps({
            "intent": "unknown_intent",
            "domain": "technical",
            "emotion": "confident",
            "confidence": {"intent": 0.9, "domain": 0.88, "emotion": 0.75},
        })
        clf = LLMClassifier(_mock_provider(response))
        result = clf.classify("I always use bun")
        # intent falls back to rule-based, domain/emotion keep LLM values
        assert result.intent in INTENT_LABELS
        assert result.domain == "technical"
        assert result.emotion == "confident"

    def test_invalid_domain_per_axis_fallback(self):
        response = json.dumps({
            "intent": "preference",
            "domain": "alien",
            "emotion": "neutral",
            "confidence": {"intent": 0.9, "domain": 0.9, "emotion": 0.9},
        })
        clf = LLMClassifier(_mock_provider(response))
        result = clf.classify("test text")
        assert result.intent == "preference"
        assert result.domain in DOMAIN_LABELS

    def test_invalid_emotion_per_axis_fallback(self):
        response = json.dumps({
            "intent": "statement",
            "domain": "personal",
            "emotion": "angry",
            "confidence": {"intent": 0.9, "domain": 0.9, "emotion": 0.9},
        })
        clf = LLMClassifier(_mock_provider(response))
        result = clf.classify("test text")
        assert result.emotion in EMOTION_LABELS

    def test_confidence_clamped(self):
        response = json.dumps({
            "intent": "statement",
            "domain": "personal",
            "emotion": "neutral",
            "confidence": {"intent": 1.5, "domain": -0.1, "emotion": 0.5},
        })
        clf = LLMClassifier(_mock_provider(response))
        result = clf.classify("test")
        assert result.confidence["intent"] == 1.0
        assert result.confidence["domain"] == 0.0
        assert result.confidence["emotion"] == 0.5

    def test_missing_confidence_defaults(self):
        response = json.dumps({
            "intent": "statement",
            "domain": "personal",
            "emotion": "neutral",
        })
        clf = LLMClassifier(_mock_provider(response))
        result = clf.classify("test")
        assert result.confidence["intent"] == 0.5
        assert result.confidence["domain"] == 0.5
        assert result.confidence["emotion"] == 0.5

    def test_prompt_contains_text(self):
        text = "I prefer dark mode"
        clf = LLMClassifier(_mock_provider('{}'))
        prompt = clf._build_prompt(text)
        assert text in prompt

    def test_empty_string_no_crash(self):
        clf = LLMClassifier(_mock_error_provider(ValueError("bad")))
        result = clf.classify("")
        assert result.intent in INTENT_LABELS
