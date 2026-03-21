"""Recommendation endpoints — /v1/recommendations."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required.")

from pydantic import BaseModel

from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/recommendations", tags=["recommendations"])


class RecommendationResponse(BaseModel):
    memory_id: str
    content_preview: str
    score: float
    explanation: str


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
) -> List[RecommendationResponse]:
    """Get suggestions with explicit context body."""
    return []


@router.post("/{memory_id}/feedback")
async def submit_feedback(
    memory_id: str,
    body: FeedbackRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> Dict[str, str]:
    """Submit feedback on a recommendation."""
    from ulid import ULID

    if body.feedback not in ("positive", "negative"):
        raise HTTPException(400, "Feedback must be 'positive' or 'negative'")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO recommendation_feedback
               (id, org_id, memory_id, actor_id, feedback)
               VALUES ($1, $2, $3, $4, $5)""",
            str(ULID()), auth.org_id, memory_id, auth.key_id,
            body.feedback,
        )

    return {"status": "recorded", "memory_id": memory_id, "feedback": body.feedback}


@router.get("/config", response_model=ConfigResponse)
async def get_config(
    auth: AuthContext = Depends(get_auth_context),
) -> ConfigResponse:
    """Get recommendation config."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM recommendation_config
               WHERE workspace_id IS NULL AND agent_id IS NULL
               LIMIT 1""",
        )
    if row:
        return ConfigResponse(
            aggressiveness=float(row["aggressiveness"]),
            enabled=row["enabled"],
            max_suggestions=row["max_suggestions"],
            cooldown_minutes=row["cooldown_minutes"],
        )
    return ConfigResponse()


@router.patch("/config", response_model=ConfigResponse)
async def update_config(
    body: ConfigUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> ConfigResponse:
    """Update recommendation config."""
    from ulid import ULID

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM recommendation_config WHERE workspace_id IS NULL AND agent_id IS NULL"
        )

        if existing:
            updates = []
            params: list = [existing["id"]]
            if body.aggressiveness is not None:
                params.append(body.aggressiveness)
                updates.append(f"aggressiveness = ${len(params)}")
            if body.enabled is not None:
                params.append(body.enabled)
                updates.append(f"enabled = ${len(params)}")
            if body.max_suggestions is not None:
                params.append(body.max_suggestions)
                updates.append(f"max_suggestions = ${len(params)}")
            if body.cooldown_minutes is not None:
                params.append(body.cooldown_minutes)
                updates.append(f"cooldown_minutes = ${len(params)}")
            if updates:
                updates.append("updated_at = now()")
                await conn.execute(
                    f"UPDATE recommendation_config SET {', '.join(updates)} WHERE id = $1",
                    *params,
                )
        else:
            await conn.execute(
                """INSERT INTO recommendation_config
                   (id, aggressiveness, enabled, max_suggestions, cooldown_minutes)
                   VALUES ($1, $2, $3, $4, $5)""",
                str(ULID()),
                body.aggressiveness or 0.5,
                body.enabled if body.enabled is not None else True,
                body.max_suggestions or 3,
                body.cooldown_minutes or 15,
            )

    return await get_config(auth)
