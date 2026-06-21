"""Migration 026: per-user visibility (private/shared) + promote/demote.

Exercises the store contract on BOTH backends via the parametrized ``store``
fixture (Postgres skipped when no test DB; SQLite always). Covers the brief's
required behaviours:

- captures default to PRIVATE and owned by the writing user;
- recall returns the requester's own private rows ∪ the team's shared rows —
  a user must NOT see another user's private rows (vector AND FTS branches);
- ``promote`` flips PRIVATE→SHARED (owner-gated); ``demote`` reverses it;
- get_memory / list_memories enforce the same predicate;
- the solo / no-identity path (``requesting_user_id=None``) is unchanged —
  it returns everything, exactly as before the column existed.
"""

from __future__ import annotations

from typing import Sequence

import pytest

from lore.persistence import NewMemory, RecallParams


def _vec(seed: int) -> Sequence[float]:
    """Deterministic 384-dim vector (matches the contract-test helper)."""
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


async def _recall_ids(store, *, requesting_user_id, query_seed: int = 1) -> set:
    results = await store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=_vec(query_seed),
            limit=50,
            min_score=0.0,  # membership test — don't depend on the decay threshold
            scope_mode="all",  # ignore project/global; we test visibility only
            requesting_user_id=requesting_user_id,
        )
    )
    return {m.id for m in results}


@pytest.mark.asyncio
async def test_capture_defaults_to_private_and_owned(store):
    stored = await store.insert_memory(
        NewMemory(org_id="solo", content="alice secret", embedding=_vec(1), user_id="alice")
    )
    assert stored.visibility == "private"
    assert stored.user_id == "alice"


@pytest.mark.asyncio
async def test_recall_isolates_other_users_private(store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="alice private note", embedding=_vec(1), user_id="alice")
    )
    # Owner sees it; another user does NOT; solo/no-identity sees it.
    assert m.id in await _recall_ids(store, requesting_user_id="alice")
    assert m.id not in await _recall_ids(store, requesting_user_id="bob")
    assert m.id in await _recall_ids(store, requesting_user_id=None)


@pytest.mark.asyncio
async def test_unowned_memory_is_visible_to_everyone(store):
    # Legacy / solo / background writes carry no owner → visible to all (the
    # fail-open backward-compat guarantee). user_id defaults to None.
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="ownerless fact", embedding=_vec(1))
    )
    assert m.user_id is None
    assert m.id in await _recall_ids(store, requesting_user_id="bob")


@pytest.mark.asyncio
async def test_promote_shares_with_team(store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="promote me", embedding=_vec(1), user_id="alice")
    )
    assert m.id not in await _recall_ids(store, requesting_user_id="bob")

    promoted = await store.promote_memory("solo", m.id, promoted_by="alice")
    assert promoted is not None
    assert promoted.visibility == "shared"
    # Now visible to a different user.
    assert m.id in await _recall_ids(store, requesting_user_id="bob")


@pytest.mark.asyncio
async def test_promote_is_owner_gated(store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="alice only", embedding=_vec(1), user_id="alice")
    )
    # Bob is not the owner → no-op, stays private + invisible to bob.
    assert await store.promote_memory("solo", m.id, promoted_by="bob") is None
    assert m.id not in await _recall_ids(store, requesting_user_id="bob")


@pytest.mark.asyncio
async def test_demote_unshares(store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="shared then private", embedding=_vec(1), user_id="alice")
    )
    await store.promote_memory("solo", m.id, promoted_by="alice")
    assert m.id in await _recall_ids(store, requesting_user_id="bob")

    demoted = await store.demote_memory("solo", m.id, demoted_by="alice")
    assert demoted is not None
    assert demoted.visibility == "private"
    assert m.id not in await _recall_ids(store, requesting_user_id="bob")


@pytest.mark.asyncio
async def test_get_memory_hides_other_users_private(store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="by id secret", embedding=_vec(1), user_id="alice")
    )
    assert await store.get_memory("solo", m.id, requesting_user_id="bob") is None
    assert await store.get_memory("solo", m.id, requesting_user_id="alice") is not None
    assert await store.get_memory("solo", m.id) is not None  # internal/solo: unfiltered


@pytest.mark.asyncio
async def test_recall_by_text_isolates_private(store):
    # The FTS branch must enforce the same predicate as the vector branch,
    # otherwise a private memory leaks through full-text search.
    if not hasattr(store, "recall_by_text"):
        pytest.skip("store has no FTS branch")
    m = await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="zylophone qwoptastic unique token",
            embedding=_vec(1),
            user_id="alice",
        )
    )

    async def _text_ids(uid):
        rows = await store.recall_by_text(
            "solo", "zylophone qwoptastic", limit=50, scope_mode="all", requesting_user_id=uid
        )
        return {sm.id for sm, _rank in rows}

    assert m.id in await _text_ids("alice")
    assert m.id not in await _text_ids("bob")
    assert m.id in await _text_ids(None)


@pytest.mark.asyncio
async def test_list_memories_filters_private(store):
    from lore.persistence import MemoryFilter

    m = await store.insert_memory(
        NewMemory(org_id="solo", content="listed private", embedding=_vec(1), user_id="alice")
    )

    async def _list_ids(uid):
        rows = await store.list_memories(
            MemoryFilter(org_id="solo", requesting_user_id=uid)
        )
        return {r.id for r in rows}

    assert m.id in await _list_ids("alice")
    assert m.id not in await _list_ids("bob")
    assert m.id in await _list_ids(None)


# ── Secondary read surfaces over the same pool (each must enforce the filter,
#    or a private memory leaks through it) ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_memories_paginated_filters_private(store):
    from lore.persistence import MemoryFilter

    m = await store.insert_memory(
        NewMemory(org_id="solo", content="paginated private", embedding=_vec(1), user_id="alice")
    )

    async def _ids(uid):
        _total, rows = await store.list_memories_paginated(
            MemoryFilter(org_id="solo", requesting_user_id=uid), limit=100
        )
        return {r.id for r in rows}

    assert m.id in await _ids("alice")
    assert m.id not in await _ids("bob")
    assert m.id in await _ids(None)


@pytest.mark.asyncio
async def test_export_with_embeddings_filters_private(store):
    from lore.persistence import MemoryFilter

    if not hasattr(store, "list_memories_with_embeddings"):
        pytest.skip("store has no bulk-export method")
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="exported private", embedding=_vec(1), user_id="alice")
    )

    async def _ids(uid):
        rows = await store.list_memories_with_embeddings(
            MemoryFilter(org_id="solo", requesting_user_id=uid)
        )
        return {r.id for r in rows}

    assert m.id in await _ids("alice")
    assert m.id not in await _ids("bob")


@pytest.mark.asyncio
async def test_session_snapshots_filter_private(store):
    m = await store.insert_memory(
        NewMemory(
            org_id="solo",
            content="alice session",
            embedding=_vec(1),
            user_id="alice",
            meta={"type": "session_snapshot"},
        )
    )

    async def _ids(uid):
        rows = await store.list_recent_session_snapshots("solo", limit=20, requesting_user_id=uid)
        return {r.id for r in rows}

    assert m.id in await _ids("alice")
    assert m.id not in await _ids("bob")
    assert m.id in await _ids(None)


@pytest.mark.asyncio
async def test_recommendation_candidates_filter_private(store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="rec candidate", embedding=_vec(1), user_id="alice")
    )

    async def _ids(uid):
        rows = await store.list_candidate_memories_for_recommendation("solo", requesting_user_id=uid)
        return {r.id for r in rows}

    assert m.id in await _ids("alice")
    assert m.id not in await _ids("bob")
    assert m.id in await _ids(None)


@pytest.mark.asyncio
async def test_at_time_filters_private(store):
    from datetime import datetime, timezone

    m = await store.insert_memory(
        NewMemory(org_id="solo", content="temporal private", embedding=_vec(1), user_id="alice")
    )
    now = datetime.now(timezone.utc)

    async def _ids(uid):
        rows = await store.list_memories_at_time("solo", at=now, limit=50, requesting_user_id=uid)
        return {r.id for r in rows}

    assert m.id in await _ids("alice")
    assert m.id not in await _ids("bob")
    assert m.id in await _ids(None)
