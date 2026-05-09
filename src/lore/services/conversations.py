"""Conversations service — async job creation, status fetch, and background extraction orchestration."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional

from lore.persistence import (
    NewConversationJob,
    Store,
    StoredConversationJob,
)
from lore.persistence.exceptions import StoreNotFoundError

logger = logging.getLogger(__name__)


def _validate_messages(messages: List[Dict[str, str]]) -> None:
    """Validate the messages list. Raises ValueError on invalid input."""
    if not messages:
        raise ValueError("messages must be non-empty")
    for msg in messages:
        if "role" not in msg or "content" not in msg:
            raise ValueError("Each message must have 'role' and 'content'")


def _get_server_lore(org_id: str):
    """Create an in-process Lore instance for server-side extraction.

    Lifted verbatim from routes/conversations.py:215-228 (pre-1G).
    Imports are deferred to avoid loading ML modules at module import time.
    """
    from lore.lore import Lore
    from lore.store.memory import MemoryStore

    enrichment_model = os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")

    return Lore(
        store=MemoryStore(),
        enrichment=True,
        enrichment_model=enrichment_model,
    )


async def create_job(
    store: Store,
    *,
    org_id: str,
    messages: List[Dict[str, str]],
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project: Optional[str] = None,
) -> StoredConversationJob:
    """Validate, persist, and return a new conversation job."""
    _validate_messages(messages)
    new_job = NewConversationJob(
        org_id=org_id,
        message_count=len(messages),
        messages_json=json.dumps(messages),
        user_id=user_id,
        session_id=session_id,
        project=project,
    )
    return await store.create_conversation_job(new_job)


async def get_job_status(
    store: Store,
    job_id: str,
    org_id: str,
) -> StoredConversationJob:
    """Fetch a conversation job by id and org. Raises StoreNotFoundError if missing."""
    job = await store.get_conversation_job(job_id, org_id)
    if job is None:
        raise StoreNotFoundError("conversation_jobs", job_id)
    return job


async def process_job_async(
    store: Store,
    job_id: str,
    org_id: str,
) -> None:
    """Run the full extraction pipeline for a queued conversation job."""
    start = time.monotonic()
    try:
        job = await store.mark_conversation_job_processing(job_id)
        if job is None:
            logger.warning("Job %s not found at processing time; nothing to do", job_id)
            return

        from lore.conversation import ConversationExtractor
        from lore.types import ConversationMessage

        messages = json.loads(job.messages_json)
        conv_messages = [
            ConversationMessage(role=m["role"], content=m["content"])
            for m in messages
        ]

        lore = _get_server_lore(org_id)
        try:
            extractor = ConversationExtractor(lore)
            result = extractor.extract(
                conv_messages,
                user_id=job.user_id,
                session_id=job.session_id,
                project=job.project,
            )

            # Persist extracted memories
            if result.memory_ids and hasattr(lore, "_store"):
                for mid in result.memory_ids:
                    mem = lore._store.get(mid)
                    if mem is None:
                        continue
                    meta_dict = dict(mem.metadata or {})
                    meta_dict["type"] = mem.type or "fact"
                    meta_dict["source"] = mem.source or "conversation"
                    await store.import_extracted_memory(
                        memory_id=mem.id,
                        org_id=org_id,
                        content=mem.content,
                        context=mem.content,
                        tags=list(mem.tags or []),
                        source=mem.source or "conversation",
                        meta=meta_dict,
                    )

            elapsed_ms = int((time.monotonic() - start) * 1000)
            await store.complete_conversation_job(
                job_id,
                memory_ids=list(result.memory_ids),
                memories_extracted=result.memories_extracted,
                duplicates_skipped=result.duplicates_skipped,
                processing_time_ms=elapsed_ms,
            )
        finally:
            try:
                lore.close()
            except Exception:
                logger.exception("Failed to close in-process Lore for job %s", job_id)
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.exception("Conversation job %s failed", job_id)
        await store.fail_conversation_job(
            job_id, error=str(e), processing_time_ms=elapsed_ms,
        )
