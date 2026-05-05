"""PostgresStore — asyncpg + pgvector implementation of Store.

Phase 1A implements only the MemoryOps slice. Other slices remain in the
existing route SQL until 1B–1G migrate them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]

from ulid import ULID

from lore.persistence.exceptions import BackendUnavailable, StoreNotFound
from lore.persistence.types import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    ScoredMemory,
    StoredMemory,
)


def _row_to_stored(row: "asyncpg.Record") -> StoredMemory:
    tags = row["tags"]
    if isinstance(tags, str):
        tags = json.loads(tags)
    meta = row["meta"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    # Schema stores context as NOT NULL TEXT; surface "" as None at the API
    raw_context = row["context"]
    return StoredMemory(
        id=row["id"],
        org_id=row["org_id"],
        content=row["content"],
        context=raw_context if raw_context else None,
        tags=tuple(tags or ()),
        confidence=float(row["confidence"]) if row["confidence"] is not None else 0.5,
        source=row["source"],
        project=row["project"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        upvotes=row["upvotes"] or 0,
        downvotes=row["downvotes"] or 0,
        meta=dict(meta or {}),
        importance_score=float(row["importance_score"]) if row["importance_score"] is not None else 1.0,
        access_count=row["access_count"] or 0,
        last_accessed_at=row["last_accessed_at"],
    )


class PostgresStore:
    """Store implementation backed by Postgres+pgvector."""

    def __init__(self, *, pool=None, conn=None):
        if asyncpg is None:
            raise BackendUnavailable(
                "asyncpg is not installed. Install with: pip install lore-sdk[server]"
            )
        if (pool is None) == (conn is None):
            raise ValueError("PostgresStore needs exactly one of pool=, conn=")
        self._pool = pool
        self._conn = conn

    @classmethod
    def from_pool(cls, pool) -> "PostgresStore":
        return cls(pool=pool)

    @classmethod
    def from_connection(cls, conn) -> "PostgresStore":
        """Bind to a specific connection (used by contract tests inside a transaction)."""
        return cls(conn=conn)

    def _acquire(self):
        """Return an async context manager that yields a connection.

        - Pool mode: returns ``self._pool.acquire()`` (asyncpg's PoolAcquireContext).
        - Bound mode: wraps the pre-acquired conn in ``_BoundConn`` so the
          ``async with`` site is identical regardless of mode.
        """
        if self._conn is not None:
            return _BoundConn(self._conn)
        return self._pool.acquire()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    # ── MemoryOps: insert, get ──────────────────────────────────────

    async def insert_memory(self, memory: NewMemory) -> StoredMemory:
        memory_id = f"mem_{ULID()}"
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO memories
                    (id, org_id, content, context, tags, confidence, source,
                     project, embedding, expires_at, meta)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9::vector, $10, $11::jsonb)
                RETURNING id, org_id, content, context, tags, confidence, source,
                          project, created_at, updated_at, expires_at, upvotes,
                          downvotes, meta, importance_score, access_count,
                          last_accessed_at
                """,
                memory_id,
                memory.org_id,
                memory.content,
                memory.context or "",  # context is NOT NULL in the schema; coerce None to ""
                json.dumps(list(memory.tags)),
                memory.confidence,
                memory.source,
                memory.project,
                json.dumps(list(memory.embedding)),
                memory.expires_at,
                json.dumps(dict(memory.meta)),
            )
        return _row_to_stored(row)

    async def get_memory(self, org_id: str, memory_id: str) -> Optional[StoredMemory]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, content, context, tags, confidence, source,
                       project, created_at, updated_at, expires_at, upvotes,
                       downvotes, meta, importance_score, access_count,
                       last_accessed_at
                FROM memories
                WHERE id = $1
                  AND org_id = $2
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                memory_id,
                org_id,
            )
        return _row_to_stored(row) if row else None

    # ── MemoryOps stubs — implemented in subsequent Phase 1A tasks ──

    async def update_memory(
        self,
        org_id: str,
        memory_id: str,
        patch: "MemoryPatch",
    ) -> StoredMemory:
        # Build SET clause from non-None patch fields
        sets: list[str] = []
        params: list = [memory_id, org_id]
        if patch.content is not None:
            params.append(patch.content)
            sets.append(f"content = ${len(params)}")
        if patch.context is not None:
            params.append(patch.context)
            sets.append(f"context = ${len(params)}")
        if patch.tags is not None:
            params.append(json.dumps(list(patch.tags)))
            sets.append(f"tags = ${len(params)}::jsonb")
        if patch.confidence is not None:
            params.append(patch.confidence)
            sets.append(f"confidence = ${len(params)}")
        if patch.source is not None:
            params.append(patch.source)
            sets.append(f"source = ${len(params)}")
        if patch.project is not None:
            params.append(patch.project)
            sets.append(f"project = ${len(params)}")
        if patch.expires_at is not None:
            params.append(patch.expires_at)
            sets.append(f"expires_at = ${len(params)}")
        if patch.meta is not None:
            params.append(json.dumps(dict(patch.meta)))
            sets.append(f"meta = ${len(params)}::jsonb")

        if not sets:
            # No-op patch: just return the current row
            existing = await self.get_memory(org_id, memory_id)
            if existing is None:
                raise StoreNotFound("memories", memory_id)
            return existing

        sets.append("updated_at = now()")
        sql = (
            "UPDATE memories "
            f"SET {', '.join(sets)} "
            "WHERE id = $1 AND org_id = $2 "
            "RETURNING id, org_id, content, context, tags, confidence, source, "
            "project, created_at, updated_at, expires_at, upvotes, downvotes, "
            "meta, importance_score, access_count, last_accessed_at"
        )
        async with self._acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        if row is None:
            raise StoreNotFound("memories", memory_id)
        return _row_to_stored(row)

    async def delete_memory(self, org_id: str, memory_id: str) -> bool:
        async with self._acquire() as conn:
            result = await conn.execute(
                "DELETE FROM memories WHERE id = $1 AND org_id = $2",
                memory_id,
                org_id,
            )
        # asyncpg returns "DELETE n"
        return result.endswith(" 1")

    async def list_memories(
        self, filter: "MemoryFilter"
    ) -> Sequence[StoredMemory]:
        where: list[str] = ["org_id = $1"]
        params: list[Any] = [filter.org_id]
        if filter.project is not None:
            params.append(filter.project)
            where.append(f"project = ${len(params)}")
        if filter.type is not None:
            params.append(filter.type)
            where.append(f"meta->>'type' = ${len(params)}")
        if filter.tier is not None:
            params.append(filter.tier)
            where.append(f"meta->>'tier' = ${len(params)}")
        if filter.tags:
            params.append(json.dumps(list(filter.tags)))
            where.append(f"tags @> ${len(params)}::jsonb")
        if filter.since is not None:
            params.append(filter.since)
            where.append(f"created_at >= ${len(params)}")
        if filter.until is not None:
            params.append(filter.until)
            where.append(f"created_at < ${len(params)}")
        if not filter.include_expired:
            where.append("(expires_at IS NULL OR expires_at > now())")

        sql = (
            "SELECT id, org_id, content, context, tags, confidence, source, "
            "project, created_at, updated_at, expires_at, upvotes, downvotes, "
            "meta, importance_score, access_count, last_accessed_at "
            "FROM memories "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC"
        )
        if filter.limit is not None:
            params.append(filter.limit)
            sql += f" LIMIT ${len(params)}"
        if filter.offset:
            params.append(filter.offset)
            sql += f" OFFSET ${len(params)}"

        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_stored(r) for r in rows]

    async def recall_by_embedding(self, params: RecallParams) -> Sequence[ScoredMemory]:
        raise NotImplementedError("recall_by_embedding: implemented in T10")

    async def expire_memories(self) -> int:
        raise NotImplementedError("expire_memories: implemented in T11")

    async def bump_access_counts(self, memory_ids: Sequence[str]) -> None:
        raise NotImplementedError("bump_access_counts: implemented in T12")

    async def vote_memory(self, org_id: str, memory_id: str, *, direction: str) -> StoredMemory:
        raise NotImplementedError("vote_memory: implemented in T13")


class _BoundConn:
    """Async context manager that returns a pre-acquired connection without closing it."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False
