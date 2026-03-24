"""Retention policy endpoints — preview and apply age/importance-based cleanup."""

from __future__ import annotations

import logging
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel, Field

from lore.server.auth import AuthContext, get_auth_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/retention", tags=["retention"])


class RetentionRequest(BaseModel):
    max_age_days: int = Field(90, ge=1, description="Max age in days")
    min_importance_score: float = Field(0.3, ge=0.0, le=1.0, description="Importance threshold")
    archive: bool = Field(False, description="Archive before deleting")
    dry_run: bool = Field(False, description="Preview only, do not delete")


class AffectedMemory(BaseModel):
    id: str
    content_preview: str
    importance_score: float
    created_at: str


class RetentionResponse(BaseModel):
    deleted_count: int
    archived_count: int
    dry_run: bool
    affected: List[AffectedMemory] = []


def _get_lore():
    """Lazy-import and instantiate a local Lore instance."""
    import os
    from lore import Lore

    enrichment = bool(os.environ.get("OPENAI_API_KEY"))
    enrichment_model = os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")
    return Lore(enrichment=enrichment, enrichment_model=enrichment_model, knowledge_graph=True)


def _run_retention(params: RetentionRequest) -> RetentionResponse:
    from lore.retention import RetentionPolicy, apply_retention

    lore = _get_lore()
    policy = RetentionPolicy(
        max_age_days=params.max_age_days,
        min_importance_score=params.min_importance_score,
        archive_on_expire=params.archive,
        dry_run=params.dry_run,
    )

    # For the preview, always do a dry-run find first so we can return details.
    from lore.retention import _find_expired
    expired = _find_expired(lore, policy)

    affected = [
        AffectedMemory(
            id=m.id,
            content_preview=m.content[:120],
            importance_score=m.importance_score,
            created_at=m.created_at,
        )
        for m in expired
    ]

    result = apply_retention(lore, policy)
    lore.close()

    return RetentionResponse(
        deleted_count=result.deleted_count,
        archived_count=result.archived_count,
        dry_run=result.dry_run,
        affected=affected,
    )


@router.get("/preview", response_model=RetentionResponse)
async def preview_retention(
    max_age_days: int = 90,
    min_importance_score: float = 0.3,
    auth: AuthContext = Depends(get_auth_context),
) -> RetentionResponse:
    """Preview which memories would be affected by a retention policy (dry-run)."""
    params = RetentionRequest(
        max_age_days=max_age_days,
        min_importance_score=min_importance_score,
        archive=False,
        dry_run=True,
    )
    return _run_retention(params)


@router.post("/apply", response_model=RetentionResponse)
async def apply_retention_policy(
    body: RetentionRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> RetentionResponse:
    """Apply a retention policy — delete (and optionally archive) expired memories."""
    return _run_retention(body)
