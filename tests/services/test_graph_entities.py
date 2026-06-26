"""Service tests for graph entity + topic services."""

from __future__ import annotations

import pytest

from lore.persistence import (
    NewEntity,
    NewMemory,
    NewMention,
    NewRelationship,
)
from lore.services.graph.entities import (
    get_entity,
    get_entity_with_connections,
    get_topic_detail,
    list_topics,
)

ORG = "solo"


@pytest.mark.asyncio
async def test_get_entity_returns_entity(store):
    e = await store.upsert_entity(NewEntity(org_id=ORG, name="svc_a", entity_type="topic"))
    fetched = await get_entity(store, e.id, ORG)
    assert fetched is not None
    assert fetched.id == e.id


@pytest.mark.asyncio
async def test_get_entity_returns_none_when_missing(store):
    assert (await get_entity(store, "ent_missing", ORG)) is None


@pytest.mark.asyncio
async def test_list_topics_filters_by_min_mentions(store):
    await store.upsert_entity(
        NewEntity(org_id=ORG, name="svc_low", entity_type="t", mention_count=1)
    )
    await store.upsert_entity(
        NewEntity(org_id=ORG, name="svc_hot", entity_type="t", mention_count=10)
    )
    rows = await list_topics(store, org_id=ORG, min_mentions=5)
    names = {r.name for r in rows}
    assert "svc_hot" in names
    assert "svc_low" not in names


@pytest.mark.asyncio
async def test_get_topic_detail_returns_none_for_missing(store):
    assert (await get_topic_detail(store, "no_such_topic", org_id=ORG)) is None


@pytest.mark.asyncio
async def test_get_topic_detail_finds_entity_by_normalized_name(store):
    await store.upsert_entity(NewEntity(org_id=ORG, name="redis", entity_type="db"))
    detail = await get_topic_detail(store, "REDIS", org_id=ORG)
    assert detail is not None
    assert detail.entity.name == "redis"


@pytest.mark.asyncio
async def test_get_topic_detail_includes_related_entities(store):
    a = await store.upsert_entity(NewEntity(org_id=ORG, name="td_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(org_id=ORG, name="td_b", entity_type="topic"))
    await store.save_relationship(
        NewRelationship(
            org_id=ORG,
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="uses",
            status="approved",
        )
    )
    detail = await get_topic_detail(store, "td_a", org_id=ORG)
    assert detail is not None
    related_names = {r.name for r in detail.related_entities}
    assert "td_b" in related_names
    # Direction is outgoing because td_a is the source
    outgoing = [r for r in detail.related_entities if r.name == "td_b"]
    assert outgoing[0].direction == "outgoing"


@pytest.mark.asyncio
async def test_get_topic_detail_excludes_pending_relationships(store):
    a = await store.upsert_entity(NewEntity(org_id=ORG, name="ex_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(org_id=ORG, name="ex_b", entity_type="topic"))
    await store.save_relationship(
        NewRelationship(
            org_id=ORG,
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="uses",
            status="pending",
        )
    )
    detail = await get_topic_detail(store, "ex_a", org_id=ORG)
    assert detail is not None
    assert all(r.name != "ex_b" for r in detail.related_entities)


@pytest.mark.asyncio
async def test_get_topic_detail_counts_memories(store):
    e = await store.upsert_entity(NewEntity(org_id=ORG, name="cm", entity_type="topic"))
    for i in range(3):
        m = await store.insert_memory(
            NewMemory(org_id=ORG, content=f"m{i}", embedding=[0.0] * 384)
        )
        await store.save_mention(NewMention(org_id=ORG, entity_id=e.id, memory_id=m.id))
    detail = await get_topic_detail(store, "cm", org_id=ORG)
    assert detail.memory_count == 3
    assert len(detail.memories) <= 20


@pytest.mark.asyncio
async def test_get_entity_with_connections_returns_none_when_missing(store):
    assert (await get_entity_with_connections(store, "ent_missing", org_id=ORG)) is None


@pytest.mark.asyncio
async def test_get_entity_with_connections_returns_data(store):
    a = await store.upsert_entity(NewEntity(org_id=ORG, name="ec_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(org_id=ORG, name="ec_b", entity_type="topic"))
    m = await store.insert_memory(
        NewMemory(org_id=ORG, content="content for ec_a", embedding=[0.0] * 384)
    )
    await store.save_mention(NewMention(org_id=ORG, entity_id=a.id, memory_id=m.id))
    await store.save_relationship(
        NewRelationship(
            org_id=ORG,
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="uses", status="approved",
        )
    )
    detail = await get_entity_with_connections(store, a.id, org_id=ORG)
    assert detail is not None
    assert detail.entity.id == a.id
    assert any(cm.id == m.id for cm in detail.connected_memories)
    related_names = {r.name for r in detail.connected_entities}
    assert "ec_b" in related_names


@pytest.mark.asyncio
async def test_get_entity_with_connections_dedupe_other_entity(store):
    """Two relationships A→B with different rel_types should produce ONE connected_entities entry for B."""
    a = await store.upsert_entity(NewEntity(org_id=ORG, name="dd_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(org_id=ORG, name="dd_b", entity_type="topic"))
    await store.save_relationship(
        NewRelationship(
            org_id=ORG,
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="uses", status="approved",
        )
    )
    await store.save_relationship(
        NewRelationship(
            org_id=ORG,
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="depends_on", status="approved",
        )
    )
    detail = await get_entity_with_connections(store, a.id, org_id=ORG)
    matching = [r for r in detail.connected_entities if r.name == "dd_b"]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_get_entity_with_connections_respects_max_related(store):
    a = await store.upsert_entity(NewEntity(org_id=ORG, name="mr_main", entity_type="topic"))
    for i in range(5):
        other = await store.upsert_entity(
            NewEntity(org_id=ORG, name=f"mr_other_{i}", entity_type="topic")
        )
        await store.save_relationship(
            NewRelationship(
                org_id=ORG,
                source_entity_id=a.id, target_entity_id=other.id,
                rel_type="uses", status="approved",
            )
        )
    detail = await get_entity_with_connections(store, a.id, org_id=ORG, max_related=2)
    assert len(detail.connected_entities) == 2
