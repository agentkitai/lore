"""Temporal recall engine: filter resolution and on-this-day queries."""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from lore.store.base import Store
from lore.types import VALID_WINDOWS, Memory, RecallConfig

logger = logging.getLogger(__name__)


class OnThisDayEngine:
    """Query memories by month+day across all years, grouped by year.

    Enables temporal recall: "what happened on this day in past years?"
    Respects tier visibility, archived status, and importance ordering.

    Examples::

        engine = OnThisDayEngine(store, logger)

        # Memories from March 6 across all years
        results = engine.on_this_day(month=3, day=6)
        # => {2024: [mem1, mem2], 2023: [mem3]}

        # With date window (March 5-7)
        results = engine.on_this_day(month=3, day=6, date_window_days=1)

        # Filtered by project and tier
        results = engine.on_this_day(month=3, day=6, project="work", tier="long")
    """

    def __init__(self, store: Store, log: Optional[logging.Logger] = None) -> None:
        self.store = store
        self.logger = log or logger

    def on_this_day(
        self,
        month: Optional[int] = None,
        day: Optional[int] = None,
        project: Optional[str] = None,
        tier: Optional[str] = None,
        date_window_days: int = 1,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Dict[int, List[Memory]]:
        """Query memories from month+day across all years, grouped by year.

        Args:
            month: Month to query (1-12). Defaults to today's month.
            day: Day to query (1-31). Defaults to today's day.
            project: Filter by project namespace.
            tier: Filter by memory tier (working/short/long).
            date_window_days: Day range around target day (default 1, meaning day-1 to day+1).
            limit: Maximum total memories to return.
            offset: Number of memories to skip (for pagination).

        Returns:
            Dict mapping year (int) to list of Memory objects, sorted by
            year DESC, then importance_score DESC within each year.

        Raises:
            ValueError: If month or day values are out of range.

        Examples:
            >>> engine = OnThisDayEngine(store, logger)
            >>> results = engine.on_this_day(month=3, day=6)
            >>> for year, memories in results.items():
            ...     print(f"{year}: {len(memories)} memories")
        """
        today = date.today()
        if month is None:
            month = today.month
        if day is None:
            day = today.day

        if not (1 <= month <= 12):
            raise ValueError(f"month must be 1-12, got {month}")
        if not (1 <= day <= 31):
            raise ValueError(f"day must be 1-31, got {day}")

        day_min = max(1, day - date_window_days)
        day_max = min(31, day + date_window_days)

        self.logger.debug(
            "on_this_day: month=%d, day=%d, window=%d (days %d-%d)",
            month, day, date_window_days, day_min, day_max,
        )

        # Fetch all non-archived memories (filter date in Python for portability)
        all_memories = self.store.list(
            project=project,
            tier=tier,
            include_archived=False,
        )

        # Filter by month+day, valid_until, and archived
        matched: List[Memory] = []
        for mem in all_memories:
            if not mem.created_at:
                continue

            try:
                created = datetime.fromisoformat(mem.created_at)
            except (ValueError, TypeError):
                continue

            if created.month != month:
                continue
            if not (day_min <= created.day <= day_max):
                continue

            # Respect valid_until (expired tier memories excluded)
            if hasattr(mem, "expires_at") and mem.expires_at:
                try:
                    expires = datetime.fromisoformat(mem.expires_at)
                    if expires < datetime.now(expires.tzinfo):
                        continue
                except (ValueError, TypeError):
                    pass

            matched.append(mem)

        # Sort by year DESC, then importance DESC, then created_at DESC
        matched.sort(
            key=lambda m: (
                -datetime.fromisoformat(m.created_at).year,
                -m.importance_score,
                m.created_at,
            ),
            reverse=False,
        )

        # Apply offset and limit
        if offset > 0:
            matched = matched[offset:]
        if limit is not None:
            matched = matched[:limit]

        # Group by year
        results_by_year: Dict[int, List[Memory]] = {}
        for mem in matched:
            year = datetime.fromisoformat(mem.created_at).year
            if year not in results_by_year:
                results_by_year[year] = []
            results_by_year[year].append(mem)

        self.logger.debug(
            "on_this_day: found %d memories across %d year(s)",
            sum(len(v) for v in results_by_year.values()),
            len(results_by_year),
        )

        return results_by_year

    def format_results(
        self,
        results: Dict[int, List[Memory]],
        include_metadata: bool = False,
    ) -> str:
        """Format on-this-day results as a readable string.

        Used by CLI and as_prompt integration for displaying results.
        """
        if not results:
            return "No memories found for this day."

        lines: List[str] = []
        total = sum(len(mems) for mems in results.values())
        lines.append(f"On this day: {total} memory(ies) across {len(results)} year(s)\n")

        for year in sorted(results.keys(), reverse=True):
            memories = results[year]
            lines.append(f"--- {year} ---")
            for mem in memories:
                lines.append(
                    f"  [{mem.id}] (importance: {mem.importance_score:.2f}, "
                    f"type: {mem.type}, tier: {mem.tier})"
                )
                lines.append(f"    {mem.content[:200]}")
                if include_metadata:
                    if mem.created_at:
                        lines.append(f"    Created: {mem.created_at[:19]}")
                    if mem.source:
                        lines.append(f"    Source: {mem.source}")
                    if mem.project:
                        lines.append(f"    Project: {mem.project}")
                    if mem.tags:
                        lines.append(f"    Tags: {', '.join(mem.tags)}")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ISO 8601 parsing helper
# ---------------------------------------------------------------------------


def parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string to a timezone-aware datetime (UTC default)."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Temporal Filter Resolver (F3)
# ---------------------------------------------------------------------------


class TemporalFilterResolver:
    """Convert temporal RecallConfig params to a (date_from, date_to) range.

    Priority order (highest to lowest):
      1. Explicit dates: date_from / date_to
      2. Before / after boundaries
      3. Relative times: days_ago / hours_ago
      4. Calendar shorthand: year / month / day
      5. Preset windows: window
    """

    @staticmethod
    def resolve(
        config: RecallConfig,
        now: Optional[datetime] = None,
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Return (date_from, date_to) resolved from temporal config."""
        if now is None:
            now = datetime.now(timezone.utc)

        date_from: Optional[datetime] = None
        date_to: Optional[datetime] = None

        # --- Layer 5 (lowest priority): preset windows ---
        if config.window is not None:
            if config.window not in VALID_WINDOWS:
                raise ValueError(
                    f"Invalid window {config.window!r}. "
                    f"Valid options: {', '.join(VALID_WINDOWS)}"
                )
            date_from, date_to = _resolve_window(config.window, now)

        # --- Layer 4: year / month / day ---
        if (
            config.year is not None
            or config.month is not None
            or config.day is not None
        ):
            date_from, date_to = _resolve_ymd(
                config.year, config.month, config.day,
            )

        # --- Layer 3: relative times ---
        if config.days_ago is not None or config.hours_ago is not None:
            days = config.days_ago or 0
            hours = config.hours_ago or 0
            if days < 0:
                raise ValueError("days_ago must be non-negative")
            if hours < 0:
                raise ValueError("hours_ago must be non-negative")
            if config.days_ago == 0 and config.hours_ago is None:
                # "today only" - from start of current UTC day
                date_from = now.replace(
                    hour=0, minute=0, second=0, microsecond=0,
                )
            else:
                date_from = now - timedelta(days=days, hours=hours)
            date_to = None  # up to now

        # --- Layer 2: before / after (compose with above) ---
        if config.after is not None:
            parsed = parse_iso(config.after)
            if date_from is None or parsed > date_from:
                date_from = parsed
        if config.before is not None:
            parsed = parse_iso(config.before)
            if date_to is None or parsed < date_to:
                date_to = parsed

        # --- Layer 1 (highest priority): explicit dates ---
        if config.date_from is not None:
            date_from = parse_iso(config.date_from)
        if config.date_to is not None:
            date_to = parse_iso(config.date_to)

        return date_from, date_to

    @staticmethod
    def has_temporal_filters(config: RecallConfig) -> bool:
        """Return True if any temporal filter is set."""
        return any([
            config.date_from, config.date_to,
            config.before, config.after,
            config.year is not None, config.month is not None,
            config.day is not None,
            config.days_ago is not None, config.hours_ago is not None,
            config.window,
        ])


def _resolve_window(
    window: str, now: datetime,
) -> Tuple[datetime, Optional[datetime]]:
    """Resolve a preset window name to (from, to)."""
    if window == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), None
    if window == "last_hour":
        return now - timedelta(hours=1), None
    if window == "last_day":
        return now - timedelta(days=1), None
    if window == "last_week":
        return now - timedelta(weeks=1), None
    if window == "last_month":
        return now - timedelta(days=30), None
    if window == "last_year":
        return now - timedelta(days=365), None
    raise ValueError(f"Unknown window: {window!r}")


def _resolve_ymd(
    year: Optional[int],
    month: Optional[int],
    day: Optional[int],
) -> Tuple[datetime, datetime]:
    """Resolve year/month/day shorthand to a date range."""
    if month is not None and not (1 <= month <= 12):
        raise ValueError(f"Invalid month: {month}")
    if day is not None and not (1 <= day <= 31):
        raise ValueError(f"Invalid day: {day}")

    tz = timezone.utc

    if year is not None and month is not None and day is not None:
        dt = datetime(year, month, day, tzinfo=tz)
        return dt, dt + timedelta(days=1)

    if year is not None and month is not None:
        start = datetime(year, month, 1, tzinfo=tz)
        last_day = calendar.monthrange(year, month)[1]
        end = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=tz)
        return start, end + timedelta(microseconds=1)

    if year is not None and day is None:
        start = datetime(year, 1, 1, tzinfo=tz)
        end = datetime(year + 1, 1, 1, tzinfo=tz)
        return start, end

    if month is not None and year is None and day is None:
        raise ValueError(
            "month-only filter without year is not supported. "
            "Please specify year along with month."
        )

    if day is not None and month is None:
        raise ValueError("day requires month to be specified")

    return None, None  # type: ignore[return-value]
