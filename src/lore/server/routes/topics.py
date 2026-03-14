"""Topic Notes endpoints for Lore Cloud Server (E4)."""

from __future__ import annotations

import json
import logging
from typing import Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/topics", tags=["topics"])


@router.get("")
async def list_topics(
    entity_type: Optional[str] = Query(None),
    min_mentions: int = Query(3, ge=1, le=100),
    limit: int = Query(50, ge=1, le=200),
    project: Optional[str] = Query(None),
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
    """List auto-detected topics (entities with mention_count >= threshold)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        params: list = [min_mentions, limit]
        type_filter = ""
        if entity_type:
            type_filter = "AND entity_type = $3"
            params.append(entity_type)

        rows = await conn.fetch(
            f"""SELECT id, name, entity_type, mention_count,
                       first_seen_at, last_seen_at
                FROM entities
                WHERE mention_count >= $1
                  {type_filter}
                ORDER BY mention_count DESC
                LIMIT $2""",
            *params,
        )

    topics = []
    for row in rows:
        topics.append({
            "entity_id": row["id"],
            "name": row["name"],
            "entity_type": row["entity_type"],
            "mention_count": row["mention_count"],
            "first_seen_at": str(row["first_seen_at"]) if row["first_seen_at"] else None,
            "last_seen_at": str(row["last_seen_at"]) if row["last_seen_at"] else None,
            "related_entity_count": 0,
        })

    return {
        "topics": topics,
        "total": len(topics),
        "threshold": min_mentions,
    }


@router.get("/{name}")
async def get_topic_detail(
    name: str,
    max_memories: int = Query(20, ge=1, le=100),
    format: str = Query("brief"),
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
    """Get comprehensive detail for a single topic."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Resolve entity by name (case-insensitive)
        row = await conn.fetchrow(
            "SELECT * FROM entities WHERE LOWER(name) = LOWER($1)",
            name,
        )
        if row is None:
            # Try alias lookup
            row = await conn.fetchrow(
                "SELECT e.* FROM entities e WHERE EXISTS ("
                "  SELECT 1 FROM unnest(e.aliases) a WHERE LOWER(a) = LOWER($1)"
                ")",
                name,
            )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Topic '{name}' not found")

        entity_id = row["id"]
        entity_data = {
            "id": row["id"],
            "name": row["name"],
            "entity_type": row["entity_type"],
            "aliases": row.get("aliases") or [],
            "description": row.get("description"),
            "mention_count": row["mention_count"],
            "first_seen_at": str(row["first_seen_at"]) if row.get("first_seen_at") else None,
            "last_seen_at": str(row["last_seen_at"]) if row.get("last_seen_at") else None,
        }

        # Get related entities via relationships
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
               LIMIT 50""",
            entity_id,
        )

        related_entities = []
        for rr in rel_rows:
            direction = "outgoing" if rr["source_entity_id"] == entity_id else "incoming"
            related_entities.append({
                "name": rr["other_name"],
                "entity_type": rr["other_type"],
                "relationship": rr["rel_type"],
                "direction": direction,
            })

        # Get linked memories via entity_mentions
        mem_rows = await conn.fetch(
            """SELECT DISTINCT m.id, m.content, m.type, m.created_at, m.tags, m.meta
               FROM entity_mentions em
               JOIN memories m ON em.memory_id = m.id
               WHERE em.entity_id = $1
               ORDER BY m.created_at DESC
               LIMIT $2""",
            entity_id,
            max_memories,
        )

        # Get total memory count
        total_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT memory_id) FROM entity_mentions WHERE entity_id = $1",
            entity_id,
        )

        memories = []
        for mr in mem_rows:
            tags = mr.get("tags") or []
            if isinstance(tags, str):
                tags = json.loads(tags)
            content = mr["content"]
            if format == "brief" and len(content) > 100:
                content = content[:100] + "..."
            memories.append({
                "id": mr["id"],
                "content": content,
                "type": mr.get("type", "general"),
                "created_at": str(mr["created_at"]) if mr["created_at"] else None,
                "tags": tags,
            })

    return {
        "entity": entity_data,
        "related_entities": related_entities,
        "memories": memories,
        "summary": None,
        "summary_method": None,
        "summary_generated_at": None,
        "memory_count": total_count or 0,
    }
