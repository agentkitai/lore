"""Migration 027: bi-temporal facts (#67).

Lore stores "facts" (subject–predicate–object assertions) as graph
relationships, which already carry a validity window (valid_from / valid_until).
This exercises the supersede-not-delete + as-of-date-query layer added on top,
on BOTH backends via the parametrized ``store`` fixture:

- ``supersede_relationship`` closes the edge's window, sets ``superseded_by``,
  and appends an auditable correction row (``relationship_supersessions``);
- ``query_relationships(at_time=...)`` / ``facts_at_time`` return the edge that
  was canonical at a past timestamp and the corrected edge after;
- the re-extraction write path (``replace_memory_relationships``) EXPIRES the
  edges a memory drops instead of deleting them — history survives — and leaves
  still-asserted edges untouched (no churn).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lore.persistence import NewEntity, NewRelationship
from lore.services import temporal as temporal_svc


async def _entity(store, name: str, etype: str = "technology"):
    now = datetime.now(timezone.utc)
    return await store.upsert_entity(
        NewEntity(
            name=name,
            entity_type=etype,
            aliases=(),
            description=None,
            first_seen_at=now,
            last_seen_at=now,
        )
    )


@pytest.mark.asyncio
async def test_supersede_relationship_sets_window_and_logs(store):
    a = await _entity(store, "Postgres")
    b = await _entity(store, "pgvector")
    c = await _entity(store, "MySQL")
    old = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="depends_on")
    )
    new = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=c.id, rel_type="depends_on")
    )
    assert old.superseded_by is None

    await temporal_svc.supersede_relationship(
        store, old.id, superseded_by=new.id, reason="switched to MySQL", agent="test"
    )

    refreshed = await store.get_relationship(old.id)
    assert refreshed.superseded_by == new.id
    assert refreshed.valid_until is not None
    assert await store.is_relationship_superseded(old.id) is True

    chain = await store.get_relationship_supersession_chain(old.id)
    assert len(chain) == 1
    assert chain[0].relationship_id == old.id
    assert chain[0].superseded_by == new.id
    assert chain[0].reason == "switched to MySQL"
    assert chain[0].agent == "test"


@pytest.mark.asyncio
async def test_facts_at_time_old_before_new_after(store):
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(days=2)
    a = await _entity(store, "Postgres")
    b = await _entity(store, "pgvector")
    c = await _entity(store, "MySQL")
    old = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id,
                        rel_type="depends_on", valid_from=t0)
    )
    new = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=c.id,
                        rel_type="depends_on", valid_from=now)
    )
    await temporal_svc.supersede_relationship(
        store, old.id, superseded_by=new.id, reason="corrected", agent="test"
    )

    # As-of a day ago (before the correction): old fact is canonical.
    past = await temporal_svc.facts_at_time(store, entity="Postgres", at=now - timedelta(days=1))
    spo_past = {(f.subject, f.predicate, f.object) for f in past}
    assert ("Postgres", "depends_on", "pgvector") in spo_past
    assert ("Postgres", "depends_on", "MySQL") not in spo_past

    # As-of now (after the correction): new fact is canonical, old is gone.
    cur = await temporal_svc.facts_at_time(store, entity="Postgres", at=now + timedelta(seconds=5))
    spo_cur = {(f.subject, f.predicate, f.object) for f in cur}
    assert ("Postgres", "depends_on", "MySQL") in spo_cur
    assert ("Postgres", "depends_on", "pgvector") not in spo_cur


@pytest.mark.asyncio
async def test_facts_at_time_predicate_filter_and_unknown_entity(store):
    now = datetime.now(timezone.utc)
    a = await _entity(store, "ServiceX", etype="project")
    b = await _entity(store, "Redis")
    await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="uses",
                        valid_from=now - timedelta(days=1))
    )
    facts = await temporal_svc.facts_at_time(store, entity="ServiceX", at=now, predicate="uses")
    assert {(f.subject, f.predicate, f.object) for f in facts} == {("ServiceX", "uses", "Redis")}
    # Predicate that doesn't match → no facts.
    assert await temporal_svc.facts_at_time(store, entity="ServiceX", at=now, predicate="owns") == []
    # Unknown entity → empty, not an error.
    assert await temporal_svc.facts_at_time(store, entity="NoSuchEntity", at=now) == []


@pytest.mark.asyncio
async def test_write_path_supersede_not_delete_preserves_history(store):
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(days=2)
    x = await _entity(store, "ServiceX", etype="project")
    y = await _entity(store, "Redis")
    z = await _entity(store, "Kafka")
    mem = "mem_writepath"

    n1 = await store.replace_memory_relationships(mem, [
        NewRelationship(source_entity_id=x.id, target_entity_id=y.id, rel_type="uses", valid_from=t0),
        NewRelationship(source_entity_id=x.id, target_entity_id=z.id, rel_type="uses", valid_from=t0),
    ])
    assert n1 == 2

    active1 = [r for r in await store.query_relationships([x.id]) if r.source_memory_id == mem]
    xy_valid_from = next(r.valid_from for r in active1 if r.target_entity_id == y.id)

    # Re-extract: memory now asserts only X->Redis (drops X->Kafka).
    n2 = await store.replace_memory_relationships(mem, [
        NewRelationship(source_entity_id=x.id, target_entity_id=y.id, rel_type="uses"),
    ])
    assert n2 == 0  # X->Redis still asserted → nothing new inserted

    active2 = [r for r in await store.query_relationships([x.id]) if r.source_memory_id == mem]
    assert len(active2) == 1
    assert active2[0].target_entity_id == y.id
    # Still-asserted edge kept its original valid_from (no churn).
    assert active2[0].valid_from == xy_valid_from

    # Dropped edge was EXPIRED, not deleted: still visible as-of before re-extraction.
    hist = [r for r in await store.query_relationships([x.id], at_time=now - timedelta(days=1))
            if r.source_memory_id == mem]
    assert any(r.target_entity_id == z.id for r in hist)
    # ...and absent from the current view.
    assert not any(r.target_entity_id == z.id for r in active2)


@pytest.mark.asyncio
async def test_supersede_at_time_history(store):
    """is_relationship_superseded honors the as-of timestamp."""
    a = await _entity(store, "EntA")
    b = await _entity(store, "EntB")
    c = await _entity(store, "EntC")
    old = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=b.id, rel_type="rel")
    )
    new = await store.save_relationship(
        NewRelationship(source_entity_id=a.id, target_entity_id=c.id, rel_type="rel")
    )
    before = datetime.now(timezone.utc) - timedelta(hours=1)
    await temporal_svc.supersede_relationship(store, old.id, superseded_by=new.id, reason="x")
    # Superseded now, but NOT as of an hour ago (the event hadn't happened yet).
    assert await store.is_relationship_superseded(old.id) is True
    assert await store.is_relationship_superseded(old.id, at=before) is False
