"""Lesson CRUD endpoints for Lore Cloud Server."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

try:
    from ulid import ULID
except ImportError:
    raise ImportError("python-ulid is required. Install with: pip install python-ulid")

from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_pool
from lore.server.models import (
    LessonCreateRequest,
    LessonCreateResponse,
    LessonExportItem,
    LessonExportResponse,
    LessonImportRequest,
    LessonImportResponse,
    LessonListResponse,
    LessonResponse,
    LessonUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/lessons", tags=["lessons"])


def _row_to_response(row: dict) -> LessonResponse:
    """Convert a DB row to a LessonResponse (no embedding)."""
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = json.loads(tags)
    meta = row.get("meta") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return LessonResponse(
        id=row["id"],
        problem=row["problem"],
        resolution=row["resolution"],
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
    """Build WHERE clause for org + project scoping.

    Returns (sql_fragment, params) starting at $1.
    Project-scoped keys only see their project (returns 404 for others).
    """
    if auth.project is not None:
        return "org_id = $1 AND project = $2", [auth.org_id, auth.project]
    return "org_id = $1", [auth.org_id]


# ── Create ─────────────────────────────────────────────────────────


@router.post("", response_model=LessonCreateResponse, status_code=201)
async def create_lesson(
    body: LessonCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> LessonCreateResponse:
    """Create a new lesson."""
    # Project-scoped key: force project
    project = body.project
    if auth.project is not None:
        project = auth.project

    now = datetime.now(timezone.utc)
    lesson_id = str(ULID())

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO lessons
               (id, org_id, problem, resolution, context, tags, confidence,
                source, project, embedding, created_at, updated_at, expires_at,
                upvotes, downvotes, meta)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10,
                       $11, $12, $13, $14, $15, $16::jsonb)""",
            lesson_id,
            auth.org_id,
            body.problem,
            body.resolution,
            body.context,
            json.dumps(body.tags),
            body.confidence,
            body.source,
            project,
            json.dumps(body.embedding),
            now,
            now,
            body.expires_at,
            0,
            0,
            json.dumps(body.meta),
        )

    return LessonCreateResponse(id=lesson_id)


# ── Read ───────────────────────────────────────────────────────────


@router.get("/{lesson_id}", response_model=LessonResponse)
async def get_lesson(
    lesson_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> LessonResponse:
    """Get a single lesson by ID."""
    scope_sql, scope_params = _scope_filter(auth)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""SELECT id, problem, resolution, context, tags, confidence,
                       source, project, created_at, updated_at, expires_at,
                       upvotes, downvotes, meta
                FROM lessons WHERE id = ${len(scope_params) + 1} AND {scope_sql}""",
            *scope_params,
            lesson_id,
        )

    if row is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    return _row_to_response(dict(row))


# ── Update ─────────────────────────────────────────────────────────


@router.patch("/{lesson_id}", response_model=LessonResponse)
async def update_lesson(
    lesson_id: str,
    body: LessonUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> LessonResponse:
    """Update a lesson. Supports atomic upvote/downvote."""
    scope_sql, scope_params = _scope_filter(auth)
    base_idx = len(scope_params) + 1  # next param index

    # Build SET clause dynamically
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

    # Handle atomic vote increments
    for vote_field in ("upvotes", "downvotes"):
        val = getattr(body, vote_field)
        if val is not None:
            if isinstance(val, str):
                # "+1" or "-1" → atomic increment
                delta = 1 if val == "+1" else -1
                params.append(delta)
                set_parts.append(f"{vote_field} = {vote_field} + ${len(params)}")
            else:
                params.append(val)
                set_parts.append(f"{vote_field} = ${len(params)}")

    if not set_parts:
        raise HTTPException(status_code=422, detail="No fields to update")

    # Always update updated_at
    set_parts.append("updated_at = now()")

    params.append(lesson_id)
    id_idx = len(params)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""UPDATE lessons SET {', '.join(set_parts)}
                WHERE id = ${id_idx} AND {scope_sql}
                RETURNING id, problem, resolution, context, tags, confidence,
                          source, project, created_at, updated_at, expires_at,
                          upvotes, downvotes, meta""",
            *params,
        )

    if row is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    return _row_to_response(dict(row))


# ── Delete ─────────────────────────────────────────────────────────


@router.delete("/{lesson_id}", status_code=204)
async def delete_lesson(
    lesson_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> None:
    """Delete a lesson."""
    scope_sql, scope_params = _scope_filter(auth)

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            f"DELETE FROM lessons WHERE id = ${len(scope_params) + 1} AND {scope_sql}",
            *scope_params,
            lesson_id,
        )

    # asyncpg returns "DELETE N"
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Lesson not found")


# ── List ───────────────────────────────────────────────────────────


@router.get("", response_model=LessonListResponse)
async def list_lessons(
    project: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_auth_context),
) -> LessonListResponse:
    """List lessons with pagination."""
    # Build WHERE
    where_parts: list[str] = ["org_id = $1"]
    params: list = [auth.org_id]

    # Project scoping
    if auth.project is not None:
        params.append(auth.project)
        where_parts.append(f"project = ${len(params)}")
    elif project is not None:
        params.append(project)
        where_parts.append(f"project = ${len(params)}")

    where_sql = " AND ".join(where_parts)

    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM lessons WHERE {where_sql}",
            *params,
        )

        params.append(limit)
        limit_idx = len(params)
        params.append(offset)
        offset_idx = len(params)

        rows = await conn.fetch(
            f"""SELECT id, problem, resolution, context, tags, confidence,
                       source, project, created_at, updated_at, expires_at,
                       upvotes, downvotes, meta
                FROM lessons WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ${limit_idx} OFFSET ${offset_idx}""",
            *params,
        )

    return LessonListResponse(
        lessons=[_row_to_response(dict(r)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── Export ─────────────────────────────────────────────────────────


@router.post("/export", response_model=LessonExportResponse)
async def export_lessons(
    auth: AuthContext = Depends(get_auth_context),
) -> LessonExportResponse:
    """Bulk export all lessons (with embeddings) for the org/project."""
    scope_sql, scope_params = _scope_filter(auth)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id, problem, resolution, context, tags, confidence,
                       source, project, embedding, created_at, updated_at,
                       expires_at, upvotes, downvotes, meta
                FROM lessons WHERE {scope_sql}
                ORDER BY created_at""",
            *scope_params,
        )

    items = []
    for r in rows:
        rd = dict(r)
        tags = rd.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags)
        meta = rd.get("meta") or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        emb = rd.get("embedding")
        if isinstance(emb, str):
            emb = json.loads(emb)
        items.append(LessonExportItem(
            id=rd["id"],
            problem=rd["problem"],
            resolution=rd["resolution"],
            context=rd.get("context"),
            tags=tags,
            confidence=rd["confidence"],
            source=rd.get("source"),
            project=rd.get("project"),
            embedding=emb,
            created_at=rd["created_at"],
            updated_at=rd["updated_at"],
            expires_at=rd.get("expires_at"),
            upvotes=rd.get("upvotes", 0),
            downvotes=rd.get("downvotes", 0),
            meta=meta,
        ))

    return LessonExportResponse(lessons=items)


# ── Import ─────────────────────────────────────────────────────────


@router.post("/import", response_model=LessonImportResponse)
async def import_lessons(
    body: LessonImportRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> LessonImportResponse:
    """Bulk import (upsert) lessons."""
    now = datetime.now(timezone.utc)
    imported = 0

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in body.lessons:
                lesson_id = item.id or str(ULID())
                project = item.project
                if auth.project is not None:
                    project = auth.project

                await conn.execute(
                    """INSERT INTO lessons
                       (id, org_id, problem, resolution, context, tags, confidence,
                        source, project, embedding, created_at, updated_at, expires_at,
                        upvotes, downvotes, meta)
                       VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10,
                               $11, $12, $13, $14, $15, $16::jsonb)
                       ON CONFLICT (id) DO UPDATE SET
                           problem = EXCLUDED.problem,
                           resolution = EXCLUDED.resolution,
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
                       WHERE lessons.org_id = EXCLUDED.org_id""",
                    lesson_id,
                    auth.org_id,
                    item.problem,
                    item.resolution,
                    item.context,
                    json.dumps(item.tags),
                    item.confidence,
                    item.source,
                    project,
                    json.dumps(item.embedding),
                    now,
                    now,
                    item.expires_at,
                    item.upvotes,
                    item.downvotes,
                    json.dumps(item.meta),
                )
                imported += 1

    return LessonImportResponse(imported=imported)
