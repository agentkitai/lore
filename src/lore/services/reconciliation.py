"""Write-time AUDN reconciliation (Add / Update / Delete / None) for memory writes (#66).

Before a memory is inserted, reconcile the incoming content against existing
memories in the same org/scope (via semantic k-NN). Instead of pure append-only:

  * **Add**    — no strong near-duplicate → insert a new row (status quo).
  * **None**   — a redundant near-duplicate already exists → skip the write.
  * **Update** — a near-duplicate the writer owns gains new tags → merge tags in place.
  * **Delete** — a strong-but-changed prior version → insert the fresh row and
                 *supersede* the old one (soft; read-side suppresses it).

Conservative by design: it only reconciles against **same-type**, non-superseded
candidates the writer may see (shared or their own), and never supersedes across
memory types. ``update_memory`` does NOT re-embed, so content changes never patch
in place — they always go through Delete (fresh row + supersession), keeping the
stored vector consistent with the stored content.

Gated by ``LORE_RECONCILIATION_ENABLED`` (default on); set it false to restore
append-only behavior.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass
from typing import Literal, Optional, Sequence

from lore.persistence import RecallParams, Store, StoredMemory

logger = logging.getLogger(__name__)

# recall_by_embedding decays the score by recency; a huge half-life makes the
# decay negligible so the returned score is ~raw cosine similarity for thresholding.
_NO_DECAY_HALF_LIFE = 1_000_000

# Types that must stay append-only. Observations are a high-volume auto-capture
# tier whose multiplicity/temporal density is the signal — deduping them would
# erase exactly what the capture/dream subagents rely on.
NO_RECONCILE_TYPES = frozenset({"observation"})

Action = Literal["add", "update", "delete", "none"]


@dataclass(frozen=True)
class ReconcileConfig:
    enabled: bool
    duplicate_threshold: float  # sim >= this (same type) → near-exact: Update or None
    supersede_threshold: float  # supersede_threshold <= sim < duplicate → Delete
    max_candidates: int


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _num(name: str, default: float, cast):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw)
    except ValueError:
        return default


@functools.lru_cache(maxsize=1)
def get_reconcile_config() -> ReconcileConfig:
    """Read reconciliation settings from the environment (cached; call
    ``get_reconcile_config.cache_clear()`` in tests after mutating env)."""
    return ReconcileConfig(
        enabled=_flag("LORE_RECONCILIATION_ENABLED", True),
        duplicate_threshold=_num("LORE_RECON_DUPLICATE_THRESHOLD", 0.97, float),
        # 0.90 (not 0.85): with default-ON, only supersede when clearly the same
        # memory revised — related-but-distinct knowledge stays as separate rows.
        supersede_threshold=_num("LORE_RECON_SUPERSEDE_THRESHOLD", 0.90, float),
        max_candidates=_num("LORE_RECON_MAX_CANDIDATES", 5, int),
    )


@dataclass(frozen=True)
class ReconcileDecision:
    action: Action
    candidate: Optional[StoredMemory] = None
    similarity: float = 0.0
    reason: str = ""


def _same_type(a: Optional[str], b: Optional[str]) -> bool:
    return (a or None) == (b or None)


async def reconcile_for_write(
    store: Store,
    *,
    org_id: str,
    embedding: Sequence[float],
    tags: Sequence[str],
    mem_type: Optional[str],
    project: Optional[str],
    user_id: Optional[str],
) -> ReconcileDecision:
    """Decide the AUDN action for an incoming memory against existing ones.

    Returns ``Add`` when reconciliation is disabled, no candidate clears the
    supersede threshold, or no same-type candidate is found (fail-safe to the
    append-only default rather than a surprising merge).
    """
    cfg = get_reconcile_config()
    if not cfg.enabled:
        return ReconcileDecision("add", reason="reconciliation disabled")

    if mem_type in NO_RECONCILE_TYPES:
        return ReconcileDecision("add", reason=f"type {mem_type!r} is append-only")

    # A zero/degenerate embedding carries no semantic signal (cosine is undefined),
    # so there's nothing to reconcile against → just add.
    if not any(embedding):
        return ReconcileDecision("add", reason="degenerate embedding")

    # Semantic k-NN over the same org/project/scope, visibility-gated to the
    # writer (shared memories + their own). min_score caps to the reconcile band.
    candidates = await store.recall_by_embedding(
        RecallParams(
            org_id=org_id,
            query_vec=embedding,
            limit=max(cfg.max_candidates, 1),
            min_score=cfg.supersede_threshold,
            project=project,
            half_life_days=_NO_DECAY_HALF_LIFE,
            scope_mode="default",
            requesting_user_id=user_id,
        )
    )
    if not candidates:
        return ReconcileDecision("add", reason="no near-duplicate candidate")

    # Don't reconcile against already-superseded rows.
    superseded = await store.are_superseded({c.id for c in candidates})

    # Highest-similarity (recall returns score-desc), same-type, live candidate that
    # the writer OWNS. Only reconciling your own memories avoids surprises with team
    # data: another user's row (even a visible shared one) is never updated,
    # superseded, or silently deduped — the incoming write just becomes a new row.
    target = next(
        (
            c
            for c in candidates
            if c.id not in superseded
            and _same_type(c.meta.get("type"), mem_type)
            and c.user_id == user_id
        ),
        None,
    )
    if target is None:
        return ReconcileDecision("add", reason="no owned same-type candidate above threshold")

    sim = float(target.score)
    if sim >= cfg.duplicate_threshold:
        # Near-exact duplicate of the writer's own row: fold in any new tags, else
        # it's redundant → skip the write and return the existing row.
        new_tags = {t for t in tags if t} - set(target.tags)
        if new_tags:
            return ReconcileDecision(
                "update", target, sim, f"near-duplicate; merging {len(new_tags)} tag(s)"
            )
        return ReconcileDecision("none", target, sim, f"redundant near-duplicate (sim={sim:.3f})")

    # Strong but changed → the incoming row is the fresh truth; supersede the old.
    return ReconcileDecision("delete", target, sim, f"supersedes prior version (sim={sim:.3f})")
