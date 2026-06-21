"""Recommendation service — config CRUD, feedback, and engine orchestration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Sequence

from lore.persistence import (
    NewRecommendationFeedback,
    RecommendationCandidate,
    Store,
)

logger = logging.getLogger(__name__)


# Defaults match the pre-1F route's fallback values.
DEFAULT_AGGRESSIVENESS = 0.5
DEFAULT_ENABLED = True
DEFAULT_MAX_SUGGESTIONS = 3
DEFAULT_COOLDOWN_MINUTES = 15


class _CandidatesAdapter:
    """Wrap a list of candidates to satisfy the engine's `.list()` interface."""

    def __init__(self, candidates: Sequence[RecommendationCandidate]) -> None:
        self._c = list(candidates)

    def list(self, limit: int = 500):
        return self._c[:limit]


async def get_config(
    store: Store,
    *,
    workspace_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the recommendation config for the given scope as a dict.

    Falls back to defaults when no row is found.
    """
    row = await store.get_recommendation_config(
        workspace_id=workspace_id, agent_id=agent_id
    )
    if row is None:
        return {
            "aggressiveness": DEFAULT_AGGRESSIVENESS,
            "enabled": DEFAULT_ENABLED,
            "max_suggestions": DEFAULT_MAX_SUGGESTIONS,
            "cooldown_minutes": DEFAULT_COOLDOWN_MINUTES,
        }
    return {
        "aggressiveness": row.aggressiveness,
        "enabled": row.enabled,
        "max_suggestions": row.max_suggestions,
        "cooldown_minutes": row.cooldown_minutes,
    }


async def update_config(
    store: Store,
    *,
    workspace_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    aggressiveness: Optional[float] = None,
    enabled: Optional[bool] = None,
    max_suggestions: Optional[int] = None,
    cooldown_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """Upsert the recommendation config and return the updated values as a dict.

    None fields are preserved (existing row's value kept).
    """
    row = await store.upsert_recommendation_config(
        workspace_id=workspace_id,
        agent_id=agent_id,
        aggressiveness=aggressiveness,
        enabled=enabled,
        max_suggestions=max_suggestions,
        cooldown_minutes=cooldown_minutes,
    )
    return {
        "aggressiveness": row.aggressiveness,
        "enabled": row.enabled,
        "max_suggestions": row.max_suggestions,
        "cooldown_minutes": row.cooldown_minutes,
    }


async def submit_feedback(
    store: Store,
    *,
    org_id: str,
    memory_id: str,
    actor_id: str,
    feedback: str,
    workspace_id: Optional[str] = None,
) -> None:
    """Record a positive/negative feedback signal on a recommended memory."""
    if feedback not in ("positive", "negative"):
        raise ValueError("Feedback must be 'positive' or 'negative'")
    await store.record_recommendation_feedback(
        NewRecommendationFeedback(
            org_id=org_id,
            workspace_id=workspace_id,
            memory_id=memory_id,
            actor_id=actor_id,
            feedback=feedback,
        )
    )


async def recommend(
    store: Store,
    *,
    org_id: str,
    context: str,
    session_entities: Optional[Sequence[str]] = None,
    max_results: int = 3,
    requesting_user_id: Optional[str] = None,
) -> list:
    """Run the recommendation engine. Returns engine `Recommendation` objects.

    Returns [] when context is blank, when no embedded candidates exist,
    or when the engine raises (errors logged).
    """
    if not context:
        return []

    cfg_row = await store.get_recommendation_config()
    aggressiveness = cfg_row.aggressiveness if cfg_row else DEFAULT_AGGRESSIVENESS
    max_suggestions = cfg_row.max_suggestions if cfg_row else DEFAULT_MAX_SUGGESTIONS

    candidates = await store.list_candidate_memories_for_recommendation(
        org_id, requesting_user_id=requesting_user_id
    )
    if not candidates:
        return []

    try:
        from lore.embed import LocalEmbedder
        from lore.recommend.engine import RecommendationEngine

        engine = RecommendationEngine(
            store=_CandidatesAdapter(candidates),
            embedder=LocalEmbedder(),
            aggressiveness=aggressiveness,
            max_suggestions=max_suggestions,
        )
        return await asyncio.to_thread(
            engine.suggest,
            context=context,
            session_entities=list(session_entities) if session_entities else None,
            limit=max_results,
        )
    except Exception:
        logger.exception("Recommendation engine failed")
        return []
