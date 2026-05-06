"""Conversation extraction REST endpoints — POST/GET /v1/conversations."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.persistence import Store
from lore.persistence.exceptions import StoreNotFoundError
from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_store
from lore.services import conversations as conversations_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


# -- Request/Response Models --------------------------------------------------


class ConversationRequest(BaseModel):
    messages: List[Dict[str, str]]
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    project: Optional[str] = None


class ConversationAcceptedResponse(BaseModel):
    job_id: str
    status: str = "accepted"
    message_count: int


class ConversationStatusResponse(BaseModel):
    job_id: str
    status: str
    message_count: int
    memories_extracted: int = 0
    memory_ids: List[str] = []
    duplicates_skipped: int = 0
    processing_time_ms: int = 0
    error: Optional[str] = None


# -- Endpoints ----------------------------------------------------------------


@router.post("", response_model=ConversationAcceptedResponse, status_code=202)
async def create_conversation_job(
    body: ConversationRequest,
    auth: AuthContext = Depends(require_role("writer", "admin")),
    store: Store = Depends(get_store),
) -> ConversationAcceptedResponse:
    """Accept conversation for async extraction."""
    try:
        job = await conversations_service.create_job(
            store,
            org_id=auth.org_id,
            messages=body.messages,
            user_id=body.user_id,
            session_id=body.session_id,
            project=body.project or auth.project,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    asyncio.create_task(
        conversations_service.process_job_async(store, job.id, auth.org_id)
    )

    return ConversationAcceptedResponse(
        job_id=job.id,
        status=job.status,
        message_count=job.message_count,
    )


@router.get("/{job_id}", response_model=ConversationStatusResponse)
async def get_conversation_status(
    job_id: str,
    auth: AuthContext = Depends(get_auth_context),
    store: Store = Depends(get_store),
) -> ConversationStatusResponse:
    """Check status of a conversation extraction job."""
    try:
        job = await conversations_service.get_job_status(store, job_id, auth.org_id)
    except StoreNotFoundError:
        raise HTTPException(404, "Job not found")

    return ConversationStatusResponse(
        job_id=job.id,
        status=job.status,
        message_count=job.message_count,
        memories_extracted=job.memories_extracted,
        memory_ids=list(job.memory_ids),
        duplicates_skipped=job.duplicates_skipped,
        processing_time_ms=job.processing_time_ms,
        error=job.error,
    )
