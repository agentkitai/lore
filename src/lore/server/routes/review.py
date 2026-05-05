"""Review endpoints for approval UX (E6).

Refactored in Phase 1B to delegate to services.graph.review. The risk-score
math and rejected-pattern persistence live in the service layer; this module
is a thin FastAPI shell.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lore.persistence import Store
from lore.persistence.exceptions import StoreNotFoundError
from lore.server.db import get_store
from lore.services.graph.review import (
    PendingReview,
    list_pending_reviews,
)
from lore.services.graph.review import (
    bulk_review as bulk_review_service,
)
from lore.services.graph.review import (
    review_relationship as review_relationship_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/review", tags=["review"])


# ── Wire models (unchanged from pre-1B) ──────────────────────────


class RiskScore(BaseModel):
    """Risk scoring breakdown for a pending connection."""
    total: float = 0.0
    confidence_risk: float = 0.0
    source_reliability: float = 0.0
    entity_importance: float = 0.0
    staleness_risk: float = 0.0


class ReviewItemResponse(BaseModel):
    id: str
    source_entity: Dict[str, Any]
    target_entity: Dict[str, Any]
    rel_type: str
    weight: float = 1.0
    source_memory_id: Optional[str] = None
    source_memory_content: Optional[str] = None
    risk_score: Optional[RiskScore] = None
    created_at: Optional[str] = None


class ReviewListResponse(BaseModel):
    pending: List[ReviewItemResponse] = []
    total_pending: int = 0


class ReviewActionRequest(BaseModel):
    action: str  # "approve" or "reject"
    reason: Optional[str] = None


class ReviewActionResponse(BaseModel):
    id: str
    status: str
    previous_status: str


class BulkReviewRequest(BaseModel):
    action: str
    ids: List[str]
    reason: Optional[str] = None


class BulkReviewResponse(BaseModel):
    updated: int
    action: str


def _to_review_item(p: PendingReview) -> ReviewItemResponse:
    return ReviewItemResponse(
        id=p.id,
        source_entity={
            "id": p.source_entity_id,
            "name": p.source_name,
            "entity_type": p.source_entity_type,
        },
        target_entity={
            "id": p.target_entity_id,
            "name": p.target_name,
            "entity_type": p.target_entity_type,
        },
        rel_type=p.rel_type,
        weight=p.weight,
        source_memory_id=p.source_memory_id,
        source_memory_content=p.source_memory_content,
        risk_score=RiskScore(
            total=p.risk_score.total,
            confidence_risk=p.risk_score.confidence_risk,
            source_reliability=p.risk_score.source_reliability,
            entity_importance=p.risk_score.entity_importance,
            staleness_risk=p.risk_score.staleness_risk,
        ),
        created_at=p.created_at.isoformat() if p.created_at else None,
    )


# ── Handlers ──────────────────────────────────────────────────────


@router.get("", response_model=ReviewListResponse)
async def get_pending_reviews(
    limit: int = Query(50, ge=1, le=500),
    rel_type: Optional[str] = Query(None),
    store: Store = Depends(get_store),
) -> ReviewListResponse:
    """List pending relationships for review."""
    listing = await list_pending_reviews(store, rel_type=rel_type, limit=limit)
    return ReviewListResponse(
        pending=[_to_review_item(p) for p in listing.pending],
        total_pending=listing.total_pending,
    )


@router.get("/inbox", response_model=ReviewListResponse)
async def review_inbox(
    limit: int = Query(50, ge=1, le=500),
    rel_type: Optional[str] = Query(None),
    min_risk: Optional[float] = Query(None, ge=0.0, description="Minimum risk score to include"),
    store: Store = Depends(get_store),
) -> ReviewListResponse:
    """Pending review items sorted by risk score (highest first); same shape as /v1/review."""
    listing = await list_pending_reviews(
        store, rel_type=rel_type, limit=limit, min_risk=min_risk,
    )
    return ReviewListResponse(
        pending=[_to_review_item(p) for p in listing.pending],
        total_pending=listing.total_pending,
    )


@router.post("/bulk", response_model=BulkReviewResponse)
async def bulk_review(
    body: BulkReviewRequest,
    store: Store = Depends(get_store),
) -> BulkReviewResponse:
    """Approve or reject multiple relationships at once."""
    try:
        result = await bulk_review_service(
            store, body.ids, action=body.action, reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return BulkReviewResponse(updated=result.updated, action=result.action)


@router.post("/{relationship_id}", response_model=ReviewActionResponse)
async def review_relationship(
    relationship_id: str,
    body: ReviewActionRequest,
    store: Store = Depends(get_store),
) -> ReviewActionResponse:
    """Approve or reject a relationship."""
    try:
        result = await review_relationship_service(
            store, relationship_id, action=body.action, reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return ReviewActionResponse(
        id=result.id,
        status=result.status,
        previous_status=result.previous_status,
    )
