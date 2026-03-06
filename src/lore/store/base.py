"""Abstract store interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from lore.types import ConflictEntry, Fact, Memory


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
