"""Recommendation endpoints — /v1/recommendations."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required.")

from pydantic import BaseModel

from lore.persistence import Store
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services import recommendations as recommendations_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/recommendations", tags=["recommendations"])


class RecommendationResponse(BaseModel):
    memory_id: str
    content_preview: str
    score: float
    explanation: str
    reason: str = ""
    confidence: float = 0.0


class RecommendationRequest(BaseModel):
    context: str = ""
    session_entities: List[str] = []
    max_results: int = 3


class FeedbackRequest(BaseModel):
    feedback: str  # "positive" or "negative"


class ConfigResponse(BaseModel):
    aggressiveness: float = 0.5
    enabled: bool = True
    max_suggestions: int = 3
    cooldown_minutes: int = 15


class ConfigUpdateRequest(BaseModel):
    aggressiveness: Optional[float] = None
    enabled: Optional[bool] = None
    max_suggestions: Optional[int] = None
    cooldown_minutes: Optional[int] = None


def _to_response(rec) -> RecommendationResponse:
    return RecommendationResponse(
        memory_id=rec.memory_id,
        content_preview=rec.content_preview,
        score=round(rec.score, 4),
        explanation=rec.explanation,
        reason=rec.reason,
        confidence=rec.confidence,
    )


@router.get("", response_model=List[RecommendationResponse])
async def get_recommendations(
    context: str = Query("", description="Session context text"),
    max_results: int = Query(3, ge=1, le=10),
    auth: AuthContext = Depends(get_auth_context),
) -> List[RecommendationResponse]:
    """Get proactive memory suggestions."""
    # Placeholder — in production would use RecommendationEngine
    return []


@router.post("", response_model=List[RecommendationResponse])
async def post_recommendations(
    body: RecommendationRequest,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[RecommendationResponse]:
    """Get suggestions with explicit context body."""
    results = await recommendations_service.recommend(
        store,
        org_id=auth.org_id,
        context=body.context,
        session_entities=body.session_entities or None,
        max_results=body.max_results,
    )
    return [_to_response(r) for r in results]


@router.get("/proactive", response_model=List[RecommendationResponse])
async def proactive_recommendations(
    context: str = Query("", description="Current session context"),
    entities: str = Query("", description="Comma-separated entity names"),
    max_results: int = Query(5, ge=1, le=20),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> List[RecommendationResponse]:
    """Return relevant memories the user might not have asked for.

    This endpoint takes current context and proactively surfaces
    memories that could be useful based on multi-signal scoring.
    """
    if not context:
        return []

    session_entities = [e.strip() for e in entities.split(",") if e.strip()] if entities else []

    results = await recommendations_service.recommend(
        store,
        org_id=auth.org_id,
        context=context,
        session_entities=session_entities or None,
        max_results=max_results,
    )
    return [_to_response(r) for r in results]


@router.post("/{memory_id}/feedback")
async def submit_feedback(
    memory_id: str,
    body: FeedbackRequest,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> Dict[str, str]:
    """Submit feedback on a recommendation."""
    try:
        await recommendations_service.submit_feedback(
            store,
            org_id=auth.org_id,
            memory_id=memory_id,
            actor_id=auth.key_id,
            feedback=body.feedback,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"status": "recorded", "memory_id": memory_id, "feedback": body.feedback}


@router.get("/config", response_model=ConfigResponse)
async def get_config(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> ConfigResponse:
    """Get recommendation config."""
    cfg = await recommendations_service.get_config(store)
    return ConfigResponse(**cfg)


@router.patch("/config", response_model=ConfigResponse)
async def update_config(
    body: ConfigUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> ConfigResponse:
    """Update recommendation config."""
    cfg = await recommendations_service.update_config(
        store, **body.model_dump(exclude_unset=True)
    )
    return ConfigResponse(**cfg)
