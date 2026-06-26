"""Entity-related graph endpoints. Refactored in Phase 1B to delegate to services."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lore.persistence import Store
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services.graph.entities import get_entity_with_connections

from .models import EntityDetailResponse

router = APIRouter()


@router.get("/entity/{entity_id}", response_model=EntityDetailResponse)
async def get_entity_detail(
    entity_id: str,
    store: Store = Depends(get_store),
    auth: AuthContext = Depends(get_auth_context),
) -> EntityDetailResponse:
    detail = await get_entity_with_connections(store, entity_id, org_id=auth.org_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    e = detail.entity
    return EntityDetailResponse(
        id=e.id,
        name=e.name,
        entity_type=e.entity_type,
        mention_count=e.mention_count,
        aliases=list(e.aliases) if e.aliases else [],
        first_seen_at=e.first_seen_at.isoformat() if e.first_seen_at else None,
        last_seen_at=e.last_seen_at.isoformat() if e.last_seen_at else None,
        connected_memories=[
            {
                "id": cm.id,
                "label": cm.label,
                "type": cm.type,
                "created_at": cm.created_at.isoformat() if cm.created_at else None,
            }
            for cm in detail.connected_memories
        ],
        connected_entities=[
            {
                "id": ce.id,
                "name": ce.name,
                "type": ce.entity_type,
                "rel_type": ce.rel_type,
                "weight": ce.weight,
            }
            for ce in detail.connected_entities
        ],
    )
