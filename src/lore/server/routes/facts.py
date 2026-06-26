"""Fact (bi-temporal relationship) endpoints — #67.

Lore stores "facts" (subject–predicate–object assertions) as graph
relationships, which already carry a validity window (valid_from / valid_until).
These routes expose the governance/audit capability over that substrate:

  * ``GET  /v1/facts/at_time``                       — facts valid at a point in time.
  * ``POST /v1/facts/{relationship_id}/supersede``   — supersede-not-delete a fact.
  * ``GET  /v1/facts/{relationship_id}/supersession-chain`` — correction trail.

Routes call into ``services/temporal.py``; no raw SQL lives here.

Scope note: the knowledge graph (entities / relationships) is global per
migration 007 — it is not partitioned by org or per-user visibility the way
memories are. These endpoints inherit that, matching the existing graph routes
(``/v1/ui/...``). They still require an authenticated principal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Literal, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
    from pydantic import BaseModel, Field
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.services import temporal as temporal_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/facts", tags=["facts"])


# ── Models ─────────────────────────────────────────────────────────


class FactResponse(BaseModel):
    relationship_id: str
    subject: str
    predicate: str
    object: str
    valid_from: Optional[datetime]
    valid_until: Optional[datetime]
    superseded_by: Optional[str]
    weight: float
    source_memory_id: Optional[str]


class FactsAtTimeResponse(BaseModel):
    at: datetime
    facts: List[FactResponse]
    total: int


class SupersedeFactRequest(BaseModel):
    by: str = Field(..., min_length=1, description="ID of the newer fact (relationship) that replaces this one")
    reason: Optional[str] = None


class SupersedeFactResponse(BaseModel):
    relationship_id: str
    superseded_by: str
    reason: Optional[str]


class FactSupersessionEvent(BaseModel):
    id: int
    relationship_id: str
    superseded_by: Optional[str]
    reason: Optional[str]
    ts: datetime
    agent: str


class FactSupersessionChainResponse(BaseModel):
    relationship_id: str
    events: List[FactSupersessionEvent]


# ── Routes ─────────────────────────────────────────────────────────


@router.get("/at_time", response_model=FactsAtTimeResponse)
async def facts_at_time(
    at: datetime = Query(..., description="ISO-8601 timestamp"),
    entity: str = Query(..., description="Entity name (subject or object) the facts must involve"),
    predicate: Optional[str] = Query(None, description="Filter by relationship type / predicate"),
    direction: Literal["inbound", "outbound", "both"] = Query("both"),
    limit: int = Query(50, ge=1, le=500),
    auth: AuthContext = Depends(get_auth_context),
    store=Depends(get_store),
) -> FactsAtTimeResponse:
    """Subject–predicate–object facts involving ``entity`` that were valid at ``at``."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    facts = await temporal_svc.facts_at_time(
        store,
        entity=entity,
        at=at,
        org_id=auth.org_id,
        predicate=predicate,
        direction=direction,
        limit=limit,
    )
    return FactsAtTimeResponse(
        at=at,
        facts=[
            FactResponse(
                relationship_id=f.relationship_id,
                subject=f.subject,
                predicate=f.predicate,
                object=f.object,
                valid_from=f.valid_from,
                valid_until=f.valid_until,
                superseded_by=f.superseded_by,
                weight=f.weight,
                source_memory_id=f.source_memory_id,
            )
            for f in facts
        ],
        total=len(facts),
    )


@router.post("/{relationship_id}/supersede", response_model=SupersedeFactResponse)
async def supersede_fact(
    relationship_id: str,
    body: SupersedeFactRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store=Depends(get_store),
) -> SupersedeFactResponse:
    """Supersede-not-delete a fact: expire it, point it at the newer fact
    ``by``, and record the correction in the audit log."""
    if body.by == relationship_id:
        raise HTTPException(status_code=400, detail="A fact cannot supersede itself")
    target = await store.get_relationship(relationship_id, auth.org_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    replacement = await store.get_relationship(body.by, auth.org_id)
    if replacement is None:
        raise HTTPException(status_code=404, detail="Replacement fact not found")
    await temporal_svc.supersede_relationship(
        store,
        relationship_id,
        auth.org_id,
        superseded_by=body.by,
        reason=body.reason,
        agent="api",
    )
    return SupersedeFactResponse(
        relationship_id=relationship_id, superseded_by=body.by, reason=body.reason
    )


@router.get(
    "/{relationship_id}/supersession-chain",
    response_model=FactSupersessionChainResponse,
)
async def fact_supersession_chain(
    relationship_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store=Depends(get_store),
) -> FactSupersessionChainResponse:
    """Full correction trail for a fact, oldest first."""
    target = await store.get_relationship(relationship_id, auth.org_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    events = await temporal_svc.relationship_supersession_chain(store, relationship_id, auth.org_id)
    return FactSupersessionChainResponse(
        relationship_id=relationship_id,
        events=[
            FactSupersessionEvent(
                id=e.id,
                relationship_id=e.relationship_id,
                superseded_by=e.superseded_by,
                reason=e.reason,
                ts=e.ts,
                agent=e.agent,
            )
            for e in events
        ],
    )
