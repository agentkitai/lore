"""Entity-related graph endpoints."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from lore.server.db import get_pool
from lore.server.routes._parsers import _ts

from ._helpers import _memory_type
from .models import EntityDetailResponse

router = APIRouter()


@router.get("/entity/{entity_id}", response_model=EntityDetailResponse)
async def get_entity_detail(entity_id: str) -> EntityDetailResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, entity_type, mention_count, first_seen_at, last_seen_at FROM entities WHERE id = $1",
            entity_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Connected memories via entity_mentions
        connected_memories: List[Dict[str, Any]] = []
        mem_rows = await conn.fetch(
            """SELECT m.id, m.content, m.meta, m.created_at
               FROM entity_mentions em
               JOIN memories m ON m.id = em.memory_id
               WHERE em.entity_id = $1
               ORDER BY m.created_at DESC
               LIMIT 30""",
            entity_id,
        )
        for m in mem_rows:
            label = (m["content"] or "")[:200].replace("\n", " ")
            connected_memories.append({
                "id": m["id"],
                "label": label,
                "type": _memory_type(m["meta"]),
                "created_at": _ts(m["created_at"]),
            })

        # Connected entities via co-occurrence relationships (deduplicated)
        connected_entities: List[Dict[str, Any]] = []
        rel_rows = await conn.fetch(
            """SELECT DISTINCT ON (e.id) e.id, e.name, e.entity_type, r.rel_type, r.weight
               FROM relationships r
               JOIN entities e ON e.id = CASE
                   WHEN r.source_entity_id = $1 THEN r.target_entity_id
                   ELSE r.source_entity_id
               END
               WHERE (r.source_entity_id = $1 OR r.target_entity_id = $1)
                 AND e.id != $1
                 AND COALESCE(r.status, 'approved') = 'approved'
               ORDER BY e.id, r.weight DESC
               LIMIT 20""",
            entity_id,
        )
        for r in rel_rows:
            connected_entities.append({
                "id": r["id"],
                "name": r["name"],
                "type": r["entity_type"],
                "rel_type": r["rel_type"],
                "weight": float(r["weight"]),
            })

    return EntityDetailResponse(
        id=row["id"],
        name=row["name"],
        entity_type=row["entity_type"],
        mention_count=row["mention_count"] or 0,
        first_seen_at=_ts(row["first_seen_at"]),
        last_seen_at=_ts(row["last_seen_at"]),
        connected_memories=connected_memories,
        connected_entities=connected_entities,
    )
