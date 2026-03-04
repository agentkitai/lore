"""Lore SDK — cross-agent memory library."""

from lore.exceptions import LessonNotFoundError, MemoryNotFoundError
from lore.lore import Lore
from lore.types import Lesson, Memory, MemoryStats, QueryResult, RecallResult

__all__ = [
    "Lore",
    "Memory",
    "RecallResult",
    "MemoryStats",
    "MemoryNotFoundError",
    # Deprecated aliases
    "Lesson",
    "QueryResult",
    "LessonNotFoundError",
]
