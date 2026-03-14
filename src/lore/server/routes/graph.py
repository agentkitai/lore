"""Postgres-backed graph visualization endpoints.

Ports the UI graph endpoints from SQLite Store to asyncpg,
using the same get_pool() pattern as other server routes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ui", tags=["graph"])


# ── Response models ───────────────────────────────────────────────


class GraphNode(BaseModel):
    id: str
    kind: str
    label: str
    type: str
    tier: Optional[str] = None
    project: Optional[str] = None
    importance: Optional[float] = None
    confidence: Optional[float] = None
    tags: Optional[List[str]] = None
    created_at: Optional[str] = None
    upvotes: Optional[int] = None
    downvotes: Optional[int] = None
    access_count: Optional[int] = None
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


class ClusterItem(BaseModel):
    id: str
    label: str
    group_by: str
    node_count: int
    node_ids: List[str] = []


class ClusterResponse(BaseModel):
    clusters: List[ClusterItem] = []
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []


class SearchResult(BaseModel):
    id: str
    content: str
    type: str
    project: Optional[str] = None
    score: float = 0.0
    created_at: str = ""


class SearchResponse(BaseModel):
    results: List[SearchResult] = []
    total: int = 0


# ── Helpers ───────────────────────────────────────────────────────


async def _table_exists(conn, table_name: str) -> bool:
    return await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = $1)",
        table_name,
    )


def _parse_tags(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    return list(raw)


def _parse_meta(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
    return dict(raw)


def _ts(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _memory_type(meta) -> str:
    m = _parse_meta(meta)
    return m.get("type", "general")


def _memory_tier(meta) -> str:
    m = _parse_meta(meta)
    return m.get("tier", "long")


# ── GET /v1/graph ─────────────────────────────────────────────────


@router.get("/graph", response_model=GraphResponse)
async def get_graph(
    project: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    min_importance: float = Query(0.0, ge=0.0, le=1.0),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
    include_orphans: bool = Query(True),
) -> GraphResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Build memory query with filters
        where_parts = ["1=1"]
        params: list = []

        if project:
            params.append(project)
            where_parts.append(f"project = ${len(params)}")
        if min_importance > 0:
            params.append(min_importance)
            where_parts.append(f"COALESCE(importance_score, 1.0) >= ${len(params)}")
        if since:
            from datetime import datetime as _dt
            try:
                params.append(_dt.fromisoformat(since))
            except ValueError:
                params.append(since)
            where_parts.append(f"created_at >= ${len(params)}::timestamptz")
        if until:
            from datetime import datetime as _dt
            try:
                params.append(_dt.fromisoformat(until))
            except ValueError:
                params.append(until)
            where_parts.append(f"created_at <= ${len(params)}::timestamptz")

        where_sql = " AND ".join(where_parts)

        # Get total counts first
        total_memories = await conn.fetchval("SELECT COUNT(*) FROM memories")

        # Get filtered memories
        params.append(limit)
        limit_idx = len(params)
        mem_rows = await conn.fetch(
            f"""SELECT id, content, tags, confidence, source, project,
                       created_at, updated_at, importance_score, access_count,
                       upvotes, downvotes, meta
                FROM memories
                WHERE {where_sql}
                ORDER BY COALESCE(importance_score, 1.0) DESC
                LIMIT ${limit_idx}""",
            *params,
        )

        # Filter by type/tier in Python (stored in meta jsonb)
        nodes: List[GraphNode] = []
        memory_ids: set = set()
        for r in mem_rows:
            mtype = _memory_type(r["meta"])
            mtier = _memory_tier(r["meta"])
            if type and mtype != type:
                continue
            if tier and mtier != tier:
                continue
            content = r["content"] or ""
            label = (content[:60] + "...") if len(content) > 60 else content
            label = label.replace("\n", " ")
            tags = _parse_tags(r["tags"])
            nodes.append(GraphNode(
                id=r["id"],
                kind="memory",
                label=label,
                type=mtype,
                tier=mtier,
                project=r["project"],
                importance=float(r["importance_score"]) if r["importance_score"] else 1.0,
                confidence=float(r["confidence"]) if r["confidence"] else 1.0,
                tags=tags,
                created_at=_ts(r["created_at"]),
                upvotes=r["upvotes"] or 0,
                downvotes=r["downvotes"] or 0,
                access_count=r["access_count"] or 0,
            ))
            memory_ids.add(r["id"])

        # Entities and relationships (may not exist)
        has_entities = await _table_exists(conn, "entities")
        has_relationships = await _table_exists(conn, "relationships")
        has_mentions = await _table_exists(conn, "entity_mentions")

        total_entities = 0
        total_relationships = 0
        edges: List[GraphEdge] = []

        if has_entities:
            total_entities = await conn.fetchval("SELECT COUNT(*) FROM entities")
            ent_rows = await conn.fetch(
                "SELECT id, name, entity_type, aliases, mention_count, first_seen_at, last_seen_at FROM entities"
            )
            entity_ids = set()
            for e in ent_rows:
                aliases = _parse_tags(e["aliases"])
                nodes.append(GraphNode(
                    id=e["id"],
                    kind="entity",
                    label=e["name"],
                    type=e["entity_type"],
                    mention_count=e["mention_count"] or 0,
                    aliases=aliases,
                    first_seen_at=_ts(e["first_seen_at"]),
                    last_seen_at=_ts(e["last_seen_at"]),
                ))
                entity_ids.add(e["id"])

            if has_mentions:
                mention_rows = await conn.fetch(
                    "SELECT entity_id, memory_id, confidence FROM entity_mentions"
                )
                for em in mention_rows:
                    if em["memory_id"] in memory_ids and em["entity_id"] in entity_ids:
                        edges.append(GraphEdge(
                            source=em["memory_id"],
                            target=em["entity_id"],
                            rel_type="mentions",
                            weight=float(em["confidence"] or 1.0),
                            label="mentions",
                        ))

            if has_relationships:
                total_relationships = await conn.fetchval("SELECT COUNT(*) FROM relationships")
                rel_rows = await conn.fetch(
                    "SELECT source_entity_id, target_entity_id, rel_type, weight FROM relationships"
                )
                for rel in rel_rows:
                    if rel["source_entity_id"] in entity_ids and rel["target_entity_id"] in entity_ids:
                        edges.append(GraphEdge(
                            source=rel["source_entity_id"],
                            target=rel["target_entity_id"],
                            rel_type=rel["rel_type"],
                            weight=float(rel["weight"] or 1.0),
                            label=rel["rel_type"],
                        ))

        # Filter orphans
        if not include_orphans:
            connected_ids = set()
            for e in edges:
                connected_ids.add(e.source)
                connected_ids.add(e.target)
            nodes = [n for n in nodes if n.id in connected_ids]

    return GraphResponse(
        nodes=nodes,
        edges=edges,
        stats=GraphStats(
            total_memories=total_memories,
            total_entities=total_entities,
            total_relationships=total_relationships,
            filtered_nodes=len(nodes),
            filtered_edges=len(edges),
        ),
    )


# ── GET /v1/ui/graph/clusters ─────────────────────────────────────


@router.get("/graph/clusters", response_model=ClusterResponse)
async def get_clusters(
    group_by: str = Query("project"),
    project: Optional[str] = Query(None),
) -> ClusterResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        where_parts = ["1=1"]
        params: list = []
        if project:
            params.append(project)
            where_parts.append(f"project = ${len(params)}")
        where_sql = " AND ".join(where_parts)

        rows = await conn.fetch(
            f"""SELECT id, content, tags, confidence, source, project,
                       created_at, updated_at, importance_score, access_count,
                       upvotes, downvotes, meta
                FROM memories WHERE {where_sql}
                ORDER BY created_at DESC LIMIT 10000""",
            *params,
        )

        nodes: List[GraphNode] = []
        groups: Dict[str, List[str]] = {}
        for r in rows:
            mtype = _memory_type(r["meta"])
            mtier = _memory_tier(r["meta"])
            content = r["content"] or ""
            label = (content[:60] + "...") if len(content) > 60 else content
            label = label.replace("\n", " ")
            tags = _parse_tags(r["tags"])
            nodes.append(GraphNode(
                id=r["id"],
                kind="memory",
                label=label,
                type=mtype,
                tier=mtier,
                project=r["project"],
                importance=float(r["importance_score"]) if r["importance_score"] else None,
                confidence=float(r["confidence"]) if r["confidence"] else None,
                tags=tags,
                created_at=r["created_at"].isoformat() if r["created_at"] else None,
                upvotes=r["upvotes"],
                downvotes=r["downvotes"],
                access_count=r["access_count"],
            ))

            if group_by == "type":
                key = mtype
            elif group_by == "tier":
                key = mtier
            else:
                key = r["project"] or "(no project)"
            groups.setdefault(key, []).append(r["id"])

        clusters = [
            ClusterItem(
                id=f"cluster_{label}",
                label=label,
                group_by=group_by,
                node_count=len(node_ids),
                node_ids=node_ids,
            )
            for label, node_ids in groups.items()
        ]

        return ClusterResponse(clusters=clusters, nodes=nodes, edges=[])


# ── POST /v1/ui/search ───────────────────────────────────────────


@router.post("/search", response_model=SearchResponse)
async def search_memories(request: dict) -> SearchResponse:
    query = request.get("query", "")
    limit = request.get("limit", 20)
    if not query:
        return SearchResponse(results=[], total=0)

    pool = await get_pool()
    async with pool.acquire() as conn:
        pattern = f"%{query}%"
        rows = await conn.fetch(
            """SELECT id, content, project, created_at, importance_score, meta
               FROM memories
               WHERE content ILIKE $1
               ORDER BY importance_score DESC NULLS LAST
               LIMIT $2""",
            pattern, limit,
        )
        results = [
            SearchResult(
                id=r["id"],
                content=(r["content"] or "")[:200],
                type=_memory_type(r["meta"]),
                project=r["project"],
                score=float(r["importance_score"]) if r["importance_score"] else 0.0,
                created_at=r["created_at"].isoformat() if r["created_at"] else "",
            )
            for r in rows
        ]
        return SearchResponse(results=results, total=len(results))


# ── GET /v1/ui/stats ─────────────────────────────────────────────


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    project: Optional[str] = Query(None),
) -> StatsResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        project_filter = ""
        params: list = []
        if project:
            params.append(project)
            project_filter = "WHERE project = $1"

        total = await conn.fetchval(f"SELECT COUNT(*) FROM memories {project_filter}", *params)

        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)
        cutoff_7d = now - timedelta(days=7)

        if project:
            recent_24h = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE project = $1 AND created_at >= $2",
                project, cutoff_24h,
            )
            recent_7d = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE project = $1 AND created_at >= $2",
                project, cutoff_7d,
            )
            avg_imp = await conn.fetchval(
                "SELECT AVG(COALESCE(importance_score, 1.0)) FROM memories WHERE project = $1",
                project,
            )
            oldest = await conn.fetchval(
                "SELECT MIN(created_at) FROM memories WHERE project = $1", project,
            )
            newest = await conn.fetchval(
                "SELECT MAX(created_at) FROM memories WHERE project = $1", project,
            )
            type_rows = await conn.fetch(
                "SELECT COALESCE(meta->>'type', 'general') as t, COUNT(*) as c FROM memories WHERE project = $1 GROUP BY t",
                project,
            )
            proj_rows = await conn.fetch(
                "SELECT COALESCE(project, '(no project)') as p, COUNT(*) as c FROM memories WHERE project = $1 GROUP BY p",
                project,
            )
        else:
            recent_24h = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE created_at >= $1", cutoff_24h,
            )
            recent_7d = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE created_at >= $1", cutoff_7d,
            )
            avg_imp = await conn.fetchval(
                "SELECT AVG(COALESCE(importance_score, 1.0)) FROM memories",
            )
            oldest = await conn.fetchval("SELECT MIN(created_at) FROM memories")
            newest = await conn.fetchval("SELECT MAX(created_at) FROM memories")
            type_rows = await conn.fetch(
                "SELECT COALESCE(meta->>'type', 'general') as t, COUNT(*) as c FROM memories GROUP BY t",
            )
            proj_rows = await conn.fetch(
                "SELECT COALESCE(project, '(no project)') as p, COUNT(*) as c FROM memories GROUP BY p",
            )

        by_type = {r["t"]: r["c"] for r in type_rows}
        by_project = {r["p"]: r["c"] for r in proj_rows}

        # Entities
        total_entities = 0
        total_relationships = 0
        by_entity_type: Dict[str, int] = {}
        top_entities: List[Dict[str, Any]] = []

        if await _table_exists(conn, "entities"):
            total_entities = await conn.fetchval("SELECT COUNT(*) FROM entities")
            et_rows = await conn.fetch(
                "SELECT entity_type, COUNT(*) as c FROM entities GROUP BY entity_type"
            )
            by_entity_type = {r["entity_type"]: r["c"] for r in et_rows}
            top_rows = await conn.fetch(
                "SELECT name, entity_type, mention_count FROM entities ORDER BY mention_count DESC LIMIT 5"
            )
            top_entities = [
                {"name": r["name"], "type": r["entity_type"], "mention_count": r["mention_count"]}
                for r in top_rows
            ]

        if await _table_exists(conn, "relationships"):
            total_relationships = await conn.fetchval("SELECT COUNT(*) FROM relationships")

    return StatsResponse(
        total_memories=total,
        total_entities=total_entities,
        total_relationships=total_relationships,
        by_type=by_type,
        by_project=by_project,
        by_tier={},
        by_entity_type=by_entity_type,
        avg_importance=round(float(avg_imp or 0), 3),
        top_entities=top_entities,
        recent_24h=recent_24h,
        recent_7d=recent_7d,
        oldest_memory=_ts(oldest),
        newest_memory=_ts(newest),
    )


# ── GET /v1/graph/timeline ───────────────────────────────────────


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline(
    bucket: str = Query("day"),
    project: Optional[str] = Query(None),
) -> TimelineResponse:
    pool = await get_pool()

    # Map bucket to Postgres date_trunc interval
    trunc_map = {"hour": "hour", "day": "day", "week": "week", "month": "month"}
    trunc = trunc_map.get(bucket, "day")

    async with pool.acquire() as conn:
        params: list = []
        project_filter = ""
        if project:
            params.append(project)
            project_filter = "WHERE project = $1"

        rows = await conn.fetch(
            f"""SELECT date_trunc('{trunc}', created_at) as bucket_date,
                       COALESCE(meta->>'type', 'general') as mem_type,
                       COUNT(*) as cnt
                FROM memories {project_filter}
                GROUP BY bucket_date, mem_type
                ORDER BY bucket_date""",
            *params,
        )

        if not rows:
            return TimelineResponse()

        # Aggregate into buckets
        bucket_data: Dict[str, Dict[str, int]] = {}
        for r in rows:
            key = r["bucket_date"].strftime("%Y-%m-%d") if trunc != "hour" else r["bucket_date"].strftime("%Y-%m-%dT%H:00")
            if key not in bucket_data:
                bucket_data[key] = {}
            bucket_data[key][r["mem_type"]] = r["cnt"]

        oldest = await conn.fetchval(
            f"SELECT MIN(created_at) FROM memories {project_filter}", *params,
        )
        newest = await conn.fetchval(
            f"SELECT MAX(created_at) FROM memories {project_filter}", *params,
        )

    buckets = []
    for date_key in sorted(bucket_data.keys()):
        by_type = bucket_data[date_key]
        buckets.append(TimelineBucket(date=date_key, count=sum(by_type.values()), by_type=by_type))

    return TimelineResponse(
        buckets=buckets,
        range={
            "start": oldest.strftime("%Y-%m-%d") if oldest else None,
            "end": newest.strftime("%Y-%m-%d") if newest else None,
        },
    )


# ── GET /v1/graph/memory/{id} ────────────────────────────────────


@router.get("/memory/{memory_id}", response_model=MemoryDetailResponse)
async def get_memory_detail(memory_id: str) -> MemoryDetailResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, content, tags, confidence, source, project,
                      created_at, updated_at, importance_score, access_count,
                      upvotes, downvotes, meta
               FROM memories WHERE id = $1""",
            memory_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Memory not found")

        meta = _parse_meta(row["meta"])
        tags = _parse_tags(row["tags"])

        connected_entities: List[Dict[str, Any]] = []
        connected_memories: List[Dict[str, Any]] = []

        if await _table_exists(conn, "entity_mentions") and await _table_exists(conn, "entities"):
            # Get connected entities
            ent_rows = await conn.fetch(
                """SELECT e.id, e.name, e.entity_type
                   FROM entity_mentions em
                   JOIN entities e ON e.id = em.entity_id
                   WHERE em.memory_id = $1""",
                memory_id,
            )
            for e in ent_rows:
                connected_entities.append({
                    "id": e["id"],
                    "name": e["name"],
                    "type": e["entity_type"],
                    "rel_type": "mentions",
                })

            # Get connected memories via shared entities
            if ent_rows:
                entity_ids = [e["id"] for e in ent_rows]
                related_rows = await conn.fetch(
                    """SELECT DISTINCT m.id, m.content, m.meta
                       FROM entity_mentions em
                       JOIN memories m ON m.id = em.memory_id
                       WHERE em.entity_id = ANY($1) AND em.memory_id != $2
                       LIMIT 20""",
                    entity_ids,
                    memory_id,
                )
                for rm in related_rows:
                    label = (rm["content"] or "")[:60].replace("\n", " ")
                    connected_memories.append({
                        "id": rm["id"],
                        "label": label,
                        "type": _memory_type(rm["meta"]),
                        "rel_type": "related_to",
                    })

    return MemoryDetailResponse(
        id=row["id"],
        content=row["content"] or "",
        type=meta.get("type", "general"),
        tier=meta.get("tier", "long"),
        project=row["project"],
        tags=tags,
        importance_score=float(row["importance_score"]) if row["importance_score"] else 1.0,
        confidence=float(row["confidence"]) if row["confidence"] else 1.0,
        upvotes=row["upvotes"] or 0,
        downvotes=row["downvotes"] or 0,
        access_count=row["access_count"] or 0,
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
        source=row["source"],
        connected_entities=connected_entities,
        connected_memories=connected_memories,
    )


# ── GET /v1/ui/entity/{entity_id} ─────────────────────────────────


class EntityDetailResponse(BaseModel):
    id: str
    name: str
    entity_type: str
    mention_count: int = 0
    aliases: List[str] = []
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    connected_memories: List[Dict[str, Any]] = []
    connected_entities: List[Dict[str, Any]] = []


@router.get("/entity/{entity_id}", response_model=EntityDetailResponse)
async def get_entity_detail(entity_id: str) -> EntityDetailResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, entity_type, mention_count, first_seen_at, last_seen_at FROM entities WHERE id = $1",
            entity_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Connected memories via entity_mentions
        connected_memories: List[Dict[str, Any]] = []
        mem_rows = await conn.fetch(
            """SELECT m.id, m.content, m.meta, m.created_at
               FROM entity_mentions em
               JOIN memories m ON m.id = em.memory_id
               WHERE em.entity_id = $1
               ORDER BY m.created_at DESC
               LIMIT 30""",
            entity_id,
        )
        for m in mem_rows:
            label = (m["content"] or "")[:80].replace("\n", " ")
            connected_memories.append({
                "id": m["id"],
                "label": label,
                "type": _memory_type(m["meta"]),
                "created_at": _ts(m["created_at"]),
            })

        # Connected entities via co-occurrence relationships (deduplicated)
        connected_entities: List[Dict[str, Any]] = []
        rel_rows = await conn.fetch(
            """SELECT DISTINCT ON (e.id) e.id, e.name, e.entity_type, r.rel_type, r.weight
               FROM relationships r
               JOIN entities e ON e.id = CASE
                   WHEN r.source_entity_id = $1 THEN r.target_entity_id
                   ELSE r.source_entity_id
               END
               WHERE (r.source_entity_id = $1 OR r.target_entity_id = $1)
                 AND e.id != $1
               ORDER BY e.id, r.weight DESC
               LIMIT 20""",
            entity_id,
        )
        for r in rel_rows:
            connected_entities.append({
                "id": r["id"],
                "name": r["name"],
                "type": r["entity_type"],
                "rel_type": r["rel_type"],
                "weight": float(r["weight"]),
            })

    return EntityDetailResponse(
        id=row["id"],
        name=row["name"],
        entity_type=row["entity_type"],
        mention_count=row["mention_count"] or 0,
        first_seen_at=_ts(row["first_seen_at"]),
        last_seen_at=_ts(row["last_seen_at"]),
        connected_memories=connected_memories,
        connected_entities=connected_entities,
    )


# ── Topics (E4 sidebar) ──────────────────────────────────────────


class TopicListItem(BaseModel):
    entity_id: str
    name: str
    entity_type: str
    mention_count: int


class TopicListResponse(BaseModel):
    topics: List[TopicListItem] = []


@router.get("/topics", response_model=TopicListResponse)
async def get_topics(
    min_mentions: int = Query(3, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> TopicListResponse:
    """List topics for sidebar display."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, entity_type, mention_count
               FROM entities
               WHERE mention_count >= $1
               ORDER BY mention_count DESC
               LIMIT $2""",
            min_mentions, limit,
        )
    return TopicListResponse(
        topics=[
            TopicListItem(
                entity_id=row["id"],
                name=row["name"],
                entity_type=row["entity_type"],
                mention_count=row["mention_count"],
            )
            for row in rows
        ]
    )


@router.get("/topics/{name}")
async def get_topic_detail_graph(
    name: str,
    max_memories: int = Query(20, ge=1, le=100),
) -> dict:
    """Get topic detail for sidebar panel."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM entities WHERE LOWER(name) = LOWER($1)", name,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"Topic '{name}' not found")

        entity_id = row["id"]

        # Related entities
        rel_rows = await conn.fetch(
            """SELECT r.rel_type, r.source_entity_id, r.target_entity_id,
                      e.name as other_name, e.entity_type as other_type
               FROM relationships r
               JOIN entities e ON (
                   CASE WHEN r.source_entity_id = $1 THEN r.target_entity_id
                        ELSE r.source_entity_id END = e.id
               )
               WHERE (r.source_entity_id = $1 OR r.target_entity_id = $1)
                 AND r.valid_until IS NULL
               LIMIT 50""",
            entity_id,
        )

        related = []
        for rr in rel_rows:
            direction = "outgoing" if rr["source_entity_id"] == entity_id else "incoming"
            related.append({
                "name": rr["other_name"],
                "entity_type": rr["other_type"],
                "relationship": rr["rel_type"],
                "direction": direction,
            })

        # Linked memories
        mem_rows = await conn.fetch(
            """SELECT DISTINCT m.id, m.content, m.type, m.created_at, m.tags
               FROM entity_mentions em
               JOIN memories m ON em.memory_id = m.id
               WHERE em.entity_id = $1
               ORDER BY m.created_at DESC
               LIMIT $2""",
            entity_id, max_memories,
        )

        total_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT memory_id) FROM entity_mentions WHERE entity_id = $1",
            entity_id,
        )

        memories = []
        for mr in mem_rows:
            tags = mr.get("tags") or []
            if isinstance(tags, str):
                tags = json.loads(tags)
            memories.append({
                "id": mr["id"],
                "content": mr["content"][:200] if mr["content"] else "",
                "type": mr.get("type", "general"),
                "created_at": _ts(mr["created_at"]),
                "tags": tags,
            })

    return {
        "entity": {
            "id": row["id"],
            "name": row["name"],
            "entity_type": row["entity_type"],
            "mention_count": row["mention_count"],
            "first_seen_at": _ts(row.get("first_seen_at")),
            "last_seen_at": _ts(row.get("last_seen_at")),
        },
        "related_entities": related,
        "memories": memories,
        "memory_count": total_count or 0,
    }
