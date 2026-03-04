"""Core data types for Lore SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Memory:
    """A single memory stored by an agent."""

    id: str
    content: str
    type: str = "general"
    context: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    project: Optional[str] = None
    embedding: Optional[bytes] = None
    created_at: str = ""
    updated_at: str = ""
    ttl: Optional[int] = None
    expires_at: Optional[str] = None
    confidence: float = 1.0
    upvotes: int = 0
    downvotes: int = 0


@dataclass
class RecallResult:
    """A recall result containing a memory and its relevance score."""

    memory: Memory
    score: float


@dataclass
class MemoryStats:
    """Aggregate statistics about stored memories."""

    total: int
    by_type: Dict[str, int] = field(default_factory=dict)
    oldest: Optional[str] = None
    newest: Optional[str] = None
    expired_cleaned: int = 0


# Deprecated aliases for backward compatibility
Lesson = Memory
QueryResult = RecallResult
