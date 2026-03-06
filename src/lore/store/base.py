"""Abstract store interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from lore.types import (
    ConflictEntry, ConsolidationLogEntry, Entity, EntityMention,
    Fact, Memory, Relationship,
)


class Store(ABC):
    """Abstract base class for memory storage backends."""

    @abstractmethod
    def save(self, memory: Memory) -> None:
        """Save a memory (insert or update)."""

    @abstractmethod
    def get(self, memory_id: str) -> Optional[Memory]:
        """Get a memory by ID, or None if not found."""

    @abstractmethod
    def list(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
        limit: Optional[int] = None,
        include_archived: bool = False,
    ) -> List[Memory]:
        """List memories, optionally filtered by project/type/tier, ordered by created_at desc."""

    @abstractmethod
    def update(self, memory: Memory) -> bool:
        """Update an existing memory. Returns True if it existed."""

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID. Returns True if it existed."""

    @abstractmethod
    def count(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> int:
        """Count memories, optionally filtered by project/type/tier."""

    @abstractmethod
    def cleanup_expired(self) -> int:
        """Delete memories where expires_at < now. Returns count deleted."""

    # ------------------------------------------------------------------
    # Fact + conflict storage (default no-op implementations)
    # ------------------------------------------------------------------

    def save_fact(self, fact: Fact) -> None:
        """Save a fact (insert or update). No-op by default."""
        pass

    def get_facts(self, memory_id: str) -> List[Fact]:
        """Get all facts for a memory. Returns empty list by default."""
        return []

    def get_active_facts(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        limit: int = 50,
    ) -> List[Fact]:
        """Get active (non-invalidated) facts. Returns empty list by default."""
        return []

    def invalidate_fact(self, fact_id: str, invalidated_by: str) -> None:
        """Mark a fact as invalidated. No-op by default."""
        pass

    def save_conflict(self, entry: ConflictEntry) -> None:
        """Save a conflict log entry. No-op by default."""
        pass

    def list_conflicts(
        self,
        resolution: Optional[str] = None,
        limit: int = 20,
    ) -> List[ConflictEntry]:
        """List conflict log entries. Returns empty list by default."""
        return []

    # ------------------------------------------------------------------
    # Graph storage (default no-op implementations)
    # ------------------------------------------------------------------

    def save_entity(self, entity: Entity) -> None:
        pass

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return None

    def get_entity_by_name(self, name: str) -> Optional[Entity]:
        return None

    def get_entity_by_alias(self, alias: str) -> Optional[Entity]:
        return None

    def update_entity(self, entity: Entity) -> None:
        pass

    def delete_entity(self, entity_id: str) -> None:
        pass

    def list_entities(
        self,
        entity_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Entity]:
        return []

    def save_relationship(self, rel: Relationship) -> None:
        pass

    def get_relationship(self, rel_id: str) -> Optional[Relationship]:
        return None

    def get_active_relationship(
        self, source_id: str, target_id: str, rel_type: str
    ) -> Optional[Relationship]:
        return None

    def get_relationship_by_fact(self, fact_id: str) -> Optional[Relationship]:
        return None

    def update_relationship(self, rel: Relationship) -> None:
        pass

    def delete_relationship(self, rel_id: str) -> None:
        pass

    def get_relationships_from(
        self, entity_ids: List[str], active_only: bool = True
    ) -> List[Relationship]:
        return []

    def get_relationships_to(
        self, entity_ids: List[str], active_only: bool = True
    ) -> List[Relationship]:
        return []

    def list_relationships(
        self,
        entity_id: Optional[str] = None,
        rel_type: Optional[str] = None,
        include_expired: bool = False,
        limit: int = 100,
    ) -> List[Relationship]:
        return []

    def save_entity_mention(self, mention: EntityMention) -> None:
        pass

    def get_entity_mentions_for_memory(self, memory_id: str) -> List[EntityMention]:
        return []

    def get_entity_mentions_for_entity(self, entity_id: str) -> List[EntityMention]:
        return []

    def transfer_entity_mentions(self, from_id: str, to_id: str) -> None:
        pass

    def transfer_entity_relationships(self, from_id: str, to_id: str) -> None:
        pass

    def query_relationships(
        self,
        entity_ids: List[str],
        direction: str = "both",
        active_only: bool = True,
        at_time: Optional[str] = None,
        rel_types: Optional[List[str]] = None,
    ) -> List[Relationship]:
        """Query relationships for hop traversal. Returns empty list by default."""
        return []

    # ------------------------------------------------------------------
    # Consolidation log storage (default no-op implementations)
    # ------------------------------------------------------------------

    def save_consolidation_log(self, entry: ConsolidationLogEntry) -> None:
        """Save a consolidation log entry. No-op by default."""
        pass

    def get_consolidation_log(
        self,
        limit: int = 50,
        project: Optional[str] = None,
    ) -> List[ConsolidationLogEntry]:
        """Get consolidation log entries. Returns empty list by default."""
        return []
