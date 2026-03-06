"""In-memory store implementation for testing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lore.store.base import Store
from lore.types import ConflictEntry, Entity, EntityMention, Fact, Memory, Relationship


class MemoryStore(Store):
    """In-memory store backed by a dict. Useful for testing."""

    def __init__(self) -> None:
        self._memories: Dict[str, Memory] = {}
        self._facts: Dict[str, Fact] = {}
        self._conflict_log: List[ConflictEntry] = []
        self._entities: Dict[str, Entity] = {}
        self._relationships: Dict[str, Relationship] = {}
        self._entity_mentions: List[EntityMention] = []

    def save(self, memory: Memory) -> None:
        self._memories[memory.id] = memory

    def get(self, memory_id: str) -> Optional[Memory]:
        return self._memories.get(memory_id)

    def list(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Memory]:
        memories = list(self._memories.values())
        if project is not None:
            memories = [m for m in memories if m.project == project]
        if type is not None:
            memories = [m for m in memories if m.type == type]
        if tier is not None:
            memories = [m for m in memories if m.tier == tier]
        memories.sort(key=lambda m: m.created_at, reverse=True)
        if limit is not None:
            memories = memories[:limit]
        return memories

    def update(self, memory: Memory) -> bool:
        if memory.id not in self._memories:
            return False
        self._memories[memory.id] = memory
        return True

    def delete(self, memory_id: str) -> bool:
        existed = self._memories.pop(memory_id, None) is not None
        if existed:
            # Cascade: remove facts for this memory
            to_remove = [fid for fid, f in self._facts.items() if f.memory_id == memory_id]
            for fid in to_remove:
                del self._facts[fid]
        return existed

    def count(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> int:
        memories = list(self._memories.values())
        if project is not None:
            memories = [m for m in memories if m.project == project]
        if type is not None:
            memories = [m for m in memories if m.type == type]
        if tier is not None:
            memories = [m for m in memories if m.tier == tier]
        return len(memories)

    def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        expired_ids = [
            mid for mid, m in self._memories.items()
            if m.expires_at is not None
            and datetime.fromisoformat(m.expires_at) < now
        ]
        for mid in expired_ids:
            del self._memories[mid]
        return len(expired_ids)

    # ------------------------------------------------------------------
    # Fact + conflict CRUD
    # ------------------------------------------------------------------

    def save_fact(self, fact: Fact) -> None:
        self._facts[fact.id] = fact

    def get_facts(self, memory_id: str) -> List[Fact]:
        facts = [f for f in self._facts.values() if f.memory_id == memory_id]
        facts.sort(key=lambda f: f.extracted_at)
        return facts

    def get_active_facts(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        limit: int = 50,
    ) -> List[Fact]:
        facts = [f for f in self._facts.values() if f.invalidated_by is None]
        if subject is not None:
            norm_subject = subject.strip().lower()
            facts = [f for f in facts if f.subject == norm_subject]
        if predicate is not None:
            norm_predicate = predicate.strip().lower()
            facts = [f for f in facts if f.predicate == norm_predicate]
        facts.sort(key=lambda f: f.extracted_at, reverse=True)
        return facts[:limit]

    def invalidate_fact(self, fact_id: str, invalidated_by: str) -> None:
        fact = self._facts.get(fact_id)
        if fact is not None and fact.invalidated_by is None:
            fact.invalidated_by = invalidated_by
            fact.invalidated_at = datetime.now(timezone.utc).isoformat()

    def save_conflict(self, entry: ConflictEntry) -> None:
        self._conflict_log.append(entry)

    def list_conflicts(
        self,
        resolution: Optional[str] = None,
        limit: int = 20,
    ) -> List[ConflictEntry]:
        entries = list(self._conflict_log)
        if resolution is not None:
            entries = [e for e in entries if e.resolution == resolution]
        entries.sort(key=lambda e: e.resolved_at, reverse=True)
        return entries[:limit]

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def save_entity(self, entity: Entity) -> None:
        self._entities[entity.id] = entity

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return self._entities.get(entity_id)

    def get_entity_by_name(self, name: str) -> Optional[Entity]:
        for e in self._entities.values():
            if e.name == name:
                return e
        return None

    def get_entity_by_alias(self, alias: str) -> Optional[Entity]:
        for e in self._entities.values():
            if alias in e.aliases:
                return e
        return None

    def update_entity(self, entity: Entity) -> None:
        self._entities[entity.id] = entity

    def delete_entity(self, entity_id: str) -> None:
        self._entities.pop(entity_id, None)
        # Cascade: delete relationships involving this entity
        to_remove = [
            rid for rid, r in self._relationships.items()
            if r.source_entity_id == entity_id or r.target_entity_id == entity_id
        ]
        for rid in to_remove:
            del self._relationships[rid]
        # Cascade: delete entity mentions
        self._entity_mentions = [
            m for m in self._entity_mentions if m.entity_id != entity_id
        ]

    def list_entities(
        self,
        entity_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Entity]:
        entities = list(self._entities.values())
        if entity_type:
            entities = [e for e in entities if e.entity_type == entity_type]
        entities.sort(key=lambda e: e.mention_count, reverse=True)
        return entities[:limit]

    # ------------------------------------------------------------------
    # Relationship CRUD
    # ------------------------------------------------------------------

    def save_relationship(self, rel: Relationship) -> None:
        self._relationships[rel.id] = rel

    def get_relationship(self, rel_id: str) -> Optional[Relationship]:
        return self._relationships.get(rel_id)

    def get_active_relationship(
        self, source_id: str, target_id: str, rel_type: str
    ) -> Optional[Relationship]:
        for r in self._relationships.values():
            if (r.source_entity_id == source_id and r.target_entity_id == target_id
                    and r.rel_type == rel_type and r.valid_until is None):
                return r
        return None

    def get_relationship_by_fact(self, fact_id: str) -> Optional[Relationship]:
        for r in self._relationships.values():
            if r.source_fact_id == fact_id and r.valid_until is None:
                return r
        return None

    def update_relationship(self, rel: Relationship) -> None:
        self._relationships[rel.id] = rel

    def delete_relationship(self, rel_id: str) -> None:
        self._relationships.pop(rel_id, None)

    def get_relationships_from(
        self, entity_ids: List[str], active_only: bool = True
    ) -> List[Relationship]:
        id_set = set(entity_ids)
        rels = [
            r for r in self._relationships.values()
            if r.source_entity_id in id_set
            and (not active_only or r.valid_until is None)
        ]
        rels.sort(key=lambda r: r.weight, reverse=True)
        return rels

    def get_relationships_to(
        self, entity_ids: List[str], active_only: bool = True
    ) -> List[Relationship]:
        id_set = set(entity_ids)
        rels = [
            r for r in self._relationships.values()
            if r.target_entity_id in id_set
            and (not active_only or r.valid_until is None)
        ]
        rels.sort(key=lambda r: r.weight, reverse=True)
        return rels

    def list_relationships(
        self,
        entity_id: Optional[str] = None,
        rel_type: Optional[str] = None,
        include_expired: bool = False,
        limit: int = 100,
    ) -> List[Relationship]:
        rels = list(self._relationships.values())
        if entity_id:
            rels = [r for r in rels if r.source_entity_id == entity_id or r.target_entity_id == entity_id]
        if rel_type:
            rels = [r for r in rels if r.rel_type == rel_type]
        if not include_expired:
            rels = [r for r in rels if r.valid_until is None]
        rels.sort(key=lambda r: r.weight, reverse=True)
        return rels[:limit]

    def query_relationships(
        self,
        entity_ids: List[str],
        direction: str = "both",
        active_only: bool = True,
        at_time: Optional[str] = None,
        rel_types: Optional[List[str]] = None,
    ) -> List[Relationship]:
        if not entity_ids:
            return []
        id_set = set(entity_ids)
        rels = []
        for r in self._relationships.values():
            if direction == "outbound":
                if r.source_entity_id not in id_set:
                    continue
            elif direction == "inbound":
                if r.target_entity_id not in id_set:
                    continue
            else:
                if r.source_entity_id not in id_set and r.target_entity_id not in id_set:
                    continue

            if active_only and not at_time and r.valid_until is not None:
                continue

            if at_time:
                if r.valid_from > at_time:
                    continue
                if r.valid_until is not None and r.valid_until < at_time:
                    continue

            if rel_types and r.rel_type not in rel_types:
                continue

            rels.append(r)
        rels.sort(key=lambda r: r.weight, reverse=True)
        return rels

    # ------------------------------------------------------------------
    # Entity Mention CRUD
    # ------------------------------------------------------------------

    def save_entity_mention(self, mention: EntityMention) -> None:
        # Idempotent: check for existing (entity_id, memory_id) pair
        for m in self._entity_mentions:
            if m.entity_id == mention.entity_id and m.memory_id == mention.memory_id:
                return
        self._entity_mentions.append(mention)

    def get_entity_mentions_for_memory(self, memory_id: str) -> List[EntityMention]:
        return [m for m in self._entity_mentions if m.memory_id == memory_id]

    def get_entity_mentions_for_entity(self, entity_id: str) -> List[EntityMention]:
        return [m for m in self._entity_mentions if m.entity_id == entity_id]

    def transfer_entity_mentions(self, from_id: str, to_id: str) -> None:
        existing_memory_ids = {
            m.memory_id for m in self._entity_mentions if m.entity_id == to_id
        }
        new_mentions = []
        for m in self._entity_mentions:
            if m.entity_id == from_id:
                if m.memory_id not in existing_memory_ids:
                    m.entity_id = to_id
                    new_mentions.append(m)
                # else: skip (would violate unique)
            else:
                new_mentions.append(m)
        self._entity_mentions = new_mentions

    def transfer_entity_relationships(self, from_id: str, to_id: str) -> None:
        for r in self._relationships.values():
            if r.source_entity_id == from_id:
                r.source_entity_id = to_id
            if r.target_entity_id == from_id:
                r.target_entity_id = to_id
