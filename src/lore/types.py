"""Core data types for Lore SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    staleness: Any = None  # Optional StalenessResult, set when check_freshness=True


@dataclass
class MemoryStats:
    """Aggregate statistics about stored memories.

    Plan deviation: returns a dataclass instead of ``Dict[str, Any]`` for
    type safety and IDE autocompletion. Fields match the plan's dict keys.
    """

    total: int
    by_type: Dict[str, int] = field(default_factory=dict)
    oldest: Optional[str] = None
    newest: Optional[str] = None
    expired_cleaned: int = 0


# Type-specific decay half-lives (in days).
# Memories with a matching ``type`` decay at the rate below.
# Types not listed here fall back to the default (30 days).
DECAY_HALF_LIVES: Dict[str, float] = {
    "code": 14,
    "note": 21,
    "lesson": 30,
    "convention": 60,
}

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

