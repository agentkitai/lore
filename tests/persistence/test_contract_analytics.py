"""Contract tests for the AnalyticsOps slice of Store.

Covers record_retrieval_event and bump_access_counts.
These tests run against every Store implementation (Phase 1E: Postgres only).
"""

from __future__ import annotations

import json

import pytest

from lore.persistence import Store
from lore.persistence.types import NewMemory, NewRetrievalEvent


# ── helpers ────────────────────────────────────────────────────────────────────


async def _ensure_org(store, org_id: str) -> None:
    """Insert an org row if it doesn't already exist (required by memories FK)."""
    await store._conn.execute(
        "INSERT INTO orgs (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        org_id,
        org_id,
    )


def _vec(seed: int = 0) -> list[float]:
    """Deterministic 384-dim vector seeded by an int (matches test DB schema)."""
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


async def _insert_memory(
    store,
    *,
    org_id: str = "solo",
    content: str = "test memory",
    project: str | None = None,
) -> str:
    """Insert a memory via the store and return its id."""
    await _ensure_org(store, org_id)
    memory = await store.insert_memory(
        NewMemory(
            org_id=org_id,
            content=content,
            embedding=_vec(1),
            project=project,
        )
    )
    return memory.id


# ── record_retrieval_event tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_retrieval_event_inserts_row(store: Store):
    await _ensure_org(store, "org-re1")
    event = NewRetrievalEvent(
        org_id="org-re1",
        query="what is a fact",
        results_count=3,
        scores=[0.9, 0.8, 0.7],
        memory_ids=["m1", "m2", "m3"],
        avg_score=0.8,
        max_score=0.9,
        min_score_threshold=0.3,
        query_time_ms=42.5,
        project="proj-a",
        format="json",
    )
    await store.record_retrieval_event(event)

    count = await store._conn.fetchval(
        "SELECT COUNT(*) FROM retrieval_events WHERE org_id = $1",
        "org-re1",
    )
    assert count == 1

    row = await store._conn.fetchrow(
        "SELECT query, results_count, project, format FROM retrieval_events WHERE org_id = $1",
        "org-re1",
    )
    assert row["query"] == "what is a fact"
    assert row["results_count"] == 3
    assert row["project"] == "proj-a"
    assert row["format"] == "json"


@pytest.mark.asyncio
async def test_record_retrieval_event_with_empty_results(store: Store):
    await _ensure_org(store, "org-re2")
    event = NewRetrievalEvent(
        org_id="org-re2",
        query="empty query",
        results_count=0,
        scores=[],
        memory_ids=[],
        avg_score=None,
        max_score=None,
        min_score_threshold=0.3,
        query_time_ms=5.0,
    )
    # Should not raise
    await store.record_retrieval_event(event)

    count = await store._conn.fetchval(
        "SELECT COUNT(*) FROM retrieval_events WHERE org_id = $1",
        "org-re2",
    )
    assert count == 1


@pytest.mark.asyncio
async def test_record_retrieval_event_serializes_jsonb_arrays(store: Store):
    await _ensure_org(store, "org-re3")
    event = NewRetrievalEvent(
        org_id="org-re3",
        query="jsonb test",
        results_count=2,
        scores=[0.9, 0.8],
        memory_ids=["m1", "m2"],
        avg_score=0.85,
        max_score=0.9,
        min_score_threshold=0.3,
        query_time_ms=10.0,
    )
    await store.record_retrieval_event(event)

    row = await store._conn.fetchrow(
        "SELECT scores, memory_ids FROM retrieval_events WHERE org_id = $1",
        "org-re3",
    )
    # asyncpg returns JSONB as strings; decode for comparison
    scores = row["scores"]
    if isinstance(scores, str):
        scores = json.loads(scores)
    memory_ids = row["memory_ids"]
    if isinstance(memory_ids, str):
        memory_ids = json.loads(memory_ids)

    assert scores == [0.9, 0.8]
    assert memory_ids == ["m1", "m2"]


# ── bump_access_counts tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bump_access_counts_increments(store: Store):
    memory_id = await _insert_memory(store, org_id="org-bac1")

    # Baseline: access_count should be 0 (or NULL coerced to 0)
    before = await store.get_memory("org-bac1", memory_id)
    assert before is not None
    assert before.access_count == 0

    await store.bump_access_counts("org-bac1", [memory_id])

    after = await store.get_memory("org-bac1", memory_id)
    assert after is not None
    assert after.access_count == 1


@pytest.mark.asyncio
async def test_bump_access_counts_recomputes_importance_score(store: Store):
    memory_id = await _insert_memory(store, org_id="org-bac2")

    await store.bump_access_counts("org-bac2", [memory_id])

    after = await store.get_memory("org-bac2", memory_id)
    assert after is not None
    assert after.importance_score > 0.0


@pytest.mark.asyncio
async def test_bump_access_counts_empty_list_is_noop(store: Store):
    # Should not raise, should not hit the DB (no table access errors)
    await store.bump_access_counts("org-bac3", [])


@pytest.mark.asyncio
async def test_bump_access_counts_org_isolation(store: Store):
    memory_id = await _insert_memory(store, org_id="org-bac4")

    # Bump under the WRONG org — should be a no-op for our memory
    await store.bump_access_counts("org-other", [memory_id])

    after = await store.get_memory("org-bac4", memory_id)
    assert after is not None
    assert after.access_count == 0
