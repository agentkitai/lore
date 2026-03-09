"""Memory CRUD endpoints for Lore Cloud Server (v0.9.0+).

Uses the new `memories` table with `content` and `context` columns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

try:
    from ulid import ULID
except ImportError:
    raise ImportError("python-ulid is required. Install with: pip install python-ulid")

from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_pool
from lore.server.models import (
    MemoryCreateRequest,
    MemoryCreateResponse,
    MemoryListResponse,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResult,
    MemoryUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/memories", tags=["memories"])

# Type-specific decay half-lives (days)
_HALF_LIFE_DEFAULT = 30


def _row_to_response(row: dict) -> MemoryResponse:
    """Convert a DB row to a MemoryResponse (no embedding)."""
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = json.loads(tags)
    meta = row.get("meta") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return MemoryResponse(
        id=row["id"],
        content=row["content"],
        context=row.get("context"),
        tags=tags,
        confidence=row["confidence"],
        source=row.get("source"),
        project=row.get("project"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row.get("expires_at"),
        upvotes=row.get("upvotes", 0),
        downvotes=row.get("downvotes", 0),
        meta=meta,
    )


def _scope_filter(auth: AuthContext) -> tuple[str, list]:
    """Build WHERE clause for org + project scoping."""
    if auth.project is not None:
        return "org_id = $1 AND project = $2", [auth.org_id, auth.project]
    return "org_id = $1", [auth.org_id]


# ── Enrichment helper ─────────────────────────────────────────────


async def _enrich_memory(memory_id: str, content: str, context: str | None) -> None:
    """Fire-and-forget enrichment for a newly created memory."""
    try:
        from lore.enrichment.llm import LLMClient
        from lore.enrichment.pipeline import EnrichmentPipeline

        model = os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")
        client = LLMClient(model=model)
        pipeline = EnrichmentPipeline(client)

        result = pipeline.enrich(content, context=context)
        if result is None:
            return

        # pipeline.enrich() returns a dict with enrichment data
        enrichment_data = result

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE memories SET
                       meta = jsonb_set(COALESCE(meta, '{}'::jsonb), '{enrichment}', $2::jsonb),
                       updated_at = now()
                   WHERE id = $1""",
                memory_id,
                json.dumps(enrichment_data),
            )

        # Generate embeddings for semantic retrieval
        try:
            from lore.embed import LocalEmbedder

            embedder = LocalEmbedder()
            embedding_vector = await asyncio.to_thread(embedder.embed, content)

            async with pool.acquire() as embed_conn:
                await embed_conn.execute(
                    """UPDATE memories SET embedding = $2::vector WHERE id = $1""",
                    memory_id,
                    json.dumps(embedding_vector),
                )
            logger.info("Generated embedding for memory %s", memory_id)
        except Exception as e:
            logger.warning("Failed to generate embedding for memory %s: %s", memory_id, e)

        logger.info("Enrichment complete for memory %s", memory_id)
    except Exception:
        logger.exception("Enrichment failed for memory %s", memory_id)


# ── Create ─────────────────────────────────────────────────────────


@router.post("", response_model=MemoryCreateResponse, status_code=201)
async def create_memory(
    body: MemoryCreateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> MemoryCreateResponse:
    """Create a new memory."""
    project = body.project
    if auth.project is not None:
        project = auth.project

    now = datetime.now(timezone.utc)
    memory_id = str(ULID())

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO memories
               (id, org_id, content, context, tags, confidence,
                source, project, embedding, created_at, updated_at, expires_at,
                upvotes, downvotes, meta)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9,
                       $10, $11, $12, $13, $14, $15::jsonb)""",
            memory_id,
            auth.org_id,
            body.content,
            body.context,
            json.dumps(body.tags),
            body.confidence,
            body.source,
            project,
            json.dumps(body.embedding) if body.embedding else None,
            now,
            now,
            body.expires_at,
            0,
            0,
            json.dumps(body.meta),
        )

    # Fire-and-forget enrichment
    enrich = body.enrich
    if enrich is None:
        enrich = os.environ.get("LORE_ENRICHMENT_ENABLED", "").lower() in ("true", "1", "yes")
    if enrich:
        asyncio.create_task(_enrich_memory(memory_id, body.content, body.context))

    return MemoryCreateResponse(id=memory_id)


# ── Search ─────────────────────────────────────────────────────────


@router.post("/search", response_model=MemorySearchResponse)
async def search_memories(
    body: MemorySearchRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> MemorySearchResponse:
    """Semantic search with multiplicative scoring."""
    where_parts: list[str] = ["org_id = $1"]
    params: list = [auth.org_id]

    project = body.project
    if auth.project is not None:
        project = auth.project
    if project is not None:
        params.append(project)
        where_parts.append(f"project = ${len(params)}")

    if body.tags:
        params.append(json.dumps(body.tags))
        where_parts.append(f"tags @> ${len(params)}::jsonb")

    where_parts.append("(expires_at IS NULL OR expires_at > now())")
    where_parts.append("embedding IS NOT NULL")
    where_sql = " AND ".join(where_parts)

    params.append(json.dumps(body.embedding))
    emb_idx = len(params)
    params.append(body.limit)
    limit_idx = len(params)

    query = f"""
        SELECT id, content, context, tags, confidence,
               source, project, created_at, updated_at, expires_at,
               upvotes, downvotes, meta,
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
                   / (CASE meta->>'type'
                       WHEN 'code' THEN 14
                       WHEN 'note' THEN 21
                       WHEN 'lesson' THEN 30
                       WHEN 'convention' THEN 60
                       ELSE {_HALF_LIFE_DEFAULT}
                     END)
               )
               AS score
        FROM memories
        WHERE {where_sql}
        ORDER BY score DESC
        LIMIT ${limit_idx}
    """

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    results = []
    for r in rows:
        rd = dict(r)
        score = float(rd.pop("score", 0.0))
        rd.pop("importance_score", None)
        rd.pop("access_count", None)
        rd.pop("last_accessed_at", None)
        if score < body.min_confidence:
            continue
        mem_resp = _row_to_response(rd)
        results.append(MemorySearchResult(
            **mem_resp.model_dump(),
            score=round(max(score, 0.0), 6),
        ))

    return MemorySearchResponse(memories=results)


# ── Access tracking ────────────────────────────────────────────────


@router.post("/{memory_id}/access", status_code=200)
async def record_access(
    memory_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
    """Record an access event and recompute importance_score."""
    scope_sql, scope_params = _scope_filter(auth)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""UPDATE memories SET
                    access_count = COALESCE(access_count, 0) + 1,
                    last_accessed_at = now(),
                    importance_score = (
                        confidence
                        * GREATEST(0.1, 1.0 + (upvotes - downvotes) * 0.1)
                        * (1.0 + ln(COALESCE(access_count, 0) + 2) / ln(2) * 0.1)
                    ),
                    updated_at = now()
                WHERE id = ${len(scope_params) + 1} AND {scope_sql}
                RETURNING id, access_count, last_accessed_at, importance_score""",
            *scope_params,
            memory_id,
        )

    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {
        "id": row["id"],
        "access_count": row["access_count"],
        "last_accessed_at": row["last_accessed_at"].isoformat() if row["last_accessed_at"] else None,
        "importance_score": float(row["importance_score"]),
    }


# ── Read ───────────────────────────────────────────────────────────


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> MemoryResponse:
    """Get a single memory by ID."""
    scope_sql, scope_params = _scope_filter(auth)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""SELECT id, content, context, tags, confidence,
                       source, project, created_at, updated_at, expires_at,
                       upvotes, downvotes, meta
                FROM memories WHERE id = ${len(scope_params) + 1} AND {scope_sql}""",
            *scope_params,
            memory_id,
        )

    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    return _row_to_response(dict(row))


# ── Update ─────────────────────────────────────────────────────────


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: str,
    body: MemoryUpdateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> MemoryResponse:
    """Update a memory. Supports atomic upvote/downvote."""
    scope_sql, scope_params = _scope_filter(auth)

    set_parts: list[str] = []
    params: list = list(scope_params)

    if body.confidence is not None:
        params.append(body.confidence)
        set_parts.append(f"confidence = ${len(params)}")

    if body.tags is not None:
        params.append(json.dumps(body.tags))
        set_parts.append(f"tags = ${len(params)}::jsonb")

    if body.meta is not None:
        params.append(json.dumps(body.meta))
        set_parts.append(f"meta = ${len(params)}::jsonb")

    for vote_field in ("upvotes", "downvotes"):
        val = getattr(body, vote_field)
        if val is not None:
            if isinstance(val, str):
                delta = 1 if val == "+1" else -1
                params.append(delta)
                set_parts.append(f"{vote_field} = {vote_field} + ${len(params)}")
            else:
                params.append(val)
                set_parts.append(f"{vote_field} = ${len(params)}")

    if not set_parts:
        raise HTTPException(status_code=422, detail="No fields to update")

    set_parts.append("updated_at = now()")

    params.append(memory_id)
    id_idx = len(params)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""UPDATE memories SET {', '.join(set_parts)}
                WHERE id = ${id_idx} AND {scope_sql}
                RETURNING id, content, context, tags, confidence,
                          source, project, created_at, updated_at, expires_at,
                          upvotes, downvotes, meta""",
            *params,
        )

    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    return _row_to_response(dict(row))


# ── Delete ─────────────────────────────────────────────────────────


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    auth: AuthContext = Depends(require_role("writer", "admin")),
) -> None:
    """Delete a memory."""
    scope_sql, scope_params = _scope_filter(auth)

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            f"DELETE FROM memories WHERE id = ${len(scope_params) + 1} AND {scope_sql}",
            *scope_params,
            memory_id,
        )

    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Memory not found")


# ── List ───────────────────────────────────────────────────────────


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    project: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    min_reputation: Optional[int] = Query(None, alias="minReputation"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_auth_context),
) -> MemoryListResponse:
    """List memories with pagination."""
    where_parts: list[str] = ["org_id = $1"]
    params: list = [auth.org_id]

    if auth.project is not None:
        params.append(auth.project)
        where_parts.append(f"project = ${len(params)}")
    elif project is not None:
        params.append(project)
        where_parts.append(f"project = ${len(params)}")

    if query:
        params.append(f"%{query}%")
        idx = len(params)
        where_parts.append(f"(content ILIKE ${idx} OR context ILIKE ${idx})")

    if category:
        params.append(json.dumps([category]))
        where_parts.append(f"tags @> ${len(params)}::jsonb")

    if min_reputation is not None:
        params.append(min_reputation)
        where_parts.append(f"reputation_score >= ${len(params)}")

    where_sql = " AND ".join(where_parts)

    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM memories WHERE {where_sql}",
            *params,
        )

        params.append(limit)
        limit_idx = len(params)
        params.append(offset)
        offset_idx = len(params)

        rows = await conn.fetch(
            f"""SELECT id, content, context, tags, confidence,
                       source, project, created_at, updated_at, expires_at,
                       upvotes, downvotes, meta, reputation_score, quality_signals
                FROM memories WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ${limit_idx} OFFSET ${offset_idx}""",
            *params,
        )

    return MemoryListResponse(
        memories=[_row_to_response(dict(r)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
