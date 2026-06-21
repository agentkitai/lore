"""Temporal endpoints — Phase 6F memory supersession + at-time queries.

Routes mounted under ``/v1/memories``:

  * ``POST /v1/memories/{id}/supersede`` — record a supersession event.
  * ``GET  /v1/memories/at_time``       — list memories valid at a point in time.
  * ``GET  /v1/memories/{id}/supersession-chain`` — full audit trail.
  * ``GET  /v1/memories/{id}/provenance`` — full lineage (sources + chain).
  * ``POST /v1/memories/consolidate``     — atomic merge: create a new
    memory and supersede each source so provenance is preserved by
    construction rather than by the caller remembering two separate
    write paths.

Routes call into ``services/temporal.py`` and ``services/memories.py``;
no raw SQL lives here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
    from pydantic import BaseModel, Field
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.server.models import MemoryResponse
from lore.services import memories as memories_svc
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


class ConsolidateRequest(BaseModel):
    source_ids: List[str] = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    type: str = "lesson"
    context: Optional[str] = None
    tags: List[str] = []
    reason: Optional[str] = None
    project: Optional[str] = None
    scope: Optional[str] = None


class ConsolidateResponse(BaseModel):
    id: str
    superseded_count: int


class ProvenanceResponse(BaseModel):
    memory_id: str
    sources: List[SupersessionEvent]
    chain: List[SupersessionEvent]
    # Pre-Phase-6F consolidations stored their parent IDs in meta.consolidated_from
    # rather than as supersession rows. Surfacing both means a caller asking
    # "where did this come from?" gets a complete answer regardless of whether
    # the merge happened via the dream subagent (typed) or the classic engine
    # (untyped JSON).
    metadata_sources: List[str] = []


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
        requesting_user_id=auth.principal_id,
    )
    return AtTimeResponse(
        at=at,
        memories=[
            MemoryResponse(
                id=m.id,
                content=m.content,
                context=m.context,
                tags=list(m.tags),
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


def _to_supersession_event(e) -> SupersessionEvent:
    return SupersessionEvent(
        id=e.id,
        memory_id=e.memory_id,
        superseded_by=e.superseded_by,
        reason=e.reason,
        ts=e.ts,
        agent=e.agent,
    )


@router.get("/{memory_id}/provenance", response_model=ProvenanceResponse)
async def get_provenance(
    memory_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store=Depends(get_store),
) -> ProvenanceResponse:
    """Full lineage for a memory: sources superseded by it + its own chain.

    ``sources`` answers "where did this memory come from?" — every event
    where this id appears as ``superseded_by``. ``chain`` answers "what
    happened to this memory?" — its own ``memory_supersessions`` rows.
    """
    target = await store.get_memory(auth.org_id, memory_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    sources = await temporal_svc.supersession_sources(store, memory_id)
    chain = await temporal_svc.supersession_chain(store, memory_id)
    # Fallback for pre-Phase-6F consolidations.
    raw_meta_sources = (target.meta or {}).get("consolidated_from") or []
    meta_sources = [str(x) for x in raw_meta_sources if x]
    return ProvenanceResponse(
        memory_id=memory_id,
        sources=[_to_supersession_event(e) for e in sources],
        chain=[_to_supersession_event(e) for e in chain],
        metadata_sources=meta_sources,
    )


@router.post("/consolidate", response_model=ConsolidateResponse, status_code=201)
async def consolidate(
    body: ConsolidateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store=Depends(get_store),
) -> ConsolidateResponse:
    """Create a new memory from N sources and supersede each source atomically.

    Provenance guarantee: every ``source_ids`` entry has a row in
    ``memory_supersessions`` pointing at the new memory before this
    endpoint returns. Callers (dream subagent, classic engine, manual
    promotion) get the same audit trail without remembering to issue a
    second supersede call.
    """
    # Validate every source belongs to the caller's org. We refuse the
    # whole operation rather than silently skipping unknown ids — half-
    # consolidating is worse than failing loudly.
    seen: set[str] = set()
    deduped: list[str] = []
    for sid in body.source_ids:
        if sid in seen:
            continue
        seen.add(sid)
        deduped.append(sid)
    for sid in deduped:
        if await store.get_memory(auth.org_id, sid) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Source memory not found: {sid}",
            )

    # Embed the new memory's content. Lazy-import the embedder so test
    # environments without ONNX can monkeypatch the route to skip it.
    from lore.server.routes.retrieve import _get_embedder

    embedder = _get_embedder()
    embedding = await asyncio.to_thread(embedder.embed, body.content)

    meta = {"type": body.type, "consolidated_from": list(deduped)}
    if body.reason:
        meta["consolidation_reason"] = body.reason

    stored = await memories_svc.create_memory(
        store,
        org_id=auth.org_id,
        content=body.content,
        embedding=embedding,
        context=body.context,
        tags=body.tags or [],
        source="consolidation",
        project=auth.project or body.project,
        meta=meta,
        scope=body.scope,
    )

    superseded_count = await temporal_svc.consolidate_memories(
        store,
        org_id=auth.org_id,
        source_ids=deduped,
        new_memory_id=stored.id,
        reason=body.reason or "consolidated",
        agent="api",
    )

    return ConsolidateResponse(id=stored.id, superseded_count=superseded_count)
