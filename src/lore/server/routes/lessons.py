"""Lesson CRUD endpoints for Lore Cloud Server."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Query
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence import ExportedMemory, Store, StoredMemory
from lore.persistence.exceptions import StoreNotFoundError
from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.server.models import (
    LessonCreateRequest,
    LessonCreateResponse,
    LessonExportItem,
    LessonExportResponse,
    LessonImportRequest,
    LessonImportResponse,
    LessonListResponse,
    LessonResponse,
    LessonSearchRequest,
    LessonSearchResponse,
    LessonSearchResult,
    LessonUpdateRequest,
)
from lore.services import lessons as lessons_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/lessons", tags=["lessons"])


# ── Translation helpers ────────────────────────────────────────────


def _to_lesson_response(m: StoredMemory) -> LessonResponse:
    return LessonResponse(
        id=m.id,
        problem=m.content,
        resolution=m.context if m.context else "",
        context=None,  # legacy field; not stored
        tags=list(m.tags),
        source=m.source,
        project=m.project,
        created_at=m.created_at,
        updated_at=m.updated_at,
        expires_at=m.expires_at,
        upvotes=m.upvotes,
        downvotes=m.downvotes,
        meta=dict(m.meta),
    )


def _to_export_item(em: ExportedMemory) -> LessonExportItem:
    return LessonExportItem(
        id=em.id,
        problem=em.content,
        resolution=em.context if em.context else "",
        context=None,
        tags=list(em.tags),
        source=em.source,
        project=em.project,
        embedding=list(em.embedding) if em.embedding else None,
        created_at=em.created_at,
        updated_at=em.updated_at,
        expires_at=em.expires_at,
        upvotes=em.upvotes,
        downvotes=em.downvotes,
        meta=dict(em.meta),
    )


# ── Create ─────────────────────────────────────────────────────────


@router.post("", response_model=LessonCreateResponse, status_code=201)
async def create_lesson(
    body: LessonCreateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store: Store = Depends(get_store),
) -> LessonCreateResponse:
    """Create a new lesson."""
    project = body.project
    if auth.project is not None:
        project = auth.project

    lesson_id = await lessons_service.create(
        store,
        org_id=auth.org_id,
        problem=body.problem,
        resolution=body.resolution,
        context=body.context,
        tags=body.tags,
        source=body.source,
        project=project,
        embedding=body.embedding,
        expires_at=body.expires_at,
        meta=body.meta,
        scope=body.scope,
    )
    return LessonCreateResponse(id=lesson_id)


# ── Search ─────────────────────────────────────────────────────────


@router.post("/search", response_model=LessonSearchResponse)
async def search_lessons(
    body: LessonSearchRequest,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> LessonSearchResponse:
    """Semantic search with multiplicative scoring."""
    project = body.project
    if auth.project is not None:
        project = auth.project

    results = await lessons_service.search(
        store,
        org_id=auth.org_id,
        embedding=body.embedding,
        project=project,
        tags=body.tags,
        limit=body.limit,
        min_score=body.min_score,
        scope_mode=body.scope,
    )

    lessons = [
        LessonSearchResult(
            id=r["id"],
            problem=r["content"],
            resolution=r["context"] or "",
            context=None,
            tags=r["tags"],
            source=r["source"],
            project=r["project"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            expires_at=r["expires_at"],
            upvotes=r["upvotes"],
            downvotes=r["downvotes"],
            meta=r["meta"],
            score=r["score"],
        )
        for r in results
    ]
    return LessonSearchResponse(lessons=lessons)


# ── Access tracking ────────────────────────────────────────────────


@router.post("/{lesson_id}/access", status_code=200)
async def record_access(
    lesson_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> dict:
    """Record an access event: increment access_count and set last_accessed_at."""
    try:
        result = await lessons_service.record_access(
            store,
            org_id=auth.org_id,
            lesson_id=lesson_id,
            project=auth.project,
        )
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Lesson not found")

    last_acc = result["last_accessed_at"]
    return {
        "id": result["id"],
        "access_count": result["access_count"],
        "last_accessed_at": last_acc.isoformat() if last_acc else None,
    }


# ── Read ───────────────────────────────────────────────────────────


@router.get("/{lesson_id}", response_model=LessonResponse)
async def get_lesson(
    lesson_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> LessonResponse:
    """Get a single lesson by ID."""
    try:
        m = await lessons_service.get(
            store,
            org_id=auth.org_id,
            lesson_id=lesson_id,
            project=auth.project,
        )
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return _to_lesson_response(m)


# ── Update ─────────────────────────────────────────────────────────


@router.patch("/{lesson_id}", response_model=LessonResponse)
async def update_lesson(
    lesson_id: str,
    body: LessonUpdateRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store: Store = Depends(get_store),
) -> LessonResponse:
    """Update a lesson. Supports atomic upvote/downvote."""
    try:
        m = await lessons_service.update(
            store,
            org_id=auth.org_id,
            lesson_id=lesson_id,
            project=auth.project,
            tags=body.tags,
            meta=body.meta,
            upvotes=body.upvotes,
            downvotes=body.downvotes,
        )
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Lesson not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _to_lesson_response(m)


# ── Delete ─────────────────────────────────────────────────────────


@router.delete("/{lesson_id}", status_code=204)
async def delete_lesson(
    lesson_id: str,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store: Store = Depends(get_store),
) -> None:
    """Delete a lesson."""
    try:
        await lessons_service.delete(
            store,
            org_id=auth.org_id,
            lesson_id=lesson_id,
            project=auth.project,
        )
    except StoreNotFoundError:
        raise HTTPException(status_code=404, detail="Lesson not found")


# ── List ───────────────────────────────────────────────────────────


@router.get("", response_model=LessonListResponse)
async def list_lessons(
    project: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    min_reputation: Optional[int] = Query(None, alias="minReputation"),
    since: Optional[str] = Query(None, description="ISO 8601 datetime — only return records created at or after"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> LessonListResponse:
    """List lessons with pagination."""
    # Project-scoped key overrides query param
    effective_project = auth.project if auth.project is not None else project

    since_dt: Optional[datetime] = None
    if since is not None:
        _dt = datetime.fromisoformat(since)
        since_dt = _dt if _dt.tzinfo else _dt.replace(tzinfo=timezone.utc)

    total, memories = await lessons_service.list_lessons(
        store,
        org_id=auth.org_id,
        project=effective_project,
        query=query,
        category=category,
        since=since_dt,
        min_reputation=min_reputation,
        limit=limit,
        offset=offset,
    )
    return LessonListResponse(
        lessons=[_to_lesson_response(m) for m in memories],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── Export ─────────────────────────────────────────────────────────


@router.post("/export", response_model=LessonExportResponse)
async def export_lessons(
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> LessonExportResponse:
    """Bulk export all lessons (with embeddings) for the org/project."""
    items = await lessons_service.export(
        store,
        org_id=auth.org_id,
        project=auth.project,
    )
    return LessonExportResponse(lessons=[_to_export_item(em) for em in items])


# ── Import ─────────────────────────────────────────────────────────


@router.post("/import", response_model=LessonImportResponse)
async def import_lessons(
    body: LessonImportRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store: Store = Depends(get_store),
) -> LessonImportResponse:
    """Bulk import (upsert) lessons."""
    count = await lessons_service.import_lessons(
        store,
        org_id=auth.org_id,
        lessons=body.lessons,
        project_override=auth.project,
    )
    return LessonImportResponse(imported=count)
