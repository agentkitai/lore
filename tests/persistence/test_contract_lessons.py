"""Contract tests for list_memories_paginated and list_memories_with_embeddings (Phase 1H — T3/T4).

Tests run against every Store implementation (currently Postgres only).
"""

from __future__ import annotations

from typing import Sequence

import pytest

from lore.persistence import (
    MemoryFilter,
    NewMemory,
    Store,
)


def _vec(seed: int) -> Sequence[float]:
    """Deterministic 384-dim vector seeded by an int."""
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


async def _set_reputation(store: Store, memory_id: str, score: int) -> None:
    """Raw-SQL helper: set reputation_score on a specific memory row."""
    await store._conn.execute(  # type: ignore[attr-defined]
        "UPDATE memories SET reputation_score = $1 WHERE id = $2",
        score,
        memory_id,
    )


# ---------------------------------------------------------------------------
# T3-1: basic pagination shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_paginated_returns_total_and_rows(store: Store) -> None:
    """total reflects all matches; page is capped at limit."""
    for i in range(3):
        await store.insert_memory(
            NewMemory(org_id="solo", content=f"item-{i}", embedding=_vec(100 + i))
        )

    total, rows = await store.list_memories_paginated(
        MemoryFilter(org_id="solo"), limit=2, offset=0
    )

    assert total == 3
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# T3-2: text_query ILIKE filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_paginated_text_query_filters_by_content_or_context(
    store: Store,
) -> None:
    """text_query matches content OR context (case-insensitive)."""
    # Matches via content
    m1 = await store.insert_memory(
        NewMemory(org_id="solo", content="alpha bravo", embedding=_vec(200))
    )
    # Matches via context
    m2 = await store.insert_memory(
        NewMemory(org_id="solo", content="x", context="alpha y", embedding=_vec(201))
    )
    # Should NOT match
    await store.insert_memory(
        NewMemory(org_id="solo", content="delta", context="echo foxtrot", embedding=_vec(202))
    )

    total, rows = await store.list_memories_paginated(
        MemoryFilter(org_id="solo", text_query="alpha"), limit=50, offset=0
    )

    ids = {r.id for r in rows}
    assert m1.id in ids
    assert m2.id in ids
    assert total == 2


# ---------------------------------------------------------------------------
# T3-3: min_reputation filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_paginated_min_reputation_filter(store: Store) -> None:
    """Only rows with reputation_score >= min_reputation are returned."""
    low = await store.insert_memory(
        NewMemory(org_id="solo", content="low-rep", embedding=_vec(300))
    )
    high = await store.insert_memory(
        NewMemory(org_id="solo", content="high-rep", embedding=_vec(301))
    )
    _mid = await store.insert_memory(
        NewMemory(org_id="solo", content="mid-rep", embedding=_vec(302))
    )

    await _set_reputation(store, low.id, 5)
    await _set_reputation(store, high.id, 15)
    # _mid stays at default (NULL / 0)

    total, rows = await store.list_memories_paginated(
        MemoryFilter(org_id="solo", min_reputation=10), limit=50, offset=0
    )

    ids = {r.id for r in rows}
    assert high.id in ids
    assert low.id not in ids
    assert _mid.id not in ids
    assert total == 1


# ---------------------------------------------------------------------------
# T3-4: offset paging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_paginated_offset_paging(store: Store) -> None:
    """offset skips the correct number of rows (ORDER BY created_at DESC)."""
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc)
    ids_by_timestamp: list[tuple[datetime, str]] = []
    for i in range(5):
        # Use explicit expires_at-free inserts with staggered times via the DB
        m = await store.insert_memory(
            NewMemory(org_id="solo", content=f"page-item-{i}", embedding=_vec(400 + i))
        )
        ids_by_timestamp.append((base + timedelta(milliseconds=i * 100), m.id))

    # Fetch all (no limit) to get true DB ordering
    full_total, all_rows = await store.list_memories_paginated(
        MemoryFilter(org_id="solo"), limit=100, offset=0
    )
    assert full_total == 5
    all_ids_in_db_order = [r.id for r in all_rows]

    total, page = await store.list_memories_paginated(
        MemoryFilter(org_id="solo"), limit=2, offset=2
    )

    assert total == 5
    assert len(page) == 2
    # Page must be exactly the middle slice of the full result
    assert [r.id for r in page] == all_ids_in_db_order[2:4]


# ---------------------------------------------------------------------------
# T3-5: org isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_paginated_org_isolation(store: Store) -> None:
    """Filtering by org_b returns only org_b rows, not org_a rows."""
    await store.insert_memory(
        NewMemory(org_id="org_a", content="belongs to a", embedding=_vec(500))
    )

    total, rows = await store.list_memories_paginated(
        MemoryFilter(org_id="org_b"), limit=50, offset=0
    )

    assert total == 0
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# T3-6: combined filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_paginated_combined_filters(store: Store) -> None:
    """project + tags + text_query all apply together (AND semantics)."""
    # Matches all three
    match = await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="alpha combined test",
            project="proj-x",
            tags=("tagA",),
            embedding=_vec(600),
        )
    )
    # Wrong project
    await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="alpha combined test",
            project="proj-y",
            tags=("tagA",),
            embedding=_vec(601),
        )
    )
    # Missing tag
    await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="alpha combined test",
            project="proj-x",
            tags=("tagB",),
            embedding=_vec(602),
        )
    )
    # Missing text_query match
    await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="no match here",
            project="proj-x",
            tags=("tagA",),
            embedding=_vec(603),
        )
    )

    total, rows = await store.list_memories_paginated(
        MemoryFilter(
            org_id="solo",
            project="proj-x",
            tags=("tagA",),
            text_query="alpha",
        ),
        limit=50,
        offset=0,
    )

    assert total == 1
    assert rows[0].id == match.id


# ---------------------------------------------------------------------------
# T4-1: full shape — 2 memories with non-null embeddings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_with_embeddings_returns_full_shape(store: Store) -> None:
    """list_memories_with_embeddings returns ExportedMemory with decoded embeddings."""
    vec = [0.1] * 384
    await store.insert_memory(NewMemory(org_id="solo", content="embed-1", embedding=vec))
    await store.insert_memory(NewMemory(org_id="solo", content="embed-2", embedding=vec))

    rows = await store.list_memories_with_embeddings(MemoryFilter(org_id="solo"))

    assert len(rows) == 2
    for row in rows:
        assert row.embedding is not None
        assert isinstance(row.embedding, list)
        assert len(row.embedding) == 384
        assert all(isinstance(v, float) for v in row.embedding)


# ---------------------------------------------------------------------------
# T4-2: null embedding is surfaced as None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_with_embeddings_handles_null_embedding(store: Store) -> None:
    """A NULL embedding column is returned as None (not an empty list)."""
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="no-embed", embedding=[0.1] * 384)
    )
    # Raw SQL: NULL out the embedding after insert
    await store._conn.execute(  # type: ignore[attr-defined]
        "UPDATE memories SET embedding = NULL WHERE id = $1", m.id
    )

    rows = await store.list_memories_with_embeddings(MemoryFilter(org_id="solo"))

    assert len(rows) == 1
    assert rows[0].embedding is None


# ---------------------------------------------------------------------------
# T4-3: org isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_with_embeddings_org_isolation(store: Store) -> None:
    """Memories from org_a are not visible when filtering by org_b."""
    await store.insert_memory(
        NewMemory(org_id="org_a", content="belongs to a", embedding=[0.1] * 384)
    )

    rows = await store.list_memories_with_embeddings(MemoryFilter(org_id="org_b"))

    assert len(rows) == 0


# ---------------------------------------------------------------------------
# T4-4: project filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_with_embeddings_project_filter(store: Store) -> None:
    """project filter returns only memories matching that project."""
    m1 = await store.insert_memory(
        NewMemory(org_id="solo", content="proj-x item", project="proj-x", embedding=[0.1] * 384)
    )
    await store.insert_memory(
        NewMemory(org_id="solo", content="proj-y item", project="proj-y", embedding=[0.2] * 384)
    )

    rows = await store.list_memories_with_embeddings(
        MemoryFilter(org_id="solo", project="proj-x")
    )

    assert len(rows) == 1
    assert rows[0].id == m1.id


# ---------------------------------------------------------------------------
# T5-1: upsert fresh id → True + row visible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_inserts_new_id_returns_true(store: Store) -> None:
    """Upserting a brand-new memory_id returns True and row is retrievable."""
    result = await store.upsert_memory_with_embedding(
        memory_id="upsert-new-001",
        org_id="solo",
        content="fresh content",
        context=None,
        tags=["t1"],
        confidence=0.9,
        source="test",
        project=None,
        embedding=_vec(1),
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )

    assert result is True

    row = await store.get_memory("solo", "upsert-new-001")
    assert row is not None
    assert row.content == "fresh content"


# ---------------------------------------------------------------------------
# T5-2: upsert same id → False + content updated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_updates_existing_returns_false(store: Store) -> None:
    """Second upsert with same memory_id returns False and content is updated."""
    kwargs = dict(
        memory_id="upsert-dup-001",
        org_id="solo",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=_vec(2),
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )

    first = await store.upsert_memory_with_embedding(content="original", **kwargs)  # type: ignore[arg-type]
    second = await store.upsert_memory_with_embedding(content="updated", **kwargs)  # type: ignore[arg-type]

    assert first is True
    assert second is False

    row = await store.get_memory("solo", "upsert-dup-001")
    assert row is not None
    assert row.content == "updated"


# ---------------------------------------------------------------------------
# T5-3: org mismatch → silent no-op, original row unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_with_org_mismatch_is_silent_noop(store: Store) -> None:
    """Upserting with wrong org_id is a silent no-op; original row untouched."""
    memory_id = "upsert-mismatch-001"

    await store.upsert_memory_with_embedding(
        memory_id=memory_id,
        org_id="solo",
        content="original solo content",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=_vec(3),
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )

    # Attempt upsert with different org — should be silent no-op
    result = await store.upsert_memory_with_embedding(
        memory_id=memory_id,
        org_id="org-b",
        content="malicious overwrite",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=_vec(3),
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )

    assert result is False  # treated as "no new insert"

    # Original row must be unchanged
    row = await store.get_memory("solo", memory_id)
    assert row is not None
    assert row.content == "original solo content"


# ---------------------------------------------------------------------------
# T5-4: null embedding → row inserts with NULL embedding column
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_with_null_embedding(store: Store) -> None:
    """Passing embedding=None results in a NULL embedding column."""
    result = await store.upsert_memory_with_embedding(
        memory_id="upsert-nullemb-001",
        org_id="solo",
        content="no embedding here",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )

    assert result is True

    rows = await store.list_memories_with_embeddings(MemoryFilter(org_id="solo"))
    match = next((r for r in rows if r.id == "upsert-nullemb-001"), None)
    assert match is not None
    assert match.embedding is None


# ---------------------------------------------------------------------------
# T5-5: exact id preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_preserves_id_exactly(store: Store) -> None:
    """The inserted row has exactly the memory_id that was passed in."""
    custom_id = "custom_id_xyz"

    await store.upsert_memory_with_embedding(
        memory_id=custom_id,
        org_id="solo",
        content="id test",
        context=None,
        tags=[],
        confidence=0.5,
        source=None,
        project=None,
        embedding=_vec(5),
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )

    row = await store.get_memory("solo", custom_id)
    assert row is not None
    assert row.id == custom_id


# ---------------------------------------------------------------------------
# T5-6: JSONB round-trip for tags and meta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_jsonb_roundtrip_for_tags_meta(store: Store) -> None:
    """tags and meta survive a JSONB round-trip through Postgres."""
    await store.upsert_memory_with_embedding(
        memory_id="upsert-jsonb-001",
        org_id="solo",
        content="jsonb test",
        context=None,
        tags=["a", "b"],
        confidence=0.5,
        source=None,
        project=None,
        embedding=_vec(6),
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={"x": 1},
    )

    row = await store.get_memory("solo", "upsert-jsonb-001")
    assert row is not None
    assert set(row.tags) == {"a", "b"}
    assert row.meta == {"x": 1}
