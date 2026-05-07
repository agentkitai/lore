# tests/services/test_retrieve.py
"""Tests for the retrieve service (without analytics — that's left at the route)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from lore.services.memories import create_memory
from lore.services.retrieve import (
    RetrieveOutput,
    bump_access_counts,
    recent_session_snapshots,
    record_retrieval_event,
    retrieve,
)


@pytest.mark.asyncio
async def test_retrieve_returns_ranked_memories(store):
    # Insert one memory; query with the same embedding
    embed = [0.1] * 384
    await create_memory(
        store, org_id="solo", content="alpha doc", embedding=embed
    )
    out: RetrieveOutput = await retrieve(
        store,
        org_id="solo",
        query_text="alpha",
        query_vec=embed,
        limit=5,
        min_score=0.0,
    )
    assert out.count >= 1
    assert any(m.content == "alpha doc" for m in out.memories)
    assert isinstance(out.formatted, str)


@pytest.mark.asyncio
async def test_retrieve_format_xml(store):
    embed = [0.2] * 384
    await create_memory(
        store, org_id="solo", content="xml me", embedding=embed
    )
    out = await retrieve(
        store, org_id="solo", query_text="xml", query_vec=embed,
        limit=5, min_score=0.0, format="xml",
    )
    assert "<memories" in out.formatted


@pytest.mark.asyncio
async def test_retrieve_invalid_format_raises():
    with pytest.raises(ValueError):
        await retrieve(
            store=None,  # type: ignore[arg-type]
            org_id="solo",
            query_text="x",
            query_vec=[0.0] * 384,
            limit=5,
            min_score=0.0,
            format="bogus",
        )


# ---------------------------------------------------------------------------
# record_retrieval_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_retrieval_event_calls_store_and_metrics(store):
    """Insert a row via the service; verify it appears in retrieval_events."""
    mem_ids = ["mem-aaa", "mem-bbb"]
    scores = [0.9, 0.7]
    await record_retrieval_event(
        store,
        org_id="solo",
        query_text="test query",
        memory_ids=mem_ids,
        scores=scores,
        min_score=0.3,
        elapsed_ms=42.0,
        fmt="xml",
        project=None,
    )
    # Verify the row was inserted
    row = await store._conn.fetchrow(
        "SELECT results_count FROM retrieval_events WHERE org_id=$1 AND query=$2",
        "solo",
        "test query",
    )
    assert row is not None
    assert row["results_count"] == 2


@pytest.mark.asyncio
async def test_record_retrieval_event_swallows_store_error(store, monkeypatch):
    """If the store raises, the service swallows the error."""
    monkeypatch.setattr(
        store,
        "record_retrieval_event",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    # Must not raise
    await record_retrieval_event(
        store,
        org_id="solo",
        query_text="q",
        memory_ids=["x"],
        scores=[0.5],
        min_score=0.3,
        elapsed_ms=10.0,
        fmt="xml",
        project=None,
    )


@pytest.mark.asyncio
async def test_record_retrieval_event_with_empty_results(store):
    """Empty memory_ids inserts a row with results_count=0, max_score=None, avg_score=None."""
    await record_retrieval_event(
        store,
        org_id="solo",
        query_text="empty query",
        memory_ids=[],
        scores=[],
        min_score=0.3,
        elapsed_ms=5.0,
        fmt="markdown",
        project=None,
    )
    row = await store._conn.fetchrow(
        "SELECT results_count, max_score, avg_score FROM retrieval_events "
        "WHERE org_id=$1 AND query=$2",
        "solo",
        "empty query",
    )
    assert row is not None
    assert row["results_count"] == 0
    assert row["max_score"] is None
    assert row["avg_score"] is None


# ---------------------------------------------------------------------------
# bump_access_counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bump_access_counts_calls_store(store, monkeypatch):
    """Service delegates to store.bump_access_counts with correct args."""
    mock = AsyncMock()
    monkeypatch.setattr(store, "bump_access_counts", mock)
    await bump_access_counts(store, "solo", ["m1", "m2"])
    mock.assert_awaited_once_with("solo", ["m1", "m2"])


@pytest.mark.asyncio
async def test_bump_access_counts_swallows_error(store, monkeypatch):
    """If the store raises, the service swallows the error."""
    monkeypatch.setattr(
        store,
        "bump_access_counts",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    result = await bump_access_counts(store, "solo", ["m1"])
    assert result is None


# ---------------------------------------------------------------------------
# recent_session_snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_session_snapshots_returns_results(store):
    """Insert a session-snapshot memory; service returns it.

    Phase 3E unskipped this on SqliteStore: ``list_recent_session_snapshots``
    is now implemented across both backends.
    """
    from lore.persistence import NewMemory

    embed = [0.3] * 384
    await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="snapshot content",
            embedding=embed,
            meta={"type": "session_snapshot"},
        )
    )
    results = await recent_session_snapshots(store, org_id="solo", limit=5)
    assert any(m.content == "snapshot content" for m in results)


@pytest.mark.asyncio
async def test_recent_session_snapshots_returns_empty_on_error(store, monkeypatch):
    """If the store raises, the service returns an empty tuple."""
    monkeypatch.setattr(
        store,
        "list_recent_session_snapshots",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    results = await recent_session_snapshots(store, org_id="solo")
    assert results == ()
