"""Contract tests for the AnalyticsOps slice of Store.

Covers record_retrieval_event, bump_access_counts, record_memory_access,
and list_recent_session_snapshots.
These tests run against every Store implementation (Phase 1E: Postgres only).
"""

from __future__ import annotations

import json

import pytest
from ulid import ULID

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


# ── snapshot seed helper ───────────────────────────────────────────────────────


async def _insert_snapshot_memory(
    store,
    *,
    memory_id: str | None = None,
    org_id: str = "solo",
    project: str | None = None,
    meta: dict | None = None,
    created_at_sql: str | None = None,
    content: str = "snap",
) -> str:
    """Insert a memory row directly via raw SQL and return its id."""
    await _ensure_org(store, org_id)
    mid = memory_id or f"mem_{ULID()}"
    meta_dict = meta if meta is not None else {"type": "session_snapshot"}
    embedding_json = json.dumps(_vec(0))
    if created_at_sql is not None:
        await store._conn.execute(
            """
            INSERT INTO memories
                (id, org_id, content, context, tags, confidence, source,
                 project, embedding, meta, created_at)
            VALUES ($1, $2, $3, '', '[]'::jsonb, 0.8, NULL, $4, $5::vector, $6::jsonb,
                    """ + created_at_sql + """)
            """,
            mid, org_id, content, project, embedding_json, json.dumps(meta_dict),
        )
    else:
        await store._conn.execute(
            """
            INSERT INTO memories
                (id, org_id, content, context, tags, confidence, source,
                 project, embedding, meta)
            VALUES ($1, $2, $3, '', '[]'::jsonb, 0.8, NULL, $4, $5::vector, $6::jsonb)
            """,
            mid, org_id, content, project, embedding_json, json.dumps(meta_dict),
        )
    return mid


# ── record_memory_access tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_memory_access_increments_and_returns_row(store: Store):
    memory_id = await _insert_memory(store, org_id="org-rma1")

    result = await store.record_memory_access("org-rma1", memory_id)

    assert result is not None
    assert result.id == memory_id
    assert result.access_count == 1
    assert result.last_accessed_at is not None


@pytest.mark.asyncio
async def test_record_memory_access_returns_none_when_missing(store: Store):
    result = await store.record_memory_access("org-rma2", "mem_nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_record_memory_access_org_isolation(store: Store):
    memory_id = await _insert_memory(store, org_id="org-a")

    # Call under wrong org — should return None
    result = await store.record_memory_access("org-b", memory_id)
    assert result is None

    # Original row should be unchanged
    original = await store.get_memory("org-a", memory_id)
    assert original is not None
    assert original.access_count == 0


# ── list_recent_session_snapshots tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_recent_session_snapshots_returns_recent(store: Store):
    await _insert_snapshot_memory(store, org_id="org-snap1")

    results = await store.list_recent_session_snapshots("org-snap1")

    assert len(results) == 1
    assert results[0].meta.get("type") == "session_snapshot"


@pytest.mark.asyncio
async def test_list_recent_session_snapshots_excludes_old(store: Store):
    # Insert a snapshot that is 25 hours old — should be excluded
    await _insert_snapshot_memory(
        store,
        org_id="org-snap2",
        created_at_sql="now() - interval '25 hours'",
    )

    results = await store.list_recent_session_snapshots("org-snap2")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_list_recent_session_snapshots_filters_project(store: Store):
    await _insert_snapshot_memory(store, org_id="org-snap3", project="proj-a", content="snap-a")
    await _insert_snapshot_memory(store, org_id="org-snap3", project="proj-b", content="snap-b")

    results = await store.list_recent_session_snapshots("org-snap3", project="proj-a")

    assert len(results) == 1
    assert results[0].content == "snap-a"


@pytest.mark.asyncio
async def test_list_recent_session_snapshots_excludes_ids(store: Store):
    id1 = await _insert_snapshot_memory(store, org_id="org-snap4", content="snap-1")
    id2 = await _insert_snapshot_memory(store, org_id="org-snap4", content="snap-2")
    id3 = await _insert_snapshot_memory(store, org_id="org-snap4", content="snap-3")

    results = await store.list_recent_session_snapshots("org-snap4", exclude_ids=[id2])

    returned_ids = {r.id for r in results}
    assert id2 not in returned_ids
    assert id1 in returned_ids
    assert id3 in returned_ids


@pytest.mark.asyncio
async def test_list_recent_session_snapshots_respects_limit(store: Store):
    for i in range(5):
        await _insert_snapshot_memory(store, org_id="org-snap5", content=f"snap-{i}")

    results = await store.list_recent_session_snapshots("org-snap5", limit=2)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_list_recent_session_snapshots_skips_non_snapshot_memories(store: Store):
    # Insert a regular memory (no meta.type='session_snapshot')
    await _insert_memory(store, org_id="org-snap6", content="regular memory")
    # Insert a proper snapshot
    await _insert_snapshot_memory(store, org_id="org-snap6", content="the snapshot")

    results = await store.list_recent_session_snapshots("org-snap6")

    assert len(results) == 1
    assert results[0].content == "the snapshot"


# ── enrich_memory_meta tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_memory_meta_sets_enrichment_key(store: Store):
    memory_id = await _insert_memory(store, org_id="org-enrich1")

    await store.enrich_memory_meta(memory_id, {"summary": "x"})

    result = await store.get_memory("org-enrich1", memory_id)
    assert result is not None
    assert result.meta["enrichment"] == {"summary": "x"}


@pytest.mark.asyncio
async def test_enrich_memory_meta_overwrites_existing_enrichment(store: Store):
    memory_id = await _insert_memory(store, org_id="org-enrich2")

    await store.enrich_memory_meta(memory_id, {"summary": "first"})
    await store.enrich_memory_meta(memory_id, {"summary": "second"})

    result = await store.get_memory("org-enrich2", memory_id)
    assert result is not None
    assert result.meta["enrichment"] == {"summary": "second"}


@pytest.mark.asyncio
async def test_enrich_memory_meta_preserves_other_meta_keys(store: Store):
    await _ensure_org(store, "org-enrich3")
    memory = await store.insert_memory(
        NewMemory(
            org_id="org-enrich3",
            content="test with meta",
            embedding=_vec(1),
            meta={"foo": "bar"},
        )
    )

    await store.enrich_memory_meta(memory.id, {"key": "value"})

    result = await store.get_memory("org-enrich3", memory.id)
    assert result is not None
    assert result.meta == {"foo": "bar", "enrichment": {"key": "value"}}


@pytest.mark.asyncio
async def test_enrich_memory_meta_silent_on_missing_id(store: Store):
    # Should not raise even when the memory_id doesn't exist
    await store.enrich_memory_meta("mem_nonexistent_id", {"summary": "x"})
