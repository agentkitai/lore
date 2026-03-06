"""Importance scoring and adaptive decay functions.

All functions are pure (no I/O, no side effects) except that
``time_adjusted_importance`` defaults ``now`` to ``datetime.utcnow()``.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, Optional, Tuple

from lore.types import DECAY_HALF_LIVES, TIER_DECAY_HALF_LIVES, Memory


def compute_importance(memory: Memory) -> float:
    """Compute base importance score from local signals.

    Formula: confidence * vote_factor * access_factor
    """
    vote_factor = max(0.1, 1.0 + (memory.upvotes - memory.downvotes) * 0.1)
    access_factor = 1.0 + math.log2(1 + memory.access_count) * 0.1
    return memory.confidence * vote_factor * access_factor


def decay_factor(age_days: float, half_life_days: float) -> float:
    """Pure decay multiplier. Returns value in (0, 1]."""
    return 0.5 ** (age_days / max(half_life_days, 0.001))


def time_adjusted_importance(
    memory: Memory,
    half_life_days: float,
    now: Optional[datetime] = None,
) -> float:
    """Apply exponential decay to base importance score.

    Uses min(age_since_created, age_since_last_accessed) as the effective
    age so that recently-accessed memories decay from their last access.
    """
    now = now or datetime.utcnow()
    created = datetime.fromisoformat(memory.created_at)

    if memory.last_accessed_at:
        last_access = datetime.fromisoformat(memory.last_accessed_at)
        age_days = min(
            (now - created).total_seconds() / 86400,
            (now - last_access).total_seconds() / 86400,
        )
    else:
        age_days = (now - created).total_seconds() / 86400

    return memory.importance_score * decay_factor(age_days, half_life_days)


def resolve_half_life(
    tier: Optional[str],
    memory_type: str,
    overrides: Optional[Dict[Tuple[str, str], float]] = None,
) -> float:
    """Resolve half-life with fallback chain.

    Resolution order:
    1. overrides[(tier, type)] -- per-project config
    2. TIER_DECAY_HALF_LIVES[tier][type] -- tier+type specific
    3. TIER_DECAY_HALF_LIVES[tier]["default"] -- tier default
    4. DECAY_HALF_LIVES[type] -- legacy flat lookup (= long tier)
    5. 30.0 -- global default
    """
    effective_tier = tier or "long"

    if overrides and (effective_tier, memory_type) in overrides:
        return overrides[(effective_tier, memory_type)]

    tier_config = TIER_DECAY_HALF_LIVES.get(effective_tier, {})
    if memory_type in tier_config:
        return tier_config[memory_type]
    if "default" in tier_config:
        return tier_config["default"]

    return DECAY_HALF_LIVES.get(memory_type, 30.0)
