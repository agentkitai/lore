# src/lore/services/retrieve.py
"""Retrieve service: vector recall + formatting + session-context injection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from lore.persistence import (
    NewRetrievalEvent,
    RecallParams,
    ScoredMemory,
    Store,
    StoredMemory,
)

logger = logging.getLogger(__name__)

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


async def record_retrieval_event(
    store: Store,
    *,
    org_id: str,
    query_text: str,
    memory_ids: Sequence[str],
    scores: Sequence[float],
    min_score: float,
    elapsed_ms: float,
    fmt: str,
    project: Optional[str],
) -> None:
    """Insert a retrieval_events row + update Prometheus metrics. Fire-and-forget."""
    try:
        from lore.server.metrics import (
            retrieve_empty_total,
            retrieve_latency,
            retrieve_max_score,
            retrieve_queries_total,
            retrieve_results_total,
        )

        retrieve_queries_total.inc()
        retrieve_results_total.inc(amount=float(len(memory_ids)))
        if not memory_ids:
            retrieve_empty_total.inc()
        retrieve_latency.observe(elapsed_ms / 1000.0)

        max_sc = max(scores) if scores else 0.0
        if scores:
            retrieve_max_score.observe(max_sc)

        avg_sc = sum(scores) / len(scores) if scores else None

        event = NewRetrievalEvent(
            org_id=org_id,
            query=query_text,
            results_count=len(memory_ids),
            scores=list(scores),
            memory_ids=list(memory_ids),
            avg_score=avg_sc,
            max_score=max_sc if scores else None,
            min_score_threshold=min_score,
            query_time_ms=elapsed_ms,
            project=project,
            format=fmt,
        )
        await store.record_retrieval_event(event)
    except Exception:
        logger.warning("Failed to record retrieval event", exc_info=True)


async def bump_access_counts(
    store: Store,
    org_id: str,
    memory_ids: Sequence[str],
) -> None:
    """Bump access_count + last_accessed_at + importance for the given memories. Fire-and-forget."""
    try:
        await store.bump_access_counts(org_id, memory_ids)
    except Exception:
        logger.warning("Failed to bump access counts", exc_info=True)


async def recent_session_snapshots(
    store: Store,
    *,
    org_id: str,
    project: Optional[str] = None,
    exclude_ids: Sequence[str] = (),
    limit: int = 3,
) -> Sequence[StoredMemory]:
    """Recent (last-24h) session-snapshot memories for the org. Returns empty on error."""
    try:
        return await store.list_recent_session_snapshots(
            org_id, project=project, exclude_ids=exclude_ids, limit=limit
        )
    except Exception:
        logger.warning("Failed to fetch session snapshots", exc_info=True)
        return ()
