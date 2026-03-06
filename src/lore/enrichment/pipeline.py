"""Enrichment pipeline for extracting structured metadata from memory content."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lore.enrichment.llm import LLMClient
from lore.enrichment.prompts import build_extraction_prompt

logger = logging.getLogger(__name__)

VALID_CATEGORIES = frozenset({
    "infrastructure", "architecture", "debugging", "workflow",
    "learning", "preference", "incident", "convention",
    "planning", "documentation", "testing", "security",
    "performance", "other",
})

VALID_ENTITY_TYPES = frozenset({
    "person", "tool", "project", "platform",
    "organization", "concept", "language", "framework",
})

VALID_SENTIMENTS = frozenset({"positive", "negative", "neutral"})


class EnrichmentResult:
    """Parsed, validated enrichment data ready for storage."""

    def __init__(
        self,
        topics: List[str],
        sentiment: Dict[str, Any],
        entities: List[Dict[str, str]],
        categories: List[str],
        enriched_at: str,
        enrichment_model: str,
    ) -> None:
        self.topics = topics
        self.sentiment = sentiment
        self.entities = entities
        self.categories = categories
        self.enriched_at = enriched_at
        self.enrichment_model = enrichment_model

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topics": self.topics,
            "sentiment": self.sentiment,
            "entities": self.entities,
            "categories": self.categories,
            "enriched_at": self.enriched_at,
            "enrichment_model": self.enrichment_model,
        }


class EnrichmentPipeline:
    """Extracts structured metadata from memory content using an LLM."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def enrich(self, content: str, context: Optional[str] = None) -> Dict[str, Any]:
        """Extract topics, sentiment, entities, categories from content.

        Returns enrichment dict ready to store in metadata["enrichment"].
        Raises on LLM failure -- caller must handle.
        """
        if not self.llm.check_api_key():
            raise RuntimeError("API key not configured")

        prompt = build_extraction_prompt(content, context)
        response = self.llm.complete(prompt)
        result = self._parse_and_validate(response)
        result["enriched_at"] = datetime.now(timezone.utc).isoformat()
        result["enrichment_model"] = self.llm.model
        return result

    def _parse_and_validate(self, response: str) -> Dict[str, Any]:
        """Parse LLM JSON response and validate/sanitize fields.

        Best-effort: returns partial results for malformed responses.
        """
        result: Dict[str, Any] = {
            "topics": [],
            "sentiment": {"label": "neutral", "score": 0.0},
            "entities": [],
            "categories": [],
        }

        try:
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:])
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Enrichment: malformed JSON response: %s", e)
            return result

        # Topics: list of 1-5 lowercase strings
        if isinstance(data.get("topics"), list):
            topics = [
                str(t).lower().strip()
                for t in data["topics"]
                if isinstance(t, str) and t.strip()
            ]
            result["topics"] = topics[:5]

        # Sentiment: {label, score}
        if isinstance(data.get("sentiment"), dict):
            sent = data["sentiment"]
            label = str(sent.get("label", "neutral")).lower()
            if label not in VALID_SENTIMENTS:
                label = "neutral"
            score = sent.get("score", 0.0)
            try:
                score = float(score)
                score = max(-1.0, min(1.0, score))
            except (TypeError, ValueError):
                score = 0.0
            result["sentiment"] = {"label": label, "score": score}

        # Entities: list of {name, type}
        if isinstance(data.get("entities"), list):
            entities = []
            for e in data["entities"]:
                if not isinstance(e, dict):
                    continue
                name = str(e.get("name", "")).strip()
                etype = str(e.get("type", "concept")).lower().strip()
                if not name:
                    continue
                if etype not in VALID_ENTITY_TYPES:
                    etype = "concept"
                entities.append({"name": name, "type": etype})
            result["entities"] = entities

        # Categories: list of 1-3 from fixed set
        if isinstance(data.get("categories"), list):
            categories = [
                str(c).lower().strip()
                for c in data["categories"]
                if isinstance(c, str) and str(c).lower().strip() in VALID_CATEGORIES
            ]
            result["categories"] = categories[:3]

        return result
