"""Recent-activity service — wraps MemoryOps.list_memories with time-window logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from lore.persistence import MemoryFilter, Store, StoredMemory


async def get_recent_activity(
    store: Store,
    *,
    org_id: str,
    project: Optional[str],
    hours: int,
    max_memories: int = 50,
    requesting_user_id: Optional[str] = None,
) -> Sequence[StoredMemory]:
    """Fetch memories created within the last `hours` for the org.

    Caller does any project grouping and response shaping.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    filter = MemoryFilter(
        org_id=org_id, project=project, since=since,
        requesting_user_id=requesting_user_id,
    )
    memories = await store.list_memories(filter)
    # MemoryOps.list_memories has its own ordering; the route's pre-1I behavior
    # was ORDER BY created_at DESC LIMIT N. The list_memories impl already orders
    # by created_at DESC; just slice to max_memories.
    return list(memories)[:max_memories]
