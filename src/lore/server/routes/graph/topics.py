"""Topic-related graph endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from lore.server.db import get_pool
from lore.server.routes._parsers import _ts

from .models import TopicListItem, TopicListResponse

router = APIRouter()


@router.get("/topics", response_model=TopicListResponse)
async def get_topics(
    min_mentions: int = Query(3, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> TopicListResponse:
    """List topics for sidebar display."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, entity_type, mention_count
               FROM entities
               WHERE mention_count >= $1
               ORDER BY mention_count DESC
               LIMIT $2""",
            min_mentions, limit,
        )
    return TopicListResponse(
        topics=[
            TopicListItem(
                entity_id=row["id"],
                name=row["name"],
                entity_type=row["entity_type"],
                mention_count=row["mention_count"],
            )
            for row in rows
        ]
    )


@router.get("/topics/{name}")
async def get_topic_detail_graph(
    name: str,
    max_memories: int = Query(20, ge=1, le=100),
) -> dict:
    """Get topic detail for sidebar panel."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM entities WHERE LOWER(name) = LOWER($1)", name,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Topic '{name}' not found")

        entity_id = row["id"]

        # Related entities
        rel_rows = await conn.fetch(
            """SELECT r.rel_type, r.source_entity_id, r.target_entity_id,
                      e.name as other_name, e.entity_type as other_type
               FROM relationships r
               JOIN entities e ON (
                   CASE WHEN r.source_entity_id = $1 THEN r.target_entity_id
                        ELSE r.source_entity_id END = e.id
               )
               WHERE (r.source_entity_id = $1 OR r.target_entity_id = $1)
                 AND r.valid_until IS NULL
                 AND COALESCE(r.status, 'approved') = 'approved'
               LIMIT 50""",
            entity_id,
        )

        related = []
        for rr in rel_rows:
            direction = "outgoing" if rr["source_entity_id"] == entity_id else "incoming"
            related.append({
                "name": rr["other_name"],
                "entity_type": rr["other_type"],
                "relationship": rr["rel_type"],
                "direction": direction,
            })

        # Linked memories
        mem_rows = await conn.fetch(
            """SELECT DISTINCT m.id, m.content, m.type, m.created_at, m.tags
               FROM entity_mentions em
               JOIN memories m ON em.memory_id = m.id
               WHERE em.entity_id = $1
               ORDER BY m.created_at DESC
               LIMIT $2""",
            entity_id, max_memories,
        )

        total_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT memory_id) FROM entity_mentions WHERE entity_id = $1",
            entity_id,
        )

        memories = []
        for mr in mem_rows:
            tags = mr.get("tags") or []
            if isinstance(tags, str):
                tags = json.loads(tags)
            memories.append({
                "id": mr["id"],
                "content": mr["content"][:200] if mr["content"] else "",
                "type": mr.get("type", "general"),
                "created_at": _ts(mr["created_at"]),
                "tags": tags,
            })

    return {
        "entity": {
            "id": row["id"],
            "name": row["name"],
            "entity_type": row["entity_type"],
            "mention_count": row["mention_count"],
            "first_seen_at": _ts(row.get("first_seen_at")),
            "last_seen_at": _ts(row.get("last_seen_at")),
        },
        "related_entities": related,
        "memories": memories,
        "memory_count": total_count or 0,
    }
