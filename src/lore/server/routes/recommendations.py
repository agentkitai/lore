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
from lore.server.routes._helpers import build_update

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
    import asyncio
    import json as _json
    from types import SimpleNamespace

    if not body.context:
        return []

    pool = await get_pool()

    # Load config
    async with pool.acquire() as conn:
        cfg = await conn.fetchrow(
            "SELECT aggressiveness, max_suggestions FROM recommendation_config "
            "WHERE workspace_id IS NULL AND agent_id IS NULL LIMIT 1",
        )

    aggressiveness = float(cfg["aggressiveness"]) if cfg else 0.5
    max_suggestions = cfg["max_suggestions"] if cfg else 3

    # Build a lightweight store adapter for the engine
    class _AsyncpgStore:
        def __init__(self, rows):
            self._rows = rows

        def list(self, limit=500):
            return self._rows[:limit]

    async with pool.acquire() as conn:
        mem_rows = await conn.fetch(
            """SELECT id, content, embedding, meta,
                      created_at, access_count, last_accessed_at
               FROM memories
               WHERE org_id = $1
                 AND embedding IS NOT NULL
               ORDER BY importance_score DESC NULLS LAST
               LIMIT 500""",
            auth.org_id,
        )

    # Wrap rows as objects the engine expects
    candidates = []
    for r in mem_rows:
        meta = r["meta"]
        if isinstance(meta, str):
            meta = _json.loads(meta) if meta else {}
        elif meta is None:
            meta = {}
        candidates.append(SimpleNamespace(
            id=r["id"],
            content=r["content"] or "",
            embedding=r["embedding"],
            metadata=meta,
            created_at=r["created_at"],
            access_count=r["access_count"] or 0,
            last_accessed_at=r["last_accessed_at"],
        ))

    if not candidates:
        return []

    # Build embedder and engine
    try:
        from lore.embed import LocalEmbedder
        from lore.recommend.engine import RecommendationEngine

        embedder = LocalEmbedder()
        engine = RecommendationEngine(
            store=_AsyncpgStore(candidates),
            embedder=embedder,
            aggressiveness=aggressiveness,
            max_suggestions=max_suggestions,
        )

        results = await asyncio.to_thread(
            engine.suggest,
            context=body.context,
            session_entities=body.session_entities or None,
            limit=body.max_results,
        )
    except Exception:
        logger.exception("Recommendation engine failed")
        return []

    return [
        RecommendationResponse(
            memory_id=rec.memory_id,
            content_preview=rec.content_preview,
            score=round(rec.score, 4),
            explanation=rec.explanation,
        )
        for rec in results
    ]


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
            sql, params = build_update(
                "recommendation_config",
                {
                    "aggressiveness": body.aggressiveness,
                    "enabled": body.enabled,
                    "max_suggestions": body.max_suggestions,
                    "cooldown_minutes": body.cooldown_minutes,
                },
                where_field="id",
                where_value=existing["id"],
            )
            if sql:
                # Append updated_at = now() to the SET clause
                # Insert before the WHERE clause
                sql = sql.replace(" WHERE ", ", updated_at = now() WHERE ", 1)
                await conn.execute(sql, *params)
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
