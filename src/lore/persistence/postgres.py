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

from lore.persistence.exceptions import BackendUnavailableError, StoreNotFoundError
from lore.persistence.types import (
    GraphStats,
    MemoryFilter,
    MemoryPatch,
    NewEntity,
    NewMemory,
    NewMention,
    NewRelationship,
    PendingRelationshipRow,
    RecallParams,
    ScoredMemory,
    StoredEntity,
    StoredMemory,
    StoredMention,
    StoredRelationship,
    TimelineBucketRow,
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


def _row_to_mention(row: "asyncpg.Record") -> StoredMention:
    return StoredMention(
        id=row["id"],
        entity_id=row["entity_id"],
        memory_id=row["memory_id"],
        mention_type=row["mention_type"],
        confidence=float(row["confidence"]) if row["confidence"] is not None else 1.0,
        created_at=row["created_at"],
    )


def _row_to_relationship(row: "asyncpg.Record") -> StoredRelationship:
    properties = row["properties"]
    if isinstance(properties, str):
        properties = json.loads(properties)
    return StoredRelationship(
        id=row["id"],
        source_entity_id=row["source_entity_id"],
        target_entity_id=row["target_entity_id"],
        rel_type=row["rel_type"],
        weight=float(row["weight"]) if row["weight"] is not None else 1.0,
        properties=dict(properties or {}),
        source_fact_id=row["source_fact_id"],
        source_memory_id=row["source_memory_id"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        status=row["status"] or "approved",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_entity(row: "asyncpg.Record") -> StoredEntity:
    aliases = row["aliases"]
    if isinstance(aliases, str):
        aliases = json.loads(aliases)
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return StoredEntity(
        id=row["id"],
        name=row["name"],
        entity_type=row["entity_type"],
        aliases=tuple(aliases or ()),
        description=row["description"],
        metadata=dict(metadata or {}),
        mention_count=row["mention_count"] or 0,
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresStore:
    """Store implementation backed by Postgres+pgvector."""

    def __init__(self, *, pool=None, conn=None):
        if asyncpg is None:
            raise BackendUnavailableError(
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
                raise StoreNotFoundError("memories", memory_id)
            return existing

        sets.append("updated_at = now()")
        sql = (
            "UPDATE memories "
            f"SET {', '.join(sets)} "
            "WHERE id = $1 AND org_id = $2 "
            "AND (expires_at IS NULL OR expires_at > now()) "
            "RETURNING id, org_id, content, context, tags, confidence, source, "
            "project, created_at, updated_at, expires_at, upvotes, downvotes, "
            "meta, importance_score, access_count, last_accessed_at"
        )
        async with self._acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        if row is None:
            raise StoreNotFoundError("memories", memory_id)
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

    async def recall_by_embedding(
        self, params: "RecallParams"
    ) -> Sequence[ScoredMemory]:
        where: list[str] = ["org_id = $1"]
        sql_params: list[Any] = [params.org_id]
        if params.project is not None:
            sql_params.append(params.project)
            where.append(f"project = ${len(sql_params)}")
        if params.exclude_expired:
            where.append("(expires_at IS NULL OR expires_at > now())")
        where.append("embedding IS NOT NULL")

        sql_params.append(json.dumps(list(params.query_vec)))
        emb_idx = len(sql_params)
        sql_params.append(params.min_score)
        score_idx = len(sql_params)
        sql_params.append(params.limit)
        limit_idx = len(sql_params)

        sql = f"""
            SELECT id, org_id, content, context, tags, confidence, source, project,
                   created_at, updated_at, expires_at, upvotes, downvotes, meta,
                   importance_score, access_count, last_accessed_at,
                   (1 - (embedding <=> ${emb_idx}::vector)) *
                   COALESCE(importance_score, 1.0) *
                   power(0.5,
                       LEAST(
                           EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0,
                           COALESCE(
                               EXTRACT(EPOCH FROM (now() - last_accessed_at)) / 86400.0,
                               EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0
                           )
                       )
                       / {params.half_life_days}
                   ) AS score
            FROM memories
            WHERE {' AND '.join(where)}
              AND (1 - (embedding <=> ${emb_idx}::vector)) >= ${score_idx}
            ORDER BY score DESC
            LIMIT ${limit_idx}
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *sql_params)
        scored: list[ScoredMemory] = []
        for r in rows:
            sm = _row_to_stored(r)
            scored.append(
                ScoredMemory(
                    id=sm.id,
                    org_id=sm.org_id,
                    content=sm.content,
                    context=sm.context,
                    tags=sm.tags,
                    confidence=sm.confidence,
                    source=sm.source,
                    project=sm.project,
                    created_at=sm.created_at,
                    updated_at=sm.updated_at,
                    expires_at=sm.expires_at,
                    upvotes=sm.upvotes,
                    downvotes=sm.downvotes,
                    meta=sm.meta,
                    importance_score=sm.importance_score,
                    access_count=sm.access_count,
                    last_accessed_at=sm.last_accessed_at,
                    score=float(r["score"]),
                )
            )
        return scored

    async def expire_memories(self) -> int:
        async with self._acquire() as conn:
            result = await conn.execute(
                "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < now()"
            )
        # asyncpg "DELETE n"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def bump_access_counts(self, org_id: str, memory_ids: Sequence[str]) -> None:
        if not memory_ids:
            return
        async with self._acquire() as conn:
            await conn.execute(
                """
                UPDATE memories
                SET access_count = COALESCE(access_count, 0) + 1,
                    last_accessed_at = now(),
                    importance_score = COALESCE(confidence, 1.0)
                        * GREATEST(0.1, 1.0 + (COALESCE(upvotes, 0) - COALESCE(downvotes, 0)) * 0.1)
                        * (1.0 + ln(COALESCE(access_count, 0) + 2) / ln(2) * 0.1)
                WHERE id = ANY($1) AND org_id = $2
                """,
                list(memory_ids),
                org_id,
            )

    async def vote_memory(
        self,
        org_id: str,
        memory_id: str,
        *,
        direction: str,
    ) -> StoredMemory:
        if direction == "up":
            column = "upvotes"
        elif direction == "down":
            column = "downvotes"
        else:
            raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")

        async with self._acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE memories
                SET {column} = COALESCE({column}, 0) + 1,
                    updated_at = now()
                WHERE id = $1 AND org_id = $2
                RETURNING id, org_id, content, context, tags, confidence, source,
                          project, created_at, updated_at, expires_at, upvotes,
                          downvotes, meta, importance_score, access_count,
                          last_accessed_at
                """,
                memory_id,
                org_id,
            )
        if row is None:
            raise StoreNotFoundError("memories", memory_id)
        return _row_to_stored(row)


    # ── GraphOps: upsert_entity, get_entity ────────────────────────

    async def upsert_entity(self, entity: NewEntity) -> StoredEntity:
        entity_id = f"ent_{ULID()}"
        now = datetime.now(timezone.utc)
        first_seen = entity.first_seen_at or now
        last_seen = entity.last_seen_at or now

        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO entities (id, name, entity_type, aliases, description,
                                      metadata, mention_count, first_seen_at,
                                      last_seen_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7, $8, $9)
                ON CONFLICT (name) DO UPDATE SET
                    mention_count = entities.mention_count + EXCLUDED.mention_count,
                    last_seen_at = GREATEST(entities.last_seen_at, EXCLUDED.last_seen_at),
                    aliases = COALESCE(
                        (SELECT jsonb_agg(DISTINCT v)
                         FROM jsonb_array_elements(
                             COALESCE(entities.aliases, '[]'::jsonb) ||
                             COALESCE(EXCLUDED.aliases, '[]'::jsonb)
                         ) v),
                        '[]'::jsonb
                    ),
                    metadata = COALESCE(entities.metadata, '{}'::jsonb) ||
                               COALESCE(EXCLUDED.metadata, '{}'::jsonb),
                    updated_at = now()
                RETURNING id, name, entity_type, aliases, description, metadata,
                          mention_count, first_seen_at, last_seen_at,
                          created_at, updated_at
                """,
                entity_id,
                entity.name,
                entity.entity_type,
                json.dumps(list(entity.aliases)),
                entity.description,
                json.dumps(dict(entity.metadata)),
                entity.mention_count,
                first_seen,
                last_seen,
            )
        return _row_to_entity(row)

    async def get_entity(self, entity_id: str) -> Optional[StoredEntity]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, entity_type, aliases, description, metadata,
                       mention_count, first_seen_at, last_seen_at,
                       created_at, updated_at
                FROM entities
                WHERE id = $1
                """,
                entity_id,
            )
        return _row_to_entity(row) if row else None

    # ── GraphOps stubs (T4–T11) ─────────────────────────────────────

    # T4
    async def get_entity_by_name(self, name: str) -> Optional[StoredEntity]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, entity_type, aliases, description, metadata,
                       mention_count, first_seen_at, last_seen_at,
                       created_at, updated_at
                FROM entities
                WHERE name = $1
                """,
                name,
            )
        return _row_to_entity(row) if row else None

    async def list_entities(
        self,
        *,
        entity_type: Optional[str] = None,
        min_mentions: int = 0,
        limit: int = 100,
    ) -> Sequence[StoredEntity]:
        where: list[str] = []
        params: list[Any] = []
        if entity_type is not None:
            params.append(entity_type)
            where.append(f"entity_type = ${len(params)}")
        if min_mentions > 0:
            params.append(min_mentions)
            where.append(f"mention_count >= ${len(params)}")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(limit)
        sql = f"""
            SELECT id, name, entity_type, aliases, description, metadata,
                   mention_count, first_seen_at, last_seen_at,
                   created_at, updated_at
            FROM entities
            {where_sql}
            ORDER BY mention_count DESC
            LIMIT ${len(params)}
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_entity(r) for r in rows]

    # T5
    async def update_entity_counts(
        self,
        entity_id: str,
        *,
        mention_delta: int,
        last_seen_at: datetime,
    ) -> None:
        async with self._acquire() as conn:
            await conn.execute(
                """
                UPDATE entities
                SET mention_count = mention_count + $2,
                    last_seen_at = GREATEST(last_seen_at, $3),
                    updated_at = now()
                WHERE id = $1
                """,
                entity_id,
                mention_delta,
                last_seen_at,
            )

    async def delete_entity(self, entity_id: str) -> bool:
        async with self._acquire() as conn:
            result = await conn.execute(
                "DELETE FROM entities WHERE id = $1",
                entity_id,
            )
        return result.endswith(" 1")

    # T6
    async def save_mention(self, mention: NewMention) -> None:
        mention_id = f"emen_{ULID()}"
        async with self._acquire() as conn:
            await conn.execute(
                """
                INSERT INTO entity_mentions (id, entity_id, memory_id, mention_type, confidence)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (entity_id, memory_id) DO NOTHING
                """,
                mention_id,
                mention.entity_id,
                mention.memory_id,
                mention.mention_type,
                mention.confidence,
            )

    async def get_mentions_for_memory(self, memory_id: str) -> Sequence[StoredMention]:
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, entity_id, memory_id, mention_type, confidence, created_at
                FROM entity_mentions
                WHERE memory_id = $1
                ORDER BY created_at DESC
                """,
                memory_id,
            )
        return [_row_to_mention(r) for r in rows]

    async def get_mentions_for_entity(
        self,
        entity_id: str,
        *,
        limit: int = 100,
    ) -> Sequence[StoredMention]:
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, entity_id, memory_id, mention_type, confidence, created_at
                FROM entity_mentions
                WHERE entity_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                entity_id,
                limit,
            )
        return [_row_to_mention(r) for r in rows]

    async def count_memories_for_entity(self, entity_id: str) -> int:
        async with self._acquire() as conn:
            result = await conn.fetchval(
                "SELECT COUNT(DISTINCT memory_id) FROM entity_mentions WHERE entity_id = $1",
                entity_id,
            )
        return int(result or 0)

    # T7
    async def get_relationship(self, rel_id: str) -> Optional[StoredRelationship]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, source_entity_id, target_entity_id, rel_type, weight,
                       properties, source_fact_id, source_memory_id,
                       valid_from, valid_until, status, created_at, updated_at
                FROM relationships
                WHERE id = $1
                """,
                rel_id,
            )
        return _row_to_relationship(row) if row else None

    async def get_active_relationship(
        self,
        source_id: str,
        target_id: str,
        *,
        rel_type: str,
    ) -> Optional[StoredRelationship]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, source_entity_id, target_entity_id, rel_type, weight,
                       properties, source_fact_id, source_memory_id,
                       valid_from, valid_until, status, created_at, updated_at
                FROM relationships
                WHERE source_entity_id = $1
                  AND target_entity_id = $2
                  AND rel_type = $3
                  AND valid_until IS NULL
                """,
                source_id,
                target_id,
                rel_type,
            )
        return _row_to_relationship(row) if row else None

    async def save_relationship(self, rel: NewRelationship) -> StoredRelationship:
        rel_id = f"rel_{ULID()}"
        valid_from = rel.valid_from or datetime.now(timezone.utc)
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO relationships
                    (id, source_entity_id, target_entity_id, rel_type, weight,
                     properties, source_fact_id, source_memory_id,
                     valid_from, valid_until, status)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11)
                RETURNING id, source_entity_id, target_entity_id, rel_type, weight,
                          properties, source_fact_id, source_memory_id,
                          valid_from, valid_until, status, created_at, updated_at
                """,
                rel_id,
                rel.source_entity_id,
                rel.target_entity_id,
                rel.rel_type,
                rel.weight,
                json.dumps(dict(rel.properties)),
                rel.source_fact_id,
                rel.source_memory_id,
                valid_from,
                rel.valid_until,
                rel.status,
            )
        return _row_to_relationship(row)

    # T8
    async def list_relationships_for_entity(
        self,
        entity_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> Sequence[StoredRelationship]:
        where: list[str] = ["(source_entity_id = $1 OR target_entity_id = $1)"]
        params: list[Any] = [entity_id]
        if status is not None:
            params.append(status)
            where.append(f"COALESCE(status, 'approved') = ${len(params)}")
        params.append(limit)
        sql = f"""
            SELECT id, source_entity_id, target_entity_id, rel_type, weight,
                   properties, source_fact_id, source_memory_id,
                   valid_from, valid_until, status, created_at, updated_at
            FROM relationships
            WHERE {' AND '.join(where)}
            ORDER BY weight DESC NULLS LAST, created_at DESC
            LIMIT ${len(params)}
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_relationship(r) for r in rows]

    async def update_relationship_status(
        self,
        rel_id: str,
        *,
        status: str,
    ) -> StoredRelationship:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE relationships
                SET status = $2, updated_at = now()
                WHERE id = $1
                RETURNING id, source_entity_id, target_entity_id, rel_type, weight,
                          properties, source_fact_id, source_memory_id,
                          valid_from, valid_until, status, created_at, updated_at
                """,
                rel_id,
                status,
            )
        if row is None:
            raise StoreNotFoundError("relationships", rel_id)
        return _row_to_relationship(row)

    async def update_relationship_weight(
        self,
        rel_id: str,
        *,
        weight: float,
    ) -> None:
        async with self._acquire() as conn:
            await conn.execute(
                "UPDATE relationships SET weight = $2, updated_at = now() WHERE id = $1",
                rel_id,
                weight,
            )

    async def expire_relationship(self, rel_id: str) -> None:
        async with self._acquire() as conn:
            await conn.execute(
                "UPDATE relationships SET valid_until = now(), updated_at = now() WHERE id = $1",
                rel_id,
            )

    # T9
    async def list_pending_relationships(
        self,
        *,
        rel_type: Optional[str] = None,
        limit: int = 100,
    ) -> Sequence[PendingRelationshipRow]:
        where: list[str] = ["r.status = 'pending'"]
        params: list[Any] = []
        if rel_type is not None:
            params.append(rel_type)
            where.append(f"r.rel_type = ${len(params)}")
        params.append(limit)
        sql = f"""
            SELECT r.id, r.source_entity_id, r.target_entity_id, r.rel_type,
                   r.weight, r.source_memory_id, r.created_at,
                   se.name AS source_name,
                   se.entity_type AS source_entity_type,
                   se.mention_count AS source_mentions,
                   te.name AS target_name,
                   te.entity_type AS target_entity_type,
                   te.mention_count AS target_mentions
            FROM relationships r
            JOIN entities se ON se.id = r.source_entity_id
            JOIN entities te ON te.id = r.target_entity_id
            WHERE {' AND '.join(where)}
            ORDER BY r.created_at DESC
            LIMIT ${len(params)}
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [
            PendingRelationshipRow(
                id=r["id"],
                source_entity_id=r["source_entity_id"],
                target_entity_id=r["target_entity_id"],
                rel_type=r["rel_type"],
                weight=float(r["weight"]) if r["weight"] is not None else 1.0,
                source_memory_id=r["source_memory_id"],
                created_at=r["created_at"],
                source_name=r["source_name"],
                source_entity_type=r["source_entity_type"],
                source_mentions=r["source_mentions"] or 0,
                target_name=r["target_name"],
                target_entity_type=r["target_entity_type"],
                target_mentions=r["target_mentions"] or 0,
            )
            for r in rows
        ]

    async def save_rejected_pattern(
        self,
        source_name: str,
        target_name: str,
        rel_type: str,
        *,
        source_memory_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        pattern_id = f"rpat_{ULID()}"
        async with self._acquire() as conn:
            await conn.execute(
                """
                INSERT INTO rejected_patterns
                    (id, source_name, target_name, rel_type, source_memory_id, reason)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (source_name, target_name, rel_type) DO NOTHING
                """,
                pattern_id,
                source_name,
                target_name,
                rel_type,
                source_memory_id,
                reason,
            )

    # T10
    async def query_relationships(
        self,
        entity_ids: Sequence[str],
        *,
        direction: str = "both",
        active_only: bool = True,
        at_time: Optional[datetime] = None,
        rel_types: Optional[Sequence[str]] = None,
    ) -> Sequence[StoredRelationship]:
        raise NotImplementedError("Phase 1B T10")

    # T11
    async def get_graph_stats(
        self,
        *,
        project: Optional[str] = None,
    ) -> GraphStats:
        raise NotImplementedError("Phase 1B T11")

    async def get_timeline_buckets(
        self,
        *,
        trunc: str,
        project: Optional[str] = None,
    ) -> Sequence[TimelineBucketRow]:
        raise NotImplementedError("Phase 1B T11")

    async def get_memories_by_entities(
        self,
        entity_ids: Sequence[str],
        *,
        exclude_memory_id: Optional[str] = None,
        limit: int = 20,
    ) -> Sequence[StoredMemory]:
        raise NotImplementedError("Phase 1B T11")

    async def search_memories_text(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> Sequence[StoredMemory]:
        raise NotImplementedError("Phase 1B T11")


class _BoundConn:
    """Async context manager that returns a pre-acquired connection without closing it."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False
