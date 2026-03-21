"""Background scheduler for retention policy enforcement."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def policy_scheduler_loop(interval_seconds: int = 60) -> None:
    """Background task that enforces retention policies.

    Runs indefinitely every ``interval_seconds``:
    1. Check snapshot schedules (cron match) and trigger snapshots
    2. Enforce retention windows (cleanup expired memories)
    """
    while True:
        try:
            await _enforce_policies()
        except Exception:
            logger.warning("Policy enforcement iteration failed", exc_info=True)
        await asyncio.sleep(interval_seconds)


async def _enforce_policies() -> None:
    """Iterate active policies and enforce retention rules."""
    from lore.server.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        policies = await conn.fetch(
            "SELECT * FROM retention_policies WHERE is_active = TRUE"
        )

        for policy in policies:
            try:
                retention = policy["retention_window"] or {}

                # Enforce working tier retention
                working_ttl = retention.get("working")
                if working_ttl is not None:
                    await conn.execute(
                        """DELETE FROM memories
                           WHERE org_id = $1
                             AND meta->>'tier' = 'working'
                             AND created_at < now() - make_interval(secs => $2)""",
                        policy["org_id"], working_ttl,
                    )

                # Enforce short tier retention
                short_ttl = retention.get("short")
                if short_ttl is not None:
                    await conn.execute(
                        """DELETE FROM memories
                           WHERE org_id = $1
                             AND meta->>'tier' = 'short'
                             AND created_at < now() - make_interval(secs => $2)""",
                        policy["org_id"], short_ttl,
                    )

                # Prune excess snapshots
                max_snaps = policy["max_snapshots"] or 50
                excess = await conn.fetch(
                    """SELECT id, path FROM snapshot_metadata
                       WHERE policy_id = $1
                       ORDER BY created_at DESC
                       OFFSET $2""",
                    policy["id"], max_snaps,
                )
                for snap in excess:
                    await conn.execute(
                        "DELETE FROM snapshot_metadata WHERE id = $1",
                        snap["id"],
                    )

            except Exception:
                logger.warning(
                    "Failed to enforce policy %s", policy["id"], exc_info=True,
                )
