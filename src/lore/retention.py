"""Policy-based retention — find and remove stale, low-importance memories."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from lore.lore import Lore
    from lore.types import Memory

logger = logging.getLogger(__name__)


@dataclass
class RetentionPolicy:
    """Declarative retention policy."""

    max_age_days: int = 90
    min_importance_score: float = 0.3
    archive_on_expire: bool = False
    dry_run: bool = False


@dataclass
class RetentionResult:
    """Outcome of applying a retention policy."""

    deleted_count: int = 0
    archived_count: int = 0
    dry_run: bool = False
    affected_ids: List[str] = field(default_factory=list)


def _find_expired(lore: "Lore", policy: RetentionPolicy) -> List["Memory"]:
    """Return memories that exceed *max_age_days* and fall below *min_importance_score*."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=policy.max_age_days)
    memories = lore.list_memories()
    expired: List["Memory"] = []
    for m in memories:
        if not m.created_at:
            continue
        try:
            created = datetime.fromisoformat(m.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if created < cutoff and m.importance_score < policy.min_importance_score:
            expired.append(m)
    return expired


def _archive_memories(memories: List["Memory"], output_path: str) -> int:
    """Export *memories* to a JSON file.  Returns the number archived."""
    records = []
    for m in memories:
        records.append({
            "id": m.id,
            "content": m.content,
            "type": m.type,
            "tier": m.tier,
            "tags": m.tags,
            "project": m.project,
            "source": m.source,
            "importance_score": m.importance_score,
            "created_at": m.created_at,
            "metadata": m.metadata,
        })
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    with open(output_path, "w") as fh:
        json.dump({"archived_memories": records}, fh, indent=2)
    return len(records)


def apply_retention(lore: "Lore", policy: RetentionPolicy) -> RetentionResult:
    """Apply *policy* to the Lore instance.

    Returns a :class:`RetentionResult` summarising what was (or would be) done.
    """
    expired = _find_expired(lore, policy)
    result = RetentionResult(dry_run=policy.dry_run)
    result.affected_ids = [m.id for m in expired]

    if not expired:
        return result

    if policy.dry_run:
        result.deleted_count = len(expired)
        if policy.archive_on_expire:
            result.archived_count = len(expired)
        return result

    # Archive first (before deleting) so data is preserved on failure.
    if policy.archive_on_expire:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        archive_path = f"lore-archive-{ts}.json"
        result.archived_count = _archive_memories(expired, archive_path)
        logger.info("Archived %d memories to %s", result.archived_count, archive_path)

    for m in expired:
        lore.forget(m.id)
        result.deleted_count += 1

    return result
