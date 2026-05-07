"""Phase 6G — GET /v1/timeline: chronologically adjacent events around an anchor.

Middle drill-down layer: between ``search()`` (~30 tok/hit) and
``get_memories()`` (full payload). Returns ~60 tokens per entry —
enough to establish causality without paying for full content.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence.protocol import Store
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.server.models import TimelineEntry, TimelineResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/timeline", tags=["timeline"])


def _first_sentence(s: str | None, *, cap: int = 200) -> str:
    """Return the first sentence of ``s`` (up to ``cap`` chars).

    Splits on ``.``, ``!``, or ``?`` followed by whitespace. Used to
    derive a 1-line narrative summary for the timeline entry shape.
    """
    if not s:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", s.strip(), maxsplit=1)
    head = parts[0] if parts else s
    return head[:cap]


@router.get("", response_model=TimelineResponse)
async def get_timeline(
    anchor_id: str = Query(...),
    limit: int = Query(10, ge=1, le=50),
    direction: Literal["before", "after", "both"] = Query("both"),
    max_hours: float = Query(2.0, gt=0.0, le=72.0),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> TimelineResponse:
    """Return chronologically-adjacent events around an anchor memory.

    Same-project, ±``max_hours`` window, ordered by ``created_at`` ASC.
    """
    anchor, adjacent = await store.list_timeline_around(
        anchor_id=anchor_id,
        org_id=auth.org_id,
        direction=direction,
        limit=limit,
        max_hours=max_hours,
    )
    if anchor is None:
        raise HTTPException(404, detail="anchor_id not found")
    if auth.project and anchor.project != auth.project:
        raise HTTPException(403, detail="anchor not in scoped project")

    anchor_session = (anchor.meta or {}).get("session_id")
    entries: list[TimelineEntry] = []
    for m in adjacent:
        meta = dict(m.meta) if m.meta else {}
        title = meta.get("title") or (m.context or "") or (m.content or "")[:80]
        narrative = meta.get("narrative") or m.content or ""
        entries.append(
            TimelineEntry(
                id=m.id,
                created_at=(
                    m.created_at.isoformat()
                    if hasattr(m.created_at, "isoformat")
                    else str(m.created_at)
                ),
                type=meta.get("type") or "memory",
                title=str(title)[:200],
                narrative_1l=_first_sentence(narrative),
                same_session=(
                    meta.get("session_id") == anchor_session
                    and anchor_session is not None
                ),
            )
        )
    return TimelineResponse(entries=entries, count=len(entries))
