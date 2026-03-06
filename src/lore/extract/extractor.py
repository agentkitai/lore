"""LLM-powered fact extraction from memory content."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ulid import ULID

from lore.extract.prompts import build_extraction_prompt
from lore.store.base import Store
from lore.types import Fact, VALID_RESOLUTIONS

logger = logging.getLogger(__name__)

# Type for the LLM call function: takes a prompt string, returns response string.
LLMClient = Callable[[str], str]


@dataclass
class ExtractedFact:
    """A fact extracted by the LLM, with resolution metadata."""

    fact: Fact
    resolution: str  # one of VALID_RESOLUTIONS
    reasoning: str
    conflicting_fact: Optional[Fact] = None


class FactExtractor:
    """Extracts atomic facts from memory content using an LLM."""

    def __init__(
        self,
        llm_client: LLMClient,
        store: Store,
        confidence_threshold: float = 0.3,
    ) -> None:
        self._llm = llm_client
        self._store = store
        self._confidence_threshold = confidence_threshold

    def extract(
        self,
        memory_id: str,
        content: str,
        enrichment_context: Optional[Dict[str, Any]] = None,
    ) -> List[ExtractedFact]:
        """Extract facts from content with conflict context from existing facts."""
        if not content.strip():
            return []

        # Gather existing facts for subjects that might be mentioned
        existing_facts = self._store.get_active_facts(limit=200)

        prompt = build_extraction_prompt(content, existing_facts, enrichment_context)
        raw_response = self._llm(prompt)
        return self._parse_response(raw_response, memory_id, existing_facts)

    def extract_preview(self, text: str) -> List[Fact]:
        """Extract facts from text without store context (stateless preview)."""
        if not text.strip():
            return []

        prompt = build_extraction_prompt(text)
        raw_response = self._llm(prompt)
        extracted = self._parse_response(raw_response, memory_id="preview", existing_facts=[])
        return [ef.fact for ef in extracted]

    def _parse_response(
        self,
        raw: str,
        memory_id: str,
        existing_facts: List[Fact],
    ) -> List[ExtractedFact]:
        """Parse LLM JSON response into ExtractedFact objects."""
        json_str = self._extract_json(raw)
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Malformed JSON from LLM: %s", raw[:200])
            return []

        facts_data = data.get("facts", [])
        if not isinstance(facts_data, list):
            logger.warning("LLM response 'facts' is not a list")
            return []

        # Build lookup of existing facts by id
        existing_by_id = {f.id: f for f in existing_facts}

        now = datetime.now(timezone.utc).isoformat()
        results: List[ExtractedFact] = []

        for item in facts_data:
            if not isinstance(item, dict):
                continue

            subject = self._normalize_subject(item.get("subject", ""))
            predicate = self._normalize_predicate(item.get("predicate", ""))
            obj = str(item.get("object", "")).strip()

            if not subject or not predicate or not obj:
                continue

            confidence = self._clamp_confidence(item.get("confidence", 1.0))
            if confidence < self._confidence_threshold:
                continue

            resolution = item.get("resolution", "NOOP")
            if resolution not in VALID_RESOLUTIONS:
                resolution = "NOOP"

            reasoning = str(item.get("reasoning", ""))

            # Resolve conflicting fact reference
            conflicts_with_id = item.get("conflicts_with")
            conflicting_fact = existing_by_id.get(conflicts_with_id) if conflicts_with_id else None

            fact = Fact(
                id=str(ULID()),
                memory_id=memory_id,
                subject=subject,
                predicate=predicate,
                object=obj,
                confidence=confidence,
                extracted_at=now,
            )

            results.append(
                ExtractedFact(
                    fact=fact,
                    resolution=resolution,
                    reasoning=reasoning,
                    conflicting_fact=conflicting_fact,
                )
            )

        return results

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from text, handling markdown code blocks."""
        # Try to extract from ```json ... ``` blocks
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    @staticmethod
    def _normalize_subject(s: str) -> str:
        """Normalize subject: lowercase, trimmed."""
        return str(s).strip().lower()

    @staticmethod
    def _normalize_predicate(p: str) -> str:
        """Normalize predicate: lowercase, trimmed, spaces to underscores."""
        return str(p).strip().lower().replace(" ", "_")

    @staticmethod
    def _clamp_confidence(val: Any) -> float:
        """Clamp confidence to [0.0, 1.0]."""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return 1.0
        return max(0.0, min(1.0, v))
