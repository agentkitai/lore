"""Data types for freshness detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StalenessStatus = Literal[
    "fresh", "possibly_stale", "likely_stale", "stale", "unknown"
]


@dataclass
class StalenessResult:
    """Result of a staleness check for a single memory."""

    memory_id: str
    status: StalenessStatus
    confidence: float
    commits_since: int
    file_exists: bool
    reason: str
