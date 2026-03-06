"""LLM-backed classifier with rule-based fallback."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from lore.classify.base import Classification, Classifier
from lore.classify.rules import RuleBasedClassifier
from lore.classify.taxonomies import DOMAIN_LABELS, EMOTION_LABELS, INTENT_LABELS
from lore.llm.base import LLMProvider

logger = logging.getLogger(__name__)

CLASSIFY_PROMPT = """Classify the following text along three axes.

Text: "{content}"

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "intent": one of [question, statement, instruction, preference, observation, decision],
  "domain": one of [technical, personal, business, creative, administrative],
  "emotion": one of [neutral, frustrated, excited, curious, confident, uncertain],
  "confidence": {{
    "intent": 0.0-1.0,
    "domain": 0.0-1.0,
    "emotion": 0.0-1.0
  }}
}}"""


class LLMClassifier(Classifier):
    """LLM-backed classification with rule-based fallback."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._fallback = RuleBasedClassifier()

    def classify(self, text: str) -> Classification:
        try:
            response = self._provider.complete(
                self._build_prompt(text),
                max_tokens=200,
            )
            return self._parse_response(response, text)
        except Exception:
            logger.debug("LLM classification failed, falling back to rules", exc_info=True)
            return self._fallback.classify(text)

    def _build_prompt(self, text: str) -> str:
        return CLASSIFY_PROMPT.format(content=text)

    def _parse_response(self, response: str, original_text: str) -> Classification:
        """Parse JSON from LLM response. Falls back per-axis on invalid labels."""
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            data: Dict[str, Any] = json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError("Malformed JSON from LLM")

        intent = data.get("intent", "statement")
        domain = data.get("domain", "personal")
        emotion = data.get("emotion", "neutral")

        # Per-axis fallback for invalid labels
        conf = data.get("confidence", {})
        if intent not in INTENT_LABELS:
            intent = self._fallback._classify_intent(original_text)
            conf["intent"] = 0.6
        if domain not in DOMAIN_LABELS:
            domain = self._fallback._classify_domain(original_text)
            conf["domain"] = 0.6
        if emotion not in EMOTION_LABELS:
            emotion = self._fallback._classify_emotion(original_text)
            conf["emotion"] = 0.6

        # Default confidence for missing axes
        for axis in ("intent", "domain", "emotion"):
            if axis not in conf:
                conf[axis] = 0.5

        # Clamp confidence to [0.0, 1.0]
        clamped = {k: max(0.0, min(1.0, float(v))) for k, v in conf.items()}

        return Classification(
            intent=intent,
            domain=domain,
            emotion=emotion,
            confidence=clamped,
        )
