"""Typed dataclasses for the persistence layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True, slots=True)
class NewMemory:
    org_id: str
    content: str
    embedding: Sequence[float]
    context: Optional[str] = None
    tags: Sequence[str] = ()
    confidence: float = 0.5
    source: Optional[str] = None
    project: Optional[str] = None
    expires_at: Optional[datetime] = None
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredMemory:
    id: str
    org_id: str
    content: str
    context: Optional[str]
    tags: Sequence[str]
    confidence: float
    source: Optional[str]
    project: Optional[str]
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]
    upvotes: int
    downvotes: int
    meta: Mapping[str, Any]
    importance_score: float
    access_count: int
    last_accessed_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class ScoredMemory(StoredMemory):
    score: float


@dataclass(frozen=True, slots=True)
class MemoryFilter:
    org_id: str
    project: Optional[str] = None
    type: Optional[str] = None
    tier: Optional[str] = None
    tags: Optional[Sequence[str]] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    limit: Optional[int] = None
    offset: int = 0
    include_expired: bool = False


@dataclass(frozen=True, slots=True)
class MemoryPatch:
    content: Optional[str] = None
    context: Optional[str] = None
    tags: Optional[Sequence[str]] = None
    confidence: Optional[float] = None
    source: Optional[str] = None
    project: Optional[str] = None
    expires_at: Optional[datetime] = None
    meta: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True, slots=True)
class RecallParams:
    org_id: str
    query_vec: Sequence[float]
    limit: int = 5
    min_score: float = 0.3
    project: Optional[str] = None
    half_life_days: int = 30
    exclude_expired: bool = True
