"""Prompt templates for fact extraction."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from lore.types import Fact


def build_extraction_prompt(
    content: str,
    existing_facts: Optional[List[Fact]] = None,
    enrichment_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the LLM prompt for fact extraction."""
    existing_json = "[]"
    if existing_facts:
        existing_json = json.dumps(
            [
                {
                    "id": f.id,
                    "subject": f.subject,
                    "predicate": f.predicate,
                    "object": f.object,
                    "confidence": f.confidence,
                }
                for f in existing_facts
            ],
            indent=2,
        )

    context_section = ""
    if enrichment_context:
        context_section = f"\nENRICHMENT CONTEXT:\n{json.dumps(enrichment_context, indent=2)}\n"

    return f"""\
Extract atomic facts from the following memory content. Each fact should be a
(subject, predicate, object) triple.

CONTENT:
{content}
{context_section}
EXISTING FACTS for related subjects:
{existing_json}

For each extracted fact:
1. Identify the subject (entity or concept) — use lowercase, trimmed
2. Identify the predicate (relationship or attribute) — use lowercase, snake_case
3. Identify the object (value)
4. Assign a confidence score (0.0-1.0)
5. If an existing fact has the same subject+predicate, classify the resolution:
   - SUPERSEDE: the new fact replaces the old (e.g., temporal update, correction)
   - MERGE: both facts are true simultaneously (complementary, not contradictory)
   - CONTRADICT: genuine contradiction that needs human review
   - NOOP: no conflict (new subject+predicate pair, or same value)
6. If the resolution is not NOOP, include the existing fact's id in "conflicts_with"

Return JSON only (no markdown, no explanation):
{{
  "facts": [
    {{
      "subject": "...",
      "predicate": "...",
      "object": "...",
      "confidence": 0.95,
      "resolution": "NOOP",
      "reasoning": "...",
      "conflicts_with": null
    }}
  ]
}}"""
