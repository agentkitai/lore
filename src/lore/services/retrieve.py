# src/lore/services/retrieve.py
"""Retrieve service: vector recall + formatting + session-context injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from lore.persistence import RecallParams, ScoredMemory, Store

VALID_FORMATS = {"xml", "markdown", "raw"}


@dataclass(frozen=True)
class RetrieveOutput:
    memories: Sequence[ScoredMemory]
    formatted: str
    count: int


def _format_xml(memories: Sequence[ScoredMemory], query: str) -> str:
    if not memories:
        return ""
    lines = [f'<memories query="{query}">']
    for m in memories:
        m_type = (m.meta or {}).get("type", "unknown")
        lines.append(f'  <memory id="{m.id}" score="{m.score:.2f}" type="{m_type}">')
        lines.append(f"    {m.content}")
        lines.append("  </memory>")
    lines.append("</memories>")
    return "\n".join(lines)


def _format_markdown(memories: Sequence[ScoredMemory], query: str) -> str:
    if not memories:
        return ""
    lines = [f"## Relevant Memories ({len(memories)})\n"]
    for m in memories:
        lines.append(f"- **[{m.score:.2f}]** {m.content}")
    return "\n".join(lines)


def _format_raw(memories: Sequence[ScoredMemory], query: str) -> str:
    return "\n".join(m.content for m in memories) if memories else ""


_FORMATTERS = {
    "xml": _format_xml,
    "markdown": _format_markdown,
    "raw": _format_raw,
}


async def retrieve(
    store: Store,
    *,
    org_id: str,
    query_text: str,
    query_vec: Sequence[float],
    limit: int = 5,
    min_score: float = 0.3,
    project: Optional[str] = None,
    format: str = "xml",
    half_life_days: int = 30,
) -> RetrieveOutput:
    """Vector recall + formatting. Returns a typed RetrieveOutput.

    Note: analytics recording and access-count bumping are intentionally
    left at the route layer for Phase 1A; they will move into this service
    once AnalyticsOps lands on the Store (Phase 1F).
    """
    if format not in VALID_FORMATS:
        raise ValueError(
            f"Invalid format {format!r}. Must be one of: {sorted(VALID_FORMATS)}"
        )

    results = await store.recall_by_embedding(
        RecallParams(
            org_id=org_id,
            query_vec=query_vec,
            limit=limit,
            min_score=min_score,
            project=project,
            half_life_days=half_life_days,
        )
    )
    formatted = _FORMATTERS[format](results, query_text)
    return RetrieveOutput(memories=results, formatted=formatted, count=len(results))
