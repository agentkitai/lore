"""Contract tests for the GraphOps slice of Store.

Each method is exercised against every Store implementation parametrized in
tests/persistence/conftest.py — Phase 1A wires Postgres only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lore.persistence import (
    NewEntity,
    NewMemory,
    NewMention,
    NewRelationship,
    Store,
    StoredEntity,
    StoredMention,
    StoredRelationship,
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


@pytest.mark.asyncio
async def test_update_entity_counts_increments(store: Store):
    e = await store.upsert_entity(
        NewEntity(name="grafana", entity_type="tool", mention_count=2)
    )
    new_seen = datetime.now(timezone.utc)
    await store.update_entity_counts(
        e.id, mention_delta=3, last_seen_at=new_seen
    )
    after = await store.get_entity(e.id)
    assert after is not None
    assert after.mention_count == 5
    assert after.last_seen_at >= e.last_seen_at


@pytest.mark.asyncio
async def test_update_entity_counts_does_not_regress_last_seen(store: Store):
    e = await store.upsert_entity(NewEntity(name="prom", entity_type="tool"))
    earlier = e.last_seen_at - timedelta(days=1)
    await store.update_entity_counts(
        e.id, mention_delta=1, last_seen_at=earlier
    )
    after = await store.get_entity(e.id)
    assert after.last_seen_at == e.last_seen_at  # unchanged (GREATEST)
    assert after.mention_count == e.mention_count + 1


@pytest.mark.asyncio
async def test_update_entity_counts_silent_on_missing(store: Store):
    # Should not raise; just does nothing.
    await store.update_entity_counts(
        "ent_missing", mention_delta=10, last_seen_at=datetime.now(timezone.utc)
    )


@pytest.mark.asyncio
async def test_delete_entity_returns_true_when_deleted(store: Store):
    e = await store.upsert_entity(NewEntity(name="todelete", entity_type="x"))
    assert (await store.delete_entity(e.id)) is True
    assert (await store.get_entity(e.id)) is None


@pytest.mark.asyncio
async def test_delete_entity_returns_false_when_missing(store: Store):
    assert (await store.delete_entity("ent_missing")) is False


# ── T6: Mention ops ────────────────────────────────────────────────────────


async def _setup_entity_and_memory(store: Store, *, ent_name="topic", mem_content="content"):
    e = await store.upsert_entity(NewEntity(name=ent_name, entity_type="topic"))
    m = await store.insert_memory(
        NewMemory(org_id="solo", content=mem_content, embedding=[0.0] * 384)
    )
    return e, m


@pytest.mark.asyncio
async def test_save_mention_round_trip(store: Store):
    e, m = await _setup_entity_and_memory(store)
    await store.save_mention(NewMention(entity_id=e.id, memory_id=m.id))
    fetched = await store.get_mentions_for_memory(m.id)
    assert len(fetched) == 1
    assert fetched[0].entity_id == e.id
    assert fetched[0].memory_id == m.id
    assert fetched[0].mention_type == "explicit"
    assert fetched[0].confidence == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_save_mention_is_idempotent(store: Store):
    e, m = await _setup_entity_and_memory(store)
    await store.save_mention(NewMention(entity_id=e.id, memory_id=m.id))
    await store.save_mention(NewMention(entity_id=e.id, memory_id=m.id))
    fetched = await store.get_mentions_for_memory(m.id)
    assert len(fetched) == 1


@pytest.mark.asyncio
async def test_get_mentions_for_entity_filters_correctly(store: Store):
    e1, m1 = await _setup_entity_and_memory(store, ent_name="alpha", mem_content="a")
    e2 = await store.upsert_entity(NewEntity(name="beta", entity_type="topic"))
    m2 = await store.insert_memory(
        NewMemory(org_id="solo", content="b", embedding=[0.0] * 384)
    )
    await store.save_mention(NewMention(entity_id=e1.id, memory_id=m1.id))
    await store.save_mention(NewMention(entity_id=e2.id, memory_id=m2.id))
    only_e1 = await store.get_mentions_for_entity(e1.id)
    assert {m.memory_id for m in only_e1} == {m1.id}


@pytest.mark.asyncio
async def test_get_mentions_for_entity_respects_limit(store: Store):
    e = await store.upsert_entity(NewEntity(name="hot", entity_type="topic"))
    for i in range(5):
        m = await store.insert_memory(
            NewMemory(org_id="solo", content=f"c{i}", embedding=[0.0] * 384)
        )
        await store.save_mention(NewMention(entity_id=e.id, memory_id=m.id))
    rows = await store.get_mentions_for_entity(e.id, limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_count_memories_for_entity(store: Store):
    e = await store.upsert_entity(NewEntity(name="counted", entity_type="topic"))
    assert (await store.count_memories_for_entity(e.id)) == 0
    for i in range(3):
        m = await store.insert_memory(
            NewMemory(org_id="solo", content=f"d{i}", embedding=[0.0] * 384)
        )
        await store.save_mention(NewMention(entity_id=e.id, memory_id=m.id))
    assert (await store.count_memories_for_entity(e.id)) == 3


@pytest.mark.asyncio
async def test_count_memories_distinct_per_memory(store: Store):
    """save_mention dedupes by (entity, memory) so duplicate calls don't inflate the count."""
    e = await store.upsert_entity(NewEntity(name="dedup", entity_type="topic"))
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="x", embedding=[0.0] * 384)
    )
    await store.save_mention(NewMention(entity_id=e.id, memory_id=m.id))
    await store.save_mention(NewMention(entity_id=e.id, memory_id=m.id))
    assert (await store.count_memories_for_entity(e.id)) == 1


# ---------------------------------------------------------------------------
# T7 — relationship insert / get
# ---------------------------------------------------------------------------


async def _two_entities(store: Store, *, src="alpha", tgt="beta"):
    a = await store.upsert_entity(NewEntity(name=src, entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name=tgt, entity_type="topic"))
    return a, b


@pytest.mark.asyncio
async def test_save_relationship_round_trip(store: Store):
    a, b = await _two_entities(store)
    rel = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="depends_on",
            weight=0.7,
        )
    )
    assert isinstance(rel, StoredRelationship)
    assert rel.id.startswith("rel_")
    assert rel.source_entity_id == a.id
    assert rel.weight == pytest.approx(0.7)
    assert rel.valid_until is None
    assert rel.status == "approved"


@pytest.mark.asyncio
async def test_save_relationship_default_valid_from_is_now(store: Store):
    a, b = await _two_entities(store, src="x1", tgt="x2")
    before = datetime.now(timezone.utc)
    rel = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    after = datetime.now(timezone.utc)
    assert before <= rel.valid_from <= after


@pytest.mark.asyncio
async def test_get_relationship_round_trip(store: Store):
    a, b = await _two_entities(store, src="g1", tgt="g2")
    rel = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="works_on")
    )
    fetched = await store.get_relationship(rel.id)
    assert fetched is not None
    assert fetched.id == rel.id


@pytest.mark.asyncio
async def test_get_relationship_returns_none_when_missing(store: Store):
    assert (await store.get_relationship("rel_missing")) is None


@pytest.mark.asyncio
async def test_get_active_relationship_finds_active(store: Store):
    a, b = await _two_entities(store, src="a1", tgt="a2")
    rel = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    active = await store.get_active_relationship(a.id, b.id, rel_type="uses")
    assert active is not None
    assert active.id == rel.id


@pytest.mark.asyncio
async def test_get_active_relationship_ignores_different_type(store: Store):
    a, b = await _two_entities(store, src="t1", tgt="t2")
    await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    none_match = await store.get_active_relationship(a.id, b.id, rel_type="depends_on")
    assert none_match is None


@pytest.mark.asyncio
async def test_get_active_relationship_returns_none_for_expired(store: Store):
    a, b = await _two_entities(store, src="e1", tgt="e2")
    past_until = datetime.now(timezone.utc)
    await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="part_of",
            valid_until=past_until,
        )
    )
    # Even though the row exists, it's expired (valid_until IS NOT NULL)
    none_match = await store.get_active_relationship(a.id, b.id, rel_type="part_of")
    assert none_match is None


@pytest.mark.asyncio
async def test_save_relationship_with_properties_round_trip(store: Store):
    a, b = await _two_entities(store, src="p1", tgt="p2")
    rel = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="created_by",
            properties={"verified": True, "source": "manual"},
        )
    )
    assert rel.properties == {"verified": True, "source": "manual"}


# ---------------------------------------------------------------------------
# T8 — relationship lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_relationships_for_entity_either_side(store: Store):
    a, b = await _two_entities(store, src="x", tgt="y")
    c = await store.upsert_entity(NewEntity(name="z", entity_type="topic"))
    r1 = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    r2 = await store.save_relationship(
        NewRelationship(source_entity_id=c.id, target_entity_id=a.id, rel_type="depends_on")
    )
    rows = await store.list_relationships_for_entity(a.id)
    ids = {r.id for r in rows}
    assert {r1.id, r2.id}.issubset(ids)


@pytest.mark.asyncio
async def test_list_relationships_for_entity_filters_by_status(store: Store):
    a, b = await _two_entities(store, src="s1", tgt="s2")
    r_pending = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="uses", status="pending",
        )
    )
    r_approved = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="depends_on", status="approved",
        )
    )
    only_pending = await store.list_relationships_for_entity(a.id, status="pending")
    assert {r.id for r in only_pending} == {r_pending.id}


@pytest.mark.asyncio
async def test_list_relationships_respects_limit(store: Store):
    a, b = await _two_entities(store, src="l1", tgt="l2")
    for i in range(5):
        await store.save_relationship(
            NewRelationship(
                source_entity_id=a.id, target_entity_id=b.id,
                rel_type=f"rel_{i}",
            )
        )
    rows = await store.list_relationships_for_entity(a.id, limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_update_relationship_status_round_trip(store: Store):
    a, b = await _two_entities(store, src="u1", tgt="u2")
    rel = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="uses", status="pending",
        )
    )
    updated = await store.update_relationship_status(rel.id, status="approved")
    assert updated.status == "approved"
    assert updated.id == rel.id


@pytest.mark.asyncio
async def test_update_relationship_status_raises_when_missing(store: Store):
    from lore.persistence.exceptions import StoreNotFoundError
    with pytest.raises(StoreNotFoundError):
        await store.update_relationship_status("rel_missing", status="approved")


@pytest.mark.asyncio
async def test_update_relationship_weight_changes_weight(store: Store):
    a, b = await _two_entities(store, src="w1", tgt="w2")
    rel = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="uses", weight=0.3,
        )
    )
    await store.update_relationship_weight(rel.id, weight=0.9)
    after = await store.get_relationship(rel.id)
    assert after.weight == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_update_relationship_weight_silent_on_missing(store: Store):
    await store.update_relationship_weight("rel_missing", weight=0.5)


@pytest.mark.asyncio
async def test_expire_relationship_sets_valid_until(store: Store):
    a, b = await _two_entities(store, src="e1", tgt="e2")
    rel = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    assert rel.valid_until is None
    await store.expire_relationship(rel.id)
    after = await store.get_relationship(rel.id)
    assert after.valid_until is not None
    # And get_active_relationship now returns None
    active = await store.get_active_relationship(a.id, b.id, rel_type="uses")
    assert active is None


@pytest.mark.asyncio
async def test_list_pending_relationships_returns_only_pending(store: Store):
    a, b = await _two_entities(store, src="lp1", tgt="lp2")
    pending = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="uses", status="pending",
        )
    )
    await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="depends_on", status="approved",
        )
    )
    rows = await store.list_pending_relationships()
    ids = {r.id for r in rows}
    assert pending.id in ids


@pytest.mark.asyncio
async def test_list_pending_relationships_includes_joined_entity_info(store: Store):
    a = await store.upsert_entity(
        NewEntity(name="src_ent", entity_type="topic", mention_count=4)
    )
    b = await store.upsert_entity(
        NewEntity(name="tgt_ent", entity_type="library", mention_count=7)
    )
    rel = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="uses", status="pending",
        )
    )
    rows = await store.list_pending_relationships()
    matching = [r for r in rows if r.id == rel.id]
    assert len(matching) == 1
    row = matching[0]
    assert row.source_name == "src_ent"
    assert row.source_entity_type == "topic"
    assert row.source_mentions == 4
    assert row.target_name == "tgt_ent"
    assert row.target_entity_type == "library"
    assert row.target_mentions == 7


@pytest.mark.asyncio
async def test_list_pending_relationships_filter_by_type(store: Store):
    a, b = await _two_entities(store, src="ft1", tgt="ft2")
    r_uses = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="uses", status="pending",
        )
    )
    await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id, target_entity_id=b.id,
            rel_type="depends_on", status="pending",
        )
    )
    only_uses = await store.list_pending_relationships(rel_type="uses")
    ids = {r.id for r in only_uses}
    assert ids == {r_uses.id}


@pytest.mark.asyncio
async def test_save_rejected_pattern_round_trip(store: Store):
    await store.save_rejected_pattern(
        "alpha", "beta", "uses", reason="not relevant"
    )
    # Re-call with the same triple — should be idempotent (no error).
    await store.save_rejected_pattern(
        "alpha", "beta", "uses", reason="updated reason"
    )


@pytest.mark.asyncio
async def test_save_rejected_pattern_separate_triples_independent(store: Store):
    await store.save_rejected_pattern("a", "b", "uses")
    await store.save_rejected_pattern("a", "b", "depends_on")
    await store.save_rejected_pattern("a", "c", "uses")
    # All three different rows — no conflict between them. (Smoke test.)


# ---------------------------------------------------------------------------
# T10 — query_relationships (graph traversal hop query)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_relationships_outbound_only(store: Store):
    a = await store.upsert_entity(NewEntity(name="qo_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name="qo_b", entity_type="topic"))
    c = await store.upsert_entity(NewEntity(name="qo_c", entity_type="topic"))
    out_rel = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    await store.save_relationship(
        NewRelationship(source_entity_id=c.id, target_entity_id=a.id, rel_type="depends_on")
    )
    rows = await store.query_relationships([a.id], direction="outbound")
    ids = {r.id for r in rows}
    assert ids == {out_rel.id}


@pytest.mark.asyncio
async def test_query_relationships_inbound_only(store: Store):
    a, b = await _two_entities(store, src="qi_a", tgt="qi_b")
    in_rel = await store.save_relationship(
        NewRelationship(source_entity_id=b.id, target_entity_id=a.id, rel_type="uses")
    )
    await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="depends_on")
    )
    rows = await store.query_relationships([a.id], direction="inbound")
    ids = {r.id for r in rows}
    assert ids == {in_rel.id}


@pytest.mark.asyncio
async def test_query_relationships_both(store: Store):
    a, b = await _two_entities(store, src="qb_a", tgt="qb_b")
    r_out = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    r_in = await store.save_relationship(
        NewRelationship(source_entity_id=b.id, target_entity_id=a.id, rel_type="depends_on")
    )
    rows = await store.query_relationships([a.id], direction="both")
    ids = {r.id for r in rows}
    assert ids == {r_out.id, r_in.id}


@pytest.mark.asyncio
async def test_query_relationships_active_only_excludes_expired(store: Store):
    a, b = await _two_entities(store, src="qa_a", tgt="qa_b")
    rel = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    await store.expire_relationship(rel.id)
    rows = await store.query_relationships([a.id], active_only=True)
    assert all(r.id != rel.id for r in rows)


@pytest.mark.asyncio
async def test_query_relationships_active_only_false_includes_expired(store: Store):
    a, b = await _two_entities(store, src="qaf_a", tgt="qaf_b")
    rel = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    await store.expire_relationship(rel.id)
    rows = await store.query_relationships([a.id], active_only=False)
    assert any(r.id == rel.id for r in rows)


@pytest.mark.asyncio
async def test_query_relationships_at_time(store: Store):
    a, b = await _two_entities(store, src="qt_a", tgt="qt_b")
    # Save relationship that becomes valid in the past
    past_from = datetime.now(timezone.utc) - timedelta(days=2)
    past_until = datetime.now(timezone.utc) - timedelta(days=1)
    rel = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="uses",
            valid_from=past_from,
            valid_until=past_until,
        )
    )
    # Querying at a time within the validity window should find it
    middle = past_from + timedelta(hours=12)
    rows = await store.query_relationships(
        [a.id], direction="both", active_only=False, at_time=middle
    )
    ids = {r.id for r in rows}
    assert rel.id in ids
    # Querying at a time after valid_until should not
    later = datetime.now(timezone.utc)
    rows_later = await store.query_relationships(
        [a.id], direction="both", active_only=False, at_time=later
    )
    later_ids = {r.id for r in rows_later}
    assert rel.id not in later_ids


@pytest.mark.asyncio
async def test_query_relationships_filters_by_rel_types(store: Store):
    a, b = await _two_entities(store, src="qrt_a", tgt="qrt_b")
    r_uses = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses")
    )
    await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="depends_on")
    )
    rows = await store.query_relationships([a.id], rel_types=["uses"])
    ids = {r.id for r in rows}
    assert ids == {r_uses.id}


@pytest.mark.asyncio
async def test_query_relationships_empty_input_returns_empty(store: Store):
    rows = await store.query_relationships([])
    assert rows == []


@pytest.mark.asyncio
async def test_query_relationships_invalid_direction_raises(store: Store):
    with pytest.raises(ValueError):
        await store.query_relationships(["ent_x"], direction="upstream")
