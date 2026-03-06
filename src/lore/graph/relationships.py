"""Relationship management for the knowledge graph."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from ulid import ULID

from lore.graph.entities import EntityManager
from lore.store.base import Store
from lore.types import Entity, Fact, Relationship

# Predicate -> relationship type mappings
PREDICATE_TO_REL_TYPE: Dict[str, str] = {
    "depends_on": "depends_on",
    "uses": "uses",
    "implements": "implements",
    "works_on": "works_on",
    "created": "created_by",
    "deployed_on": "deployed_on",
    "part_of": "part_of",
    "extends": "extends",
    "configures": "configures",
    "communicates_with": "communicates_with",
    "mentions": "mentions",
    "is": "related_to",
    "has": "related_to",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RelationshipManager:
    """Manages relationship lifecycle: creation, strengthening, expiration."""

    def __init__(self, store: Store, entity_manager: EntityManager) -> None:
        self.store = store
        self.entity_manager = entity_manager

    @staticmethod
    def _map_predicate(predicate: str) -> str:
        """Map a fact predicate to a relationship type."""
        normalized = predicate.lower().replace(" ", "_")
        return PREDICATE_TO_REL_TYPE.get(normalized, "related_to")

    def ingest_from_fact(
        self, memory_id: str, fact: Fact
    ) -> Optional[Relationship]:
        """Convert an SPO fact into a graph edge."""
        source_entity, target_entity = self.entity_manager.ingest_from_fact(
            memory_id, fact
        )

        rel_type = self._map_predicate(fact.predicate)

        # Check for existing active edge
        existing = self.store.get_active_relationship(
            source_entity.id, target_entity.id, rel_type
        )
        if existing:
            existing.weight = min(1.0, existing.weight + 0.1)
            existing.updated_at = _utc_now_iso()
            self.store.update_relationship(existing)
            return existing

        # Create new relationship
        now = _utc_now_iso()
        rel = Relationship(
            id=str(ULID()),
            source_entity_id=source_entity.id,
            target_entity_id=target_entity.id,
            rel_type=rel_type,
            weight=fact.confidence,
            source_fact_id=fact.id,
            source_memory_id=memory_id,
            valid_from=now,
            valid_until=None,
            created_at=now,
            updated_at=now,
        )
        self.store.save_relationship(rel)
        return rel

    def ingest_co_occurrences(
        self, memory_id: str, entities: List[Entity], weight: float = 0.3
    ) -> List[Relationship]:
        """Create co-occurrence edges between entities in the same memory."""
        relationships = []
        for i, e1 in enumerate(entities):
            for e2 in entities[i + 1:]:
                for source, target in [(e1, e2), (e2, e1)]:
                    existing = self.store.get_active_relationship(
                        source.id, target.id, "co_occurs_with"
                    )
                    if existing:
                        existing.weight = min(1.0, existing.weight + 0.05)
                        existing.updated_at = _utc_now_iso()
                        self.store.update_relationship(existing)
                        relationships.append(existing)
                    else:
                        now = _utc_now_iso()
                        rel = Relationship(
                            id=str(ULID()),
                            source_entity_id=source.id,
                            target_entity_id=target.id,
                            rel_type="co_occurs_with",
                            weight=weight,
                            source_memory_id=memory_id,
                            valid_from=now,
                            created_at=now,
                            updated_at=now,
                        )
                        self.store.save_relationship(rel)
                        relationships.append(rel)
        return relationships

    def expire_relationship_for_fact(self, fact_id: str) -> None:
        """Mark relationship as expired when its source fact is invalidated."""
        rel = self.store.get_relationship_by_fact(fact_id)
        if rel:
            rel.valid_until = _utc_now_iso()
            rel.updated_at = _utc_now_iso()
            self.store.update_relationship(rel)
