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
    text_query: Optional[str] = None       # ILIKE search across content + context
    min_reputation: Optional[int] = None   # reputation_score >= N


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


# ── Recommendations slice dataclasses ───


@dataclass(frozen=True, slots=True)
class RecommendationCandidate:
    """Memory shape the recommendation engine expects: includes embedding."""

    id: str
    content: str
    embedding: Sequence[float]
    metadata: Mapping[str, Any]
    created_at: datetime
    access_count: int
    last_accessed_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class StoredRecommendationConfig:
    id: str
    workspace_id: Optional[str]
    agent_id: Optional[str]
    aggressiveness: float
    enabled: bool
    max_suggestions: int
    cooldown_minutes: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewRecommendationFeedback:
    org_id: str
    memory_id: str
    actor_id: str
    feedback: str  # validated by service: "positive" or "negative"
    workspace_id: Optional[str] = None
    signal: str = "manual"
    context_hash: Optional[str] = None


# ── Conversations slice dataclasses ───


@dataclass(frozen=True, slots=True)
class NewConversationJob:
    org_id: str
    message_count: int
    messages_json: str  # JSON-serialized list of {"role","content"} dicts
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    project: Optional[str] = None


@dataclass(frozen=True, slots=True)
class StoredConversationJob:
    id: str
    org_id: str
    status: str
    message_count: int
    messages_json: str
    user_id: Optional[str]
    session_id: Optional[str]
    project: Optional[str]
    memory_ids: Sequence[str]
    memories_extracted: int
    duplicates_skipped: int
    error: Optional[str]
    processing_time_ms: int
    created_at: datetime
    completed_at: Optional[datetime]


# ── Lessons slice dataclasses ───


@dataclass(frozen=True, slots=True)
class ExportedMemory:
    """Memory shape for bulk export — includes embedding + all wire-relevant fields."""

    id: str
    org_id: str
    content: str
    context: Optional[str]
    tags: Sequence[str]
    confidence: float
    source: Optional[str]
    project: Optional[str]
    embedding: Optional[Sequence[float]]
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]
    upvotes: int
    downvotes: int
    meta: Mapping[str, Any]


# ── Dashboard slice dataclasses ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class StoredAuditEntry:
    id: int
    org_id: str
    workspace_id: Optional[str]
    actor_id: str
    actor_type: str
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    metadata: Mapping[str, Any]
    ip_address: Optional[str]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ScoreDistributionBucket:
    bucket: str
    count: int


@dataclass(frozen=True, slots=True)
class TopQueryRow:
    query: str
    count: int
    avg_score: Optional[float]


@dataclass(frozen=True, slots=True)
class DailyStatRow:
    date: str
    queries: int
    avg_score: Optional[float]
    hit_rate: float


@dataclass(frozen=True, slots=True)
class RetrievalAnalyticsResult:
    total_queries: int
    queries_with_results: int
    queries_empty: int
    avg_results_per_query: float
    avg_score: Optional[float]
    avg_max_score: Optional[float]
    avg_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    score_distribution: Sequence[ScoreDistributionBucket]
    top_queries: Sequence[TopQueryRow]
    unique_memories_retrieved: int
    total_memories: int
    daily_stats: Sequence[DailyStatRow]


# ── Retention slice dataclasses ───


@dataclass(frozen=True, slots=True)
class NewRetentionPolicy:
    org_id: str
    name: str
    retention_window: Mapping[str, Any] = field(default_factory=lambda: {"working": 3600, "short": 604800, "long": None})
    snapshot_schedule: Optional[str] = None
    encryption_required: bool = False
    max_snapshots: int = 50
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class StoredRetentionPolicy:
    id: str
    org_id: str
    name: str
    retention_window: Mapping[str, Any]
    snapshot_schedule: Optional[str]
    encryption_required: bool
    max_snapshots: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RetentionPolicyPatch:
    name: Optional[str] = None
    retention_window: Optional[Mapping[str, Any]] = None
    snapshot_schedule: Optional[str] = None
    encryption_required: Optional[bool] = None
    max_snapshots: Optional[int] = None
    is_active: Optional[bool] = None


@dataclass(frozen=True, slots=True)
class StoredSnapshotMetadata:
    id: str
    org_id: str
    policy_id: Optional[str]
    name: str
    path: str
    size_bytes: Optional[int]
    memory_count: Optional[int]
    encrypted: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewDrillResult:
    org_id: str
    snapshot_id: Optional[str]
    snapshot_name: str
    started_at: datetime
    completed_at: Optional[datetime]
    recovery_time_ms: Optional[int]
    memories_restored: Optional[int]
    status: str
    error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class StoredDrillResult:
    id: str
    org_id: str
    snapshot_id: Optional[str]
    snapshot_name: str
    started_at: datetime
    completed_at: Optional[datetime]
    recovery_time_ms: Optional[int]
    memories_restored: Optional[int]
    status: str
    error: Optional[str]
    created_at: datetime


# ── SLO slice dataclasses ───


@dataclass(frozen=True, slots=True)
class NewSloDefinition:
    org_id: str
    name: str
    metric: str
    operator: str
    threshold: float
    window_minutes: int = 60
    enabled: bool = True
    alert_channels: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class StoredSloDefinition:
    id: str
    org_id: str
    name: str
    metric: str
    operator: str
    threshold: float
    window_minutes: int
    enabled: bool
    alert_channels: Sequence[Mapping[str, Any]]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SloDefinitionPatch:
    name: Optional[str] = None
    metric: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[float] = None
    window_minutes: Optional[int] = None
    enabled: Optional[bool] = None
    alert_channels: Optional[Sequence[Mapping[str, Any]]] = None


@dataclass(frozen=True, slots=True)
class NewSloAlert:
    org_id: str
    slo_id: str
    metric_value: float
    threshold: float
    status: str
    dispatched_to: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class StoredSloAlert:
    id: int
    org_id: str
    slo_id: str
    metric_value: float
    threshold: float
    status: str
    dispatched_to: Sequence[Mapping[str, Any]]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TimeseriesPoint:
    timestamp: datetime
    value: Optional[float]


# ── Sharing slice dataclasses ───


@dataclass(frozen=True, slots=True)
class SharingConfigData:
    enabled: bool
    human_review_enabled: bool
    rate_limit_per_hour: int
    volume_alert_threshold: int
    updated_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class SharingConfigPatch:
    enabled: Optional[bool] = None
    human_review_enabled: Optional[bool] = None
    rate_limit_per_hour: Optional[int] = None
    volume_alert_threshold: Optional[int] = None


@dataclass(frozen=True, slots=True)
class AgentSharingConfigData:
    agent_id: str
    enabled: bool
    categories: Sequence[str]
    updated_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class DenyListRuleData:
    id: str
    pattern: str
    is_regex: bool
    reason: Optional[str]
    created_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class NewDenyListRule:
    org_id: str
    pattern: str
    is_regex: bool = False
    reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class AuditEventData:
    id: str
    event_type: str
    lesson_id: Optional[str]
    query_text: Optional[str]
    initiated_by: str
    created_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class NewAuditEvent:
    org_id: str
    event_type: str
    initiated_by: str
    lesson_id: Optional[str] = None
    query_text: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SharingStatsData:
    count_shared: int
    last_shared: Optional[datetime]
    audit_summary: Mapping[str, int]
