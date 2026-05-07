"""Observation endpoints — Phase 6B.

Thin REST layer over ``services.observations.create_observation`` plus
read-only ``list``/``show`` helpers that filter the existing memories
table by ``meta.type='observation'``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence import NewObservation, StoredMemory
from lore.persistence.protocol import Store
from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.server.models import (
    ObservationCreateRequest,
    ObservationCreateResponse,
    ObservationListResponse,
    ObservationResponse,
)
from lore.services.memories import get_memory as _get_memory
from lore.services.memories import list_memories as _list_memories
from lore.services.observations import create_observation as _create_observation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/observations", tags=["observations"])


def _stored_to_observation_response(m: StoredMemory) -> ObservationResponse:
    """Translate a ``StoredMemory`` into the public observation shape."""
    meta = dict(m.meta) if m.meta else {}
    title = meta.get("title") or (m.context or "")
    facts_raw = meta.get("facts") or []
    if not isinstance(facts_raw, list):
        facts_raw = []
    return ObservationResponse(
        id=m.id,
        title=title,
        facts=[str(f) for f in facts_raw],
        narrative=meta.get("narrative") or m.content,
        tags=list(m.tags),
        project=m.project,
        source=m.source,
        captured_by=str(meta.get("captured_by") or "auto"),
        session_id=meta.get("session_id"),
        created_at=m.created_at,
        updated_at=m.updated_at,
        meta=meta,
    )


# ── Create ────────────────────────────────────────────────────────


@router.post("", response_model=ObservationCreateResponse, status_code=201)
async def create_observation(
    body: ObservationCreateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store: Store = Depends(get_store),
) -> ObservationCreateResponse:
    """Persist a structured observation."""
    # Embedding: title + narrative gives recall a slightly better surface
    # than narrative alone. Computed on a worker thread to avoid blocking
    # the event loop while the local ONNX model runs.
    from lore.server.routes.retrieve import _get_embedder

    embedder = _get_embedder()

    async def _embed(text: str):
        return await asyncio.to_thread(embedder.embed, text)

    obs = NewObservation(
        org_id=auth.org_id,
        title=body.title,
        facts=tuple(body.facts),
        narrative=body.narrative,
        tags=tuple(body.tags),
        project=auth.project or body.project,
        source=body.source,
        captured_by=body.captured_by,
        session_id=body.session_id,
    )
    stored = await _create_observation(store, obs, _embed)
    return ObservationCreateResponse(id=stored.id)


# ── List ──────────────────────────────────────────────────────────


@router.get("", response_model=ObservationListResponse)
async def list_observations(
    project: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> ObservationListResponse:
    """List observations (memories where ``meta.type='observation'``)."""
    rows = await _list_memories(
        store,
        org_id=auth.org_id,
        project=auth.project or project,
        type="observation",
        limit=limit,
        offset=offset,
    )
    return ObservationListResponse(
        observations=[_stored_to_observation_response(m) for m in rows],
        total=len(rows),
        limit=limit,
        offset=offset,
    )


# ── Show ──────────────────────────────────────────────────────────


@router.get("/{observation_id}", response_model=ObservationResponse)
async def get_observation(
    observation_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> ObservationResponse:
    """Fetch a single observation by ID."""
    m = await _get_memory(store, auth.org_id, observation_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Observation not found")
    meta = dict(m.meta) if m.meta else {}
    if meta.get("type") != "observation":
        raise HTTPException(status_code=404, detail="Observation not found")
    return _stored_to_observation_response(m)
