"""Core data types for Lore SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Valid temporal window presets.
VALID_WINDOWS: Tuple[str, ...] = (
    "today", "last_hour", "last_day", "last_week", "last_month", "last_year",
)

# Valid resolution strategies for fact conflicts.
VALID_RESOLUTIONS: Tuple[str, ...] = ("SUPERSEDE", "MERGE", "CONTRADICT", "NOOP")

# Valid entity types for the knowledge graph.
VALID_ENTITY_TYPES: Tuple[str, ...] = (
    "person", "tool", "project", "concept", "organization",
    "platform", "language", "framework", "service", "other",
)

# Valid relationship types for the knowledge graph.
VALID_REL_TYPES: Tuple[str, ...] = (
    "depends_on", "uses", "implements", "mentions", "works_on",
    "related_to", "part_of", "created_by", "deployed_on",
    "communicates_with", "extends", "configures", "co_occurs_with",
)

# Valid review statuses for knowledge graph relationships (E6).
VALID_REVIEW_STATUSES: Tuple[str, ...] = ("pending", "approved", "rejected")


@dataclass
class RecallConfig:
    """Configuration for temporal recall filters."""

    query: str = ""
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    before: Optional[str] = None
    after: Optional[str] = None
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    days_ago: Optional[int] = None
    hours_ago: Optional[int] = None
    window: Optional[str] = None
    verbatim: bool = False


@dataclass
class Fact:
    """An atomic fact extracted from a memory.

    Represents a (subject, predicate, object) triple — a single piece
    of structured knowledge derived from unstructured memory content.
    """

    id: str
    memory_id: str
    subject: str
    predicate: str
    object: str
    confidence: float = 1.0
    extracted_at: str = ""
    invalidated_by: Optional[str] = None
    invalidated_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ConflictEntry:
    """A record of a fact conflict detection and resolution."""

    id: str
    new_memory_id: str
    old_fact_id: str
    new_fact_id: Optional[str]
    subject: str
    predicate: str
    old_value: str
    new_value: str
    resolution: str
    resolved_at: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class Memory:
    """A single memory stored by an agent.

    Plan deviations (improvements):
    - ``context``: kept (plan said remove) — useful for embedding enrichment
      without polluting content (e.g. ``embed_text = content + context``).
    - ``type`` defaults to ``"general"`` not ``"note"`` — broader default.
    - ``metadata`` instead of ``meta`` — clearer naming.
    - ``ttl`` instead of ``ttl_seconds`` — simpler; unit is always seconds.
    - ``confidence`` defaults to ``1.0`` not ``0.5`` — new memories are trusted
      until evidence suggests otherwise.
    """

    id: str
    content: str
    type: str = "general"
    tier: str = "long"
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
    importance_score: float = 1.0
    access_count: int = 0
    last_accessed_at: Optional[str] = None
    archived: bool = False
    consolidated_into: Optional[str] = None


@dataclass
class RecallResult:
    """A recall result containing a memory and its relevance score."""

    memory: Memory
    score: float
    staleness: Any = None  # Optional StalenessResult, set when check_freshness=True
    verbatim: bool = False


@dataclass
class MemoryStats:
    """Aggregate statistics about stored memories.

    Plan deviation: returns a dataclass instead of ``Dict[str, Any]`` for
    type safety and IDE autocompletion. Fields match the plan's dict keys.
    """

    total: int
    by_type: Dict[str, int] = field(default_factory=dict)
    by_tier: Dict[str, int] = field(default_factory=dict)
    oldest: Optional[str] = None
    newest: Optional[str] = None
    expired_cleaned: int = 0
    avg_importance: Optional[float] = None
    below_threshold_count: int = 0
    archived_count: int = 0
    consolidation_count: int = 0
    last_consolidation_at: Optional[str] = None


# Tier-aware decay half-lives (in days).
# Two-level lookup: TIER_DECAY_HALF_LIVES[tier][type].
TIER_DECAY_HALF_LIVES: Dict[str, Dict[str, float]] = {
    "working": {
        "default": 1,
        "code": 0.5,
        "note": 1,
        "lesson": 3,
        "convention": 3,
        "fact": 2,
        "preference": 2,
        "session_snapshot": 0.5,
    },
    "short": {
        "default": 7,
        "code": 5,
        "note": 7,
        "lesson": 14,
        "convention": 14,
        "fact": 10,
        "preference": 10,
        "session_snapshot": 3,
    },
    "long": {
        "default": 30,
        "code": 14,
        "note": 21,
        "lesson": 30,
        "convention": 60,
        "fact": 90,
        "preference": 90,
        "session_snapshot": 7,
    },
}

# Backward-compatible alias: flat dict mapping type -> half-life (long tier).
DECAY_HALF_LIVES: Dict[str, float] = TIER_DECAY_HALF_LIVES["long"]

# Valid memory types.  The default is "general" — a neutral catch-all that
# suits a universal memory tool (as opposed to "lesson", which implies a
# narrower pedagogical intent).  "general" is *not* in DECAY_HALF_LIVES
# because it uses the global default half-life (30 days), identical to
# "lesson" in practice but semantically broader.
VALID_MEMORY_TYPES = frozenset(
    list(DECAY_HALF_LIVES.keys())
    + [
        "general",       # neutral catch-all (default)
        "fact",          # factual knowledge
        "preference",    # user/agent preferences
        "debug",         # debugging insights
        "pattern",       # recurring patterns
        "session_snapshot",   # session context rescue (E3)
    ]
)

# Memory tier constants — cognitive-science model of working/short/long memory.
VALID_TIERS: Tuple[str, ...] = ("working", "short", "long")

TIER_DEFAULT_TTL: Dict[str, Optional[int]] = {
    "working": 3600,       # 1 hour
    "short":   604800,     # 7 days
    "long":    None,       # no expiry
}

TIER_RECALL_WEIGHT: Dict[str, float] = {
    "working": 1.0,        # baseline
    "short":   1.1,
    "long":    1.2,
}


@dataclass
class Entity:
    """A node in the knowledge graph."""

    id: str
    name: str
    entity_type: str
    aliases: List[str] = field(default_factory=list)
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    mention_count: int = 1
    first_seen_at: str = ""
    last_seen_at: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Relationship:
    """A directed edge in the knowledge graph."""

    id: str
    source_entity_id: str
    target_entity_id: str
    rel_type: str
    weight: float = 1.0
    properties: Optional[Dict[str, Any]] = None
    source_fact_id: Optional[str] = None
    source_memory_id: Optional[str] = None
    valid_from: str = ""
    valid_until: Optional[str] = None
    status: str = "approved"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RejectedPattern:
    """A pattern that should not be re-suggested as a relationship."""

    id: str
    source_name: str
    target_name: str
    rel_type: str
    rejected_at: str = ""
    source_memory_id: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class ReviewItem:
    """A pending relationship with entity context for review."""

    relationship: Relationship
    source_entity_name: str
    source_entity_type: str
    target_entity_name: str
    target_entity_type: str
    source_memory_content: Optional[str] = None


@dataclass
class EntityMention:
    """Links an entity to a memory that mentions it."""

    id: str
    entity_id: str
    memory_id: str
    mention_type: str = "explicit"
    confidence: float = 1.0
    created_at: str = ""


@dataclass
class GraphContext:
    """Result of a graph traversal."""

    entities: List[Entity] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)
    paths: List[List[str]] = field(default_factory=list)
    relevance_score: float = 0.0


# ------------------------------------------------------------------
# Consolidation types and configuration (F3)
# ------------------------------------------------------------------

@dataclass
class ConsolidationLogEntry:
    """A record of a consolidation action."""

    id: str
    consolidated_memory_id: str
    original_memory_ids: List[str]
    strategy: str
    model_used: Optional[str]
    original_count: int
    created_at: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ConsolidationResult:
    """Result of a consolidation run."""

    groups_found: int = 0
    memories_consolidated: int = 0
    memories_created: int = 0
    duplicates_merged: int = 0
    groups: List[Dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True


DEFAULT_RETENTION_POLICIES: Dict[str, int] = {
    "working": 3600,       # 1 hour
    "short": 604800,       # 7 days
    "long": 2592000,       # 30 days
}

DEFAULT_CONSOLIDATION_CONFIG: Dict[str, Any] = {
    "retention_policies": dict(DEFAULT_RETENTION_POLICIES),
    "dedup_threshold": 0.95,
    "min_group_size": 3,
    "batch_size": 50,
    "max_groups_per_run": 100,
    "llm_model": None,
}


# ------------------------------------------------------------------
# Conversation extraction types (v0.8.0)
# ------------------------------------------------------------------

@dataclass
class ProjectGroup:
    """A group of memories belonging to one project."""

    project: str
    memories: List[Memory] = field(default_factory=list)
    count: int = 0
    summary: Optional[str] = None


@dataclass
class RecentActivityResult:
    """Result of a recent_activity query."""

    groups: List[ProjectGroup] = field(default_factory=list)
    total_count: int = 0
    hours: int = 24
    has_llm_summary: bool = False
    query_time_ms: float = 0.0
    generated_at: str = ""


@dataclass
class ConversationMessage:
    """A single message in a conversation."""

    role: str      # "user", "assistant", "system", "tool"
    content: str


@dataclass
class ConversationJob:
    """Result of a conversation extraction job."""

    job_id: str
    status: str                        # "accepted", "processing", "completed", "failed"
    message_count: int = 0
    memories_extracted: int = 0
    memory_ids: List[str] = field(default_factory=list)
    duplicates_skipped: int = 0
    processing_time_ms: int = 0
    error: Optional[str] = None


# ------------------------------------------------------------------
# Export / Import types (E5)
# ------------------------------------------------------------------

@dataclass
class ExportFilter:
    """Filters applied during export."""

    project: Optional[str] = None
    type: Optional[str] = None
    tier: Optional[str] = None
    since: Optional[str] = None


@dataclass
class ExportResult:
    """Result returned by an export operation."""

    path: str
    format: str
    memories: int = 0
    entities: int = 0
    relationships: int = 0
    entity_mentions: int = 0
    facts: int = 0
    conflicts: int = 0
    consolidation_logs: int = 0
    content_hash: str = ""
    duration_ms: int = 0


@dataclass
class ImportResult:
    """Result returned by an import operation."""

    total: int = 0
    imported: int = 0
    skipped: int = 0
    overwritten: int = 0
    errors: int = 0
    warnings: List[str] = field(default_factory=list)
    embeddings_regenerated: int = 0
    duration_ms: int = 0



# ------------------------------------------------------------------
# Topic Notes types (E4)
# ------------------------------------------------------------------

@dataclass
class TopicSummary:
    """A topic in the list view."""

    entity_id: str
    name: str
    entity_type: str
    mention_count: int
    first_seen_at: str
    last_seen_at: str
    related_entity_count: int = 0


@dataclass
class TopicDetail:
    """Full detail for a single topic."""

    entity: Entity
    related_entities: List["RelatedEntity"]
    memories: List[Memory]
    summary: Optional[str] = None
    summary_method: Optional[str] = None
    summary_generated_at: Optional[str] = None
    memory_count: int = 0


@dataclass
class RelatedEntity:
    """An entity related to a topic via a knowledge graph edge."""

    name: str
    entity_type: str
    relationship: str
    direction: str


# ------------------------------------------------------------------
# Review decisions audit trail (F5)
# ------------------------------------------------------------------

@dataclass
class ReviewDecision:
    """A record of an approval/rejection decision on a relationship."""

    id: str
    relationship_id: str
    action: str  # "approve" or "reject"
    reviewer_id: Optional[str] = None
    notes: Optional[str] = None
    decided_at: str = ""


# ------------------------------------------------------------------
# Retention policies (F6)
# ------------------------------------------------------------------

@dataclass
class RetentionPolicy:
    """A declarative lifecycle policy for retention and snapshots."""

    id: str
    org_id: str
    name: str
    retention_window: Optional[Dict[str, Any]] = None
    snapshot_schedule: Optional[str] = None
    encryption_required: bool = False
    max_snapshots: int = 50
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RestoreDrillResult:
    """Result of a restore drill execution."""

    id: str
    org_id: str
    snapshot_name: str
    status: str = "running"  # running, success, failed
    started_at: str = ""
    completed_at: Optional[str] = None
    recovery_time_ms: Optional[int] = None
    memories_restored: Optional[int] = None
    error: Optional[str] = None
