"""Memory-related graph endpoints: get_graph, search_memories, get_memory_detail.

Refactored in Phase 1B to call services exclusively. No SQL or get_pool() here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from lore.persistence import Store
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services.graph.graph import (
    get_graph_data,
    get_memory_with_graph,
    search_graph_memories,
)

from .models import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    GraphStats,
    MemoryDetailResponse,
    SearchResponse,
    SearchResult,
)

router = APIRouter()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _node_to_pydantic(n) -> GraphNode:
    return GraphNode(
        id=n.id,
        kind=n.kind,
        label=n.label,
        type=n.type,
        tier=n.tier,
        project=n.project,
        tags=list(n.tags) if n.tags else None,
        created_at=n.created_at.isoformat() if n.created_at else None,
        upvotes=n.upvotes,
        downvotes=n.downvotes,
        access_count=n.access_count,
        mention_count=n.mention_count,
        aliases=list(n.aliases) if n.aliases else None,
        first_seen_at=n.first_seen_at.isoformat() if n.first_seen_at else None,
        last_seen_at=n.last_seen_at.isoformat() if n.last_seen_at else None,
    )


def _edge_to_pydantic(e) -> GraphEdge:
    return GraphEdge(
        source=e.source,
        target=e.target,
        rel_type=e.rel_type,
        weight=e.weight,
        label=e.label,
    )


@router.get("/graph", response_model=GraphResponse)
async def get_graph(
    project: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
    include_orphans: bool = Query(True),
    store: Store = Depends(get_store),
    auth: AuthContext = Depends(get_auth_context),
) -> GraphResponse:
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)
    data = await get_graph_data(
        store,
        project=project,
        type=type,
        tier=tier,
        since=since_dt,
        until=until_dt,
        limit=limit,
        include_orphans=include_orphans,
        org_id=auth.org_id,
    )
    return GraphResponse(
        nodes=[_node_to_pydantic(n) for n in data.nodes],
        edges=[_edge_to_pydantic(e) for e in data.edges],
        stats=GraphStats(
            total_memories=data.counts.total_memories,
            total_entities=data.counts.total_entities,
            total_relationships=data.counts.total_relationships,
            filtered_nodes=data.counts.filtered_nodes,
            filtered_edges=data.counts.filtered_edges,
        ),
    )


@router.post("/search", response_model=SearchResponse)
async def search_memories(
    request: dict,
    store: Store = Depends(get_store),
    auth: AuthContext = Depends(get_auth_context),
) -> SearchResponse:
    query = request.get("query", "")
    limit = request.get("limit", 20)
    res = await search_graph_memories(store, query, limit=limit, org_id=auth.org_id)
    return SearchResponse(
        results=[
            SearchResult(
                id=h.id,
                content=h.content,
                type=h.type,
                project=h.project,
                created_at=h.created_at.isoformat() if h.created_at else "",
            )
            for h in res.results
        ],
        total=res.total,
    )


@router.get("/memory/{memory_id}", response_model=MemoryDetailResponse)
async def get_memory_detail(
    memory_id: str,
    store: Store = Depends(get_store),
    auth: AuthContext = Depends(get_auth_context),
) -> MemoryDetailResponse:
    detail = await get_memory_with_graph(store, memory_id, org_id=auth.org_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    m = detail.memory
    meta = m.meta or {}
    return MemoryDetailResponse(
        id=m.id,
        content=m.content or "",
        type=meta.get("type", "general"),
        tier=meta.get("tier", "long"),
        project=m.project,
        tags=list(m.tags),
        upvotes=m.upvotes,
        downvotes=m.downvotes,
        access_count=m.access_count,
        created_at=m.created_at.isoformat() if m.created_at else "",
        updated_at=m.updated_at.isoformat() if m.updated_at else "",
        source=m.source,
        connected_entities=[
            {
                "id": ce.id, "name": ce.name, "type": ce.entity_type,
                "rel_type": ce.rel_type,
            }
            for ce in detail.connected_entities
        ],
        connected_memories=[
            {
                "id": cm.id, "label": cm.label, "type": cm.type,
                "rel_type": cm.rel_type,
            }
            for cm in detail.connected_memories
        ],
    )
