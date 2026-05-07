# src/lore/services/retrieve.py
"""Retrieve service: vector recall + formatting + session-context injection.

Phase 6C added hybrid retrieval — vector similarity is fused with full-text
rank, graph proximity, recency, and importance via Reciprocal Rank Fusion
(RRF). The legacy single-signal ``retrieve()`` entry point still works for
back-compat (it delegates to ``recall_by_embedding``); the hybrid path is
``hybrid_retrieve()`` and the underlying primitive is ``_hybrid_recall()``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lore.persistence import (
    NewRetrievalEvent,
    RecallParams,
    ResolvedProfile,
    ScoredMemory,
    Store,
    StoredMemory,
)

logger = logging.getLogger(__name__)

VALID_FORMATS = {"xml", "markdown", "raw"}

# RRF dampener — the standard literature value (Cormack et al. 2009). Tunable
# later via env var if we ever want to surface it.
_RRF_K = 60


@dataclass(frozen=True)
class RetrieveOutput:
    memories: Sequence[ScoredMemory]
    formatted: str
    count: int


@dataclass(frozen=True)
class HybridResult:
    """Output of ``_hybrid_recall``: a memory with its fused score + per-signal breakdown."""

    memory: StoredMemory
    score: float
    signals: Mapping[str, float]


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


# ── Phase 6C: Hybrid retrieval ────────────────────────────────────────────────


# Per the spec: profile fallback when no profile resolves. Mirrors the single
# preset's defaults that today's path implicitly uses.
_DEFAULT_HYBRID_PROFILE = ResolvedProfile(
    name="__default__",
    source="default",
    semantic_weight=1.0,
    graph_weight=0.5,
    recency_bias=30.0,
    min_score=0.3,
    max_results=10,
    tier_filters=None,
    k=None,
    threshold=None,
    rerank=False,
    include_graph=True,
    fts_weight=1.0,
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")


@dataclass(frozen=True)
class HybridParams:
    """Inputs to ``_hybrid_recall``."""

    org_id: str
    query_text: str
    query_vec: Sequence[float]
    limit: int = 5
    project: Optional[str] = None
    half_life_days: int = 30


def _recency_signal(created_at: Optional[datetime], recency_bias: float) -> float:
    """Exponential decay ``exp(-age_days / recency_bias)``.

    Returns a value in (0, 1]. ``recency_bias <= 0`` collapses to 0 because
    a non-positive timescale is nonsensical; rather than divide-by-zero we
    return 0 (no recency weight).
    """
    if created_at is None or recency_bias is None or recency_bias <= 0:
        return 0.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return math.exp(-age_days / recency_bias)


def _rrf_fuse(
    sources: Sequence[Tuple[Sequence[Tuple[StoredMemory, float]], float]],
    *,
    k: int = _RRF_K,
) -> List[Tuple[StoredMemory, float, Dict[str, float]]]:
    """Reciprocal Rank Fusion over weighted ranked candidate lists.

    ``sources`` is ``[(ranked_candidates, weight), ...]`` where
    ``ranked_candidates`` is already sorted by descending raw score. Each
    entry contributes ``weight / (k + rank)`` to its memory's fused score.

    The raw RRF score is then **normalized by the sum of weights divided by
    (k+1)** so the theoretical maximum (item at rank 0 in every source) maps
    to 1.0. This keeps RRF's robust ordering behaviour but produces a
    user-friendly 0–1 range that interacts sensibly with profile-level
    ``min_score`` thresholds (which historically applied to vector cosine
    similarity in the same range).

    Returns the union of memories sorted by descending fused score, plus a
    per-signal breakdown. Signal keys are positional: ``signal_0``,
    ``signal_1``, ... The caller relabels them (vector / fts / graph)
    because RRF itself is signal-agnostic.
    """
    fused: Dict[str, float] = {}
    by_id: Dict[str, StoredMemory] = {}
    signals: Dict[str, Dict[str, float]] = {}
    total_weight = sum(max(0.0, float(w)) for _cands, w in sources)
    # Normalizer: top-rank-everywhere maps to 1.0. Avoid /0 when no weights set.
    normalizer = (total_weight / (k + 1)) if total_weight > 0 else 1.0
    for idx, (candidates, weight) in enumerate(sources):
        signal_name = f"signal_{idx}"
        w = max(0.0, float(weight))
        for rank, (memory, raw_score) in enumerate(candidates):
            mid = memory.id
            contribution = w / (k + rank + 1)
            fused[mid] = fused.get(mid, 0.0) + contribution
            by_id.setdefault(mid, memory)
            sigs = signals.setdefault(mid, {})
            # Track the raw score per signal so the route can surface it.
            # Take max in case the memory shows up twice in the same source
            # (shouldn't happen, but defensive).
            sigs[signal_name] = max(sigs.get(signal_name, 0.0), float(raw_score))
    out: List[Tuple[StoredMemory, float, Dict[str, float]]] = []
    for mid, score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True):
        out.append((by_id[mid], score / normalizer, signals[mid]))
    return out


async def _safe_text_recall(
    store: Store,
    org_id: str,
    query: str,
    *,
    limit: int,
    project: Optional[str],
) -> Sequence[Tuple[StoredMemory, float]]:
    """Wrap ``store.recall_by_text`` so missing migrations / FTS errors don't kill the call."""
    if not hasattr(store, "recall_by_text"):
        return []
    try:
        return await store.recall_by_text(org_id, query, limit=limit, project=project)
    except Exception:
        logger.warning("recall_by_text failed; falling through with no FTS signal", exc_info=True)
        return []


async def _safe_graph_recall(
    store: Store,
    org_id: str,
    query: str,
    *,
    limit: int,
) -> Sequence[Tuple[StoredMemory, int]]:
    """Extract entity ids from ``query`` (best-effort) and call ``recall_by_entities``.

    Punts to "no graph candidates" gracefully when no extractor is available
    or the lookup raises. Phase 6C uses a simple tokeniser + ``get_entity_by_name``
    lookup against the existing entities table — Phase 6D will swap in a
    proper extractor.
    """
    if not hasattr(store, "recall_by_entities"):
        return []
    try:
        # Best-effort entity resolution: split on word boundaries, look up
        # each token (and bigrams) by name. Cheap, deterministic, and
        # zero-config; misses everything that wasn't already registered as
        # an entity, which is fine — the FTS branch covers the long tail.
        tokens = _TOKEN_RE.findall(query)
        candidates: list[str] = []
        seen: set[str] = set()
        for tok in tokens:
            for variant in (tok, tok.lower(), tok.title()):
                if variant in seen:
                    continue
                seen.add(variant)
                candidates.append(variant)
        # Bigrams (e.g. "New York")
        for i in range(len(tokens) - 1):
            bg = f"{tokens[i]} {tokens[i + 1]}"
            if bg not in seen:
                seen.add(bg)
                candidates.append(bg)
        entity_ids: list[str] = []
        if hasattr(store, "get_entity_by_name"):
            for cand in candidates:
                try:
                    ent = await store.get_entity_by_name(cand)
                except Exception:
                    continue
                if ent is not None:
                    entity_ids.append(ent.id)
        if not entity_ids:
            return []
        return await store.recall_by_entities(org_id, entity_ids, limit=limit)
    except Exception:
        logger.warning("recall_by_entities failed; falling through with no graph signal", exc_info=True)
        return []


async def _hybrid_recall(
    store: Store,
    profile: ResolvedProfile,
    params: HybridParams,
) -> Sequence[HybridResult]:
    """Hybrid recall: vector + FTS + graph fused via RRF, then recency × importance.

    Each branch is independently optional — an exception in one signal
    degrades it to "no contribution" rather than failing the whole call
    (uses ``asyncio.gather(..., return_exceptions=True)``).
    """
    fan_out = max(params.limit, 1) * 4
    graph_fan_out = max(params.limit, 1) * 2

    vec_task = store.recall_by_embedding(
        RecallParams(
            org_id=params.org_id,
            query_vec=params.query_vec,
            limit=fan_out,
            min_score=0.0,  # post-RRF threshold lives on the profile
            project=params.project,
            half_life_days=params.half_life_days,
        )
    )
    fts_task = _safe_text_recall(
        store, params.org_id, params.query_text, limit=fan_out, project=params.project
    )
    graph_task = _safe_graph_recall(
        store, params.org_id, params.query_text, limit=graph_fan_out
    )

    vec_raw, fts_raw, graph_raw = await asyncio.gather(
        vec_task, fts_task, graph_task, return_exceptions=True
    )

    def _normalize(raw: Any) -> Sequence[Tuple[StoredMemory, float]]:
        if isinstance(raw, BaseException):
            logger.warning("Hybrid recall branch failed: %s", raw)
            return []
        return raw or []

    vec_list = _normalize(vec_raw)
    fts_list = _normalize(fts_raw)
    graph_list = _normalize(graph_raw)

    # ScoredMemory for vector → coerce to (StoredMemory, float). Convert all
    # branches into the same uniform shape RRF expects.
    vec_pairs: list[Tuple[StoredMemory, float]] = [
        (m, float(getattr(m, "score", 0.0))) for m in vec_list
    ]
    fts_pairs: list[Tuple[StoredMemory, float]] = [
        (m, float(s)) for m, s in fts_list  # type: ignore[misc]
    ]
    graph_pairs: list[Tuple[StoredMemory, float]] = [
        (m, float(s)) for m, s in graph_list  # type: ignore[misc]
    ]

    fused = _rrf_fuse(
        sources=[
            (vec_pairs, max(0.0, profile.semantic_weight)),
            (fts_pairs, max(0.0, profile.fts_weight)),
            (graph_pairs, max(0.0, profile.graph_weight)),
        ],
        k=_RRF_K,
    )

    # Phase 6F: annotate the fused candidates with supersession state. Hard
    # filtering is the wrong call (explicit at_time queries still want to
    # see the row); instead we score-multiply by 0.1 so the natural
    # ``min_score`` filter downstream tends to drop them. Empty candidate
    # set short-circuits — no point in a round trip.
    superseded_set: set[str] = set()
    if fused and hasattr(store, "are_superseded"):
        candidate_ids = {memory.id for memory, *_ in fused}
        try:
            superseded_set = await store.are_superseded(candidate_ids)
        except Exception:
            logger.warning(
                "are_superseded failed; skipping supersession suppression",
                exc_info=True,
            )
            superseded_set = set()

    # Annotate with recency + importance, multiplicative.
    annotated: list[HybridResult] = []
    for memory, base_score, raw_signals in fused[: max(params.limit * 2, params.limit)]:
        recency = _recency_signal(memory.created_at, profile.recency_bias)
        importance = (
            float(memory.importance_score) if memory.importance_score is not None else 0.5
        )
        is_superseded = memory.id in superseded_set
        supersession_multiplier = 0.1 if is_superseded else 1.0
        # multiplicative annotations: recency multiplier ∈ [1.0, 1.5];
        # importance multiplier ∈ [0.75, 1.25] for importance ∈ [0, 1].
        final = (
            base_score
            * (1.0 + 0.5 * recency)
            * (1.0 + 0.5 * (importance - 0.5))
            * supersession_multiplier
        )
        signals: Dict[str, float] = {
            "vector": raw_signals.get("signal_0", 0.0),
            "fts": raw_signals.get("signal_1", 0.0),
            "graph": raw_signals.get("signal_2", 0.0),
            "recency": recency,
            "importance": importance,
            # ``superseded`` lands in signals so the route can surface it
            # alongside the per-signal breakdown. Float (1.0/0.0) keeps
            # the existing ``Mapping[str, float]`` shape from changing.
            "superseded": 1.0 if is_superseded else 0.0,
        }
        annotated.append(HybridResult(memory=memory, score=final, signals=signals))

    annotated = [r for r in annotated if r.score >= profile.min_score]
    annotated.sort(key=lambda r: r.score, reverse=True)
    return annotated[: params.limit]


async def hybrid_retrieve(
    store: Store,
    *,
    org_id: str,
    query_text: str,
    query_vec: Sequence[float],
    limit: int = 5,
    project: Optional[str] = None,
    profile: Optional[ResolvedProfile] = None,
    half_life_days: int = 30,
    min_score_override: Optional[float] = None,
) -> Sequence[HybridResult]:
    """Public entry point for Phase 6C hybrid recall.

    Resolves the profile (falling back to the same defaults today's vector
    path uses) and dispatches to ``_hybrid_recall``. The route may pass
    ``min_score_override`` to honor an HTTP-level ``min_score`` query param;
    when supplied it replaces ``profile.min_score`` so a stricter threshold
    can be applied without mutating the cached profile.
    """
    effective = profile or _DEFAULT_HYBRID_PROFILE
    if min_score_override is not None:
        # Build a one-off profile with the overridden threshold; cheap dataclass swap.
        effective = ResolvedProfile(
            name=effective.name,
            source=effective.source,
            semantic_weight=effective.semantic_weight,
            graph_weight=effective.graph_weight,
            recency_bias=effective.recency_bias,
            min_score=min_score_override,
            max_results=effective.max_results,
            tier_filters=effective.tier_filters,
            k=effective.k,
            threshold=effective.threshold,
            rerank=effective.rerank,
            include_graph=effective.include_graph,
            fts_weight=effective.fts_weight,
        )
    return await _hybrid_recall(
        store,
        effective,
        HybridParams(
            org_id=org_id,
            query_text=query_text,
            query_vec=query_vec,
            limit=limit,
            project=project,
            half_life_days=half_life_days,
        ),
    )
