"""Topics-dashboard service — adapts graph services for the public /v1/topics API."""

from __future__ import annotations

from typing import Any, Optional

from lore.persistence import Store
from lore.services.graph import entities as _graph_entities


async def list_topics(
    store: Store,
    *,
    entity_type: Optional[str] = None,
    min_mentions: int = 3,
    limit: int = 50,
) -> dict[str, Any]:
    """List topics (entities with mention_count >= threshold)."""
    entities = await _graph_entities.list_topics(
        store, entity_type=entity_type, min_mentions=min_mentions, limit=limit,
    )
    topics = [
        {
            "entity_id": e.id,
            "name": e.name,
            "entity_type": e.entity_type,
            "mention_count": e.mention_count,
            "first_seen_at": e.first_seen_at.isoformat() if e.first_seen_at else None,
            "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
            "related_entity_count": 0,
        }
        for e in entities
    ]
    return {"topics": topics, "total": len(topics), "threshold": min_mentions}


async def get_topic_detail(
    store: Store,
    *,
    name: str,
    max_memories: int = 20,
    format: str = "brief",
) -> Optional[dict[str, Any]]:
    """Get topic detail. Returns None if entity not found."""
    detail = await _graph_entities.get_topic_detail(
        store, name=name, max_memories=max_memories,
    )
    if detail is None:
        return None

    e = detail.entity
    entity_data = {
        "id": e.id,
        "name": e.name,
        "entity_type": e.entity_type,
        "aliases": list(e.aliases),
        "description": e.description,
        "mention_count": e.mention_count,
        "first_seen_at": e.first_seen_at.isoformat() if e.first_seen_at else None,
        "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
    }

    related_entities = [
        {
            "name": r.name,
            "entity_type": r.entity_type,
            "relationship": r.relationship,
            "direction": r.direction,
        }
        for r in detail.related_entities
    ]

    memories = []
    for m in detail.memories:
        content = m.content or ""
        if format == "brief" and len(content) > 100:
            content = content[:100] + "..."
        memories.append({
            "id": m.id,
            "content": content,
            "type": (m.meta or {}).get("type", "general"),
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "tags": list(m.tags),
        })

    return {
        "entity": entity_data,
        "related_entities": related_entities,
        "memories": memories,
        "summary": None,
        "summary_method": None,
        "summary_generated_at": None,
        "memory_count": detail.memory_count,
    }
