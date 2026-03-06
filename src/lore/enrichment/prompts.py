"""Extraction prompt templates for metadata enrichment."""

from __future__ import annotations

from typing import Optional

_EXTRACTION_TEMPLATE = """\
Extract structured metadata from the following text. Return a JSON object with these fields:

- "topics": list of 1-5 topic keywords (lowercase). What is this text about?
- "sentiment": {{"label": "positive"|"negative"|"neutral", "score": float from -1.0 to 1.0}}
- "entities": list of {{"name": string, "type": string}} where type is one of: person, tool, project, platform, organization, concept, language, framework
- "categories": list of 1-3 categories from this set: infrastructure, architecture, debugging, workflow, learning, preference, incident, convention, planning, documentation, testing, security, performance, other

Text:
\"\"\"
{content}
\"\"\"
{context_section}
Return ONLY valid JSON. No explanation."""

_CONTEXT_SECTION = """
Additional context:
\"\"\"
{context}
\"\"\""""


def build_extraction_prompt(content: str, context: Optional[str] = None) -> str:
    """Build the extraction prompt for a memory's content."""
    context_section = ""
    if context:
        context_section = _CONTEXT_SECTION.format(context=context)
    return _EXTRACTION_TEMPLATE.format(
        content=content,
        context_section=context_section,
    )
