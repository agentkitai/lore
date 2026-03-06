"""Core data types for Lore SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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


@dataclass
class RecallResult:
    """A recall result containing a memory and its relevance score."""

    memory: Memory
    score: float
    staleness: Any = None  # Optional StalenessResult, set when check_freshness=True


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
    },
    "short": {
        "default": 7,
        "code": 5,
        "note": 7,
        "lesson": 14,
        "convention": 14,
        "fact": 10,
        "preference": 10,
    },
    "long": {
        "default": 30,
        "code": 14,
        "note": 21,
        "lesson": 30,
        "convention": 60,
        "fact": 90,
        "preference": 90,
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
    created_at: str = ""
    updated_at: str = ""


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

