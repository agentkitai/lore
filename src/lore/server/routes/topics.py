"""Topic Notes endpoints for Lore Cloud Server (E4)."""

from __future__ import annotations

import logging
from typing import Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence import Store
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services import topics_dashboard as topics_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/topics", tags=["topics"])


@router.get("")
async def list_topics(
    entity_type: Optional[str] = Query(None),
    min_mentions: int = Query(3, ge=1, le=100),
    limit: int = Query(50, ge=1, le=200),
    project: Optional[str] = Query(None),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> dict:
    """List auto-detected topics (entities with mention_count >= threshold)."""
    return await topics_service.list_topics(
        store,
        entity_type=entity_type,
        min_mentions=min_mentions,
        limit=limit,
    )


@router.get("/{name}")
async def get_topic_detail(
    name: str,
    max_memories: int = Query(20, ge=1, le=100),
    format: str = Query("brief"),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> dict:
    """Get comprehensive detail for a single topic."""
    result = await topics_service.get_topic_detail(
        store,
        name=name,
        max_memories=max_memories,
        format=format,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Topic '{name}' not found")
    return result
