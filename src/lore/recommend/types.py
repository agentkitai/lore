"""Recommendation data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RecommendationSignal:
    """A single signal contributing to a recommendation score."""
    name: str
    score: float
    weight: float
    explanation: str


@dataclass
class Recommendation:
    """A proactive memory recommendation."""
    memory_id: str
    content_preview: str
    score: float
    signals: List[RecommendationSignal] = field(default_factory=list)
    explanation: str = ""


@dataclass
class RecommendationFeedback:
    """User feedback on a recommendation."""
    memory_id: str
    feedback: str  # "positive" or "negative"
    actor_id: str
    context_hash: Optional[str] = None
