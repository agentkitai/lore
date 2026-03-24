"""Stats, clusters, and timeline graph endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from lore.server.db import get_pool
from lore.server.routes._parsers import _parse_tags, _ts

from ._helpers import _memory_tier, _memory_type, _table_exists
from .models import (
    ClusterItem,
    ClusterResponse,
    GraphNode,
    StatsResponse,
    TimelineBucket,
    TimelineResponse,
)

router = APIRouter()


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
