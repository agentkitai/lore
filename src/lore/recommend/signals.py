"""Signal extractors for proactive recommendations."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def context_similarity(
    session_embedding: List[float],
    memory_embedding: bytes,
) -> Tuple[float, str]:
    """Cosine similarity between session context and memory."""
    import struct
    import numpy as np

    count = len(memory_embedding) // 4
    mem_vec = np.array(struct.unpack(f"{count}f", memory_embedding), dtype=np.float32)
    sess_vec = np.array(session_embedding, dtype=np.float32)

    norm_m = np.linalg.norm(mem_vec)
    norm_s = np.linalg.norm(sess_vec)
    if norm_m < 1e-9 or norm_s < 1e-9:
        return 0.0, "No meaningful similarity"

    sim = float(np.dot(mem_vec, sess_vec) / (norm_m * norm_s))
    return max(0, sim), f"Context similarity: {sim:.2f}"


def entity_overlap(
    session_entities: List[str],
    memory_entities: List[str],
) -> Tuple[float, str]:
    """Fraction of session entities found in memory entities."""
    if not session_entities or not memory_entities:
        return 0.0, "No entity overlap"

    session_set = set(e.lower() for e in session_entities)
    memory_set = set(e.lower() for e in memory_entities)
    overlap = session_set & memory_set

    if not overlap:
        return 0.0, "No entity overlap"

    score = len(overlap) / len(session_set)
    names = ", ".join(list(overlap)[:3])
    return score, f"Shared entities: {names}"


def temporal_pattern(
    memory_created_at: str,
    current_hour: Optional[int] = None,
) -> Tuple[float, str]:
    """Score based on time-of-day access patterns."""
    if current_hour is None:
        current_hour = datetime.now(timezone.utc).hour

    try:
        created = datetime.fromisoformat(memory_created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        memory_hour = created.hour

        # Higher score for memories created at similar time of day
        diff = abs(current_hour - memory_hour)
        if diff > 12:
            diff = 24 - diff
        score = max(0, 1.0 - diff / 12.0) * 0.3  # weak signal
        return score, f"Similar time of day (hour {memory_hour})"
    except (ValueError, TypeError):
        return 0.0, "No temporal pattern"


def access_pattern(
    access_count: int,
    last_accessed_at: Optional[str],
) -> Tuple[float, str]:
    """Score based on access frequency and recency."""
    if access_count == 0:
        return 0.0, "Never accessed"

    score = min(1.0, math.log(access_count + 1) / 5.0) * 0.5
    return score, f"Accessed {access_count} times"
