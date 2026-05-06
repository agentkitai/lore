"""Contract tests for the ConversationOps slice of Store.

Covers create_conversation_job and get_conversation_job (T3).
"""

from __future__ import annotations

import pytest

from lore.persistence import Store
from lore.persistence.types import NewConversationJob, StoredConversationJob

# ── create_conversation_job ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_job_round_trip(store: Store):
    job = NewConversationJob(
        org_id="solo",
        message_count=3,
        messages_json='[{"role":"user","content":"hello"}]',
    )
    stored = await store.create_conversation_job(job)
    assert isinstance(stored, StoredConversationJob)

    fetched = await store.get_conversation_job(stored.id, "solo")
    assert fetched is not None
    assert fetched.id == stored.id
    assert fetched.org_id == "solo"
    assert fetched.message_count == 3
    assert fetched.messages_json == '[{"role":"user","content":"hello"}]'
    assert fetched.created_at is not None


@pytest.mark.asyncio
async def test_create_job_with_optional_fields(store: Store):
    job = NewConversationJob(
        org_id="solo",
        message_count=5,
        messages_json="[]",
        user_id="user_42",
        session_id="sess_abc",
        project="my-project",
    )
    stored = await store.create_conversation_job(job)

    fetched = await store.get_conversation_job(stored.id, "solo")
    assert fetched is not None
    assert fetched.user_id == "user_42"
    assert fetched.session_id == "sess_abc"
    assert fetched.project == "my-project"


@pytest.mark.asyncio
async def test_get_job_returns_none_when_missing(store: Store):
    result = await store.get_conversation_job("nonexistent-id", "solo")
    assert result is None


@pytest.mark.asyncio
async def test_get_job_org_isolation(store: Store):
    job = NewConversationJob(
        org_id="org_a",
        message_count=2,
        messages_json="[]",
    )
    stored = await store.create_conversation_job(job)

    # Fetching with a different org returns None
    result = await store.get_conversation_job(stored.id, "org_b")
    assert result is None


@pytest.mark.asyncio
async def test_create_job_initial_status_is_accepted(store: Store):
    job = NewConversationJob(
        org_id="solo",
        message_count=1,
        messages_json="[]",
    )
    stored = await store.create_conversation_job(job)
    assert stored.status == "accepted"


@pytest.mark.asyncio
async def test_create_job_initial_memory_ids_empty(store: Store):
    job = NewConversationJob(
        org_id="solo",
        message_count=1,
        messages_json="[]",
    )
    stored = await store.create_conversation_job(job)
    assert stored.memory_ids == ()


# ── mark_conversation_job_processing ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_processing_updates_status(store: Store):
    job = NewConversationJob(
        org_id="solo",
        message_count=2,
        messages_json="[]",
    )
    created = await store.create_conversation_job(job)

    result = await store.mark_conversation_job_processing(created.id)

    assert result is not None
    assert result.status == "processing"
    assert result.id == created.id
    assert result.org_id == created.org_id
    assert result.message_count == created.message_count


@pytest.mark.asyncio
async def test_mark_processing_returns_none_when_missing(store: Store):
    result = await store.mark_conversation_job_processing("job_nonexistent")
    assert result is None


# ── complete_conversation_job ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_job_sets_status_and_payload(store: Store):
    job = NewConversationJob(
        org_id="solo",
        message_count=3,
        messages_json="[]",
    )
    created = await store.create_conversation_job(job)
    await store.mark_conversation_job_processing(created.id)

    await store.complete_conversation_job(
        created.id,
        memory_ids=["m1", "m2"],
        memories_extracted=2,
        duplicates_skipped=1,
        processing_time_ms=500,
    )

    fetched = await store.get_conversation_job(created.id, "solo")
    assert fetched is not None
    assert fetched.status == "completed"
    assert fetched.memory_ids == ("m1", "m2")
    assert fetched.memories_extracted == 2
    assert fetched.duplicates_skipped == 1
    assert fetched.processing_time_ms == 500
    assert fetched.completed_at is not None


@pytest.mark.asyncio
async def test_complete_job_silent_on_missing_id(store: Store):
    # Should not raise
    await store.complete_conversation_job(
        "job_nonexistent",
        memory_ids=[],
        memories_extracted=0,
        duplicates_skipped=0,
        processing_time_ms=0,
    )


# ── fail_conversation_job ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fail_job_sets_error_and_status(store: Store):
    job = NewConversationJob(
        org_id="solo",
        message_count=1,
        messages_json="[]",
    )
    created = await store.create_conversation_job(job)
    await store.mark_conversation_job_processing(created.id)

    await store.fail_conversation_job(
        created.id,
        error="oops",
        processing_time_ms=42,
    )

    fetched = await store.get_conversation_job(created.id, "solo")
    assert fetched is not None
    assert fetched.status == "failed"
    assert fetched.error == "oops"
    assert fetched.processing_time_ms == 42
    assert fetched.completed_at is not None


@pytest.mark.asyncio
async def test_fail_job_silent_on_missing_id(store: Store):
    # Should not raise
    await store.fail_conversation_job(
        "job_nonexistent",
        error="irrelevant",
        processing_time_ms=0,
    )
