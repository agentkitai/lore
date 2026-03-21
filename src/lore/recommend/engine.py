"""Recommendation engine — multi-signal scoring."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from lore.recommend.types import Recommendation, RecommendationSignal

logger = logging.getLogger(__name__)

# Default signal weights
DEFAULT_WEIGHTS = {
    "context_similarity": 0.4,
    "entity_overlap": 0.25,
    "temporal_pattern": 0.1,
    "access_pattern": 0.15,
    "graph_proximity": 0.1,
}


class RecommendationEngine:
    """Generate proactive memory recommendations."""

    def __init__(
        self,
        store: Any,
        embedder: Any,
        weights: Optional[Dict[str, float]] = None,
        aggressiveness: float = 0.5,
        max_suggestions: int = 3,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self.aggressiveness = aggressiveness
        self.max_suggestions = max_suggestions

    def suggest(
        self,
        context: str = "",
        session_entities: Optional[List[str]] = None,
        exclude_ids: Optional[set] = None,
        limit: Optional[int] = None,
    ) -> List[Recommendation]:
        """Generate recommendations based on session context."""
        from lore.recommend import signals

        limit = limit or self.max_suggestions
        exclude = exclude_ids or set()

        # Embed context
        context_vec = self.embedder.embed(context) if context else None

        # Get candidate memories
        candidates = self.store.list(limit=500)
        candidates = [m for m in candidates if m.id not in exclude and m.embedding]

        if not candidates:
            return []

        recommendations: List[Recommendation] = []

        for memory in candidates:
            signal_scores: List[RecommendationSignal] = []
            total_score = 0.0

            # Context similarity
            if context_vec and memory.embedding:
                score, explanation = signals.context_similarity(context_vec, memory.embedding)
                weight = self.weights.get("context_similarity", 0.4)
                signal_scores.append(RecommendationSignal(
                    name="context_similarity", score=score,
                    weight=weight, explanation=explanation,
                ))
                total_score += score * weight

            # Entity overlap
            if session_entities:
                memory_entities = []
                enrichment = (memory.metadata or {}).get("enrichment", {})
                for e in enrichment.get("entities", []):
                    memory_entities.append(e.get("name", ""))
                score, explanation = signals.entity_overlap(session_entities, memory_entities)
                weight = self.weights.get("entity_overlap", 0.25)
                signal_scores.append(RecommendationSignal(
                    name="entity_overlap", score=score,
                    weight=weight, explanation=explanation,
                ))
                total_score += score * weight

            # Temporal pattern
            if memory.created_at:
                score, explanation = signals.temporal_pattern(memory.created_at)
                weight = self.weights.get("temporal_pattern", 0.1)
                signal_scores.append(RecommendationSignal(
                    name="temporal_pattern", score=score,
                    weight=weight, explanation=explanation,
                ))
                total_score += score * weight

            # Access pattern
            score, explanation = signals.access_pattern(
                memory.access_count, memory.last_accessed_at,
            )
            weight = self.weights.get("access_pattern", 0.15)
            signal_scores.append(RecommendationSignal(
                name="access_pattern", score=score,
                weight=weight, explanation=explanation,
            ))
            total_score += score * weight

            # Filter by aggressiveness threshold
            threshold = 1.0 - self.aggressiveness
            if total_score >= threshold * 0.5:
                preview = memory.content[:150] + "..." if len(memory.content) > 150 else memory.content
                recommendations.append(Recommendation(
                    memory_id=memory.id,
                    content_preview=preview,
                    score=total_score,
                    signals=signal_scores,
                    explanation=self._build_explanation(signal_scores),
                ))

        # Sort by score and return top N
        recommendations.sort(key=lambda r: r.score, reverse=True)
        return recommendations[:limit]

    def _build_explanation(self, signals: List[RecommendationSignal]) -> str:
        """Build a human-readable explanation from top signals."""
        top = sorted(signals, key=lambda s: s.score * s.weight, reverse=True)[:3]
        parts = [s.explanation for s in top if s.score > 0]
        return "; ".join(parts) if parts else "Low relevance"
