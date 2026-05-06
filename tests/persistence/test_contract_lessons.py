"""Contract tests for list_memories_paginated (Phase 1H — T3).

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
