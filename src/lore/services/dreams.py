"""Dreams service — Phase 6E memory consolidation orchestration.

This module is the thin service layer between the ``lore dream`` CLI and
the Store. It owns:

  * **Eligibility check** — 24h since last completed dream AND ≥5 distinct
    sessions of activity since then. Manual ``--force`` bypasses both.
  * **Run lifecycle** — ``start_dream`` inserts a 'running' row;
    ``complete_dream`` / ``fail_dream`` close it with a summary or error.
  * **Status snapshot** — ``get_status`` returns a JSON-friendly dict the
    CLI renders for ``lore dream --status`` and the trigger hook reads.

Phases 1 (Orient), 2 (Gather Signal), 3 (Consolidate), 4 (Prune) all live
in the CLI command and the subagent prompt — this module just persists
the run record + computes eligibility.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from lore.persistence import Store
from lore.persistence.types import DreamRun, NewDreamRun

# Eligibility thresholds (per spec, decision #1).
DREAM_INTERVAL_HOURS = 24
DREAM_MIN_SESSIONS = 5


async def is_dream_eligible(
    store: Store,
    org_id: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Return True iff (24h elapsed since last completed dream) AND
    (≥5 distinct sessions captured since then).

    Special cases:
      * No prior dream run at all → eligible immediately.
      * Last dream was 'failed' → use its ``started_at`` as the lower
        bound for both checks (don't penalize a successful retry).
      * Last dream is still 'running' → NOT eligible (concurrency
        guard, even with --force the CLI flock check applies).
    """
    now = now or datetime.now(timezone.utc)
    last = await store.get_last_dream_run(org_id)
    if last is None:
        return True
    if last.status == "running":
        return False
    # Use started_at as anchor — completed/failed both have it set.
    elapsed = now - _ensure_aware(last.started_at)
    if elapsed < timedelta(hours=DREAM_INTERVAL_HOURS):
        return False
    sessions = await store.count_distinct_sessions_since(
        org_id, _ensure_aware(last.started_at),
    )
    return sessions >= DREAM_MIN_SESSIONS


async def start_dream(store: Store, org_id: str) -> DreamRun:
    """Insert a 'running' dream-run row; returns the stored row."""
    return await store.start_dream(NewDreamRun(org_id=org_id))


async def complete_dream(
    store: Store, run_id: str, summary: Mapping[str, Any],
) -> None:
    """Mark a dream run completed with a structured summary blob."""
    await store.complete_dream(run_id, summary)


async def fail_dream(store: Store, run_id: str, error: str) -> None:
    """Mark a dream run failed with an error string."""
    await store.fail_dream(run_id, error)


async def get_status(
    store: Store,
    org_id: str,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Return a status snapshot for ``lore dream --status`` / hook checks.

    Shape:
        {
          "last_run_at":           ISO-8601 string | None,
          "last_run_status":       "completed" | "failed" | "running" | None,
          "next_eligible_at":      ISO-8601 string,  # earliest time a
                                                     # dream will fire
          "sessions_since_last":   int,
          "sessions_required":     5,
          "interval_hours":        24,
          "last_summary":          dict | None,
          "last_error":            str | None,
          "eligible_now":          bool,
        }

    ``next_eligible_at`` is the max(time-based, session-based) bound:
      * time-based: ``last_run.started_at + 24h`` (or now if no prior run)
      * sessions-based: not a timestamp; surfaced as ``sessions_since_last``
                        + ``sessions_required``. The hook treats
                        ``next_eligible_at`` as "earliest time elapsed-check
                        passes"; the session check is done separately.
    """
    now = now or datetime.now(timezone.utc)
    last = await store.get_last_dream_run(org_id)

    if last is None:
        return {
            "last_run_at": None,
            "last_run_status": None,
            "next_eligible_at": now.isoformat(),
            "sessions_since_last": 0,
            "sessions_required": DREAM_MIN_SESSIONS,
            "interval_hours": DREAM_INTERVAL_HOURS,
            "last_summary": None,
            "last_error": None,
            "eligible_now": True,
        }

    started_at = _ensure_aware(last.started_at)
    next_eligible = started_at + timedelta(hours=DREAM_INTERVAL_HOURS)
    sessions = await store.count_distinct_sessions_since(org_id, started_at)
    eligible = (
        last.status != "running"
        and now >= next_eligible
        and sessions >= DREAM_MIN_SESSIONS
    )

    return {
        "last_run_at": started_at.isoformat(),
        "last_run_status": last.status,
        "next_eligible_at": max(next_eligible, now).isoformat()
            if last.status == "running"
            else next_eligible.isoformat(),
        "sessions_since_last": sessions,
        "sessions_required": DREAM_MIN_SESSIONS,
        "interval_hours": DREAM_INTERVAL_HOURS,
        "last_summary": dict(last.summary) if last.summary else None,
        "last_error": last.error,
        "eligible_now": eligible,
    }


def _ensure_aware(dt: datetime) -> datetime:
    """Coerce a naive datetime to UTC-aware (defensive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


__all__ = [
    "DREAM_INTERVAL_HOURS",
    "DREAM_MIN_SESSIONS",
    "is_dream_eligible",
    "start_dream",
    "complete_dream",
    "fail_dream",
    "get_status",
]
