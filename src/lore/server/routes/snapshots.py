"""Session snapshot endpoint for Lore Cloud Server (E3)."""

from __future__ import annotations

import logging
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends
    from pydantic import BaseModel, Field
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence import Store
from lore.server.auth import AuthContext, require_role
from lore.server.db import get_store
from lore.services import snapshots as snapshots_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/snapshots", tags=["snapshots"])


# ── Models ────────────────────────────────────────────────────────


class SnapshotCreateRequest(BaseModel):
    content: str = Field(..., min_length=1)
    title: Optional[str] = None
    session_id: Optional[str] = None
    tags: Optional[List[str]] = None
    project: Optional[str] = None


class SnapshotCreateResponse(BaseModel):
    id: str
    session_id: str
    title: str
    extraction_method: str
    created_at: str


# ── Create ────────────────────────────────────────────────────────


@router.post("", response_model=SnapshotCreateResponse, status_code=201)
async def create_snapshot(
    body: SnapshotCreateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store: Store = Depends(get_store),
) -> SnapshotCreateResponse:
    """Create a session snapshot."""
    project = auth.project if auth.project is not None else body.project
    stored = await snapshots_service.create_snapshot(
        store,
        org_id=auth.org_id,
        content=body.content,
        title=body.title,
        session_id=body.session_id,
        tags=body.tags,
        project=project,
    )
    return SnapshotCreateResponse(
        id=stored.id,
        session_id=stored.meta.get("session_id", ""),
        title=stored.meta.get("title", ""),
        extraction_method=stored.meta.get("extraction_method", "raw"),
        created_at=stored.created_at.isoformat() if stored.created_at else "",
    )
