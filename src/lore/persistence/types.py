"""Typed dataclasses for the persistence layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping, Optional, Sequence


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


# Profile slice dataclasses


@dataclass(frozen=True, slots=True)
class NewProfile:
    org_id: str
    name: str
    semantic_weight: float = 1.0
    graph_weight: float = 1.0
    recency_bias: float = 30.0
    tier_filters: Optional[Sequence[str]] = None
    min_score: float = 0.3
    max_results: int = 10
    is_preset: bool = False
    k: Optional[int] = None
    threshold: Optional[float] = None
    rerank: bool = False
    include_graph: bool = True


@dataclass(frozen=True, slots=True)
class StoredProfile:
    id: str
    org_id: str
    name: str
    semantic_weight: float
    graph_weight: float
    recency_bias: float
    tier_filters: Optional[Sequence[str]]
    min_score: float
    max_results: int
    is_preset: bool
    k: Optional[int]
    threshold: Optional[float]
    rerank: bool
    include_graph: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProfilePatch:
    name: Optional[str] = None
    semantic_weight: Optional[float] = None
    graph_weight: Optional[float] = None
    recency_bias: Optional[float] = None
    tier_filters: Optional[Sequence[str]] = None
    min_score: Optional[float] = None
    max_results: Optional[int] = None
    is_preset: Optional[bool] = None
    k: Optional[int] = None
    threshold: Optional[float] = None
    rerank: Optional[bool] = None
    include_graph: Optional[bool] = None


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    name: str
    source: Literal["stored", "default"]
    semantic_weight: float
    graph_weight: float
    recency_bias: float
    min_score: float
    max_results: int
    tier_filters: Optional[Sequence[str]]
    k: Optional[int]
    threshold: Optional[float]
    rerank: bool
    include_graph: bool


# Identity slice dataclasses


# ── Workspace ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NewWorkspace:
    org_id: str
    name: str
    slug: str
    settings: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredWorkspace:
    id: str
    org_id: str
    name: str
    slug: str
    settings: Mapping[str, Any]
    created_at: datetime
    archived_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class WorkspacePatch:
    name: Optional[str] = None
    settings: Optional[Mapping[str, Any]] = None


# ── Workspace member ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NewMember:
    workspace_id: str
    user_id: str
    role: str = "writer"


@dataclass(frozen=True, slots=True)
class StoredMember:
    id: str
    workspace_id: str
    user_id: Optional[str]
    role: str
    invited_at: datetime
    accepted_at: Optional[datetime]


# ── API key ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NewApiKey:
    org_id: str
    name: str
    key_hash: str
    key_prefix: str
    project: Optional[str] = None
    is_root: bool = False
    workspace_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class StoredApiKey:
    id: str
    org_id: str
    name: str
    key_hash: str
    key_prefix: str
    project: Optional[str]
    is_root: bool
    workspace_id: Optional[str]
    revoked_at: Optional[datetime]
    created_at: datetime
    last_used_at: Optional[datetime]


# ── Retrieval analytics ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NewRetrievalEvent:
    org_id: str
    query: str
    results_count: int
    scores: Sequence[float]
    memory_ids: Sequence[str]
    avg_score: Optional[float]
    max_score: Optional[float]
    min_score_threshold: Optional[float]
    query_time_ms: Optional[float]
    project: Optional[str] = None
    format: Optional[str] = None
