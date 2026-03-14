"""Review endpoints for approval UX (E6).

Lets users view, approve, and reject pending knowledge graph connections.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/review", tags=["review"])


# ── Response models ───────────────────────────────────────────────


class ReviewItemResponse(BaseModel):
    id: str
    source_entity: Dict[str, Any]
    target_entity: Dict[str, Any]
    rel_type: str
    weight: float = 1.0
    source_memory_id: Optional[str] = None
    source_memory_content: Optional[str] = None
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
    action: str  # "approve" or "reject"
    ids: List[str]
    reason: Optional[str] = None


class BulkReviewResponse(BaseModel):
    updated: int
    action: str


# ── Helpers ───────────────────────────────────────────────────────


def _ts(val) -> Optional[str]:
    if val is None:
        return None
    from datetime import datetime
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


async def _table_exists(conn, table_name: str) -> bool:
    return await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = $1)",
        table_name,
    )


# ── GET /v1/review ────────────────────────────────────────────────


@router.get("", response_model=ReviewListResponse)
async def get_pending_reviews(
    limit: int = Query(50, ge=1, le=500),
    rel_type: Optional[str] = Query(None),
) -> ReviewListResponse:
    """List pending relationships for review."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "relationships"):
            return ReviewListResponse()

        params: list = []
        where_parts = ["r.status = 'pending'"]

        if rel_type:
            params.append(rel_type)
            where_parts.append(f"r.rel_type = ${len(params)}")

        where_sql = " AND ".join(where_parts)
        params.append(limit)
        limit_idx = len(params)

        rows = await conn.fetch(
            f"""SELECT r.id, r.source_entity_id, r.target_entity_id,
                       r.rel_type, r.weight, r.source_memory_id, r.created_at,
                       se.name as source_name, se.entity_type as source_type,
                       se.id as se_id,
                       te.name as target_name, te.entity_type as target_type,
                       te.id as te_id
                FROM relationships r
                JOIN entities se ON se.id = r.source_entity_id
                JOIN entities te ON te.id = r.target_entity_id
                WHERE {where_sql}
                ORDER BY r.created_at DESC
                LIMIT ${limit_idx}""",
            *params,
        )

        total = await conn.fetchval(
            "SELECT COUNT(*) FROM relationships WHERE status = 'pending'",
        )

        items: List[ReviewItemResponse] = []
        for row in rows:
            mem_content = None
            if row["source_memory_id"]:
                mem_row = await conn.fetchrow(
                    "SELECT content FROM memories WHERE id = $1",
                    row["source_memory_id"],
                )
                if mem_row:
                    content = mem_row["content"] or ""
                    mem_content = content[:200]

            items.append(ReviewItemResponse(
                id=row["id"],
                source_entity={
                    "id": row["se_id"],
                    "name": row["source_name"],
                    "entity_type": row["source_type"],
                },
                target_entity={
                    "id": row["te_id"],
                    "name": row["target_name"],
                    "entity_type": row["target_type"],
                },
                rel_type=row["rel_type"],
                weight=float(row["weight"] or 1.0),
                source_memory_id=row["source_memory_id"],
                source_memory_content=mem_content,
                created_at=_ts(row["created_at"]),
            ))

    return ReviewListResponse(pending=items, total_pending=total or 0)


# ── POST /v1/review/{relationship_id} ────────────────────────────


@router.post("/{relationship_id}", response_model=ReviewActionResponse)
async def review_relationship(
    relationship_id: str,
    body: ReviewActionRequest,
) -> ReviewActionResponse:
    """Approve or reject a relationship."""
    if body.action not in ("approve", "reject"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action: {body.action!r}. Must be 'approve' or 'reject'.",
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, status, source_entity_id, target_entity_id, rel_type, source_memory_id "
            "FROM relationships WHERE id = $1",
            relationship_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Relationship not found")

        previous_status = row["status"]
        new_status = "approved" if body.action == "approve" else "rejected"

        await conn.execute(
            "UPDATE relationships SET status = $1, updated_at = now() WHERE id = $2",
            new_status, relationship_id,
        )

        if body.action == "reject":
            # Save rejected pattern
            source_name = await conn.fetchval(
                "SELECT name FROM entities WHERE id = $1", row["source_entity_id"],
            )
            target_name = await conn.fetchval(
                "SELECT name FROM entities WHERE id = $1", row["target_entity_id"],
            )
            if source_name and target_name:
                if await _table_exists(conn, "rejected_patterns"):
                    from ulid import ULID
                    await conn.execute(
                        """INSERT INTO rejected_patterns (id, source_name, target_name, rel_type, source_memory_id, reason)
                           VALUES ($1, $2, $3, $4, $5, $6)
                           ON CONFLICT (source_name, target_name, rel_type) DO NOTHING""",
                        str(ULID()), source_name, target_name,
                        row["rel_type"], row["source_memory_id"], body.reason,
                    )

    return ReviewActionResponse(
        id=relationship_id,
        status=new_status,
        previous_status=previous_status,
    )


# ── POST /v1/review/bulk ─────────────────────────────────────────


@router.post("/bulk", response_model=BulkReviewResponse)
async def bulk_review(body: BulkReviewRequest) -> BulkReviewResponse:
    """Approve or reject multiple relationships at once."""
    if body.action not in ("approve", "reject"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action: {body.action!r}. Must be 'approve' or 'reject'.",
        )
    if not body.ids:
        return BulkReviewResponse(updated=0, action=body.action)

    new_status = "approved" if body.action == "approve" else "rejected"
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "UPDATE relationships SET status = $1, updated_at = now() WHERE id = ANY($2)",
                new_status, body.ids,
            )
            # Parse "UPDATE N" to get count
            updated = int(result.split()[-1]) if result else 0

            if body.action == "reject" and await _table_exists(conn, "rejected_patterns"):
                from ulid import ULID
                rows = await conn.fetch(
                    """SELECT r.rel_type, r.source_memory_id,
                              se.name as source_name, te.name as target_name
                       FROM relationships r
                       JOIN entities se ON se.id = r.source_entity_id
                       JOIN entities te ON te.id = r.target_entity_id
                       WHERE r.id = ANY($1)""",
                    body.ids,
                )
                for row in rows:
                    await conn.execute(
                        """INSERT INTO rejected_patterns (id, source_name, target_name, rel_type, source_memory_id, reason)
                           VALUES ($1, $2, $3, $4, $5, $6)
                           ON CONFLICT (source_name, target_name, rel_type) DO NOTHING""",
                        str(ULID()), row["source_name"], row["target_name"],
                        row["rel_type"], row["source_memory_id"], body.reason,
                    )

    return BulkReviewResponse(updated=updated, action=body.action)
