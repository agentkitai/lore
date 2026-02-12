"""Pydantic request/response models for Lore Cloud Server."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

try:
    from pydantic import BaseModel, Field, field_validator
except ImportError:
    raise ImportError("Pydantic is required. Install with: pip install lore-sdk[server]")


# ── Lesson Create ──────────────────────────────────────────────────


class LessonCreateRequest(BaseModel):
    """Request body for POST /v1/lessons."""

    problem: str = Field(..., min_length=1)
    resolution: str = Field(..., min_length=1)
    context: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: Optional[str] = None
    project: Optional[str] = None
    embedding: List[float] = Field(..., min_length=384, max_length=384)
    expires_at: Optional[datetime] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class LessonCreateResponse(BaseModel):
    """Response for POST /v1/lessons."""

    id: str


# ── Lesson Read ────────────────────────────────────────────────────


class LessonResponse(BaseModel):
    """Single lesson (no embedding)."""

    id: str
    problem: str
    resolution: str
    context: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    confidence: float
    source: Optional[str] = None
    project: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
    upvotes: int = 0
    downvotes: int = 0
    meta: Dict[str, Any] = Field(default_factory=dict)


# ── Lesson Update ──────────────────────────────────────────────────


class LessonUpdateRequest(BaseModel):
    """Request body for PATCH /v1/lessons/{id}.

    All fields optional. upvotes/downvotes can be "+1"/"-1" for atomic increment.
    """

    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    tags: Optional[List[str]] = None
    upvotes: Optional[Union[str, int]] = None
    downvotes: Optional[Union[str, int]] = None
    meta: Optional[Dict[str, Any]] = None

    @field_validator("upvotes", "downvotes")
    @classmethod
    def validate_vote_field(cls, v: Optional[Union[str, int]]) -> Optional[Union[str, int]]:
        if v is None:
            return v
        if isinstance(v, str) and v not in ("+1", "-1"):
            raise ValueError("Vote string must be '+1' or '-1'")
        return v


# ── Lesson List ────────────────────────────────────────────────────


class LessonListResponse(BaseModel):
    """Response for GET /v1/lessons."""

    lessons: List[LessonResponse]
    total: int
    limit: int
    offset: int


# ── Export / Import ────────────────────────────────────────────────


class LessonExportItem(BaseModel):
    """Single lesson with embedding for export."""

    id: str
    problem: str
    resolution: str
    context: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    confidence: float
    source: Optional[str] = None
    project: Optional[str] = None
    embedding: Optional[List[float]] = None
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
    upvotes: int = 0
    downvotes: int = 0
    meta: Dict[str, Any] = Field(default_factory=dict)


class LessonExportResponse(BaseModel):
    """Response for POST /v1/lessons/export."""

    lessons: List[LessonExportItem]


class LessonImportItem(BaseModel):
    """Single lesson for import (upsert)."""

    id: Optional[str] = None
    problem: str = Field(..., min_length=1)
    resolution: str = Field(..., min_length=1)
    context: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: Optional[str] = None
    project: Optional[str] = None
    embedding: List[float] = Field(..., min_length=384, max_length=384)
    expires_at: Optional[datetime] = None
    upvotes: int = 0
    downvotes: int = 0
    meta: Dict[str, Any] = Field(default_factory=dict)


class LessonImportRequest(BaseModel):
    """Request body for POST /v1/lessons/import."""

    lessons: List[LessonImportItem]


class LessonImportResponse(BaseModel):
    """Response for POST /v1/lessons/import."""

    imported: int
