"""Core data types for Lore SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Legacy lesson types (backward compatible) ────────────────────


@dataclass
class Lesson:
    """A single lesson learned by an agent."""

    id: str
    problem: str
    resolution: str
    context: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    confidence: float = 0.5
    source: Optional[str] = None
    project: Optional[str] = None
    embedding: Optional[bytes] = None
    created_at: str = ""
    updated_at: str = ""
    expires_at: Optional[str] = None
    upvotes: int = 0
    downvotes: int = 0
    meta: Optional[Dict[str, Any]] = None


@dataclass
class QueryResult:
    """A query result containing a lesson and its relevance score."""

    lesson: Lesson
    score: float


# ── General memory types ─────────────────────────────────────────


@dataclass
class Memory:
    """A single memory."""

    id: str
    content: str
    type: str = "note"
    source: Optional[str] = None
    project: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[bytes] = None  # Serialized float32 vector (local store)
    created_at: str = ""
    updated_at: str = ""
    expires_at: Optional[str] = None


@dataclass
class SearchResult:
    """A memory with its relevance score."""

    memory: Memory
    score: float


@dataclass
class StoreStats:
    """Summary statistics about the memory store."""

    total_count: int
    count_by_type: Dict[str, int]
    count_by_project: Dict[str, int]
    oldest_memory: Optional[str]  # ISO timestamp
    newest_memory: Optional[str]  # ISO timestamp
