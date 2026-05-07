"""Temporal endpoints — Phase 6F memory supersession + at-time queries.

Three routes, all under ``/v1/memories``:

  * ``POST /v1/memories/{id}/supersede`` — record a supersession event.
  * ``GET  /v1/memories/at_time``       — list memories valid at a point in time.
  * ``GET  /v1/memories/{id}/supersession-chain`` — full audit trail.

Routes call into ``services/temporal.py``; no raw SQL lives here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.server.models import MemoryResponse
from lore.services import temporal as temporal_svc

logger = logging.getLogger(__name__)

# Mounted under /v1/memories so it sits next to the existing memories
# router. Both routers share the same prefix; FastAPI dispatches by the
# route path itself, so there's no collision.
router = APIRouter(prefix="/v1/memories", tags=["temporal"])


# ── Models ─────────────────────────────────────────────────────────


class SupersedeRequest(BaseModel):
    by: Optional[str] = None
    reason: Optional[str] = None


class SupersedeResponse(BaseModel):
    id: str
    superseded_by: Optional[str]
    reason: Optional[str]


class SupersessionEvent(BaseModel):
    id: int
    memory_id: str
    superseded_by: Optional[str]
    reason: Optional[str]
    ts: datetime
    agent: str


class SupersessionChainResponse(BaseModel):
    memory_id: str
    events: List[SupersessionEvent]


class AtTimeResponse(BaseModel):
    at: datetime
    memories: List[MemoryResponse]
    total: int


# ── Routes ─────────────────────────────────────────────────────────


@router.post("/{memory_id}/supersede", response_model=SupersedeResponse)
async def supersede_memory(
    memory_id: str,
    body: SupersedeRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store=Depends(get_store),
) -> SupersedeResponse:
    """Record a supersession event. ``by=None`` un-supersedes."""

    # Enforce that the memory exists in the caller's org. ``by`` is
    # optional; when present we also verify it.
    target = await store.get_memory(auth.org_id, memory_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    if body.by is not None:
        new = await store.get_memory(auth.org_id, body.by)
        if new is None:
            raise HTTPException(status_code=404, detail="Replacement memory not found")

    await temporal_svc.supersede_memory(
        store,
        memory_id,
        superseded_by=body.by,
        reason=body.reason,
        agent="api",
    )
    return SupersedeResponse(id=memory_id, superseded_by=body.by, reason=body.reason)


@router.get("/at_time", response_model=AtTimeResponse)
async def list_at_time(
    at: datetime = Query(..., description="ISO-8601 timestamp"),
    entity: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    store=Depends(get_store),
) -> AtTimeResponse:
    """List memories that existed and were not superseded at ``at``."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    rows = await temporal_svc.memories_at_time(
        store,
        auth.org_id,
        at=at,
        entity_name=entity,
        type_filter=type,
        limit=limit,
    )
    return AtTimeResponse(
        at=at,
        memories=[
            MemoryResponse(
                id=m.id,
                content=m.content,
                context=m.context,
                tags=list(m.tags),
                confidence=m.confidence,
                source=m.source,
                project=m.project,
                created_at=m.created_at,
                updated_at=m.updated_at,
                expires_at=m.expires_at,
                upvotes=m.upvotes,
                downvotes=m.downvotes,
                meta=dict(m.meta),
            )
            for m in rows
        ],
        total=len(rows),
    )


@router.get("/{memory_id}/supersession-chain", response_model=SupersessionChainResponse)
async def get_supersession_chain(
    memory_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store=Depends(get_store),
) -> SupersessionChainResponse:
    """Full audit trail for a memory, oldest first."""
    target = await store.get_memory(auth.org_id, memory_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    events = await temporal_svc.supersession_chain(store, memory_id)
    return SupersessionChainResponse(
        memory_id=memory_id,
        events=[
            SupersessionEvent(
                id=e.id,
                memory_id=e.memory_id,
                superseded_by=e.superseded_by,
                reason=e.reason,
                ts=e.ts,
                agent=e.agent,
            )
            for e in events
        ],
    )
