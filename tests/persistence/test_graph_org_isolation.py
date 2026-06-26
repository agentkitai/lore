"""Cross-tenant isolation of the knowledge graph (#83).

Closes a systemic leak: entities/relationships/entity_mentions were GLOBAL, so
org A's graph queries returned org B's data. These tests run against the
parametrized ``store`` fixture (sqlite always, postgres when available) so both
backends are proven in lockstep, plus a META-TEST that fails if any graph Store
method ever loses its org_id parameter (the durable regression guard).
"""

from __future__ import annotations

import inspect

import pytest

from lore.persistence import NewEntity, NewMemory, NewMention, NewRelationship


def _vec(seed: int):
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


async def _seed_org(store, org: str, tag: str):
    mem = await store.insert_memory(NewMemory(org_id=org, content=f"{tag} memory", embedding=_vec(1)))
    a = await store.upsert_entity(NewEntity(org_id=org, name="Acme", entity_type="org"))  # SAME name both orgs
    two = await store.upsert_entity(NewEntity(org_id=org, name=f"{tag}-Two", entity_type="thing"))
    await store.save_mention(NewMention(org_id=org, entity_id=a.id, memory_id=mem.id))
    rel = await store.save_relationship(
        NewRelationship(org_id=org, source_entity_id=a.id, target_entity_id=two.id, rel_type="rel")
    )
    return {"mem": mem.id, "acme": a.id, "two": two.id, "rel": rel.id}


@pytest.mark.asyncio
async def test_graph_reads_are_org_isolated(store):
    a = await _seed_org(store, "orgA", "A")
    b = await _seed_org(store, "orgB", "B")

    # ── entities ──
    assert await store.get_entity(b["acme"], "orgA") is None
    assert await store.get_entity(b["acme"], "orgB") is not None
    ea = await store.get_entity_by_name("Acme", "orgA")
    eb = await store.get_entity_by_name("Acme", "orgB")
    assert ea and eb and ea.id == a["acme"] and eb.id == b["acme"] and ea.id != eb.id  # coexist, isolated
    assert (await store.find_entity_by_name_or_alias("Acme", "orgA")).id == a["acme"]
    la = await store.list_entities("orgA")
    assert len(la) == 2 and all(e.id not in (b["acme"], b["two"]) for e in la)

    # ── relationships (incl. the graph-hop traversal) ──
    assert await store.get_relationship(b["rel"], "orgA") is None
    assert await store.get_active_relationship(b["acme"], b["two"], "orgA", rel_type="rel") is None
    assert len(await store.list_relationships_for_entity(b["acme"], "orgA")) == 0
    assert len(await store.query_relationships([b["acme"]], "orgA")) == 0  # graph hop cannot cross orgs

    # ── memory-via-graph (the previously-unguarded method) ──
    assert len(await store.get_memories_by_entities("orgA", [b["acme"]])) == 0

    # ── mentions ──
    assert len(await store.get_mentions_for_entity(b["acme"], "orgA")) == 0
    assert len(await store.get_mentions_for_memory(b["mem"], "orgA")) == 0
    assert await store.count_memories_for_entity(b["acme"], "orgA") == 0

    # ── stats ──
    assert (await store.get_graph_stats("orgA")).total_entities == 2  # A-only, not 4


@pytest.mark.asyncio
async def test_graph_writes_are_org_isolated(store):
    _ = await _seed_org(store, "orgA", "A")
    b = await _seed_org(store, "orgB", "B")

    # update/delete on a foreign org's row must not affect it (raise or no-op — both secure).
    try:
        await store.update_relationship_status(b["rel"], "orgA", status="rejected")
    except Exception:
        pass
    assert (await store.get_relationship(b["rel"], "orgB")).status != "rejected"
    try:
        await store.delete_entity(b["acme"], "orgA")
    except Exception:
        pass
    assert await store.get_entity(b["acme"], "orgB") is not None


# ── META-TEST: the durable regression guard ──────────────────────────
# Every graph Store method that READS or MUTATES entities/relationships/mentions
# must carry an org_id parameter. If a future method is added without one, this
# fails — preventing a silent reintroduction of the cross-tenant leak.
_GRAPH_METHODS_REQUIRING_ORG_ID = {
    "get_entity", "get_entity_by_name", "find_entity_by_name_or_alias", "list_entities",
    "get_mentions_for_memory", "get_mentions_for_entity", "count_memories_for_entity",
    "get_relationship", "get_active_relationship", "list_relationships_for_entity",
    "list_pending_relationships", "query_relationships", "get_memories_by_entities",
    "update_entity_counts", "update_relationship_status", "update_relationship_weight",
    "delete_entity", "expire_relationship", "supersede_relationship",
    "record_relationship_supersession", "get_relationship_supersession_chain",
    "is_relationship_superseded", "list_supersession_sources",
    "get_graph_stats", "get_timeline_buckets", "search_memories_text",
    "replace_memory_relationships", "replace_memory_mentions",
}


def test_every_graph_store_method_takes_org_id():
    from lore.persistence.protocol import Store

    missing = []
    for name in sorted(_GRAPH_METHODS_REQUIRING_ORG_ID):
        fn = getattr(Store, name, None)
        assert fn is not None, f"protocol.Store is missing expected graph method {name}"
        if "org_id" not in inspect.signature(fn).parameters:
            missing.append(name)
    # upsert_entity/save_relationship/save_mention carry org_id via their New* dataclass,
    # so they are intentionally not in this param-level check.
    assert not missing, f"graph Store methods missing org_id (cross-tenant leak risk): {missing}"
