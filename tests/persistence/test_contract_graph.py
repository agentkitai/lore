"""Contract tests for the GraphOps slice of Store.

Each method is exercised against every Store implementation parametrized in
tests/persistence/conftest.py — Phase 1A wires Postgres only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lore.persistence import (
    NewEntity,
    Store,
    StoredEntity,
)


@pytest.mark.asyncio
async def test_upsert_entity_inserts_new(store: Store):
    e = await store.upsert_entity(
        NewEntity(name="postgres", entity_type="technology")
    )
    assert isinstance(e, StoredEntity)
    assert e.id.startswith("ent_")
    assert e.name == "postgres"
    assert e.entity_type == "technology"
    assert e.mention_count == 1
    assert e.aliases == ()


@pytest.mark.asyncio
async def test_upsert_entity_with_aliases_and_metadata(store: Store):
    e = await store.upsert_entity(
        NewEntity(
            name="kubernetes",
            entity_type="technology",
            aliases=["k8s", "kube"],
            metadata={"version": "1.30"},
        )
    )
    assert set(e.aliases) == {"k8s", "kube"}
    assert e.metadata == {"version": "1.30"}


@pytest.mark.asyncio
async def test_upsert_entity_merges_on_conflict_by_name(store: Store):
    a = await store.upsert_entity(
        NewEntity(name="redis", entity_type="db", mention_count=2)
    )
    b = await store.upsert_entity(
        NewEntity(
            name="redis",
            entity_type="db",
            mention_count=3,
            aliases=["valkey"],
            metadata={"version": "7"},
        )
    )
    # ON CONFLICT (name) DO UPDATE ... RETURNING returns the merged row's existing id
    assert a.id == b.id
    # Mention count is sum of both upserts
    assert b.mention_count == 5
    assert "valkey" in tuple(b.aliases)
    assert b.metadata.get("version") == "7"


@pytest.mark.asyncio
async def test_get_entity_round_trip(store: Store):
    e = await store.upsert_entity(
        NewEntity(name="ulid", entity_type="library")
    )
    fetched = await store.get_entity(e.id)
    assert fetched is not None
    assert fetched.id == e.id
    assert fetched.name == "ulid"


@pytest.mark.asyncio
async def test_get_entity_returns_none_when_missing(store: Store):
    assert await store.get_entity("ent_missing") is None


@pytest.mark.asyncio
async def test_upsert_first_seen_defaults_to_now(store: Store):
    before = datetime.now(timezone.utc)
    e = await store.upsert_entity(
        NewEntity(name="asyncpg", entity_type="library")
    )
    after = datetime.now(timezone.utc)
    assert before <= e.first_seen_at <= after
    assert before <= e.last_seen_at <= after


@pytest.mark.asyncio
async def test_get_entity_by_name_round_trip(store: Store):
    e = await store.upsert_entity(
        NewEntity(name="fastapi", entity_type="framework")
    )
    fetched = await store.get_entity_by_name("fastapi")
    assert fetched is not None
    assert fetched.id == e.id


@pytest.mark.asyncio
async def test_get_entity_by_name_is_case_sensitive(store: Store):
    await store.upsert_entity(
        NewEntity(name="Postgres", entity_type="db")
    )
    # Different case must not match (services normalize)
    assert (await store.get_entity_by_name("postgres")) is None


@pytest.mark.asyncio
async def test_get_entity_by_name_returns_none_when_missing(store: Store):
    assert (await store.get_entity_by_name("nonexistent")) is None


@pytest.mark.asyncio
async def test_list_entities_returns_all_when_unfiltered(store: Store):
    await store.upsert_entity(NewEntity(name="a", entity_type="x"))
    await store.upsert_entity(NewEntity(name="b", entity_type="y"))
    rows = await store.list_entities()
    names = {r.name for r in rows}
    assert {"a", "b"}.issubset(names)


@pytest.mark.asyncio
async def test_list_entities_filters_by_type(store: Store):
    await store.upsert_entity(NewEntity(name="alpha", entity_type="lang"))
    await store.upsert_entity(NewEntity(name="beta", entity_type="db"))
    only_lang = await store.list_entities(entity_type="lang")
    assert all(r.entity_type == "lang" for r in only_lang)
    assert any(r.name == "alpha" for r in only_lang)


@pytest.mark.asyncio
async def test_list_entities_filters_by_min_mentions(store: Store):
    await store.upsert_entity(NewEntity(name="rare", entity_type="x", mention_count=1))
    await store.upsert_entity(NewEntity(name="popular", entity_type="x", mention_count=10))
    high = await store.list_entities(min_mentions=5)
    names = {r.name for r in high}
    assert "popular" in names
    assert "rare" not in names


@pytest.mark.asyncio
async def test_list_entities_orders_by_mention_count_desc(store: Store):
    await store.upsert_entity(NewEntity(name="low", entity_type="x", mention_count=1))
    await store.upsert_entity(NewEntity(name="mid", entity_type="x", mention_count=5))
    await store.upsert_entity(NewEntity(name="high", entity_type="x", mention_count=20))
    rows = await store.list_entities(entity_type="x")
    counts = [r.mention_count for r in rows]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.asyncio
async def test_list_entities_respects_limit(store: Store):
    for i in range(5):
        await store.upsert_entity(NewEntity(name=f"e{i}", entity_type="t"))
    rows = await store.list_entities(entity_type="t", limit=2)
    assert len(rows) == 2
