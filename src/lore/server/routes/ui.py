"""UI visualization API routes.

Endpoints for the graph visualization web UI (E1).
These routes are mounted on a standalone UI app, not the cloud server.
They use a Store instance directly (SQLite or memory-based).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from lore.types import Entity, EntityMention, Memory, Relationship

router = APIRouter(prefix="/v1/ui", tags=["ui"])


# ── Pydantic response models ─────────────────────────────────────


class GraphNode(BaseModel):
    id: str
    kind: str  # "memory" or "entity"
    label: str
    type: str
    # Memory-specific
    tier: Optional[str] = None
    project: Optional[str] = None
    importance: Optional[float] = None
    confidence: Optional[float] = None
    tags: Optional[List[str]] = None
    created_at: Optional[str] = None
    upvotes: Optional[int] = None
    downvotes: Optional[int] = None
    access_count: Optional[int] = None
    # Entity-specific
    mention_count: Optional[int] = None
    aliases: Optional[List[str]] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None


class GraphEdge(BaseModel):
    source: str
    target: str
    rel_type: str
    weight: float = 1.0
    label: str = ""


class GraphStats(BaseModel):
    total_memories: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    filtered_nodes: int = 0
    filtered_edges: int = 0


class GraphResponse(BaseModel):
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []
    stats: GraphStats = Field(default_factory=GraphStats)


class MemoryDetailResponse(BaseModel):
    id: str
    content: str
    type: str
    tier: str = "long"
    project: Optional[str] = None
    tags: List[str] = []
    importance_score: float = 1.0
    confidence: float = 1.0
    upvotes: int = 0
    downvotes: int = 0
    access_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    source: Optional[str] = None
    connected_entities: List[Dict[str, Any]] = []
    connected_memories: List[Dict[str, Any]] = []


class EntityDetailResponse(BaseModel):
    id: str
    name: str
    entity_type: str
    aliases: List[str] = []
    description: Optional[str] = None
    mention_count: int = 0
    first_seen_at: str = ""
    last_seen_at: str = ""
    connected_entities: List[Dict[str, Any]] = []
    connected_memories: List[Dict[str, Any]] = []


class SearchRequest(BaseModel):
    query: str
    mode: str = "keyword"
    limit: int = 20
    filters: Optional[Dict[str, Any]] = None


class SearchResult(BaseModel):
    id: str
    kind: str
    label: str
    type: str
    score: float
    importance: Optional[float] = None
    project: Optional[str] = None


class SearchResponse(BaseModel):
    results: List[SearchResult] = []
    total: int = 0
    query_time_ms: float = 0.0


class ClusterItem(BaseModel):
    id: str
    label: str
    group_by: str
    node_count: int
    node_ids: List[str]


class ClusterResponse(BaseModel):
    clusters: List[ClusterItem] = []
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []


class StatsResponse(BaseModel):
    total_memories: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    by_type: Dict[str, int] = {}
    by_project: Dict[str, int] = {}
    by_tier: Dict[str, int] = {}
    by_entity_type: Dict[str, int] = {}
    avg_importance: float = 0.0
    top_entities: List[Dict[str, Any]] = []
    recent_24h: int = 0
    recent_7d: int = 0
    oldest_memory: Optional[str] = None
    newest_memory: Optional[str] = None


class TimelineBucket(BaseModel):
    date: str
    count: int
    by_type: Dict[str, int] = {}


class TimelineResponse(BaseModel):
    buckets: List[TimelineBucket] = []
    range: Dict[str, Optional[str]] = {"start": None, "end": None}


# ── Helpers ───────────────────────────────────────────────────────


def _get_store(request: Request):
    """Get the Store instance from app state."""
    return request.app.state.store


def _memory_to_node(m: Memory) -> GraphNode:
    label = (m.content[:60] + "...") if len(m.content) > 60 else m.content
    # Strip newlines from label
    label = label.replace("\n", " ")
    return GraphNode(
        id=m.id,
        kind="memory",
        label=label,
        type=m.type,
        tier=m.tier,
        project=m.project,
        importance=m.importance_score,
        confidence=m.confidence,
        tags=m.tags,
        created_at=m.created_at,
        upvotes=m.upvotes,
        downvotes=m.downvotes,
        access_count=m.access_count,
    )


def _entity_to_node(e: Entity) -> GraphNode:
    return GraphNode(
        id=e.id,
        kind="entity",
        label=e.name,
        type=e.entity_type,
        mention_count=e.mention_count,
        aliases=e.aliases,
        first_seen_at=e.first_seen_at,
        last_seen_at=e.last_seen_at,
    )


def _filter_memories(
    memories: List[Memory],
    project: Optional[str] = None,
    mem_type: Optional[str] = None,
    tier: Optional[str] = None,
    min_importance: float = 0.0,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 1000,
) -> List[Memory]:
    result = memories
    if project:
        result = [m for m in result if m.project == project]
    if mem_type:
        result = [m for m in result if m.type == mem_type]
    if tier:
        result = [m for m in result if m.tier == tier]
    if min_importance > 0:
        result = [m for m in result if m.importance_score >= min_importance]
    if since:
        result = [m for m in result if m.created_at >= since]
    if until:
        result = [m for m in result if m.created_at <= until]
    # Sort by importance descending, take top N
    result.sort(key=lambda m: m.importance_score, reverse=True)
    return result[:limit]


# ── GET /v1/ui/graph ──────────────────────────────────────────────


@router.get("/graph", response_model=GraphResponse)
async def get_graph(
    request: Request,
    project: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    min_importance: float = Query(0.0, ge=0.0, le=1.0),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
    include_orphans: bool = Query(True),
) -> GraphResponse:
    store = _get_store(request)

    # Fetch all data
    all_memories = store.list(limit=10000, include_archived=False)
    all_entities = store.list_entities(limit=10000)
    all_relationships = store.list_relationships(limit=100000)
    all_mentions = store.list_all_entity_mentions()

    total_memories = len(all_memories)
    total_entities = len(all_entities)
    total_relationships = len(all_relationships)

    # Filter memories
    filtered_memories = _filter_memories(
        all_memories,
        project=project,
        mem_type=type,
        tier=tier,
        min_importance=min_importance,
        since=since,
        until=until,
        limit=limit,
    )

    memory_ids = {m.id for m in filtered_memories}
    entity_ids = {e.id for e in all_entities}

    # Build mention edges (memory <-> entity)
    mention_edges: List[GraphEdge] = []
    for em in all_mentions:
        if em.memory_id in memory_ids and em.entity_id in entity_ids:
            mention_edges.append(GraphEdge(
                source=em.memory_id,
                target=em.entity_id,
                rel_type="mentions",
                weight=em.confidence,
                label="mentions",
            ))

    # Filter orphans if needed
    if not include_orphans:
        connected_memory_ids = {e.source for e in mention_edges} | {e.target for e in mention_edges}
        filtered_memories = [m for m in filtered_memories if m.id in connected_memory_ids]
        memory_ids = {m.id for m in filtered_memories}
        # Rebuild mention edges
        mention_edges = [e for e in mention_edges if e.source in memory_ids]

    # Entity-entity edges (only where both endpoints are in the response)
    entity_edges: List[GraphEdge] = []
    for rel in all_relationships:
        if rel.source_entity_id in entity_ids and rel.target_entity_id in entity_ids:
            entity_edges.append(GraphEdge(
                source=rel.source_entity_id,
                target=rel.target_entity_id,
                rel_type=rel.rel_type,
                weight=rel.weight,
                label=rel.rel_type,
            ))

    all_edges = entity_edges + mention_edges

    # Build nodes
    nodes: List[GraphNode] = []
    for m in filtered_memories:
        nodes.append(_memory_to_node(m))
    for e in all_entities:
        nodes.append(_entity_to_node(e))

    return GraphResponse(
        nodes=nodes,
        edges=all_edges,
        stats=GraphStats(
            total_memories=total_memories,
            total_entities=total_entities,
            total_relationships=total_relationships,
            filtered_nodes=len(nodes),
            filtered_edges=len(all_edges),
        ),
    )


# ── GET /v1/ui/memory/{id} ───────────────────────────────────────


@router.get("/memory/{memory_id}", response_model=MemoryDetailResponse)
async def get_memory_detail(request: Request, memory_id: str) -> MemoryDetailResponse:
    store = _get_store(request)
    memory = store.get(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Get connected entities via mentions
    mentions = store.get_entity_mentions_for_memory(memory_id)
    connected_entities = []
    for em in mentions:
        entity = store.get_entity(em.entity_id)
        if entity:
            connected_entities.append({
                "id": entity.id,
                "name": entity.name,
                "type": entity.entity_type,
                "rel_type": "mentions",
            })

    # Get connected memories via shared entities
    connected_memories: List[Dict[str, Any]] = []
    seen_memory_ids = {memory_id}
    for em in mentions:
        other_mentions = store.get_entity_mentions_for_entity(em.entity_id)
        for om in other_mentions:
            if om.memory_id not in seen_memory_ids:
                seen_memory_ids.add(om.memory_id)
                other_mem = store.get(om.memory_id)
                if other_mem:
                    label = other_mem.content[:60].replace("\n", " ")
                    connected_memories.append({
                        "id": other_mem.id,
                        "label": label,
                        "type": other_mem.type,
                        "rel_type": "related_to",
                    })

    return MemoryDetailResponse(
        id=memory.id,
        content=memory.content,
        type=memory.type,
        tier=memory.tier,
        project=memory.project,
        tags=memory.tags,
        importance_score=memory.importance_score,
        confidence=memory.confidence,
        upvotes=memory.upvotes,
        downvotes=memory.downvotes,
        access_count=memory.access_count,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        source=memory.source,
        connected_entities=connected_entities,
        connected_memories=connected_memories,
    )


# ── GET /v1/ui/entity/{id} ───────────────────────────────────────


@router.get("/entity/{entity_id}", response_model=EntityDetailResponse)
async def get_entity_detail(request: Request, entity_id: str) -> EntityDetailResponse:
    store = _get_store(request)
    entity = store.get_entity(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Connected entities via relationships
    rels = store.list_relationships(entity_id=entity_id, limit=200)
    connected_entities = []
    for rel in rels:
        other_id = rel.target_entity_id if rel.source_entity_id == entity_id else rel.source_entity_id
        other = store.get_entity(other_id)
        if other:
            connected_entities.append({
                "id": other.id,
                "name": other.name,
                "type": other.entity_type,
                "rel_type": rel.rel_type,
                "weight": rel.weight,
            })

    # Connected memories via mentions
    mentions = store.get_entity_mentions_for_entity(entity_id)
    connected_memories = []
    for em in mentions:
        mem = store.get(em.memory_id)
        if mem:
            label = mem.content[:60].replace("\n", " ")
            connected_memories.append({
                "id": mem.id,
                "label": label,
                "type": mem.type,
                "importance": mem.importance_score,
            })

    return EntityDetailResponse(
        id=entity.id,
        name=entity.name,
        entity_type=entity.entity_type,
        aliases=entity.aliases,
        description=entity.description,
        mention_count=entity.mention_count,
        first_seen_at=entity.first_seen_at,
        last_seen_at=entity.last_seen_at,
        connected_entities=connected_entities,
        connected_memories=connected_memories,
    )


# ── POST /v1/ui/search ───────────────────────────────────────────


@router.post("/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    if not body.query or not body.query.strip():
        return SearchResponse(results=[], total=0, query_time_ms=0.0)

    if body.mode not in ("keyword", "semantic"):
        raise HTTPException(status_code=400, detail=f"Unknown search mode: {body.mode}")

    t0 = time.monotonic()
    store = _get_store(request)
    query_lower = body.query.strip().lower()
    filters = body.filters or {}

    results: List[SearchResult] = []

    # Search memories by keyword
    all_memories = store.list(
        project=filters.get("project"),
        type=filters.get("type"),
        tier=filters.get("tier"),
        limit=10000,
    )

    min_imp = filters.get("min_importance", 0.0)
    since = filters.get("since")
    until = filters.get("until")

    for m in all_memories:
        if min_imp and m.importance_score < min_imp:
            continue
        if since and m.created_at < since:
            continue
        if until and m.created_at > until:
            continue

        content_lower = m.content.lower()
        if query_lower in content_lower:
            # Simple relevance: position-based + importance
            pos = content_lower.index(query_lower)
            score = max(0.1, 1.0 - (pos / max(len(content_lower), 1))) * 0.5
            score += m.importance_score * 0.5
            label = m.content[:60].replace("\n", " ")
            results.append(SearchResult(
                id=m.id,
                kind="memory",
                label=label,
                type=m.type,
                score=round(score, 3),
                importance=m.importance_score,
                project=m.project,
            ))

    # Search entities by name/alias
    all_entities = store.list_entities(limit=10000)
    for e in all_entities:
        name_lower = e.name.lower()
        alias_match = any(query_lower in a.lower() for a in e.aliases)
        if query_lower in name_lower or alias_match:
            score = 1.0 if query_lower == name_lower else 0.7
            results.append(SearchResult(
                id=e.id,
                kind="entity",
                label=e.name,
                type=e.entity_type,
                score=round(score, 3),
            ))

    # Sort by score descending
    results.sort(key=lambda r: r.score, reverse=True)
    results = results[:body.limit]

    elapsed = (time.monotonic() - t0) * 1000
    return SearchResponse(
        results=results,
        total=len(results),
        query_time_ms=round(elapsed, 2),
    )


# ── GET /v1/ui/graph/clusters ────────────────────────────────────


@router.get("/graph/clusters", response_model=ClusterResponse)
async def get_clusters(
    request: Request,
    group_by: str = Query("project"),
    project: Optional[str] = Query(None),
) -> ClusterResponse:
    store = _get_store(request)
    all_memories = store.list(limit=10000, include_archived=False)
    all_entities = store.list_entities(limit=10000)
    all_relationships = store.list_relationships(limit=100000)
    all_mentions = store.list_all_entity_mentions()

    if project:
        all_memories = [m for m in all_memories if m.project == project]

    # Build nodes and edges
    nodes = [_memory_to_node(m) for m in all_memories]
    nodes += [_entity_to_node(e) for e in all_entities]

    memory_ids = {m.id for m in all_memories}
    entity_ids = {e.id for e in all_entities}

    edges: List[GraphEdge] = []
    for rel in all_relationships:
        if rel.source_entity_id in entity_ids and rel.target_entity_id in entity_ids:
            edges.append(GraphEdge(
                source=rel.source_entity_id,
                target=rel.target_entity_id,
                rel_type=rel.rel_type,
                weight=rel.weight,
                label=rel.rel_type,
            ))
    for em in all_mentions:
        if em.memory_id in memory_ids and em.entity_id in entity_ids:
            edges.append(GraphEdge(
                source=em.memory_id,
                target=em.entity_id,
                rel_type="mentions",
                weight=em.confidence,
                label="mentions",
            ))

    # Group into clusters
    clusters: List[ClusterItem] = []
    groups: Dict[str, List[str]] = {}

    if group_by == "project":
        for m in all_memories:
            key = m.project or "(no project)"
            groups.setdefault(key, []).append(m.id)
    elif group_by == "type":
        for m in all_memories:
            groups.setdefault(m.type, []).append(m.id)
        for e in all_entities:
            key = f"entity:{e.entity_type}"
            groups.setdefault(key, []).append(e.id)
    else:
        for m in all_memories:
            key = m.project or "(no project)"
            groups.setdefault(key, []).append(m.id)

    for label, node_ids in groups.items():
        clusters.append(ClusterItem(
            id=f"cluster_{label}",
            label=label,
            group_by=group_by,
            node_count=len(node_ids),
            node_ids=node_ids,
        ))

    return ClusterResponse(clusters=clusters, nodes=nodes, edges=edges)


# ── GET /v1/ui/stats ──────────────────────────────────────────────


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    request: Request,
    project: Optional[str] = Query(None),
) -> StatsResponse:
    store = _get_store(request)
    all_memories = store.list(limit=100000, include_archived=False)
    all_entities = store.list_entities(limit=100000)
    all_relationships = store.list_relationships(limit=100000)

    if project:
        all_memories = [m for m in all_memories if m.project == project]

    by_type: Dict[str, int] = {}
    by_project: Dict[str, int] = {}
    by_tier: Dict[str, int] = {}
    total_importance = 0.0

    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    recent_24h = 0
    recent_7d = 0
    oldest = None
    newest = None

    for m in all_memories:
        by_type[m.type] = by_type.get(m.type, 0) + 1
        p = m.project or "(no project)"
        by_project[p] = by_project.get(p, 0) + 1
        by_tier[m.tier] = by_tier.get(m.tier, 0) + 1
        total_importance += m.importance_score
        if m.created_at >= cutoff_24h:
            recent_24h += 1
        if m.created_at >= cutoff_7d:
            recent_7d += 1
        if oldest is None or m.created_at < oldest:
            oldest = m.created_at
        if newest is None or m.created_at > newest:
            newest = m.created_at

    by_entity_type: Dict[str, int] = {}
    for e in all_entities:
        by_entity_type[e.entity_type] = by_entity_type.get(e.entity_type, 0) + 1

    # Top entities by mention count
    sorted_entities = sorted(all_entities, key=lambda e: e.mention_count, reverse=True)
    top_entities = [
        {"name": e.name, "type": e.entity_type, "mention_count": e.mention_count}
        for e in sorted_entities[:5]
    ]

    avg_importance = (total_importance / len(all_memories)) if all_memories else 0.0

    return StatsResponse(
        total_memories=len(all_memories),
        total_entities=len(all_entities),
        total_relationships=len(all_relationships),
        by_type=by_type,
        by_project=by_project,
        by_tier=by_tier,
        by_entity_type=by_entity_type,
        avg_importance=round(avg_importance, 3),
        top_entities=top_entities,
        recent_24h=recent_24h,
        recent_7d=recent_7d,
        oldest_memory=oldest,
        newest_memory=newest,
    )


# ── GET /v1/ui/timeline ──────────────────────────────────────────


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline(
    request: Request,
    bucket: str = Query("day"),
    project: Optional[str] = Query(None),
) -> TimelineResponse:
    store = _get_store(request)
    all_memories = store.list(limit=100000, include_archived=False)
    if project:
        all_memories = [m for m in all_memories if m.project == project]

    if not all_memories:
        return TimelineResponse()

    # Determine bucket format
    if bucket == "hour":
        fmt = "%Y-%m-%dT%H:00"
    elif bucket == "week":
        fmt = "%Y-W%W"
    elif bucket == "month":
        fmt = "%Y-%m"
    else:  # day
        fmt = "%Y-%m-%d"

    bucket_data: Dict[str, Dict[str, int]] = {}  # date -> {type -> count}
    oldest = None
    newest = None

    for m in all_memories:
        try:
            dt = datetime.fromisoformat(m.created_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        key = dt.strftime(fmt)
        if key not in bucket_data:
            bucket_data[key] = {}
        bucket_data[key][m.type] = bucket_data[key].get(m.type, 0) + 1

        date_str = m.created_at[:10]
        if oldest is None or date_str < oldest:
            oldest = date_str
        if newest is None or date_str > newest:
            newest = date_str

    buckets = []
    for date_key in sorted(bucket_data.keys()):
        by_type = bucket_data[date_key]
        total = sum(by_type.values())
        buckets.append(TimelineBucket(date=date_key, count=total, by_type=by_type))

    return TimelineResponse(
        buckets=buckets,
        range={"start": oldest, "end": newest},
    )
