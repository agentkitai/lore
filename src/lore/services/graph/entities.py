"""Graph entity + topic services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from lore.persistence import (
    Store,
    StoredEntity,
    StoredMemory,
)


@dataclass(frozen=True, slots=True)
class RelatedEntity:
    name: str
    entity_type: str
    relationship: str
    direction: str  # 'outgoing' or 'incoming'


@dataclass(frozen=True, slots=True)
class TopicDetail:
    entity: StoredEntity
    related_entities: Sequence[RelatedEntity]
    memories: Sequence[StoredMemory]
    memory_count: int


@dataclass(frozen=True, slots=True)
class ConnectedMemory:
    id: str
    label: str  # first 200 chars of content with newlines stripped
    type: str  # from meta.type, default "general"
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConnectedEntity:
    id: str
    name: str
    entity_type: str
    rel_type: str
    weight: float


@dataclass(frozen=True, slots=True)
class EntityDetail:
    entity: StoredEntity
    connected_memories: Sequence[ConnectedMemory]
    connected_entities: Sequence[ConnectedEntity]


async def get_entity(store: Store, entity_id: str) -> Optional[StoredEntity]:
    """Return a stored entity by id, or None if absent."""
    return await store.get_entity(entity_id)


async def list_topics(
    store: Store,
    *,
    min_mentions: int = 3,
    limit: int = 20,
) -> Sequence[StoredEntity]:
    """List entities with mention_count >= min_mentions, ordered by mention_count DESC."""
    return await store.list_entities(min_mentions=min_mentions, limit=limit)


async def get_topic_detail(
    store: Store,
    name: str,
    *,
    max_memories: int = 20,
) -> Optional[TopicDetail]:
    """Look up an entity by name (case-insensitive fallback) and return topic detail.

    Returns None if the entity is not found; callers map that to 404.
    """
    # Try exact match first, then case-normalized fallback
    entity = await store.get_entity_by_name(name)
    if entity is None:
        normalized = name.strip().lower()
        if normalized != name:
            entity = await store.get_entity_by_name(normalized)
    if entity is None:
        return None

    rels = await store.query_relationships(
        [entity.id], direction="both", active_only=True
    )
    related: list[RelatedEntity] = []
    for rel in rels[:50]:
        if (rel.status or "approved") != "approved":
            continue
        if rel.source_entity_id == entity.id:
            other_id = rel.target_entity_id
            direction = "outgoing"
        else:
            other_id = rel.source_entity_id
            direction = "incoming"
        other = await store.get_entity(other_id)
        if other is None:
            continue
        related.append(RelatedEntity(
            name=other.name,
            entity_type=other.entity_type,
            relationship=rel.rel_type,
            direction=direction,
        ))

    memories = await store.get_memories_by_entities(
        [entity.id], limit=max_memories
    )
    memory_count = await store.count_memories_for_entity(entity.id)

    return TopicDetail(
        entity=entity,
        related_entities=tuple(related),
        memories=tuple(memories),
        memory_count=memory_count,
    )


async def get_entity_with_connections(
    store: Store,
    entity_id: str,
    *,
    max_memories: int = 30,
    max_related: int = 20,
) -> Optional[EntityDetail]:
    """Return an entity with its connected memories and related entities.

    Used by the legacy GET /v1/ui/entity/{id} route. Returns None if the entity
    is not found; callers map that to 404.
    """
    entity = await store.get_entity(entity_id)
    if entity is None:
        return None

    # Connected memories (most recent first)
    memories = await store.get_memories_by_entities(
        [entity_id], limit=max_memories
    )
    connected_memories: list[ConnectedMemory] = []
    for m in memories:
        label = (m.content or "")[:200].replace("\n", " ")
        mtype = (m.meta or {}).get("type", "general")
        connected_memories.append(ConnectedMemory(
            id=m.id, label=label, type=mtype, created_at=m.created_at,
        ))

    # Connected entities (active, approved)
    rels = await store.query_relationships(
        [entity_id], direction="both", active_only=True
    )
    seen_other: dict[str, ConnectedEntity] = {}
    for rel in rels:
        if (rel.status or "approved") != "approved":
            continue
        other_id = (
            rel.target_entity_id
            if rel.source_entity_id == entity_id
            else rel.source_entity_id
        )
        if other_id == entity_id or other_id in seen_other:
            # Keep the first (highest weight via DESC ordering)
            continue
        other = await store.get_entity(other_id)
        if other is None:
            continue
        seen_other[other_id] = ConnectedEntity(
            id=other.id,
            name=other.name,
            entity_type=other.entity_type,
            rel_type=rel.rel_type,
            weight=rel.weight,
        )
        if len(seen_other) >= max_related:
            break

    return EntityDetail(
        entity=entity,
        connected_memories=tuple(connected_memories),
        connected_entities=tuple(seen_other.values()),
    )
