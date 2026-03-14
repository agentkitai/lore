"""Entity management for the knowledge graph."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ulid import ULID

from lore.store.base import Store
from lore.types import VALID_ENTITY_TYPES, Entity, EntityMention


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EntityManager:
    """Manages entity lifecycle: creation, dedup, alias resolution, merge."""

    def __init__(self, store: Store, topic_summary_cache=None) -> None:
        self.store = store
        self._topic_summary_cache = topic_summary_cache

    @staticmethod
    def _normalize_name(raw: str) -> str:
        """Normalize entity name for dedup matching."""
        name = raw.strip().lower()
        name = " ".join(name.split())  # collapse whitespace
        name = name.rstrip(".,;:!?")
        return name

    def _resolve_entity(self, name: str, entity_type: str) -> Entity:
        """Find existing entity by name/alias or create new one."""
        if entity_type not in VALID_ENTITY_TYPES:
            entity_type = "other"

        # 1. Exact match on canonical name
        existing = self.store.get_entity_by_name(name)
        if existing:
            if entity_type != "concept" and existing.entity_type == "concept":
                existing.entity_type = entity_type
                existing.updated_at = _utc_now_iso()
                self.store.update_entity(existing)
            return existing

        # 2. Alias match
        existing = self.store.get_entity_by_alias(name)
        if existing:
            return existing

        # 3. Create new entity
        now = _utc_now_iso()
        entity = Entity(
            id=str(ULID()),
            name=name,
            entity_type=entity_type,
            aliases=[],
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        self.store.save_entity(entity)
        return entity

    def add_alias(self, entity_id: str, alias: str) -> None:
        """Add an alias to an entity."""
        entity = self.store.get_entity(entity_id)
        if entity:
            normalized = self._normalize_name(alias)
            if normalized and normalized not in entity.aliases and normalized != entity.name:
                entity.aliases.append(normalized)
                entity.updated_at = _utc_now_iso()
                self.store.update_entity(entity)

    def merge_entities(self, keep_id: str, merge_id: str) -> Optional[Entity]:
        """Merge two entities. Keep entity absorbs merge entity."""
        keep = self.store.get_entity(keep_id)
        merge = self.store.get_entity(merge_id)
        if not keep or not merge:
            return None

        # Absorb name as alias
        if merge.name not in keep.aliases and merge.name != keep.name:
            keep.aliases.append(merge.name)
        # Absorb aliases
        for alias in merge.aliases:
            if alias not in keep.aliases and alias != keep.name:
                keep.aliases.append(alias)

        # Transfer mentions and relationships
        self.store.transfer_entity_mentions(from_id=merge_id, to_id=keep_id)
        self.store.transfer_entity_relationships(from_id=merge_id, to_id=keep_id)

        # Update counts
        keep.mention_count += merge.mention_count
        keep.updated_at = _utc_now_iso()

        self.store.update_entity(keep)
        self.store.delete_entity(merge_id)
        return keep

    def ingest_from_enrichment(
        self, memory_id: str, entities: List[Dict[str, str]]
    ) -> List[Entity]:
        """Process entities from F6 enrichment into graph nodes."""
        result = []
        for raw in entities:
            name = self._normalize_name(raw.get("name", ""))
            if not name:
                continue
            entity_type = raw.get("type", "concept")

            entity = self._resolve_entity(name, entity_type)

            # Create mention link
            self.store.save_entity_mention(EntityMention(
                id=str(ULID()),
                entity_id=entity.id,
                memory_id=memory_id,
                mention_type="explicit",
                confidence=1.0,
                created_at=_utc_now_iso(),
            ))

            # Update mention count and last_seen
            entity.mention_count += 1
            entity.last_seen_at = _utc_now_iso()
            entity.updated_at = _utc_now_iso()
            self.store.update_entity(entity)

            if self._topic_summary_cache is not None:
                self._topic_summary_cache.invalidate(entity.id)

            result.append(entity)
        return result

    def ingest_from_fact(
        self, memory_id: str, fact
    ) -> Tuple[Entity, Entity]:
        """Extract entities from a fact's subject and object."""
        subject_entity = self._resolve_entity(
            self._normalize_name(fact.subject),
            entity_type="concept",
        )
        object_entity = self._resolve_entity(
            self._normalize_name(fact.object),
            entity_type="concept",
        )

        now = _utc_now_iso()
        for entity in (subject_entity, object_entity):
            self.store.save_entity_mention(EntityMention(
                id=str(ULID()),
                entity_id=entity.id,
                memory_id=memory_id,
                mention_type="inferred",
                confidence=fact.confidence,
                created_at=now,
            ))
            entity.mention_count += 1
            entity.last_seen_at = now
            entity.updated_at = now
            self.store.update_entity(entity)

            if self._topic_summary_cache is not None:
                self._topic_summary_cache.invalidate(entity.id)

        return subject_entity, object_entity
