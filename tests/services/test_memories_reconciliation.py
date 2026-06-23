"""Write-time AUDN reconciliation tests (#66), against both store backends.

The suite defaults reconciliation OFF (tests/conftest.py); these opt back in via
the ``recon_on`` fixture. Embeddings are crafted for known cosine similarity:
``E0`` vs ``E0`` = 1.0 (near-exact), ``E0`` vs ``NEAR`` = 0.9 (supersede band),
``E0`` vs ``FAR`` = 0.0 (add).
"""

from __future__ import annotations

import math
from typing import Sequence

import pytest

from lore.services.memories import create_memory, list_memories
from lore.services.reconciliation import get_reconcile_config

DIM = 384


def _unit(*pairs) -> Sequence[float]:
    v = [0.0] * DIM
    for i, x in pairs:
        v[i] = x
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


E0 = _unit((0, 1.0))
NEAR = _unit((0, 0.92), (1, math.sqrt(1 - 0.92 ** 2)))  # cosine 0.92 to E0 (supersede band)
FAR = _unit((1, 1.0))  # cosine 0 to E0


@pytest.fixture
def recon_on(monkeypatch):
    monkeypatch.setenv("LORE_RECONCILIATION_ENABLED", "1")
    get_reconcile_config.cache_clear()
    yield
    get_reconcile_config.cache_clear()


async def _count(store, org="solo") -> int:
    return len(await list_memories(store, org_id=org, include_expired=True))


@pytest.mark.asyncio
async def test_reconcile_add_when_no_candidate(store, recon_on):
    m = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                            meta={"type": "note"}, content="initial unique memory", embedding=E0, tags=("x",))
    assert await _count(store) == 1
    # a clearly different vector → still Add (no near-duplicate)
    m2 = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                             meta={"type": "note"}, content="totally different", embedding=FAR, tags=())
    assert m2.id != m.id
    assert await _count(store) == 2


@pytest.mark.asyncio
async def test_reconcile_none_on_exact_duplicate(store, recon_on):
    a = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                            meta={"type": "note"}, content="dup content", embedding=E0, tags=("t",))
    again = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                                meta={"type": "note"}, content="dup content", embedding=E0, tags=("t",))
    assert again.id == a.id, "redundant near-duplicate should return the existing row"
    assert await _count(store) == 1, "None must not insert a new row"


@pytest.mark.asyncio
async def test_reconcile_update_merges_tags_when_owned(store, recon_on):
    a = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                            meta={"type": "note"}, content="mergeable", embedding=E0, tags=("a",))
    upd = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                              meta={"type": "note"}, content="mergeable", embedding=E0, tags=("a", "b"))
    assert upd.id == a.id, "Update patches the same row"
    assert set(upd.tags) == {"a", "b"}, f"tags should be merged, got {upd.tags}"
    assert await _count(store) == 1


@pytest.mark.asyncio
async def test_reconcile_delete_supersedes_changed_version(store, recon_on):
    old = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                              meta={"type": "note"}, content="v1 of the thing", embedding=E0, tags=())
    new = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                              meta={"type": "note"}, content="v2 of the thing, revised", embedding=NEAR, tags=())
    assert new.id != old.id, "Delete inserts a fresh row"
    assert await store.is_superseded(old.id), "the prior version must be superseded"
    assert await _count(store) == 2, "old row is kept (soft supersede)"


@pytest.mark.asyncio
async def test_reconcile_never_supersedes_across_types(store, recon_on):
    # Same vector but different type → not a reconciliation target → Add.
    await create_memory(store, org_id="solo", project="proj", user_id="alice",
                        meta={"type": "note"}, content="typed memory", embedding=E0, tags=())
    other = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                                meta={"type": "fact"}, content="typed memory", embedding=E0, tags=())
    assert await _count(store) == 2, "different types must not reconcile"
    assert not await store.is_superseded(other.id)


@pytest.mark.asyncio
async def test_reconcile_isolates_other_users_private_memory(store, recon_on):
    # bob's PRIVATE memory must not be a reconciliation candidate for alice.
    await create_memory(store, org_id="solo", project="proj", user_id="bob",
                        meta={"type": "note"}, content="bob's private note", embedding=E0, tags=())
    alice = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                                meta={"type": "note"}, content="alice's near-identical note", embedding=E0, tags=())
    assert await _count(store) == 2, "alice must not reconcile against bob's private memory"
    assert not await store.is_superseded(alice.id)


@pytest.mark.asyncio
async def test_reconcile_only_against_own_memories(store, recon_on):
    # bob's SHARED memory is visible to alice, but she must not reconcile against
    # it (no dedup/supersede of another user's row) — she gets her own new row.
    bob = await create_memory(store, org_id="solo", project="proj", user_id="bob",
                              meta={"type": "note"}, content="shared knowledge", embedding=E0, tags=())
    await store.promote_memory("solo", bob.id, promoted_by="bob")  # → visibility shared
    alice = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                                meta={"type": "note"}, content="shared knowledge", embedding=E0, tags=())
    assert alice.id != bob.id, "alice must not dedup into bob's shared row"
    assert await _count(store) == 2
    assert not await store.is_superseded(bob.id), "alice must not supersede bob's row"


@pytest.mark.asyncio
async def test_reconcile_skips_observations(store, recon_on):
    # Observations are append-only (high-volume capture) even with reconciliation on.
    for _ in range(3):
        await create_memory(store, org_id="solo", project="proj", user_id="alice",
                            meta={"type": "observation"}, content="same observation", embedding=E0, tags=())
    assert await _count(store) == 3, "observations must always append, never reconcile"


@pytest.mark.asyncio
async def test_reconciliation_disabled_appends(store):
    # No recon_on fixture → suite default (disabled) → append-only.
    a = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                            meta={"type": "note"}, content="same content", embedding=E0, tags=())
    b = await create_memory(store, org_id="solo", project="proj", user_id="alice",
                            meta={"type": "note"}, content="same content", embedding=E0, tags=())
    assert b.id != a.id and await _count(store) == 2, "disabled reconciliation must append"
