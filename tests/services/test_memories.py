"""Service-level tests using a real Postgres store."""

from __future__ import annotations

import pytest

from lore.persistence import MemoryFilter, NewMemory
from lore.services.memories import (
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    search_memories,
    update_memory,
    vote_memory,
)


@pytest.mark.asyncio
async def test_create_then_get(store):
    created = await create_memory(
        store,
        org_id="solo",
        content="hello world",
        embedding=[0.0] * 384,
        tags=["a", "b"],
        project="proj",
    )
    fetched = await get_memory(store, "solo", created.id)
    assert fetched is not None
    assert fetched.content == "hello world"
    assert tuple(fetched.tags) == ("a", "b")


@pytest.mark.asyncio
async def test_update_then_get(store):
    created = await create_memory(
        store, org_id="solo", content="orig", embedding=[0.0] * 384
    )
    updated = await update_memory(
        store, org_id="solo", memory_id=created.id, content="updated"
    )
    assert updated.content == "updated"


@pytest.mark.asyncio
async def test_list_filters(store):
    await create_memory(store, org_id="solo", content="a", embedding=[0.0] * 384, project="x")
    await create_memory(store, org_id="solo", content="b", embedding=[0.0] * 384, project="y")
    only_x = await list_memories(store, org_id="solo", project="x")
    assert {m.content for m in only_x} == {"a"}


@pytest.mark.asyncio
async def test_delete(store):
    created = await create_memory(
        store, org_id="solo", content="bye", embedding=[0.0] * 384
    )
    deleted = await delete_memory(store, org_id="solo", memory_id=created.id)
    assert deleted is True
    assert (await get_memory(store, "solo", created.id)) is None


@pytest.mark.asyncio
async def test_vote(store):
    created = await create_memory(
        store, org_id="solo", content="rate me", embedding=[0.0] * 384
    )
    after = await vote_memory(store, org_id="solo", memory_id=created.id, direction="up")
    assert after.upvotes == 1
