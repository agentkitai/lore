"""Pydantic request/response models for Lore Cloud Server."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel, Field
except ImportError:
    raise ImportError("Pydantic is required. Install with: pip install lore-sdk[server]")


# ══════════════════════════════════════════════════════════════════
# Memory models
# ══════════════════════════════════════════════════════════════════


class MemoryCreateRequest(BaseModel):
    """Request body for POST /v1/memories."""

    content: str = Field(..., min_length=1)
    type: str = Field(default="note")
    source: Optional[str] = None
    project: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    expires_at: Optional[datetime] = None
    ttl: Optional[str] = None
    # Embedding is NOT accepted from client — server generates it


class MemoryCreateResponse(BaseModel):
    """Response for POST /v1/memories."""

    id: str


class MemoryResponse(BaseModel):
    """Single memory (no embedding)."""

    id: str
    content: str
    type: str
    source: Optional[str] = None
    project: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None


class MemorySearchResult(MemoryResponse):
    """A memory with its computed search score."""

    score: float


class MemorySearchResponse(BaseModel):
    """Response for GET /v1/memories/search."""

    memories: List[MemorySearchResult]


class MemoryListResponse(BaseModel):
    """Response for GET /v1/memories."""

    memories: List[MemoryResponse]
    total: int
    limit: int
    offset: int


class StatsResponse(BaseModel):
    """Response for GET /v1/stats."""

    total_count: int
    count_by_type: Dict[str, int]
    count_by_project: Dict[str, int]
    oldest_memory: Optional[datetime] = None
    newest_memory: Optional[datetime] = None


class BulkDeleteResponse(BaseModel):
    """Response for DELETE /v1/memories (bulk)."""

    deleted: int
