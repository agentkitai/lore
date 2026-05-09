"""Adaptive decay functions for memory recall scoring.

Replaces the dropped ``lore.importance`` module with the bits we still
need: a pure exponential ``decay_factor`` and a tier-aware
``resolve_half_life``. ``compute_importance`` and
``time_adjusted_importance`` are gone — the importance_score column
they computed against was dropped in 025_drop_quality_score_columns.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from lore.types import DECAY_HALF_LIVES, TIER_DECAY_HALF_LIVES


def decay_factor(age_days: float, half_life_days: float) -> float:
    """Pure exponential decay multiplier. Returns value in (0, 1]."""
    return 0.5 ** (age_days / max(half_life_days, 0.001))


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
