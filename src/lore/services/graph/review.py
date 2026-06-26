"""Graph review workflow service.

Move risk-score computation here as a pure function and expose three service
functions over the Store protocol. Routes layer becomes a thin shell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from lore.persistence import (
    Store,
)

# ── Dataclasses ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RiskScore:
    total: float
    confidence_risk: float
    entity_importance: float
    staleness_risk: float


@dataclass(frozen=True, slots=True)
class PendingReview:
    """Pending relationship enriched with risk score and source memory snippet."""

    id: str
    source_entity_id: str
    target_entity_id: str
    source_name: str
    source_entity_type: str
    target_name: str
    target_entity_type: str
    rel_type: str
    weight: float
    source_memory_id: Optional[str]
    source_memory_content: Optional[str]
    risk_score: RiskScore
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewListing:
    pending: Sequence[PendingReview]
    total_pending: int


@dataclass(frozen=True, slots=True)
class ReviewActionResult:
    id: str
    status: str
    previous_status: str


@dataclass(frozen=True, slots=True)
class BulkReviewResult:
    updated: int
    action: str


VALID_ACTIONS = frozenset({"approve", "reject"})


# ── Risk score (pure function) ───────────────────────────────


def _compute_risk_score(
    weight: float,
    source_mention_count: int,
    target_mention_count: int,
    age_hours: float,
) -> RiskScore:
    """Compute composite risk score (0-100) for a pending relationship.

    Higher score = needs more careful review.
    """
    confidence_risk = round(max(0.0, (1.0 - min(weight, 1.0)) * 40.0), 2)
    max_mentions = max(source_mention_count, target_mention_count, 1)
    entity_importance = round(min(25.0, max_mentions * 2.5), 2)
    staleness_risk = round(min(10.0, age_hours / 168.0 * 10.0), 2)
    total = round(
        confidence_risk + entity_importance + staleness_risk, 2
    )
    return RiskScore(
        total=total,
        confidence_risk=confidence_risk,
        entity_importance=entity_importance,
        staleness_risk=staleness_risk,
    )


def _age_hours(created_at: datetime, *, now: Optional[datetime] = None) -> float:
    """Hours between created_at and now (UTC). Used by tests; injectable now=."""
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - created_at).total_seconds() / 3600.0)


# ── Service functions ────────────────────────────────────────


async def list_pending_reviews(
    store: Store,
    *,
    org_id: str,
    rel_type: Optional[str] = None,
    limit: int = 50,
    min_risk: Optional[float] = None,
) -> ReviewListing:
    """List pending relationships with risk score, optionally filtered by min risk
    and sorted highest-risk first.
    """
    rows = await store.list_pending_relationships(
        org_id,
        rel_type=rel_type,
        limit=limit * 2 if min_risk is not None else limit,
    )

    enriched: list[PendingReview] = []
    for row in rows:
        memory_content: Optional[str] = None
        if row.source_memory_id is not None:
            mem = await store.get_memory(org_id, row.source_memory_id)
            if mem is not None:
                memory_content = (mem.content or "")[:200]
        risk = _compute_risk_score(
            weight=row.weight,
            source_mention_count=row.source_mentions,
            target_mention_count=row.target_mentions,
            age_hours=_age_hours(row.created_at),
        )
        enriched.append(
            PendingReview(
                id=row.id,
                source_entity_id=row.source_entity_id,
                target_entity_id=row.target_entity_id,
                source_name=row.source_name,
                source_entity_type=row.source_entity_type,
                target_name=row.target_name,
                target_entity_type=row.target_entity_type,
                rel_type=row.rel_type,
                weight=row.weight,
                source_memory_id=row.source_memory_id,
                source_memory_content=memory_content,
                risk_score=risk,
                created_at=row.created_at,
            )
        )

    # Sort highest-risk first
    enriched.sort(key=lambda p: p.risk_score.total, reverse=True)
    if min_risk is not None:
        enriched = [p for p in enriched if p.risk_score.total >= min_risk]
    enriched = enriched[:limit]
    return ReviewListing(pending=tuple(enriched), total_pending=len(enriched))


async def review_relationship(
    store: Store,
    relationship_id: str,
    *,
    org_id: str,
    action: str,
    reason: Optional[str] = None,
) -> ReviewActionResult:
    """Approve or reject a single relationship; on reject, save the rejected pattern."""
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"action must be one of {sorted(VALID_ACTIONS)}; got {action!r}"
        )

    existing = await store.get_relationship(relationship_id, org_id)
    if existing is None:
        from lore.persistence.exceptions import StoreNotFoundError

        raise StoreNotFoundError("relationships", relationship_id)
    new_status = "approved" if action == "approve" else "rejected"
    updated = await store.update_relationship_status(
        relationship_id, org_id, status=new_status
    )

    if action == "reject":
        source = await store.get_entity(existing.source_entity_id, org_id)
        target = await store.get_entity(existing.target_entity_id, org_id)
        if source is not None and target is not None:
            await store.save_rejected_pattern(
                source.name,
                target.name,
                existing.rel_type,
                source_memory_id=existing.source_memory_id,
                reason=reason,
            )
    return ReviewActionResult(
        id=relationship_id,
        status=updated.status,
        previous_status=existing.status,
    )


async def bulk_review(
    store: Store,
    ids: Sequence[str],
    *,
    org_id: str,
    action: str,
    reason: Optional[str] = None,
) -> BulkReviewResult:
    """Apply review action to many relationships in a loop. Each op is idempotent."""
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"action must be one of {sorted(VALID_ACTIONS)}; got {action!r}"
        )
    if not ids:
        return BulkReviewResult(updated=0, action=action)
    updated = 0
    for rel_id in ids:
        try:
            await review_relationship(
                store, rel_id, org_id=org_id, action=action, reason=reason
            )
            updated += 1
        except Exception:
            # Tolerate per-item failures (e.g. row already deleted) — bulk-review semantics
            continue
    return BulkReviewResult(updated=updated, action=action)
