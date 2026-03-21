"""Async audit writer helper."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def write_audit_log(
    *,
    org_id: str,
    actor_id: str,
    actor_type: str,
    action: str,
    workspace_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Insert an audit log entry (fire-and-forget safe)."""
    import json

    try:
        from lore.server.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO audit_log
                   (org_id, workspace_id, actor_id, actor_type, action,
                    resource_type, resource_id, metadata, ip_address)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::inet)""",
                org_id, workspace_id, actor_id, actor_type, action,
                resource_type, resource_id,
                json.dumps(metadata or {}),
                ip_address,
            )
    except Exception:
        logger.warning("Failed to write audit log", exc_info=True)


def fire_audit_log(**kwargs) -> None:
    """Schedule an audit log write as a fire-and-forget task."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(write_audit_log(**kwargs))
    except RuntimeError:
        pass
