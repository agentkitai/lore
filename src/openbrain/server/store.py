"""ServerStore — async Postgres store with pgvector search for memories."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

logger = logging.getLogger(__name__)

# Time decay constant: exp(-0.005 * age_days) ≈ 138-day half-life
_DECAY_LAMBDA = 0.005

# Columns returned for memory reads (excludes embedding)
_MEMORY_COLUMNS = (
    "id, org_id, content, type, source, project, tags, metadata, "
    "created_at, updated_at, expires_at"
)


def _parse_jsonb(val: Any) -> Any:
    """Parse a JSONB value that may be returned as str or native type."""
    if isinstance(val, str):
        return json.loads(val)
    return val if val is not None else {}


def _row_to_dict(row: asyncpg.Record) -> Dict[str, Any]:
    """Convert an asyncpg Record to a dict with parsed JSONB fields."""
    d = dict(row)
    d["tags"] = _parse_jsonb(d.get("tags")) if d.get("tags") is not None else []
    d["metadata"] = _parse_jsonb(d.get("metadata")) if d.get("metadata") is not None else {}
    return d


class ServerStore:
    """Async Postgres store with pgvector search."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(
        self,
        org_id: str,
        memory_id: str,
        content: str,
        embedding: Optional[List[float]] = None,
        type: str = "note",
        source: Optional[str] = None,
        project: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expires_at: Optional[datetime] = None,
    ) -> str:
        """Insert a memory. Returns the memory ID."""
        now = datetime.now(timezone.utc)
        tags_json = json.dumps(tags or [])
        metadata_json = json.dumps(metadata or {})
        emb_str = json.dumps(embedding) if embedding else None

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO memories
                   (id, org_id, content, type, source, project, tags, metadata,
                    embedding, created_at, updated_at, expires_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb,
                           $9::vector, $10, $11, $12)""",
                memory_id,
                org_id,
                content,
                type,
                source,
                project,
                tags_json,
                metadata_json,
                emb_str,
                now,
                now,
                expires_at,
            )
        return memory_id

    async def get(self, org_id: str, memory_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single memory by ID + org_id."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = $1 AND org_id = $2",
                memory_id,
                org_id,
            )
        if row is None:
            return None
        return _row_to_dict(row)

    async def search(
        self,
        org_id: str,
        embedding: List[float],
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Cosine similarity search with time decay scoring.

        Score = cosine_similarity * time_decay
        where time_decay = exp(-0.005 * age_days)
        """
        where_parts: list[str] = ["org_id = $1"]
        params: list[Any] = [org_id]

        if project is not None:
            params.append(project)
            where_parts.append(f"project = ${len(params)}")

        if type is not None:
            params.append(type)
            where_parts.append(f"type = ${len(params)}")

        if tags:
            params.append(json.dumps(tags))
            where_parts.append(f"tags @> ${len(params)}::jsonb")

        # Exclude expired
        where_parts.append("(expires_at IS NULL OR expires_at > now())")
        # Must have embedding
        where_parts.append("embedding IS NOT NULL")

        where_sql = " AND ".join(where_parts)

        # Embedding parameter
        params.append(json.dumps(embedding))
        emb_idx = len(params)

        # Limit
        params.append(limit)
        limit_idx = len(params)

        query = f"""
            SELECT {_MEMORY_COLUMNS},
                   (1 - (embedding <=> ${emb_idx}::vector)) *
                   exp(-{_DECAY_LAMBDA} * EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0)
                   AS score
            FROM memories
            WHERE {where_sql}
            ORDER BY score DESC
            LIMIT ${limit_idx}
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        results = []
        for row in rows:
            d = _row_to_dict(row)
            d["score"] = round(max(float(row["score"]), 0.0), 6)
            results.append(d)
        return results

    async def list(
        self,
        org_id: str,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Paginated listing with filters, ordered by created_at DESC.

        Returns (memories, total_count).
        """
        where_parts: list[str] = ["org_id = $1"]
        params: list[Any] = [org_id]

        if project is not None:
            params.append(project)
            where_parts.append(f"project = ${len(params)}")

        if type is not None:
            params.append(type)
            where_parts.append(f"type = ${len(params)}")

        if tags:
            params.append(json.dumps(tags))
            where_parts.append(f"tags @> ${len(params)}::jsonb")

        # Exclude expired
        where_parts.append("(expires_at IS NULL OR expires_at > now())")

        where_sql = " AND ".join(where_parts)

        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM memories WHERE {where_sql}",
                *params,
            )

            params.append(limit)
            limit_idx = len(params)
            params.append(offset)
            offset_idx = len(params)

            rows = await conn.fetch(
                f"""SELECT {_MEMORY_COLUMNS}
                    FROM memories WHERE {where_sql}
                    ORDER BY created_at DESC
                    LIMIT ${limit_idx} OFFSET ${offset_idx}""",
                *params,
            )

        return [_row_to_dict(row) for row in rows], total

    async def delete(self, org_id: str, memory_id: str) -> bool:
        """Delete a single memory by ID. Returns True if deleted."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM memories WHERE id = $1 AND org_id = $2",
                memory_id,
                org_id,
            )
        return result == "DELETE 1"

    async def delete_by_filter(
        self,
        org_id: str,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
    ) -> int:
        """Bulk delete with filter combination. Returns count of deleted rows."""
        where_parts: list[str] = ["org_id = $1"]
        params: list[Any] = [org_id]

        if type is not None:
            params.append(type)
            where_parts.append(f"type = ${len(params)}")

        if tags:
            params.append(json.dumps(tags))
            where_parts.append(f"tags @> ${len(params)}::jsonb")

        if project is not None:
            params.append(project)
            where_parts.append(f"project = ${len(params)}")

        where_sql = " AND ".join(where_parts)

        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM memories WHERE {where_sql}",
                *params,
            )

        # asyncpg returns "DELETE N"
        return int(result.split()[-1])

    async def stats(self, org_id: str) -> Dict[str, Any]:
        """Aggregate statistics for an org."""
        async with self._pool.acquire() as conn:
            # Total count (excluding expired)
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM memories WHERE org_id = $1 AND (expires_at IS NULL OR expires_at > now())",
                org_id,
            )

            # Count by type
            type_rows = await conn.fetch(
                """SELECT type, COUNT(*) as cnt FROM memories
                   WHERE org_id = $1 AND (expires_at IS NULL OR expires_at > now())
                   GROUP BY type ORDER BY cnt DESC""",
                org_id,
            )

            # Count by project
            project_rows = await conn.fetch(
                """SELECT COALESCE(project, '(unscoped)') as project, COUNT(*) as cnt
                   FROM memories
                   WHERE org_id = $1 AND (expires_at IS NULL OR expires_at > now())
                   GROUP BY project ORDER BY cnt DESC""",
                org_id,
            )

            # Date range
            dates = await conn.fetchrow(
                """SELECT MIN(created_at) as oldest, MAX(created_at) as newest
                   FROM memories
                   WHERE org_id = $1 AND (expires_at IS NULL OR expires_at > now())""",
                org_id,
            )

        return {
            "total_count": total or 0,
            "count_by_type": {row["type"]: row["cnt"] for row in type_rows},
            "count_by_project": {row["project"]: row["cnt"] for row in project_rows},
            "oldest_memory": dates["oldest"] if dates else None,
            "newest_memory": dates["newest"] if dates else None,
        }
