"""Build human-readable explanations from recommendation signals."""

from __future__ import annotations

from typing import List

from lore.recommend.types import RecommendationSignal


def explain(signals: List[RecommendationSignal], top_n: int = 3) -> str:
    """Build a human-readable explanation from top signal factors."""
    if not signals:
        return "No strong signals found."

    # Sort by contribution (score * weight)
    ranked = sorted(signals, key=lambda s: s.score * s.weight, reverse=True)
    top = [s for s in ranked[:top_n] if s.score > 0]

    if not top:
        return "Weak match across all signals."

    parts = []
    for s in top:
        contribution = s.score * s.weight
        parts.append(f"{s.explanation} (contribution: {contribution:.2f})")

    return "Suggested because: " + "; ".join(parts)
