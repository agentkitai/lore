"""Conversation extraction REST endpoints — POST/GET /v1/conversations."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

try:
    from ulid import ULID
except ImportError:
    raise ImportError("python-ulid is required. Install with: pip install python-ulid")

from lore.server.auth import AuthContext, get_auth_context, require_role
from lore.server.db import get_pool

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
) -> ConversationAcceptedResponse:
    """Accept conversation for async extraction."""
    if not body.messages:
        raise HTTPException(400, "messages must be non-empty")
    for msg in body.messages:
        if "role" not in msg or "content" not in msg:
            raise HTTPException(400, "Each message must have 'role' and 'content'")

    job_id = str(ULID())
    now = datetime.now(timezone.utc)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO conversation_jobs
               (id, org_id, status, message_count, messages_json,
                user_id, session_id, project, created_at)
               VALUES ($1, $2, 'accepted', $3, $4, $5, $6, $7, $8)""",
            job_id, auth.org_id, len(body.messages),
            json.dumps(body.messages),
            body.user_id, body.session_id,
            body.project or auth.project, now,
        )

    asyncio.create_task(_process_job(job_id, auth.org_id))

    return ConversationAcceptedResponse(
        job_id=job_id,
        status="accepted",
        message_count=len(body.messages),
    )


@router.get("/{job_id}", response_model=ConversationStatusResponse)
async def get_conversation_status(
    job_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> ConversationStatusResponse:
    """Check status of a conversation extraction job."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, status, message_count, memory_ids,
                      memories_extracted, duplicates_skipped,
                      processing_time_ms, error
               FROM conversation_jobs
               WHERE id = $1 AND org_id = $2""",
            job_id, auth.org_id,
        )
    if row is None:
        raise HTTPException(404, "Job not found")
    return ConversationStatusResponse(
        job_id=row["id"],
        status=row["status"],
        message_count=row["message_count"],
        memories_extracted=row["memories_extracted"] or 0,
        memory_ids=json.loads(row["memory_ids"] or "[]"),
        duplicates_skipped=row["duplicates_skipped"] or 0,
        processing_time_ms=row["processing_time_ms"] or 0,
        error=row["error"],
    )


# -- Background Worker --------------------------------------------------------


async def _process_job(job_id: str, org_id: str) -> None:
    """Background task: run extraction pipeline and update job record."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE conversation_jobs SET status = 'processing' "
            "WHERE id = $1 RETURNING messages_json, user_id, session_id, project",
            job_id,
        )

    start = time.monotonic()
    try:
        from lore.conversation import ConversationExtractor
        from lore.types import ConversationMessage

        messages = json.loads(row["messages_json"])
        conv_messages = [
            ConversationMessage(role=m["role"], content=m["content"])
            for m in messages
        ]

        lore = _get_server_lore(org_id)
        extractor = ConversationExtractor(lore)
        result = extractor.extract(
            conv_messages,
            user_id=row["user_id"],
            session_id=row["session_id"],
            project=row["project"],
        )

        # Persist extracted memories from MemoryStore to Postgres
        if result.memory_ids and hasattr(lore, '_store'):
            async with pool.acquire() as conn:
                for mid in result.memory_ids:
                    mem = lore._store.get(mid)
                    if mem:
                        meta = mem.metadata or {}
                        meta["type"] = mem.type or "fact"
                        meta["source"] = mem.source or "conversation"
                        await conn.execute(
                            """INSERT INTO lessons (id, org_id, problem, resolution, tags, source, meta, confidence, created_at, updated_at)
                               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), now())
                               ON CONFLICT (id) DO NOTHING""",
                            mem.id, org_id,
                            mem.content,
                            mem.content,
                            json.dumps(mem.tags or []),
                            mem.source or "conversation",
                            json.dumps(meta),
                            mem.confidence,
                        )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE conversation_jobs SET
                       status = 'completed',
                       memories_extracted = $2,
                       memory_ids = $3,
                       duplicates_skipped = $4,
                       processing_time_ms = $5,
                       completed_at = now()
                   WHERE id = $1""",
                job_id, result.memories_extracted,
                json.dumps(result.memory_ids),
                result.duplicates_skipped, elapsed_ms,
            )
        lore.close()
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.exception("Conversation job %s failed", job_id)
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE conversation_jobs SET
                       status = 'failed', error = $2,
                       processing_time_ms = $3,
                       completed_at = now()
                   WHERE id = $1""",
                job_id, str(e), elapsed_ms,
            )


def _get_server_lore(org_id: str) -> "Lore":
    """Create a Lore instance for server-side extraction using MemoryStore (in-process)."""
    import os

    from lore.lore import Lore
    from lore.store.memory import MemoryStore

    enrichment_model = os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")

    return Lore(
        store=MemoryStore(),
        enrichment=True,
        enrichment_model=enrichment_model,
    )
