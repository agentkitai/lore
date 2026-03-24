"""Memory-related graph endpoints: get_graph, search_memories, get_memory_detail."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from lore.server.db import get_pool
from lore.server.routes._parsers import _parse_meta, _parse_tags, _ts

from ._helpers import _memory_tier, _memory_type, _table_exists
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
        if type:
            params.append(type)
            where_parts.append(f"COALESCE(meta->>'type', 'general') = ${len(params)}")
        if tier:
            params.append(tier)
            where_parts.append(f"COALESCE(meta->>'tier', 'long') = ${len(params)}")
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

        # Build graph nodes from filtered rows
        nodes: List[GraphNode] = []
        memory_ids: set = set()
        for r in mem_rows:
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
                    "SELECT source_entity_id, target_entity_id, rel_type, weight FROM relationships WHERE COALESCE(status, 'approved') = 'approved'"
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
