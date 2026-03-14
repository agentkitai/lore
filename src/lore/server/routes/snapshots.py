"""Session snapshot endpoint for Lore Cloud Server (E3)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, Field
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

try:
    from ulid import ULID
except ImportError:
    raise ImportError("python-ulid is required. Install with: pip install python-ulid")

from lore.server.auth import AuthContext, require_role
from lore.server.db import get_pool

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
) -> SnapshotCreateResponse:
    """Create a session snapshot."""
    session_id = body.session_id or uuid.uuid4().hex[:12]
    title = body.title or body.content[:80].strip()
    project = body.project
    if auth.project is not None:
        project = auth.project

    now = datetime.now(timezone.utc)
    memory_id = str(ULID())

    all_tags = ["session_snapshot", session_id] + (body.tags or [])
    metadata = {
        "session_id": session_id,
        "title": title,
        "extraction_method": "raw",
    }

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO memories
               (id, org_id, content, tags, confidence,
                project, created_at, updated_at,
                upvotes, downvotes, meta, importance_score, tier, type)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6,
                       $7, $8, $9, $10, $11::jsonb, $12, $13, $14)""",
            memory_id,
            auth.org_id,
            body.content,
            json.dumps(all_tags),
            1.0,
            project,
            now,
            now,
            0,
            0,
            json.dumps(metadata),
            0.95,
            "long",
            "session_snapshot",
        )

    return SnapshotCreateResponse(
        id=memory_id,
        session_id=session_id,
        title=title,
        extraction_method="raw",
        created_at=now.isoformat(),
    )
