"""Stats, clusters, and timeline graph endpoints.

Refactored in Phase 1B to delegate to services. Validation of group_by/bucket
happens in the service; route surfaces ValueError as 400.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from lore.persistence import Store
from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_store
from lore.services.graph.graph import get_clusters, get_stats, get_timeline

from .models import (
    ClusterItem,
    ClusterResponse,
    GraphNode,
    StatsResponse,
    TimelineBucket,
    TimelineResponse,
)

router = APIRouter()


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


@router.get("/stats", response_model=StatsResponse)
async def get_stats_route(
    project: Optional[str] = Query(None),
    store: Store = Depends(get_store),
    auth: AuthContext = Depends(get_auth_context),
) -> StatsResponse:
    s = await get_stats(store, project=project, org_id=auth.org_id)
    return StatsResponse(
        total_memories=s.total_memories,
        total_entities=s.total_entities,
        total_relationships=s.total_relationships,
        by_type=dict(s.by_type),
        by_project=dict(s.by_project),
        by_tier={},
        by_entity_type=dict(s.by_entity_type),
        top_entities=list(s.top_entities),
        recent_24h=s.recent_24h,
        recent_7d=s.recent_7d,
        oldest_memory=s.oldest_memory.isoformat() if s.oldest_memory else None,
        newest_memory=s.newest_memory.isoformat() if s.newest_memory else None,
    )


@router.get("/graph/clusters", response_model=ClusterResponse)
async def get_clusters_route(
    group_by: str = Query("project"),
    project: Optional[str] = Query(None),
    store: Store = Depends(get_store),
    auth: AuthContext = Depends(get_auth_context),
) -> ClusterResponse:
    try:
        result = await get_clusters(
            store, group_by=group_by, project=project, org_id=auth.org_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ClusterResponse(
        clusters=[
            ClusterItem(
                id=c.id,
                label=c.label,
                group_by=c.group_by,
                node_count=c.node_count,
                node_ids=list(c.node_ids),
            )
            for c in result.clusters
        ],
        nodes=[_node_to_pydantic(n) for n in result.nodes],
        edges=[],
    )


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline_route(
    bucket: str = Query("day"),
    project: Optional[str] = Query(None),
    store: Store = Depends(get_store),
    auth: AuthContext = Depends(get_auth_context),
) -> TimelineResponse:
    try:
        result = await get_timeline(
            store, bucket=bucket, project=project, org_id=auth.org_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TimelineResponse(
        buckets=[
            TimelineBucket(date=b.date, count=b.count, by_type=dict(b.by_type))
            for b in result.buckets
        ],
        range={
            "start": result.range_start,
            "end": result.range_end,
        },
    )
