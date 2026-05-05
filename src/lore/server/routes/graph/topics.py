"""Topic-related graph endpoints. Refactored in Phase 1B to delegate to services."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from lore.persistence import Store
from lore.server.db import get_store
from lore.services.graph.entities import get_topic_detail, list_topics

from .models import TopicListItem, TopicListResponse

router = APIRouter()


@router.get("/topics", response_model=TopicListResponse)
async def get_topics(
    min_mentions: int = Query(3, ge=1),
    limit: int = Query(20, ge=1, le=100),
    store: Store = Depends(get_store),
) -> TopicListResponse:
    """List topics for sidebar display."""
    entities = await list_topics(store, min_mentions=min_mentions, limit=limit)
    return TopicListResponse(
        topics=[
            TopicListItem(
                entity_id=e.id,
                name=e.name,
                entity_type=e.entity_type,
                mention_count=e.mention_count,
            )
            for e in entities
        ]
    )


@router.get("/topics/{name}")
async def get_topic_detail_graph(
    name: str,
    max_memories: int = Query(20, ge=1, le=100),
    store: Store = Depends(get_store),
) -> dict:
    """Get topic detail for sidebar panel."""
    detail = await get_topic_detail(store, name, max_memories=max_memories)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Topic '{name}' not found")

    e = detail.entity
    return {
        "entity": {
            "id": e.id,
            "name": e.name,
            "entity_type": e.entity_type,
            "mention_count": e.mention_count,
            "first_seen_at": e.first_seen_at.isoformat() if e.first_seen_at else None,
            "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
        },
        "related_entities": [
            {
                "name": r.name,
                "entity_type": r.entity_type,
                "relationship": r.relationship,
                "direction": r.direction,
            }
            for r in detail.related_entities
        ],
        "memories": [
            {
                "id": m.id,
                "content": (m.content or "")[:200],
                "type": (m.meta or {}).get("type", "general"),
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "tags": list(m.tags),
            }
            for m in detail.memories
        ],
        "memory_count": detail.memory_count,
    }
