"""PostgresStore — asyncpg + pgvector implementation of Store.

Phase 1A implements only the MemoryOps slice. Other slices remain in the
existing route SQL until 1B–1G migrate them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]

from ulid import ULID

from lore.persistence.exceptions import BackendUnavailableError, IntegrityError, StoreNotFoundError
from lore.persistence.types import (
    ExportedMemory,
    GraphStats,
    MemoryFilter,
    MemoryPatch,
    NewApiKey,
    NewConversationJob,
    NewDrillResult,
    NewEntity,
    NewMember,
    NewMemory,
    NewMention,
    NewProfile,
    NewRecommendationFeedback,
    NewRelationship,
    NewRetentionPolicy,
    NewRetrievalEvent,
    NewSloAlert,
    NewSloDefinition,
    NewWorkspace,
    PendingRelationshipRow,
    ProfilePatch,
    RecallParams,
    RecommendationCandidate,
    RetentionPolicyPatch,
    RetrievalAnalyticsResult,
    ScoredMemory,
    SloDefinitionPatch,
    StoredApiKey,
    StoredAuditEntry,
    StoredConversationJob,
    StoredDrillResult,
    StoredEntity,
    StoredMember,
    StoredMemory,
    StoredMention,
    StoredProfile,
    StoredRecommendationConfig,
    StoredRelationship,
    StoredRetentionPolicy,
    StoredSloAlert,
    StoredSloDefinition,
    StoredSnapshotMetadata,
    StoredWorkspace,
    TimelineBucketRow,
    TimeseriesPoint,
    WorkspacePatch,
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


def _row_to_profile(row: "asyncpg.Record") -> StoredProfile:
    tier_filters = row["tier_filters"]
    # asyncpg returns Postgres TEXT[] as list[str] | None
    tf: Optional[tuple] = tuple(tier_filters) if tier_filters is not None else None
    return StoredProfile(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        semantic_weight=float(row["semantic_weight"]),
        graph_weight=float(row["graph_weight"]),
        recency_bias=float(row["recency_bias"]),
        tier_filters=tf,
        min_score=float(row["min_score"]),
        max_results=int(row["max_results"]),
        is_preset=bool(row["is_preset"]),
        k=row["k"],
        threshold=float(row["threshold"]) if row["threshold"] is not None else None,
        # DB-level defaults ensure these are never NULL; coerce for safety
        rerank=bool(row["rerank"]) if row["rerank"] is not None else False,
        include_graph=bool(row["include_graph"]) if row["include_graph"] is not None else True,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_member(row: "asyncpg.Record") -> StoredMember:
    return StoredMember(
        id=row["id"],
        workspace_id=row["workspace_id"],
        user_id=row["user_id"],
        role=row["role"],
        invited_at=row["invited_at"],
        accepted_at=row["accepted_at"],
    )


def _row_to_api_key(row: "asyncpg.Record") -> StoredApiKey:
    return StoredApiKey(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        key_hash=row["key_hash"],
        key_prefix=row["key_prefix"],
        project=row["project"],
        is_root=bool(row["is_root"]),
        workspace_id=row["workspace_id"],
        revoked_at=row["revoked_at"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
    )


def _row_to_workspace(row: "asyncpg.Record") -> StoredWorkspace:
    settings = row["settings"]
    if isinstance(settings, str):
        settings = json.loads(settings)
    return StoredWorkspace(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        slug=row["slug"],
        settings=dict(settings or {}),
        created_at=row["created_at"],
        archived_at=row["archived_at"],
    )


def _row_to_recommendation_candidate(row: "asyncpg.Record") -> RecommendationCandidate:
    meta = row["meta"]
    if isinstance(meta, str):
        meta = json.loads(meta) if meta else {}
    elif meta is None:
        meta = {}
    embedding = row["embedding"]
    # asyncpg returns pgvector as a string '[0.1,0.2,...]' unless a codec is registered.
    # The recommendation engine accepts whatever shape is passed (the pre-1F route
    # passed it through unmodified). If it's a string, parse it; if it's already a
    # list, pass through.
    if isinstance(embedding, str):
        # pgvector text format: '[0.1,0.2,...]'
        stripped = embedding.strip("[]")
        if stripped:
            embedding = [float(x) for x in stripped.split(",")]
        else:
            embedding = []
    return RecommendationCandidate(
        id=row["id"],
        content=row["content"] or "",
        embedding=embedding if embedding is not None else [],
        metadata=dict(meta or {}),
        created_at=row["created_at"],
        access_count=row["access_count"] or 0,
        last_accessed_at=row["last_accessed_at"],
    )


def _row_to_recommendation_config(row: "asyncpg.Record") -> StoredRecommendationConfig:
    return StoredRecommendationConfig(
        id=row["id"],
        workspace_id=row["workspace_id"],
        agent_id=row["agent_id"],
        aggressiveness=float(row["aggressiveness"]),
        enabled=bool(row["enabled"]),
        max_suggestions=int(row["max_suggestions"]),
        cooldown_minutes=int(row["cooldown_minutes"]),
        updated_at=row["updated_at"],
    )


def _row_to_conversation_job(row: "asyncpg.Record") -> StoredConversationJob:
    memory_ids_raw = row["memory_ids"]
    if isinstance(memory_ids_raw, str):
        memory_ids = tuple(json.loads(memory_ids_raw or "[]"))
    elif memory_ids_raw is None:
        memory_ids = ()
    else:
        memory_ids = tuple(memory_ids_raw)
    return StoredConversationJob(
        id=row["id"],
        org_id=row["org_id"],
        status=row["status"],
        message_count=row["message_count"] or 0,
        messages_json=row["messages_json"] or "[]",
        user_id=row["user_id"],
        session_id=row["session_id"],
        project=row["project"],
        memory_ids=memory_ids,
        memories_extracted=row["memories_extracted"] or 0,
        duplicates_skipped=row["duplicates_skipped"] or 0,
        error=row["error"],
        processing_time_ms=row["processing_time_ms"] or 0,
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _row_to_exported_memory(row: "asyncpg.Record") -> ExportedMemory:
    tags = row["tags"]
    if isinstance(tags, str):
        tags = json.loads(tags)
    meta = row["meta"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    embedding = row["embedding"]
    if isinstance(embedding, str) and embedding:
        # pgvector text format '[0.1,0.2,...]'
        stripped = embedding.strip("[]")
        embedding = [float(x) for x in stripped.split(",")] if stripped else None
    elif embedding is not None and not isinstance(embedding, str):
        embedding = list(embedding)
    return ExportedMemory(
        id=row["id"],
        org_id=row["org_id"],
        content=row["content"],
        context=row["context"] if row["context"] else None,
        tags=tuple(tags or ()),
        confidence=float(row["confidence"]),
        source=row["source"],
        project=row["project"],
        embedding=embedding if embedding is not None else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        upvotes=row["upvotes"] or 0,
        downvotes=row["downvotes"] or 0,
        meta=dict(meta or {}),
    )


def _row_to_audit_entry(row: "asyncpg.Record") -> StoredAuditEntry:
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata) if metadata else {}
    return StoredAuditEntry(
        id=row["id"],
        org_id=row["org_id"],
        workspace_id=row["workspace_id"],
        actor_id=row["actor_id"],
        actor_type=row["actor_type"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        metadata=dict(metadata or {}),
        ip_address=str(row["ip_address"]) if row["ip_address"] else None,
        created_at=row["created_at"],
    )


def _row_to_retention_policy(row: "asyncpg.Record") -> StoredRetentionPolicy:
    rw = row["retention_window"]
    if isinstance(rw, str):
        rw = json.loads(rw) if rw else {}
    return StoredRetentionPolicy(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        retention_window=dict(rw or {}),
        snapshot_schedule=row["snapshot_schedule"],
        encryption_required=bool(row["encryption_required"]),
        max_snapshots=int(row["max_snapshots"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_snapshot_metadata(row: "asyncpg.Record") -> StoredSnapshotMetadata:
    return StoredSnapshotMetadata(
        id=row["id"],
        org_id=row["org_id"],
        policy_id=row["policy_id"],
        name=row["name"],
        path=row["path"],
        size_bytes=row["size_bytes"],
        memory_count=row["memory_count"],
        encrypted=bool(row["encrypted"]),
        created_at=row["created_at"],
    )


def _row_to_drill_result(row: "asyncpg.Record") -> StoredDrillResult:
    return StoredDrillResult(
        id=row["id"],
        org_id=row["org_id"],
        snapshot_id=row["snapshot_id"],
        snapshot_name=row["snapshot_name"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        recovery_time_ms=row["recovery_time_ms"],
        memories_restored=row["memories_restored"],
        status=row["status"],
        error=row["error"],
        created_at=row["created_at"],
    )


def _row_to_slo_definition(row: "asyncpg.Record") -> StoredSloDefinition:
    ac = row["alert_channels"]
    if isinstance(ac, str):
        ac = json.loads(ac) if ac else []
    return StoredSloDefinition(
        id=row["id"], org_id=row["org_id"], name=row["name"],
        metric=row["metric"], operator=row["operator"],
        threshold=float(row["threshold"]),
        window_minutes=int(row["window_minutes"]),
        enabled=bool(row["enabled"]),
        alert_channels=tuple(ac or ()),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_slo_alert(row: "asyncpg.Record") -> StoredSloAlert:
    dt = row["dispatched_to"]
    if isinstance(dt, str):
        dt = json.loads(dt) if dt else []
    return StoredSloAlert(
        id=int(row["id"]),
        org_id=row["org_id"],
        slo_id=row["slo_id"],
        metric_value=float(row["metric_value"]),
        threshold=float(row["threshold"]),
        status=row["status"],
        dispatched_to=tuple(dt or ()),
        created_at=row["created_at"],
    )


_VALID_TRUNCS = frozenset({"hour", "day", "week", "month"})

_METRIC_SQL = {
    "p50_latency": "percentile_cont(0.50) WITHIN GROUP (ORDER BY query_time_ms) AS value",
    "p95_latency": "percentile_cont(0.95) WITHIN GROUP (ORDER BY query_time_ms) AS value",
    "p99_latency": "percentile_cont(0.99) WITHIN GROUP (ORDER BY query_time_ms) AS value",
    "hit_rate": "(COUNT(*) FILTER (WHERE results_count > 0))::float / GREATEST(COUNT(*), 1) AS value",
    "retrieval_latency_p95": "percentile_cont(0.95) WITHIN GROUP (ORDER BY query_time_ms) AS value",
    "retrieval_recall": "(COUNT(*) FILTER (WHERE results_count > 0))::float / GREATEST(COUNT(*), 1) AS value",
    "uptime_pct": "(COUNT(*) FILTER (WHERE query_time_ms IS NOT NULL))::float / GREATEST(COUNT(*), 1) * 100.0 AS value",
}


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

    async def list_memories_paginated(
        self,
        filter: "MemoryFilter",
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, Sequence[StoredMemory]]:
        """Two-query paginated list: COUNT(*) then SELECT with LIMIT/OFFSET."""
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
        if filter.text_query is not None:
            params.append(f"%{filter.text_query}%")
            idx = len(params)
            where.append(f"(content ILIKE ${idx} OR context ILIKE ${idx})")
        if filter.min_reputation is not None:
            params.append(filter.min_reputation)
            where.append(f"reputation_score >= ${len(params)}")
        if not filter.include_expired:
            where.append("(expires_at IS NULL OR expires_at > now())")

        where_sql = " AND ".join(where)

        params.append(limit)
        limit_idx = len(params)
        params.append(offset)
        offset_idx = len(params)

        count_sql = f"SELECT COUNT(*) FROM memories WHERE {where_sql}"
        select_sql = (
            "SELECT id, org_id, content, context, tags, confidence, source, "
            "project, created_at, updated_at, expires_at, upvotes, downvotes, "
            "meta, importance_score, access_count, last_accessed_at "
            f"FROM memories WHERE {where_sql} "
            f"ORDER BY created_at DESC "
            f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
        )

        # COUNT uses only the WHERE params (no limit/offset)
        count_params = params[: offset_idx - 2]

        async with self._acquire() as conn:
            total = await conn.fetchval(count_sql, *count_params)
            rows = await conn.fetch(select_sql, *params)

        return (int(total), tuple(_row_to_stored(r) for r in rows))

    async def list_memories_with_embeddings(
        self,
        filter: "MemoryFilter",
    ) -> Sequence[ExportedMemory]:
        """Bulk export — no LIMIT, includes embedding column."""
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
        if filter.text_query is not None:
            params.append(f"%{filter.text_query}%")
            idx = len(params)
            where.append(f"(content ILIKE ${idx} OR context ILIKE ${idx})")
        if filter.min_reputation is not None:
            params.append(filter.min_reputation)
            where.append(f"reputation_score >= ${len(params)}")
        if not filter.include_expired:
            where.append("(expires_at IS NULL OR expires_at > now())")

        where_sql = " AND ".join(where)
        select_sql = (
            "SELECT id, org_id, content, context, tags, confidence, source, "
            "project, embedding, created_at, updated_at, expires_at, upvotes, downvotes, meta "
            f"FROM memories WHERE {where_sql} "
            "ORDER BY created_at"
        )

        async with self._acquire() as conn:
            rows = await conn.fetch(select_sql, *params)

        return tuple(_row_to_exported_memory(r) for r in rows)

    async def upsert_memory_with_embedding(
        self,
        *,
        memory_id: str,
        org_id: str,
        content: str,
        context: Optional[str],
        tags: Sequence[str],
        confidence: float,
        source: Optional[str],
        project: Optional[str],
        embedding: Optional[Sequence[float]],
        expires_at: Optional[datetime],
        upvotes: int,
        downvotes: int,
        meta: Mapping[str, Any],
    ) -> bool:
        """INSERT … ON CONFLICT (id) DO UPDATE … RETURNING (xmax = 0) AS inserted.

        Returns True if a new row was inserted, False if an existing row was
        updated or if no change occurred (e.g. org_id mismatch silently drops
        the update — caller treats None as False).
        """
        encoded_tags = json.dumps(list(tags))
        encoded_meta = json.dumps(dict(meta))
        encoded_embedding = json.dumps(list(embedding)) if embedding is not None else None
        safe_context = context if context is not None else ""

        query = """
            INSERT INTO memories
                (id, org_id, content, context, tags, confidence, source, project,
                 embedding, created_at, updated_at, expires_at,
                 upvotes, downvotes, meta)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9::vector, now(), now(),
                    $10, $11, $12, $13::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                content = EXCLUDED.content,
                context = EXCLUDED.context,
                tags = EXCLUDED.tags,
                confidence = EXCLUDED.confidence,
                source = EXCLUDED.source,
                project = EXCLUDED.project,
                embedding = EXCLUDED.embedding,
                updated_at = EXCLUDED.updated_at,
                expires_at = EXCLUDED.expires_at,
                upvotes = EXCLUDED.upvotes,
                downvotes = EXCLUDED.downvotes,
                meta = EXCLUDED.meta
            WHERE memories.org_id = EXCLUDED.org_id
            RETURNING (xmax = 0) AS inserted
        """

        async with self._acquire() as conn:
            result = await conn.fetchval(
                query,
                memory_id,
                org_id,
                content,
                safe_context,
                encoded_tags,
                confidence,
                source,
                project,
                encoded_embedding,
                expires_at,
                upvotes,
                downvotes,
                encoded_meta,
            )

        return result is True

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

    async def enrich_memory_meta(
        self,
        memory_id: str,
        enrichment_data: "Mapping[str, Any]",
    ) -> None:
        async with self._acquire() as conn:
            await conn.execute(
                """
                UPDATE memories SET
                    meta = jsonb_set(COALESCE(meta, '{}'::jsonb), '{enrichment}', $2::jsonb),
                    updated_at = now()
                WHERE id = $1
                """,
                memory_id,
                json.dumps(dict(enrichment_data)),
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

    async def import_extracted_memory(
        self,
        *,
        memory_id: str,
        org_id: str,
        content: str,
        context: str,
        tags: "Sequence[str]",
        source: str,
        meta: "Mapping[str, Any]",
        confidence: float,
    ) -> bool:
        async with self._acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO memories
                    (id, org_id, content, context, tags, source, meta, confidence,
                     created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8, now(), now())
                ON CONFLICT (id) DO NOTHING
                """,
                memory_id,
                org_id,
                content,
                context,
                json.dumps(list(tags)),
                source,
                json.dumps(dict(meta)),
                confidence,
            )
        return result.endswith(" 1")

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
        if direction not in ("inbound", "outbound", "both"):
            raise ValueError(
                f"direction must be 'inbound', 'outbound', or 'both'; got {direction!r}"
            )
        if not entity_ids:
            return []

        where: list[str] = []
        params: list[Any] = [list(entity_ids)]
        # Direction filter
        if direction == "inbound":
            where.append("target_entity_id = ANY($1)")
        elif direction == "outbound":
            where.append("source_entity_id = ANY($1)")
        else:  # both
            where.append("(source_entity_id = ANY($1) OR target_entity_id = ANY($1))")

        # Active-only filter: only applied when at_time is NOT supplied.
        # When at_time is provided, the temporal window condition replaces
        # the simple active_only check.
        if active_only and at_time is None:
            where.append("valid_until IS NULL")
        if at_time is not None:
            params.append(at_time)
            idx = len(params)
            where.append(
                f"valid_from <= ${idx} AND (valid_until IS NULL OR valid_until > ${idx})"
            )
        if rel_types:
            params.append(list(rel_types))
            where.append(f"rel_type = ANY(${len(params)})")

        sql = f"""
            SELECT id, source_entity_id, target_entity_id, rel_type, weight,
                   properties, source_fact_id, source_memory_id,
                   valid_from, valid_until, status, created_at, updated_at
            FROM relationships
            WHERE {' AND '.join(where)}
            ORDER BY weight DESC NULLS LAST, created_at DESC
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_relationship(r) for r in rows]

    # T11
    async def get_graph_stats(
        self,
        *,
        project: Optional[str] = None,
    ) -> GraphStats:
        proj_clause = "WHERE project = $1" if project else ""
        proj_args: list[Any] = [project] if project else []

        cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)

        async with self._acquire() as conn:
            total_memories = await conn.fetchval(
                f"SELECT COUNT(*) FROM memories {proj_clause}", *proj_args,
            )

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
                    "SELECT COALESCE(meta->>'type', 'general') AS t, COUNT(*) AS c "
                    "FROM memories WHERE project = $1 GROUP BY t",
                    project,
                )
                proj_rows = await conn.fetch(
                    "SELECT COALESCE(project, '(no project)') AS p, COUNT(*) AS c "
                    "FROM memories WHERE project = $1 GROUP BY p",
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
                    "SELECT AVG(COALESCE(importance_score, 1.0)) FROM memories"
                )
                oldest = await conn.fetchval("SELECT MIN(created_at) FROM memories")
                newest = await conn.fetchval("SELECT MAX(created_at) FROM memories")
                type_rows = await conn.fetch(
                    "SELECT COALESCE(meta->>'type', 'general') AS t, COUNT(*) AS c "
                    "FROM memories GROUP BY t",
                )
                proj_rows = await conn.fetch(
                    "SELECT COALESCE(project, '(no project)') AS p, COUNT(*) AS c "
                    "FROM memories GROUP BY p",
                )

            # Entities and relationships are global (no project scope)
            total_entities = await conn.fetchval("SELECT COUNT(*) FROM entities") or 0
            total_relationships = await conn.fetchval(
                "SELECT COUNT(*) FROM relationships"
            ) or 0
            et_rows = await conn.fetch(
                "SELECT entity_type, COUNT(*) AS c FROM entities GROUP BY entity_type"
            )
            top_rows = await conn.fetch(
                "SELECT name, entity_type, mention_count FROM entities "
                "ORDER BY mention_count DESC LIMIT 5"
            )

        by_type = {r["t"]: r["c"] for r in type_rows}
        by_project = {r["p"]: r["c"] for r in proj_rows}
        by_entity_type = {r["entity_type"]: r["c"] for r in et_rows}
        top_entities = [
            {
                "name": r["name"],
                "type": r["entity_type"],
                "mention_count": r["mention_count"],
            }
            for r in top_rows
        ]

        return GraphStats(
            total_memories=total_memories or 0,
            total_entities=total_entities,
            total_relationships=total_relationships,
            by_type=by_type,
            by_project=by_project,
            by_entity_type=by_entity_type,
            top_entities=top_entities,
            avg_importance=round(float(avg_imp or 0), 3),
            recent_24h=recent_24h or 0,
            recent_7d=recent_7d or 0,
            oldest_memory=oldest,
            newest_memory=newest,
        )

    async def get_timeline_buckets(
        self,
        *,
        trunc: str,
        project: Optional[str] = None,
    ) -> Sequence[TimelineBucketRow]:
        if trunc not in _VALID_TRUNCS:
            raise ValueError(
                f"trunc must be one of {sorted(_VALID_TRUNCS)}; got {trunc!r}"
            )
        proj_clause = "WHERE project = $1" if project else ""
        proj_args: list[Any] = [project] if project else []
        sql = f"""
            SELECT date_trunc('{trunc}', created_at) AS bucket_date,
                   COALESCE(meta->>'type', 'general') AS mem_type,
                   COUNT(*) AS cnt
            FROM memories
            {proj_clause}
            GROUP BY bucket_date, mem_type
            ORDER BY bucket_date
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *proj_args)
        return [
            TimelineBucketRow(
                bucket_date=r["bucket_date"],
                mem_type=r["mem_type"],
                count=r["cnt"],
            )
            for r in rows
        ]

    async def get_memories_by_entities(
        self,
        entity_ids: Sequence[str],
        *,
        exclude_memory_id: Optional[str] = None,
        limit: int = 20,
    ) -> Sequence[StoredMemory]:
        if not entity_ids:
            return []
        where: list[str] = ["em.entity_id = ANY($1)"]
        params: list[Any] = [list(entity_ids)]
        if exclude_memory_id is not None:
            params.append(exclude_memory_id)
            where.append(f"m.id != ${len(params)}")
        params.append(limit)
        sql = f"""
            SELECT DISTINCT m.id, m.org_id, m.content, m.context, m.tags,
                            m.confidence, m.source, m.project,
                            m.created_at, m.updated_at, m.expires_at,
                            m.upvotes, m.downvotes, m.meta,
                            m.importance_score, m.access_count, m.last_accessed_at
            FROM entity_mentions em
            JOIN memories m ON m.id = em.memory_id
            WHERE {' AND '.join(where)}
            ORDER BY m.created_at DESC
            LIMIT ${len(params)}
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_stored(r) for r in rows]

    async def search_memories_text(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> Sequence[StoredMemory]:
        pattern = f"%{query}%"
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, org_id, content, context, tags, confidence, source,
                       project, created_at, updated_at, expires_at, upvotes,
                       downvotes, meta, importance_score, access_count,
                       last_accessed_at
                FROM memories
                WHERE content ILIKE $1
                ORDER BY importance_score DESC NULLS LAST, created_at DESC
                LIMIT $2
                """,
                pattern,
                limit,
            )
        return [_row_to_stored(r) for r in rows]

    # ── PolicyOps ─────────────────────────────────────────────────────

    async def get_profile(self, profile_id: str) -> Optional[StoredProfile]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, name,
                       semantic_weight, graph_weight, recency_bias,
                       tier_filters, min_score, max_results, is_preset,
                       k, threshold, rerank, include_graph,
                       created_at, updated_at
                FROM retrieval_profiles
                WHERE id = $1
                """,
                profile_id,
            )
        return _row_to_profile(row) if row else None

    async def get_profile_by_name(
        self, org_id: str, name: str
    ) -> Optional[StoredProfile]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, name,
                       semantic_weight, graph_weight, recency_bias,
                       tier_filters, min_score, max_results, is_preset,
                       k, threshold, rerank, include_graph,
                       created_at, updated_at
                FROM retrieval_profiles
                WHERE name = $1 AND org_id = $2
                """,
                name,
                org_id,
            )
        return _row_to_profile(row) if row else None

    # ── PolicyOps: list, create, update, delete, resolve ──────────────

    async def list_profiles(self, org_id: str) -> Sequence[StoredProfile]:
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, org_id, name,
                       semantic_weight, graph_weight, recency_bias,
                       tier_filters, min_score, max_results, is_preset,
                       k, threshold, rerank, include_graph,
                       created_at, updated_at
                FROM retrieval_profiles
                WHERE org_id = $1 OR org_id = '__global__'
                ORDER BY name
                """,
                org_id,
            )
        return tuple(_row_to_profile(r) for r in rows)

    async def create_profile(self, profile: NewProfile) -> StoredProfile:
        profile_id = f"prof_{ULID()}"
        async with self._acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO retrieval_profiles
                      (id, org_id, name, semantic_weight, graph_weight, recency_bias,
                       tier_filters, min_score, max_results, is_preset,
                       k, threshold, rerank, include_graph)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    RETURNING id, org_id, name,
                              semantic_weight, graph_weight, recency_bias,
                              tier_filters, min_score, max_results, is_preset,
                              k, threshold, rerank, include_graph,
                              created_at, updated_at
                    """,
                    profile_id,
                    profile.org_id,
                    profile.name,
                    profile.semantic_weight,
                    profile.graph_weight,
                    profile.recency_bias,
                    list(profile.tier_filters) if profile.tier_filters is not None else None,
                    profile.min_score,
                    profile.max_results,
                    profile.is_preset,
                    profile.k,
                    profile.threshold,
                    profile.rerank,
                    profile.include_graph,
                )
            except asyncpg.UniqueViolationError as e:
                raise IntegrityError(
                    f"Profile name {profile.name!r} already exists for org_id={profile.org_id!r}"
                ) from e
        return _row_to_profile(row)

    async def update_profile(
        self, profile_id: str, patch: ProfilePatch
    ) -> Optional[StoredProfile]:
        # Build dynamic SET clause from non-None patch fields
        sets: list[str] = []
        params: list = [profile_id]

        if patch.name is not None:
            params.append(patch.name)
            sets.append(f"name = ${len(params)}")
        if patch.semantic_weight is not None:
            params.append(patch.semantic_weight)
            sets.append(f"semantic_weight = ${len(params)}")
        if patch.graph_weight is not None:
            params.append(patch.graph_weight)
            sets.append(f"graph_weight = ${len(params)}")
        if patch.recency_bias is not None:
            params.append(patch.recency_bias)
            sets.append(f"recency_bias = ${len(params)}")
        if patch.tier_filters is not None:
            params.append(list(patch.tier_filters))
            sets.append(f"tier_filters = ${len(params)}")
        if patch.min_score is not None:
            params.append(patch.min_score)
            sets.append(f"min_score = ${len(params)}")
        if patch.max_results is not None:
            params.append(patch.max_results)
            sets.append(f"max_results = ${len(params)}")
        if patch.is_preset is not None:
            params.append(patch.is_preset)
            sets.append(f"is_preset = ${len(params)}")
        if patch.k is not None:
            params.append(patch.k)
            sets.append(f"k = ${len(params)}")
        if patch.threshold is not None:
            params.append(patch.threshold)
            sets.append(f"threshold = ${len(params)}")
        if patch.rerank is not None:
            params.append(patch.rerank)
            sets.append(f"rerank = ${len(params)}")
        if patch.include_graph is not None:
            params.append(patch.include_graph)
            sets.append(f"include_graph = ${len(params)}")

        if not sets:
            raise ValueError(
                "update_profile called with empty patch — caller must ensure at least one field is set"
            )

        sets.append("updated_at = now()")
        sql = (
            "UPDATE retrieval_profiles "
            f"SET {', '.join(sets)} "
            "WHERE id = $1 "
            "RETURNING id, org_id, name, "
            "semantic_weight, graph_weight, recency_bias, "
            "tier_filters, min_score, max_results, is_preset, "
            "k, threshold, rerank, include_graph, "
            "created_at, updated_at"
        )
        async with self._acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        return _row_to_profile(row) if row else None

    async def delete_profile(self, profile_id: str, org_id: str) -> bool:
        async with self._acquire() as conn:
            result = await conn.execute(
                "DELETE FROM retrieval_profiles WHERE id = $1 AND org_id = $2",
                profile_id,
                org_id,
            )
        # asyncpg returns "DELETE n"
        return result.endswith(" 1")

    async def resolve_profile_for_key(
        self, org_id: str, name: str
    ) -> Optional[StoredProfile]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, name, semantic_weight, graph_weight, recency_bias,
                       tier_filters, min_score, max_results, is_preset, k, threshold,
                       rerank, include_graph, created_at, updated_at
                FROM retrieval_profiles
                WHERE name = $1 AND (org_id = $2 OR org_id = '__global__')
                ORDER BY CASE WHEN org_id = $2 THEN 0 ELSE 1 END
                LIMIT 1
                """,
                name,
                org_id,
            )
        return _row_to_profile(row) if row else None

    # ── WorkspaceOps ──────────────────────────────────────────────────

    async def get_workspace(
        self, workspace_id: str, org_id: str
    ) -> Optional[StoredWorkspace]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, name, slug, settings, created_at, archived_at
                FROM workspaces
                WHERE id = $1 AND org_id = $2
                """,
                workspace_id,
                org_id,
            )
        return _row_to_workspace(row) if row else None

    async def list_workspaces(
        self, org_id: str, *, include_archived: bool = False
    ) -> Sequence[StoredWorkspace]:
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, org_id, name, slug, settings, created_at, archived_at
                FROM workspaces
                WHERE org_id = $1 AND (archived_at IS NULL OR $2::boolean)
                ORDER BY name
                """,
                org_id,
                include_archived,
            )
        return tuple(_row_to_workspace(r) for r in rows)

    async def create_workspace(self, ws: NewWorkspace) -> StoredWorkspace:
        workspace_id = f"ws_{ULID()}"
        async with self._acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO workspaces (id, org_id, name, slug, settings)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    RETURNING id, org_id, name, slug, settings, created_at, archived_at
                    """,
                    workspace_id,
                    ws.org_id,
                    ws.name,
                    ws.slug,
                    json.dumps(dict(ws.settings)),
                )
            except asyncpg.UniqueViolationError as e:
                raise IntegrityError(
                    f"Workspace slug {ws.slug!r} already exists for org_id={ws.org_id!r}"
                ) from e
        return _row_to_workspace(row)

    async def update_workspace(
        self, workspace_id: str, org_id: str, patch: WorkspacePatch
    ) -> Optional[StoredWorkspace]:
        sets: list[str] = []
        params: list = [workspace_id, org_id]

        if patch.name is not None:
            params.append(patch.name)
            sets.append(f"name = ${len(params)}")
        if patch.settings is not None:
            params.append(json.dumps(dict(patch.settings)))
            sets.append(f"settings = ${len(params)}::jsonb")

        if not sets:
            raise ValueError(
                "update_workspace called with empty patch — caller must ensure at least one field is set"
            )

        sql = (
            "UPDATE workspaces "
            f"SET {', '.join(sets)} "
            "WHERE id = $1 AND org_id = $2 "
            "RETURNING id, org_id, name, slug, settings, created_at, archived_at"
        )
        async with self._acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        return _row_to_workspace(row) if row else None

    async def archive_workspace(self, workspace_id: str, org_id: str) -> bool:
        async with self._acquire() as conn:
            result = await conn.execute(
                "UPDATE workspaces SET archived_at = now() WHERE id = $1 AND org_id = $2 AND archived_at IS NULL",
                workspace_id,
                org_id,
            )
        return result.endswith(" 1")

    async def add_workspace_member(self, member: NewMember) -> StoredMember:
        member_id = f"wsm_{ULID()}"
        async with self._acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO workspace_members (id, workspace_id, user_id, role)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id, workspace_id, user_id, role, invited_at, accepted_at
                    """,
                    member_id,
                    member.workspace_id,
                    member.user_id,
                    member.role,
                )
            except asyncpg.ForeignKeyViolationError as e:
                raise IntegrityError(
                    f"workspace_id {member.workspace_id!r} does not exist"
                ) from e
        return _row_to_member(row)

    async def list_workspace_members(
        self, workspace_id: str
    ) -> Sequence[StoredMember]:
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, workspace_id, user_id, role, invited_at, accepted_at
                FROM workspace_members
                WHERE workspace_id = $1
                ORDER BY invited_at
                """,
                workspace_id,
            )
        return tuple(_row_to_member(r) for r in rows)

    async def update_workspace_member_role(
        self, workspace_id: str, user_id: str, role: str
    ) -> Optional[StoredMember]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE workspace_members
                SET role = $1
                WHERE workspace_id = $2 AND user_id = $3
                RETURNING id, workspace_id, user_id, role, invited_at, accepted_at
                """,
                role,
                workspace_id,
                user_id,
            )
        return _row_to_member(row) if row else None

    async def remove_workspace_member(
        self, workspace_id: str, user_id: str
    ) -> bool:
        async with self._acquire() as conn:
            result = await conn.execute(
                "DELETE FROM workspace_members WHERE workspace_id = $1 AND user_id = $2",
                workspace_id,
                user_id,
            )
        return result.endswith(" 1")

    # ── AuthOps ───────────────────────────────────────────────────────

    async def get_api_key(self, key_id: str) -> Optional[StoredApiKey]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, name, key_hash, key_prefix, project, is_root,
                       workspace_id, revoked_at, created_at, last_used_at
                FROM api_keys
                WHERE id = $1
                """,
                key_id,
            )
        return _row_to_api_key(row) if row else None

    async def list_api_keys(self, org_id: str) -> Sequence[StoredApiKey]:
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, org_id, name, key_hash, key_prefix, project, is_root,
                       workspace_id, revoked_at, created_at, last_used_at
                FROM api_keys
                WHERE org_id = $1
                ORDER BY created_at
                """,
                org_id,
            )
        return tuple(_row_to_api_key(r) for r in rows)

    async def create_api_key(self, key: NewApiKey) -> StoredApiKey:
        key_id = f"key_{ULID()}"
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO api_keys
                    (id, org_id, name, key_hash, key_prefix, project, is_root, workspace_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, org_id, name, key_hash, key_prefix, project, is_root,
                          workspace_id, revoked_at, created_at, last_used_at
                """,
                key_id,
                key.org_id,
                key.name,
                key.key_hash,
                key.key_prefix,
                key.project,
                key.is_root,
                key.workspace_id,
            )
        return _row_to_api_key(row)

    async def revoke_api_key(self, key_id: str) -> Optional[StoredApiKey]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE api_keys
                SET revoked_at = now()
                WHERE id = $1 AND revoked_at IS NULL
                RETURNING id, org_id, name, key_hash, key_prefix, project, is_root,
                          workspace_id, revoked_at, created_at, last_used_at
                """,
                key_id,
            )
        return _row_to_api_key(row) if row else None

    async def count_active_root_keys(self, org_id: str) -> int:
        async with self._acquire() as conn:
            result = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM api_keys
                WHERE org_id = $1 AND is_root = TRUE AND revoked_at IS NULL
                """,
                org_id,
            )
        return int(result or 0)

    # ── AnalyticsOps ─────────────────────────────────────────────────

    async def record_retrieval_event(self, event: NewRetrievalEvent) -> None:
        async with self._acquire() as conn:
            await conn.execute(
                """
                INSERT INTO retrieval_events
                    (org_id, query, results_count, scores, memory_ids,
                     avg_score, max_score, min_score_threshold, query_time_ms,
                     project, format)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10, $11)
                """,
                event.org_id,
                event.query,
                event.results_count,
                json.dumps(list(event.scores)),
                json.dumps(list(event.memory_ids)),
                event.avg_score,
                event.max_score,
                event.min_score_threshold,
                event.query_time_ms,
                event.project,
                event.format,
            )

    async def record_memory_access(
        self, org_id: str, memory_id: str
    ) -> Optional[StoredMemory]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE memories
                SET access_count = COALESCE(access_count, 0) + 1,
                    last_accessed_at = now(),
                    importance_score = (
                        confidence
                        * GREATEST(0.1, 1.0 + (upvotes - downvotes) * 0.1)
                        * (1.0 + ln(COALESCE(access_count, 0) + 2) / ln(2) * 0.1)
                    ),
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
        return _row_to_stored(row) if row else None

    async def list_recent_session_snapshots(
        self,
        org_id: str,
        *,
        project: Optional[str] = None,
        exclude_ids: Sequence[str] = (),
        limit: int = 3,
    ) -> Sequence[StoredMemory]:
        where: list[str] = [
            "org_id = $1",
            "(expires_at IS NULL OR expires_at > now())",
            "meta->>'type' = 'session_snapshot'",
            "created_at > now() - interval '24 hours'",
        ]
        params: list[Any] = [org_id]

        if project is not None:
            params.append(project)
            where.append(f"project = ${len(params)}")
        if exclude_ids:
            params.append(list(exclude_ids))
            where.append(f"id != ALL(${len(params)})")

        params.append(limit)
        limit_idx = len(params)

        sql = (
            "SELECT id, org_id, content, context, tags, confidence, source, "
            "project, created_at, updated_at, expires_at, upvotes, downvotes, "
            "meta, importance_score, access_count, last_accessed_at "
            "FROM memories "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY created_at DESC LIMIT ${limit_idx}"
        )
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return tuple(_row_to_stored(r) for r in rows)

    # ── RecommendationOps ─────────────────────────────────────────────

    async def get_recommendation_config(
        self,
        *,
        workspace_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[StoredRecommendationConfig]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, workspace_id, agent_id, aggressiveness, enabled,
                       max_suggestions, cooldown_minutes, updated_at
                FROM recommendation_config
                WHERE workspace_id IS NOT DISTINCT FROM $1
                  AND agent_id IS NOT DISTINCT FROM $2
                LIMIT 1
                """,
                workspace_id,
                agent_id,
            )
        return _row_to_recommendation_config(row) if row else None

    async def upsert_recommendation_config(
        self,
        *,
        workspace_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        aggressiveness: Optional[float] = None,
        enabled: Optional[bool] = None,
        max_suggestions: Optional[int] = None,
        cooldown_minutes: Optional[int] = None,
    ) -> StoredRecommendationConfig:
        config_id = f"reccfg_{ULID()}"
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO recommendation_config
                    (id, workspace_id, agent_id, aggressiveness, enabled,
                     max_suggestions, cooldown_minutes, updated_at)
                VALUES ($1, $2, $3,
                        COALESCE($4::real, 0.5),
                        COALESCE($5::boolean, TRUE),
                        COALESCE($6::integer, 3),
                        COALESCE($7::integer, 15),
                        now())
                ON CONFLICT (COALESCE(workspace_id, '__null__'), COALESCE(agent_id, '__null__')) DO UPDATE
                SET aggressiveness   = COALESCE($4::real,    recommendation_config.aggressiveness),
                    enabled          = COALESCE($5::boolean, recommendation_config.enabled),
                    max_suggestions  = COALESCE($6::integer, recommendation_config.max_suggestions),
                    cooldown_minutes = COALESCE($7::integer, recommendation_config.cooldown_minutes),
                    updated_at       = now()
                RETURNING id, workspace_id, agent_id, aggressiveness, enabled,
                          max_suggestions, cooldown_minutes, updated_at
                """,
                config_id,
                workspace_id,
                agent_id,
                aggressiveness,
                enabled,
                max_suggestions,
                cooldown_minutes,
            )
        return _row_to_recommendation_config(row)

    async def record_recommendation_feedback(
        self,
        feedback: "NewRecommendationFeedback",
    ) -> None:
        feedback_id = f"recfb_{ULID()}"
        async with self._acquire() as conn:
            await conn.execute(
                """
                INSERT INTO recommendation_feedback
                    (id, org_id, workspace_id, memory_id, actor_id, signal, feedback, context_hash)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                feedback_id,
                feedback.org_id,
                feedback.workspace_id,
                feedback.memory_id,
                feedback.actor_id,
                feedback.signal,
                feedback.feedback,
                feedback.context_hash,
            )

    async def list_candidate_memories_for_recommendation(
        self,
        org_id: str,
        *,
        limit: int = 500,
    ) -> "Sequence[RecommendationCandidate]":
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, content, embedding, meta, created_at, access_count, last_accessed_at
                FROM memories
                WHERE org_id = $1 AND embedding IS NOT NULL
                ORDER BY importance_score DESC NULLS LAST
                LIMIT $2
                """,
                org_id,
                limit,
            )
        return tuple(_row_to_recommendation_candidate(r) for r in rows)

    # ── ConversationOps ────────────────────────────────────────────────

    async def create_conversation_job(self, job: NewConversationJob) -> StoredConversationJob:
        job_id = str(ULID())
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO conversation_jobs
                    (id, org_id, status, message_count, messages_json,
                     user_id, session_id, project, created_at)
                VALUES ($1, $2, 'accepted', $3, $4, $5, $6, $7, now())
                RETURNING id, org_id, status, message_count, messages_json,
                          user_id, session_id, project, memory_ids,
                          memories_extracted, duplicates_skipped, error,
                          processing_time_ms, created_at, completed_at
                """,
                job_id,
                job.org_id,
                job.message_count,
                job.messages_json,
                job.user_id,
                job.session_id,
                job.project,
            )
        return _row_to_conversation_job(row)

    async def get_conversation_job(
        self, job_id: str, org_id: str
    ) -> Optional[StoredConversationJob]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, status, message_count, messages_json,
                       user_id, session_id, project, memory_ids,
                       memories_extracted, duplicates_skipped, error,
                       processing_time_ms, created_at, completed_at
                FROM conversation_jobs
                WHERE id = $1 AND org_id = $2
                """,
                job_id,
                org_id,
            )
        return _row_to_conversation_job(row) if row else None

    async def mark_conversation_job_processing(
        self, job_id: str
    ) -> Optional[StoredConversationJob]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE conversation_jobs SET status = 'processing'
                WHERE id = $1
                RETURNING id, org_id, status, message_count, messages_json,
                          user_id, session_id, project, memory_ids,
                          memories_extracted, duplicates_skipped, error,
                          processing_time_ms, created_at, completed_at
                """,
                job_id,
            )
        return _row_to_conversation_job(row) if row else None

    async def complete_conversation_job(
        self,
        job_id: str,
        *,
        memory_ids: "Sequence[str]",
        memories_extracted: int,
        duplicates_skipped: int,
        processing_time_ms: int,
    ) -> None:
        async with self._acquire() as conn:
            await conn.execute(
                """
                UPDATE conversation_jobs SET
                    status = 'completed',
                    memory_ids = $2,
                    memories_extracted = $3,
                    duplicates_skipped = $4,
                    processing_time_ms = $5,
                    completed_at = now()
                WHERE id = $1
                """,
                job_id,
                json.dumps(list(memory_ids)),
                memories_extracted,
                duplicates_skipped,
                processing_time_ms,
            )

    async def fail_conversation_job(
        self,
        job_id: str,
        *,
        error: str,
        processing_time_ms: int,
    ) -> None:
        async with self._acquire() as conn:
            await conn.execute(
                """
                UPDATE conversation_jobs SET
                    status = 'failed',
                    error = $2,
                    processing_time_ms = $3,
                    completed_at = now()
                WHERE id = $1
                """,
                job_id,
                error,
                processing_time_ms,
            )

    # ── AuditOps ─────────────────────────────────────────────────────

    async def query_audit_log(
        self,
        *,
        org_id: str,
        workspace_id: Optional[str] = None,
        action: Optional[str] = None,
        actor_id: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> "Sequence[StoredAuditEntry]":
        where: list[str] = ["org_id = $1"]
        params: list[Any] = [org_id]

        if workspace_id is not None:
            params.append(workspace_id)
            where.append(f"workspace_id = ${len(params)}")
        if action is not None:
            params.append(action)
            where.append(f"action = ${len(params)}")
        if actor_id is not None:
            params.append(actor_id)
            where.append(f"actor_id = ${len(params)}")
        if since is not None:
            # Accept both ISO-string and datetime; asyncpg needs a datetime object.
            if isinstance(since, str):
                from datetime import timezone as _tz
                since_dt = datetime.fromisoformat(since)
                if since_dt.tzinfo is None:
                    since_dt = since_dt.replace(tzinfo=_tz.utc)
            else:
                since_dt = since
            params.append(since_dt)
            where.append(f"created_at >= ${len(params)}")

        params.append(limit)
        limit_idx = len(params)

        sql = (
            "SELECT id, org_id, workspace_id, actor_id, actor_type, action, "
            "resource_type, resource_id, metadata, ip_address, created_at "
            "FROM audit_log "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY created_at DESC "
            f"LIMIT ${limit_idx}"
        )
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return tuple(_row_to_audit_entry(r) for r in rows)

    async def compute_retrieval_analytics(
        self,
        *,
        org_id: str,
        days: int,
        project: Optional[str] = None,
    ) -> "RetrievalAnalyticsResult":
        from lore.persistence.types import (
            DailyStatRow,
            ScoreDistributionBucket,
            TopQueryRow,
        )

        # Build shared WHERE clause (retrieval_events)
        where_parts = ["org_id = $1", "created_at >= now() - make_interval(days => $2)"]
        params: list[Any] = [org_id, days]

        if project is not None:
            params.append(project)
            where_parts.append(f"project = ${len(params)}")

        where_sql = " AND ".join(where_parts)

        async with self._acquire() as conn:
            # ── Summary stats ──────────────────────────────────────
            summary = await conn.fetchrow(f"""
                SELECT
                    COUNT(*)::int AS total_queries,
                    COUNT(*) FILTER (WHERE results_count > 0)::int AS queries_with_results,
                    COUNT(*) FILTER (WHERE results_count = 0)::int AS queries_empty,
                    AVG(results_count)::float AS avg_results,
                    AVG(avg_score)::float AS avg_score,
                    AVG(max_score)::float AS avg_max_score,
                    AVG(query_time_ms)::float AS avg_latency_ms
                FROM retrieval_events
                WHERE {where_sql}
            """, *params)

            total = summary["total_queries"] or 0

            # ── P95 latency ────────────────────────────────────────
            p95_row = await conn.fetchrow(f"""
                SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY query_time_ms) AS p95
                FROM retrieval_events
                WHERE {where_sql}
            """, *params)
            p95 = round(float(p95_row["p95"]), 2) if p95_row and p95_row["p95"] is not None else None

            # ── Score distribution ─────────────────────────────────
            score_dist_rows = await conn.fetch(f"""
                SELECT bucket, COUNT(*)::int AS cnt
                FROM (
                    SELECT
                        CASE
                            WHEN s::float < 0.3 THEN '0.0-0.3'
                            WHEN s::float < 0.5 THEN '0.3-0.5'
                            WHEN s::float < 0.7 THEN '0.5-0.7'
                            WHEN s::float < 0.9 THEN '0.7-0.9'
                            ELSE '0.9-1.0'
                        END AS bucket
                    FROM retrieval_events,
                         jsonb_array_elements_text(scores) AS s
                    WHERE {where_sql}
                ) sub
                GROUP BY bucket
                ORDER BY bucket
            """, *params)

            buckets_order = ["0.0-0.3", "0.3-0.5", "0.5-0.7", "0.7-0.9", "0.9-1.0"]
            bucket_counts: dict[str, int] = {r["bucket"]: r["cnt"] for r in score_dist_rows}
            score_distribution = [
                ScoreDistributionBucket(bucket=b, count=bucket_counts.get(b, 0))
                for b in buckets_order
            ]

            # ── Top queries ────────────────────────────────────────
            top_rows = await conn.fetch(f"""
                SELECT query, COUNT(*)::int AS cnt, AVG(avg_score)::float AS avg_s
                FROM retrieval_events
                WHERE {where_sql}
                GROUP BY query
                ORDER BY cnt DESC
                LIMIT 10
            """, *params)
            top_queries = [
                TopQueryRow(
                    query=r["query"],
                    count=r["cnt"],
                    avg_score=round(r["avg_s"], 4) if r["avg_s"] else None,
                )
                for r in top_rows
            ]

            # ── Unique memories retrieved ──────────────────────────
            unique_row = await conn.fetchrow(f"""
                SELECT COUNT(DISTINCT mid)::int AS unique_count
                FROM retrieval_events,
                     jsonb_array_elements_text(memory_ids) AS mid
                WHERE {where_sql}
            """, *params)
            unique_memories = unique_row["unique_count"] if unique_row else 0

            # ── Total memories (no date filter) ───────────────────
            mem_where_parts = ["org_id = $1"]
            mem_params: list[Any] = [org_id]
            if project is not None:
                mem_params.append(project)
                mem_where_parts.append(f"project = ${len(mem_params)}")
            mem_where_sql = " AND ".join(mem_where_parts)

            total_memories_row = await conn.fetchrow(
                f"SELECT COUNT(*)::int AS total FROM memories WHERE {mem_where_sql}",
                *mem_params,
            )
            total_memories = total_memories_row["total"] if total_memories_row else 0

            # ── Daily stats ────────────────────────────────────────
            daily_rows = await conn.fetch(f"""
                SELECT
                    created_at::date AS day,
                    COUNT(*)::int AS queries,
                    AVG(avg_score)::float AS avg_s,
                    (COUNT(*) FILTER (WHERE results_count > 0))::float / GREATEST(COUNT(*), 1) AS hit_rate
                FROM retrieval_events
                WHERE {where_sql}
                GROUP BY day
                ORDER BY day DESC
            """, *params)
            daily_stats = [
                DailyStatRow(
                    date=str(r["day"]),
                    queries=r["queries"],
                    avg_score=round(r["avg_s"], 4) if r["avg_s"] else None,
                    hit_rate=round(float(r["hit_rate"]), 4) if r["hit_rate"] is not None else 0.0,
                )
                for r in daily_rows
            ]

        return RetrievalAnalyticsResult(
            total_queries=total,
            queries_with_results=summary["queries_with_results"] or 0,
            queries_empty=summary["queries_empty"] or 0,
            avg_results_per_query=round(float(summary["avg_results"] or 0), 2),
            avg_score=round(float(summary["avg_score"]), 4) if summary["avg_score"] else None,
            avg_max_score=round(float(summary["avg_max_score"]), 4) if summary["avg_max_score"] else None,
            avg_latency_ms=round(float(summary["avg_latency_ms"]), 2) if summary["avg_latency_ms"] else None,
            p95_latency_ms=p95,
            score_distribution=score_distribution,
            top_queries=top_queries,
            unique_memories_retrieved=unique_memories,
            total_memories=total_memories,
            daily_stats=daily_stats,
        )

    # ── RetentionOps ────────────────────────────────────────────────

    async def list_retention_policies(self, org_id: str) -> "Sequence[StoredRetentionPolicy]":
        async with self._acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, org_id, name, retention_window, snapshot_schedule,
                       encryption_required, max_snapshots, is_active, created_at, updated_at
                FROM retention_policies
                WHERE org_id = $1
                ORDER BY name
                """,
                org_id,
            )
        return tuple(_row_to_retention_policy(r) for r in rows)

    async def get_retention_policy(
        self, policy_id: str, org_id: str
    ) -> "Optional[StoredRetentionPolicy]":
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, name, retention_window, snapshot_schedule,
                       encryption_required, max_snapshots, is_active, created_at, updated_at
                FROM retention_policies
                WHERE id = $1 AND org_id = $2
                """,
                policy_id,
                org_id,
            )
        return _row_to_retention_policy(row) if row else None

    async def create_retention_policy(
        self, policy: "NewRetentionPolicy"
    ) -> "StoredRetentionPolicy":
        policy_id = f"retpol_{ULID()}"
        try:
            async with self._acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO retention_policies
                        (id, org_id, name, retention_window, snapshot_schedule,
                         encryption_required, max_snapshots, is_active)
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
                    RETURNING id, org_id, name, retention_window, snapshot_schedule,
                              encryption_required, max_snapshots, is_active,
                              created_at, updated_at
                    """,
                    policy_id,
                    policy.org_id,
                    policy.name,
                    json.dumps(dict(policy.retention_window)),
                    policy.snapshot_schedule,
                    policy.encryption_required,
                    policy.max_snapshots,
                    policy.is_active,
                )
        except Exception as e:
            if asyncpg is not None and isinstance(e, asyncpg.UniqueViolationError):
                raise IntegrityError(
                    f"Retention policy {policy.name!r} already exists for org_id={policy.org_id!r}"
                ) from e
            raise
        return _row_to_retention_policy(row)

    async def update_retention_policy(
        self,
        policy_id: str,
        org_id: str,
        patch: "RetentionPolicyPatch",
    ) -> "Optional[StoredRetentionPolicy]":
        fields: list[str] = []
        params: list[Any] = []

        if patch.name is not None:
            params.append(patch.name)
            fields.append(f"name = ${len(params)}")
        if patch.retention_window is not None:
            params.append(json.dumps(dict(patch.retention_window)))
            fields.append(f"retention_window = ${len(params)}::jsonb")
        if patch.snapshot_schedule is not None:
            params.append(patch.snapshot_schedule)
            fields.append(f"snapshot_schedule = ${len(params)}")
        if patch.encryption_required is not None:
            params.append(patch.encryption_required)
            fields.append(f"encryption_required = ${len(params)}")
        if patch.max_snapshots is not None:
            params.append(patch.max_snapshots)
            fields.append(f"max_snapshots = ${len(params)}")
        if patch.is_active is not None:
            params.append(patch.is_active)
            fields.append(f"is_active = ${len(params)}")

        if not fields:
            raise ValueError("update_retention_policy called with empty patch")

        fields.append("updated_at = now()")
        params.append(policy_id)
        id_idx = len(params)
        params.append(org_id)
        org_idx = len(params)

        sql = (
            "UPDATE retention_policies "
            f"SET {', '.join(fields)} "
            f"WHERE id = ${id_idx} AND org_id = ${org_idx} "
            "RETURNING id, org_id, name, retention_window, snapshot_schedule, "
            "encryption_required, max_snapshots, is_active, created_at, updated_at"
        )
        async with self._acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        return _row_to_retention_policy(row) if row else None

    async def delete_retention_policy(self, policy_id: str, org_id: str) -> bool:
        async with self._acquire() as conn:
            result = await conn.execute(
                "DELETE FROM retention_policies WHERE id = $1 AND org_id = $2",
                policy_id,
                org_id,
            )
        return result.endswith(" 1")

    async def get_latest_snapshot_for_policy(
        self, policy_id: str, org_id: str
    ) -> "Optional[StoredSnapshotMetadata]":
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, org_id, policy_id, name, path, size_bytes, memory_count, encrypted, created_at "
                "FROM snapshot_metadata "
                "WHERE policy_id = $1 AND org_id = $2 "
                "ORDER BY created_at DESC "
                "LIMIT 1",
                policy_id,
                org_id,
            )
        return _row_to_snapshot_metadata(row) if row else None

    async def count_snapshots_for_policy(self, policy_id: str) -> int:
        async with self._acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*)::int FROM snapshot_metadata WHERE policy_id = $1",
                policy_id,
            )

    async def record_drill_result(self, drill: "NewDrillResult") -> "StoredDrillResult":
        drill_id = f"drill_{ULID()}"
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO restore_drill_results "
                "(id, org_id, snapshot_id, snapshot_name, started_at, completed_at, "
                "recovery_time_ms, memories_restored, status, error) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
                "RETURNING *",
                drill_id,
                drill.org_id,
                drill.snapshot_id,
                drill.snapshot_name,
                drill.started_at,
                drill.completed_at,
                drill.recovery_time_ms,
                drill.memories_restored,
                drill.status,
                drill.error,
            )
        return _row_to_drill_result(row)

    async def list_drill_results_for_policy(
        self, policy_id: str, org_id: str, *, limit: int = 20
    ) -> "Sequence[StoredDrillResult]":
        async with self._acquire() as conn:
            rows = await conn.fetch(
                "SELECT r.id, r.org_id, r.snapshot_id, r.snapshot_name, r.started_at, "
                "r.completed_at, r.recovery_time_ms, r.memories_restored, "
                "r.status, r.error, r.created_at "
                "FROM restore_drill_results r "
                "JOIN snapshot_metadata s ON s.id = r.snapshot_id "
                "WHERE s.policy_id = $1 AND r.org_id = $2 "
                "ORDER BY r.created_at DESC "
                "LIMIT $3",
                policy_id,
                org_id,
                limit,
            )
        return tuple(_row_to_drill_result(r) for r in rows)

    async def get_latest_drill_result(self, org_id: str) -> "Optional[StoredDrillResult]":
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM restore_drill_results WHERE org_id = $1 ORDER BY created_at DESC LIMIT 1",
                org_id,
            )
        return _row_to_drill_result(row) if row else None

    # ── SloOps ────────────────────────────────────────────────────────────────

    async def list_slo_definitions(
        self, org_id: "Optional[str]" = None
    ) -> "Sequence[StoredSloDefinition]":
        async with self._acquire() as conn:
            if org_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT id, org_id, name, metric, operator, threshold,
                           window_minutes, enabled, alert_channels, created_at, updated_at
                    FROM slo_definitions
                    WHERE org_id = $1
                    ORDER BY created_at DESC
                    """,
                    org_id,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, org_id, name, metric, operator, threshold,
                           window_minutes, enabled, alert_channels, created_at, updated_at
                    FROM slo_definitions
                    ORDER BY created_at DESC
                    """
                )
        return tuple(_row_to_slo_definition(r) for r in rows)

    async def get_slo_definition(
        self, slo_id: str, org_id: str
    ) -> "Optional[StoredSloDefinition]":
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, name, metric, operator, threshold,
                       window_minutes, enabled, alert_channels, created_at, updated_at
                FROM slo_definitions
                WHERE id = $1 AND org_id = $2
                """,
                slo_id,
                org_id,
            )
        return _row_to_slo_definition(row) if row else None

    async def create_slo_definition(
        self, slo: "NewSloDefinition"
    ) -> "StoredSloDefinition":
        slo_id = f"slo_{ULID()}"
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO slo_definitions
                    (id, org_id, name, metric, operator, threshold,
                     window_minutes, enabled, alert_channels)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                RETURNING id, org_id, name, metric, operator, threshold,
                          window_minutes, enabled, alert_channels, created_at, updated_at
                """,
                slo_id,
                slo.org_id,
                slo.name,
                slo.metric,
                slo.operator,
                slo.threshold,
                slo.window_minutes,
                slo.enabled,
                json.dumps(list(slo.alert_channels)),
            )
        return _row_to_slo_definition(row)

    async def update_slo_definition(
        self,
        slo_id: str,
        org_id: str,
        patch: "SloDefinitionPatch",
    ) -> "Optional[StoredSloDefinition]":
        fields: list[str] = []
        params: list[Any] = []

        if patch.name is not None:
            params.append(patch.name)
            fields.append(f"name = ${len(params)}")
        if patch.metric is not None:
            params.append(patch.metric)
            fields.append(f"metric = ${len(params)}")
        if patch.operator is not None:
            params.append(patch.operator)
            fields.append(f"operator = ${len(params)}")
        if patch.threshold is not None:
            params.append(patch.threshold)
            fields.append(f"threshold = ${len(params)}")
        if patch.window_minutes is not None:
            params.append(patch.window_minutes)
            fields.append(f"window_minutes = ${len(params)}")
        if patch.enabled is not None:
            params.append(patch.enabled)
            fields.append(f"enabled = ${len(params)}")
        if patch.alert_channels is not None:
            params.append(json.dumps(list(patch.alert_channels)))
            fields.append(f"alert_channels = ${len(params)}::jsonb")

        if not fields:
            raise ValueError("update_slo_definition called with empty patch")

        fields.append("updated_at = now()")
        params.append(slo_id)
        id_idx = len(params)
        params.append(org_id)
        org_idx = len(params)

        sql = (
            "UPDATE slo_definitions "
            f"SET {', '.join(fields)} "
            f"WHERE id = ${id_idx} AND org_id = ${org_idx} "
            "RETURNING id, org_id, name, metric, operator, threshold, "
            "window_minutes, enabled, alert_channels, created_at, updated_at"
        )
        async with self._acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        return _row_to_slo_definition(row) if row else None

    async def delete_slo_definition(self, slo_id: str, org_id: str) -> bool:
        async with self._acquire() as conn:
            result = await conn.execute(
                "DELETE FROM slo_definitions WHERE id = $1 AND org_id = $2",
                slo_id,
                org_id,
            )
        return result.endswith(" 1")

    async def list_slo_alerts(
        self,
        *,
        slo_id: "Optional[str]" = None,
        limit: int = 50,
    ) -> "Sequence[StoredSloAlert]":
        async with self._acquire() as conn:
            if slo_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT a.id, a.org_id, a.slo_id, a.metric_value, a.threshold,
                           a.status, a.dispatched_to, a.created_at
                    FROM slo_alerts a
                    WHERE a.slo_id = $1
                    ORDER BY a.created_at DESC
                    LIMIT $2
                    """,
                    slo_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT a.id, a.org_id, a.slo_id, a.metric_value, a.threshold,
                           a.status, a.dispatched_to, a.created_at
                    FROM slo_alerts a
                    ORDER BY a.created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
        return tuple(_row_to_slo_alert(r) for r in rows)

    async def record_slo_alert(self, alert: "NewSloAlert") -> "StoredSloAlert":
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO slo_alerts
                    (org_id, slo_id, metric_value, threshold, status, dispatched_to)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING id, org_id, slo_id, metric_value, threshold, status, dispatched_to, created_at
                """,
                alert.org_id,
                alert.slo_id,
                alert.metric_value,
                alert.threshold,
                alert.status,
                json.dumps(list(alert.dispatched_to)),
            )
        return _row_to_slo_alert(row)

    async def compute_metric_value(
        self,
        *,
        org_id: str,
        metric: str,
        window_minutes: int,
    ) -> "Optional[float]":
        if metric not in _METRIC_SQL:
            raise ValueError(f"Unknown metric: {metric}")
        metric_sql = _METRIC_SQL[metric]
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {metric_sql} FROM retrieval_events "
                f"WHERE org_id = $1 AND created_at >= now() - make_interval(mins => $2)",
                org_id,
                window_minutes,
            )
        if row and row["value"] is not None:
            return round(float(row["value"]), 4)
        return None

    async def compute_metric_timeseries(
        self,
        *,
        org_id: str,
        metric: str,
        window_hours: int,
        bucket_minutes: int,
    ) -> "Sequence[TimeseriesPoint]":
        if metric not in _METRIC_SQL:
            raise ValueError(f"Unknown metric: {metric}")
        metric_sql = _METRIC_SQL[metric]
        async with self._acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT
                        date_trunc('hour', created_at) +
                        (EXTRACT(minute FROM created_at)::int / $3 * $3) * interval '1 minute'
                        AS bucket,
                        {metric_sql}
                    FROM retrieval_events
                    WHERE org_id = $1
                      AND created_at >= now() - make_interval(hours => $2)
                    GROUP BY bucket
                    ORDER BY bucket""",
                org_id,
                window_hours,
                bucket_minutes,
            )
        return tuple(
            TimeseriesPoint(
                timestamp=r["bucket"],
                value=round(float(r["value"]), 4) if r["value"] is not None else None,
            )
            for r in rows
        )


class _BoundConn:
    """Async context manager that returns a pre-acquired connection without closing it."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False
