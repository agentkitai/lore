"""Abstract store interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from lore.types import Lesson


class Store(ABC):
    """Abstract base class for lesson storage backends."""

    @abstractmethod
    def save(self, lesson: Lesson) -> None:
        """Save a lesson (insert or update)."""

    @abstractmethod
    def get(self, lesson_id: str) -> Optional[Lesson]:
        """Get a lesson by ID, or None if not found."""

    @abstractmethod
    def list(
        self,
        project: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Lesson]:
        """List lessons, optionally filtered by project, ordered by created_at desc."""

    @abstractmethod
    def update(self, lesson: Lesson) -> bool:
        """Update an existing lesson. Returns True if it existed."""

    @abstractmethod
    def delete(self, lesson_id: str) -> bool:
        """Delete a lesson by ID. Returns True if it existed."""
