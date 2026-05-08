"""Graph endpoints — backfill entity / mention / relationship rows.

The create-time fire-and-forget hook in ``routes/memories.py`` and
``routes/observations.py`` covers new memories. This route covers the
historical case: memories already in the DB without ``entity_mentions``,
typically because they pre-date PR B's wiring or were created while
``LORE_GRAPH_EXTRACTION_ENABLED=false``.

Behavior:

  * Default: walk memories with no rows in ``entity_mentions``, run
    extraction on each, return per-result counts.
  * ``force=true``: re-run extraction on every memory (up to ``limit``)
    regardless of whether it already has mentions. Useful after a model
    upgrade or prompt revision.
  * Synchronous request shape with a small ``limit`` cap so a single
    HTTP call always finishes; large backfills repeat the request.

Intentionally *not* implemented as a background job — that would need
queueing infra we don't have. Repeated calls with limit=N walk the
remaining rows; the LEFT JOIN on entity_mentions naturally skips
already-processed memories on the next call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

try:
    from fastapi import APIRouter, Depends
    from pydantic import BaseModel, Field
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence.protocol import Store
from lore.server.auth import AuthContext, require_role
from lore.server.db import get_store
from lore.services import graph_extraction as graph_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/graph", tags=["graph"])

# Backfill cap: a single HTTP call processes at most this many memories.
# Larger backfills are run by repeating the request — the LEFT JOIN
# naturally skips already-processed rows on subsequent calls. Cap is
# generous enough that ``lore graph-backfill`` finishes on a typical
# session-buffer-sized run in one or two HTTP round trips, but small
# enough that no individual request runs unbounded under
# extraction-concurrency=2.
_MAX_BACKFILL_LIMIT = 100


class BackfillRequest(BaseModel):
    limit: int = Field(50, ge=1, le=_MAX_BACKFILL_LIMIT)
    force: bool = False
    project: Optional[str] = None


class BackfillResultItem(BaseModel):
    memory_id: str
    entities_inserted: int
    entities_reused: int
    mentions_inserted: int
    relationships_inserted: int
    error: Optional[str] = None


class BackfillResponse(BaseModel):
    processed: int
    failed: int
    results: list[BackfillResultItem]
    enabled: bool


@router.post("/backfill", response_model=BackfillResponse)
async def backfill_graph(
    body: BackfillRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store: Store = Depends(get_store),
) -> BackfillResponse:
    """Run graph extraction on memories that don't yet have mentions.

    Pass ``force=true`` to re-extract memories that already have mentions
    (use after a prompt / model change).
    """
    if not graph_svc.is_enabled():
        return BackfillResponse(
            processed=0, failed=0, results=[], enabled=False,
        )

    if body.force:
        # Re-extract regardless of existing mentions. Walks all memories
        # for the org/project, capped at ``limit``. We use the same
        # "without mentions" path with a wider net by clearing the
        # filter — but the store doesn't expose that, so for v1 we
        # simply pull from list_memories_without_mentions when force
        # is false, and from a generic list when force is true. The
        # store layer's existing ``list`` shape is good enough.
        memories = await _list_for_force(
            store, org_id=auth.org_id, project=body.project, limit=body.limit,
        )
    else:
        memories = await store.list_memories_without_mentions(
            auth.org_id, project=body.project, limit=body.limit,
        )

    if not memories:
        return BackfillResponse(processed=0, failed=0, results=[], enabled=True)

    # Run extraction concurrently. The service-level semaphore caps
    # actual subprocess fan-out so we can fire all of them at once
    # without flooding the host.
    async def run_one(mem):
        return await graph_svc.extract_and_persist(
            store, org_id=auth.org_id, memory_id=mem.id,
            content=mem.content, context=mem.context,
        )

    results = await asyncio.gather(
        *(run_one(m) for m in memories),
        return_exceptions=True,
    )

    items: list[BackfillResultItem] = []
    failed = 0
    processed = 0
    for mem, r in zip(memories, results):
        if isinstance(r, BaseException):
            logger.warning("graph backfill task crashed for %s: %r", mem.id, r)
            failed += 1
            items.append(BackfillResultItem(
                memory_id=mem.id,
                entities_inserted=0, entities_reused=0,
                mentions_inserted=0, relationships_inserted=0,
                error=f"task crashed: {r!r}",
            ))
            continue
        if r.error:
            failed += 1
        else:
            processed += 1
        items.append(BackfillResultItem(
            memory_id=r.memory_id,
            entities_inserted=r.entities_inserted,
            entities_reused=r.entities_reused,
            mentions_inserted=r.mentions_inserted,
            relationships_inserted=r.relationships_inserted,
            error=r.error,
        ))

    return BackfillResponse(
        processed=processed, failed=failed,
        results=items, enabled=True,
    )


async def _list_for_force(
    store: Store, *, org_id: str, project: Optional[str], limit: int,
):
    """Pull memories regardless of existing mentions (force=true path).

    The persistence layer doesn't have a direct "list everything" hook
    that respects org_id; ``list_memories`` on the service layer does.
    Using it here keeps PR B from adding yet another store method.
    """
    from lore.services import memories as mem_svc

    return await mem_svc.list_memories(
        store, org_id=org_id, project=project, limit=limit,
    )
