"""Service tests for graph visualization, stats, clusters, timeline."""

from __future__ import annotations

import pytest

from lore.persistence import (
    NewEntity,
    NewMemory,
    NewMention,
    NewRelationship,
)
from lore.services.graph.graph import (
    get_clusters,
    get_graph_data,
    get_memory_with_graph,
    get_stats,
    get_timeline,
    search_graph_memories,
)


@pytest.mark.asyncio
async def test_search_graph_memories_empty_query(store):
    res = await search_graph_memories(store, "")
    assert res.results == ()
    assert res.total == 0


@pytest.mark.asyncio
async def test_search_graph_memories_returns_hits(store):
    await store.insert_memory(
        NewMemory(org_id="solo", content="kafka streaming", embedding=[0.0] * 384)
    )
    res = await search_graph_memories(store, "kafka")
    assert res.total >= 1
    assert any("kafka" in h.content.lower() for h in res.results)


@pytest.mark.asyncio
async def test_search_graph_memories_truncates_content_to_200(store):
    long_content = "a" * 500
    await store.insert_memory(
        NewMemory(org_id="solo", content=long_content, embedding=[0.0] * 384)
    )
    res = await search_graph_memories(store, "aaaa")
    if res.results:
        assert all(len(h.content) <= 200 for h in res.results)


@pytest.mark.asyncio
async def test_get_memory_with_graph_returns_none_when_missing(store):
    res = await get_memory_with_graph(store, "mem_missing")
    assert res is None


@pytest.mark.asyncio
async def test_get_memory_with_graph_includes_entities_and_related_memories(store):
    e = await store.upsert_entity(NewEntity(name="mwg_e", entity_type="topic"))
    m1 = await store.insert_memory(
        NewMemory(org_id="solo", content="seed memory", embedding=[0.0] * 384)
    )
    m2 = await store.insert_memory(
        NewMemory(org_id="solo", content="related memory", embedding=[0.0] * 384)
    )
    await store.save_mention(NewMention(entity_id=e.id, memory_id=m1.id))
    await store.save_mention(NewMention(entity_id=e.id, memory_id=m2.id))

    res = await get_memory_with_graph(store, m1.id)
    assert res is not None
    assert res.memory.id == m1.id
    entity_ids = {ce.id for ce in res.connected_entities}
    assert e.id in entity_ids
    related_ids = {rm.id for rm in res.connected_memories}
    assert m2.id in related_ids
    assert m1.id not in related_ids  # excluded


@pytest.mark.asyncio
async def test_get_stats_delegates_to_store(store):
    await store.insert_memory(
        NewMemory(org_id="solo", content="stats", embedding=[0.0] * 384)
    )
    s = await get_stats(store)
    assert s.total_memories >= 1


@pytest.mark.asyncio
async def test_get_clusters_invalid_group_by(store):
    with pytest.raises(ValueError):
        await get_clusters(store, group_by="bogus")


@pytest.mark.asyncio
async def test_get_clusters_groups_by_project(store):
    await store.insert_memory(
        NewMemory(org_id="solo", content="ax", embedding=[0.0] * 384, project="alpha")
    )
    await store.insert_memory(
        NewMemory(org_id="solo", content="bx", embedding=[0.0] * 384, project="beta")
    )
    res = await get_clusters(store, group_by="project")
    labels = {c.label for c in res.clusters}
    assert "alpha" in labels
    assert "beta" in labels


@pytest.mark.asyncio
async def test_get_clusters_groups_by_type(store):
    await store.insert_memory(
        NewMemory(
            org_id="solo", content="t1", embedding=[0.0] * 384,
            meta={"type": "lesson"},
        )
    )
    await store.insert_memory(
        NewMemory(
            org_id="solo", content="t2", embedding=[0.0] * 384,
            meta={"type": "fact"},
        )
    )
    res = await get_clusters(store, group_by="type")
    labels = {c.label for c in res.clusters}
    assert {"lesson", "fact"}.issubset(labels)


@pytest.mark.asyncio
async def test_get_timeline_invalid_bucket(store):
    with pytest.raises(ValueError):
        await get_timeline(store, bucket="century")


@pytest.mark.asyncio
async def test_get_timeline_returns_buckets(store):
    await store.insert_memory(
        NewMemory(org_id="solo", content="tl1", embedding=[0.0] * 384)
    )
    res = await get_timeline(store, bucket="day")
    assert len(res.buckets) >= 1


@pytest.mark.asyncio
async def test_get_graph_data_basic(store):
    a = await store.upsert_entity(NewEntity(name="gd_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name="gd_b", entity_type="topic"))
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="graph data test", embedding=[0.0] * 384)
    )
    await store.save_mention(NewMention(entity_id=a.id, memory_id=m.id))
    await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="uses", status="approved",
        )
    )
    data = await get_graph_data(store)
    node_ids = {n.id for n in data.nodes}
    assert m.id in node_ids
    assert a.id in node_ids
    assert b.id in node_ids
    edge_pairs = {(e.source, e.target, e.rel_type) for e in data.edges}
    # Mention edge from memory to entity
    assert (m.id, a.id, "mentions") in edge_pairs
    # Relationship edge from a to b
    assert (a.id, b.id, "uses") in edge_pairs


@pytest.mark.asyncio
async def test_get_graph_data_orphan_filter(store):
    # An entity with no edges
    orphan = await store.upsert_entity(NewEntity(name="lone_orphan", entity_type="topic"))
    data = await get_graph_data(store, include_orphans=False)
    node_ids = {n.id for n in data.nodes}
    assert orphan.id not in node_ids
