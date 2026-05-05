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


# Graph slice dataclasses


@dataclass(frozen=True, slots=True)
class NewEntity:
    name: str
    entity_type: str
    aliases: Sequence[str] = ()
    description: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    mention_count: int = 1
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class StoredEntity:
    id: str
    name: str
    entity_type: str
    aliases: Sequence[str]
    description: Optional[str]
    metadata: Mapping[str, Any]
    mention_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewMention:
    entity_id: str
    memory_id: str
    mention_type: str = "explicit"
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class StoredMention:
    id: str
    entity_id: str
    memory_id: str
    mention_type: str
    confidence: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewRelationship:
    source_entity_id: str
    target_entity_id: str
    rel_type: str
    weight: float = 1.0
    properties: Mapping[str, Any] = field(default_factory=dict)
    source_fact_id: Optional[str] = None
    source_memory_id: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    status: str = "approved"


@dataclass(frozen=True, slots=True)
class StoredRelationship:
    id: str
    source_entity_id: str
    target_entity_id: str
    rel_type: str
    weight: float
    properties: Mapping[str, Any]
    source_fact_id: Optional[str]
    source_memory_id: Optional[str]
    valid_from: datetime
    valid_until: Optional[datetime]
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class GraphStats:
    total_memories: int
    total_entities: int
    total_relationships: int
    by_type: Mapping[str, int]
    by_project: Mapping[str, int]
    by_entity_type: Mapping[str, int]
    top_entities: Sequence[Mapping[str, Any]]
    avg_importance: float
    recent_24h: int
    recent_7d: int
    oldest_memory: Optional[datetime]
    newest_memory: Optional[datetime]


@dataclass(frozen=True, slots=True)
class TimelineBucketRow:
    bucket_date: datetime
    mem_type: str
    count: int


@dataclass(frozen=True, slots=True)
class PendingRelationshipRow:
    id: str
    source_entity_id: str
    target_entity_id: str
    rel_type: str
    weight: float
    source_memory_id: Optional[str]
    created_at: datetime
    source_name: str
    source_entity_type: str
    source_mentions: int
    target_name: str
    target_entity_type: str
    target_mentions: int
