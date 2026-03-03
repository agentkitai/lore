"""Abstract store interface for Lore memories."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from lore.types import Memory, SearchResult, StoreStats


class Store(ABC):
    """Abstract base class for memory stores."""

    @abstractmethod
    def save(self, memory: Memory) -> None:
        """Save a memory."""

    @abstractmethod
    def get(self, memory_id: str) -> Optional[Memory]:
        """Get a memory by ID."""

    @abstractmethod
    def search(
        self,
        embedding: List[float],
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 5,
    ) -> List[SearchResult]:
        """Search memories by embedding similarity."""

    @abstractmethod
    def list(
        self,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Memory], int]:
        """List memories with filters. Returns (memories, total_count)."""

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """Delete a single memory by ID. Returns True if deleted."""

    @abstractmethod
    def delete_by_filter(
        self,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
    ) -> int:
        """Bulk delete with filter combination. Returns count deleted."""

    @abstractmethod
    def stats(self, project: Optional[str] = None) -> StoreStats:
        """Get aggregate statistics, optionally filtered by project."""
