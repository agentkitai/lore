"""Recommendation feedback recording and weight adjustment."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class FeedbackRecorder:
    """Records recommendation feedback and adjusts per-user weights."""

    def __init__(self, store: Any = None) -> None:
        self.store = store
        # Per-actor weight adjustments: actor_id -> {signal_name: weight_delta}
        self._adjustments: Dict[str, Dict[str, float]] = {}

    def record(
        self,
        memory_id: str,
        feedback: str,
        actor_id: str,
        signal: str = "manual",
        context_hash: Optional[str] = None,
    ) -> None:
        """Record feedback and adjust weights."""
        if feedback not in ("positive", "negative"):
            raise ValueError(f"Invalid feedback: {feedback}")

        # Adjust weights based on feedback
        delta = 0.05 if feedback == "positive" else -0.05
        actor_weights = self._adjustments.setdefault(actor_id, {})
        current = actor_weights.get(signal, 0.0)
        actor_weights[signal] = max(-0.5, min(0.5, current + delta))

        logger.debug(
            "Feedback recorded: %s for memory %s by %s (signal: %s)",
            feedback, memory_id, actor_id, signal,
        )

    def get_weight_adjustment(self, actor_id: str, signal_name: str) -> float:
        """Get the cumulative weight adjustment for an actor+signal."""
        return self._adjustments.get(actor_id, {}).get(signal_name, 0.0)
