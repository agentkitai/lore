"""In-memory store implementation for testing."""

from __future__ import annotations

from typing import Dict, List, Optional

from lore.store.base import Store
from lore.types import Lesson


class MemoryStore(Store):
    """In-memory store backed by a dict. Useful for testing."""

    def __init__(self) -> None:
        self._lessons: Dict[str, Lesson] = {}

    def save(self, lesson: Lesson) -> None:
        self._lessons[lesson.id] = lesson

    def get(self, lesson_id: str) -> Optional[Lesson]:
        return self._lessons.get(lesson_id)

    def list(
        self,
        project: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Lesson]:
        lessons = list(self._lessons.values())
        if project is not None:
            lessons = [
                item for item in lessons if item.project == project
            ]
        lessons.sort(key=lambda item: item.created_at, reverse=True)
        if limit is not None:
            lessons = lessons[:limit]
        return lessons

    def update(self, lesson: Lesson) -> bool:
        if lesson.id not in self._lessons:
            return False
        self._lessons[lesson.id] = lesson
        return True

    def delete(self, lesson_id: str) -> bool:
        return self._lessons.pop(lesson_id, None) is not None
