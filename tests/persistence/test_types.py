"""Tests for persistence-layer dataclasses."""

import dataclasses
from datetime import datetime, timezone

import pytest

from lore.persistence.types import (
    GraphStats,
    MemoryFilter,
    MemoryPatch,
    NewEntity,
    NewMemory,
    NewMention,
    NewProfile,
    NewRelationship,
    PendingRelationshipRow,
    ProfilePatch,
    RecallParams,
    ResolvedProfile,
    ScoredMemory,
    StoredEntity,
    StoredMemory,
    StoredMention,
    StoredProfile,
    StoredRelationship,
    TimelineBucketRow,
)


def test_new_memory_required_fields():
    nm = NewMemory(
        org_id="org_1",
        content="hello world",
        embedding=[0.0] * 384,
    )
    assert nm.content == "hello world"
    assert len(nm.embedding) == 384
    assert nm.tags == ()  # default empty
    assert nm.meta == {}


def test_stored_memory_round_trip():
    now = datetime.now(timezone.utc)
    m = StoredMemory(
        id="mem_01",
        org_id="org_1",
        content="hello",
        context=None,
        tags=("a", "b"),
        confidence=0.9,
        source=None,
        project="proj",
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={"type": "lesson"},
        importance_score=1.0,
        access_count=0,
        last_accessed_at=None,
    )
    assert m.id == "mem_01"
    assert m.tags == ("a", "b")


def test_scored_memory_extends_stored():
    now = datetime.now(timezone.utc)
    sm = ScoredMemory(
        id="mem_02",
        org_id="org_1",
        content="ranked",
        context=None,
        tags=(),
        confidence=1.0,
        source=None,
        project=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
        importance_score=1.0,
        access_count=0,
        last_accessed_at=None,
        score=0.87,
    )
    assert sm.score == 0.87


def test_memory_filter_defaults():
    f = MemoryFilter(org_id="org_1")
    assert f.project is None
    assert f.type is None
    assert f.tier is None
    assert f.limit is None
    assert f.include_expired is False


def test_memory_patch_partial_update():
    p = MemoryPatch(content="new text")
    assert p.content == "new text"
    assert p.tags is None  # explicit "no change"


def test_recall_params_required_query_vec():
    rp = RecallParams(
        org_id="org_1",
        query_vec=[0.0] * 384,
        limit=10,
        min_score=0.3,
    )
    assert rp.limit == 10
    assert rp.project is None


# Graph slice dataclass tests


def test_new_entity_required_fields():
    ne = NewEntity(
        name="Alice",
        entity_type="person",
    )
    assert ne.name == "Alice"
    assert ne.entity_type == "person"
    assert ne.aliases == ()
    assert ne.description is None
    assert ne.metadata == {}
    assert ne.mention_count == 1
    assert ne.first_seen_at is None
    assert ne.last_seen_at is None


def test_new_entity_with_aliases():
    ne = NewEntity(
        name="Bob",
        entity_type="person",
        aliases=("Robert", "Bobby"),
        description="A person",
        metadata={"role": "engineer"},
        mention_count=5,
    )
    assert ne.aliases == ("Robert", "Bobby")
    assert ne.description == "A person"
    assert ne.metadata == {"role": "engineer"}
    assert ne.mention_count == 5


def test_stored_entity_round_trip():
    now = datetime.now(timezone.utc)
    se = StoredEntity(
        id="ent_01",
        name="Charlie",
        entity_type="person",
        aliases=("Chuck",),
        description="Developer",
        metadata={"team": "backend"},
        mention_count=3,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    assert se.id == "ent_01"
    assert se.name == "Charlie"
    assert se.mention_count == 3
    assert se.aliases == ("Chuck",)


def test_new_mention_defaults():
    nm = NewMention(
        entity_id="ent_01",
        memory_id="mem_01",
    )
    assert nm.entity_id == "ent_01"
    assert nm.memory_id == "mem_01"
    assert nm.mention_type == "explicit"
    assert nm.confidence == 1.0


def test_new_mention_with_custom_values():
    nm = NewMention(
        entity_id="ent_02",
        memory_id="mem_02",
        mention_type="implicit",
        confidence=0.75,
    )
    assert nm.mention_type == "implicit"
    assert nm.confidence == 0.75


def test_stored_mention_round_trip():
    now = datetime.now(timezone.utc)
    sm = StoredMention(
        id="mnt_01",
        entity_id="ent_01",
        memory_id="mem_01",
        mention_type="explicit",
        confidence=0.95,
        created_at=now,
    )
    assert sm.id == "mnt_01"
    assert sm.confidence == 0.95
    assert sm.created_at == now


def test_new_relationship_defaults():
    nr = NewRelationship(
        source_entity_id="ent_01",
        target_entity_id="ent_02",
        rel_type="knows",
    )
    assert nr.source_entity_id == "ent_01"
    assert nr.target_entity_id == "ent_02"
    assert nr.rel_type == "knows"
    assert nr.weight == 1.0
    assert nr.properties == {}
    assert nr.source_fact_id is None
    assert nr.source_memory_id is None
    assert nr.valid_from is None
    assert nr.valid_until is None
    assert nr.status == "approved"


def test_new_relationship_with_properties():
    props = {"confidence": 0.9, "since": "2025-01-01"}
    nr = NewRelationship(
        source_entity_id="ent_01",
        target_entity_id="ent_02",
        rel_type="collaborates_with",
        weight=0.8,
        properties=props,
        source_memory_id="mem_01",
        status="pending",
    )
    assert nr.properties == props
    assert nr.weight == 0.8
    assert nr.source_memory_id == "mem_01"
    assert nr.status == "pending"


def test_stored_relationship_round_trip():
    now = datetime.now(timezone.utc)
    sr = StoredRelationship(
        id="rel_01",
        source_entity_id="ent_01",
        target_entity_id="ent_02",
        rel_type="manages",
        weight=1.0,
        properties={"title": "manager"},
        source_fact_id="fact_01",
        source_memory_id=None,
        valid_from=now,
        valid_until=None,
        status="approved",
        created_at=now,
        updated_at=now,
    )
    assert sr.id == "rel_01"
    assert sr.rel_type == "manages"
    assert sr.source_fact_id == "fact_01"
    assert sr.valid_from == now


def test_graph_stats_construction():
    gs = GraphStats(
        total_memories=100,
        total_entities=50,
        total_relationships=75,
        by_type={"lesson": 40, "fact": 60},
        by_project={"proj_a": 50, "proj_b": 50},
        by_entity_type={"person": 30, "project": 20},
        top_entities=[
            {"name": "Alice", "type": "person", "mention_count": 10},
            {"name": "Bob", "type": "person", "mention_count": 8},
        ],
        avg_importance=0.65,
        recent_24h=5,
        recent_7d=15,
        oldest_memory=None,
        newest_memory=None,
    )
    assert gs.total_memories == 100
    assert gs.total_entities == 50
    assert gs.total_relationships == 75
    assert len(gs.top_entities) == 2
    assert gs.avg_importance == 0.65


def test_timeline_bucket_row_construction():
    now = datetime.now(timezone.utc)
    tbr = TimelineBucketRow(
        bucket_date=now,
        mem_type="lesson",
        count=5,
    )
    assert tbr.bucket_date == now
    assert tbr.mem_type == "lesson"
    assert tbr.count == 5


def test_pending_relationship_row_construction():
    now = datetime.now(timezone.utc)
    prr = PendingRelationshipRow(
        id="rel_pending_01",
        source_entity_id="ent_01",
        target_entity_id="ent_02",
        rel_type="references",
        weight=0.5,
        source_memory_id="mem_01",
        created_at=now,
        source_name="Alice",
        source_entity_type="person",
        source_mentions=5,
        target_name="Bob",
        target_entity_type="person",
        target_mentions=3,
    )
    assert prr.id == "rel_pending_01"
    assert prr.source_name == "Alice"
    assert prr.target_mentions == 3
    assert prr.weight == 0.5


# Profile dataclass tests


def test_new_profile_defaults():
    np = NewProfile(org_id="org_1", name="default")
    assert np.org_id == "org_1"
    assert np.name == "default"
    assert np.semantic_weight == 1.0
    assert np.graph_weight == 1.0
    assert np.recency_bias == 30.0
    assert np.tier_filters is None
    assert np.min_score == 0.3
    assert np.max_results == 10
    assert np.is_preset is False
    assert np.k is None
    assert np.threshold is None
    assert np.rerank is False
    assert np.include_graph is True


def test_new_profile_all_fields():
    np = NewProfile(
        org_id="org_2",
        name="strict",
        semantic_weight=0.8,
        graph_weight=0.6,
        recency_bias=14.0,
        tier_filters=["hot", "warm"],
        min_score=0.5,
        max_results=5,
        is_preset=True,
        k=3,
        threshold=0.7,
        rerank=True,
        include_graph=False,
    )
    assert np.semantic_weight == 0.8
    assert np.graph_weight == 0.6
    assert np.recency_bias == 14.0
    assert np.tier_filters == ["hot", "warm"]
    assert np.min_score == 0.5
    assert np.max_results == 5
    assert np.is_preset is True
    assert np.k == 3
    assert np.threshold == 0.7
    assert np.rerank is True
    assert np.include_graph is False


def test_new_profile_frozen():
    np = NewProfile(org_id="org_1", name="default")
    with pytest.raises(dataclasses.FrozenInstanceError):
        np.name = "other"  # type: ignore[misc]


def test_new_profile_slots():
    np = NewProfile(org_id="org_1", name="default")
    assert not hasattr(np, "__dict__")


def test_stored_profile_round_trip():
    now = datetime.now(timezone.utc)
    sp = StoredProfile(
        id="prof_01",
        org_id="org_1",
        name="balanced",
        semantic_weight=1.0,
        graph_weight=1.0,
        recency_bias=30.0,
        tier_filters=None,
        min_score=0.3,
        max_results=10,
        is_preset=False,
        k=None,
        threshold=None,
        rerank=False,
        include_graph=True,
        created_at=now,
        updated_at=now,
    )
    assert sp.id == "prof_01"
    assert sp.org_id == "org_1"
    assert sp.name == "balanced"
    assert sp.created_at == now
    assert sp.updated_at == now


def test_stored_profile_frozen():
    now = datetime.now(timezone.utc)
    sp = StoredProfile(
        id="prof_02",
        org_id="org_1",
        name="test",
        semantic_weight=1.0,
        graph_weight=1.0,
        recency_bias=30.0,
        tier_filters=None,
        min_score=0.3,
        max_results=10,
        is_preset=False,
        k=None,
        threshold=None,
        rerank=False,
        include_graph=True,
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sp.name = "mutated"  # type: ignore[misc]


def test_stored_profile_slots():
    now = datetime.now(timezone.utc)
    sp = StoredProfile(
        id="prof_03",
        org_id="org_1",
        name="test",
        semantic_weight=1.0,
        graph_weight=1.0,
        recency_bias=30.0,
        tier_filters=None,
        min_score=0.3,
        max_results=10,
        is_preset=False,
        k=None,
        threshold=None,
        rerank=False,
        include_graph=True,
        created_at=now,
        updated_at=now,
    )
    assert not hasattr(sp, "__dict__")


def test_profile_patch_all_none():
    pp = ProfilePatch()
    assert pp.name is None
    assert pp.semantic_weight is None
    assert pp.graph_weight is None
    assert pp.recency_bias is None
    assert pp.tier_filters is None
    assert pp.min_score is None
    assert pp.max_results is None
    assert pp.is_preset is None
    assert pp.k is None
    assert pp.threshold is None
    assert pp.rerank is None
    assert pp.include_graph is None


def test_profile_patch_partial():
    pp = ProfilePatch(name="renamed", min_score=0.6)
    assert pp.name == "renamed"
    assert pp.min_score == 0.6
    assert pp.semantic_weight is None


def test_profile_patch_frozen():
    pp = ProfilePatch(name="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        pp.name = "y"  # type: ignore[misc]


def test_profile_patch_slots():
    pp = ProfilePatch()
    assert not hasattr(pp, "__dict__")


def test_resolved_profile_stored_source():
    rp = ResolvedProfile(
        name="balanced",
        source="stored",
        semantic_weight=1.0,
        graph_weight=1.0,
        recency_bias=30.0,
        min_score=0.3,
        max_results=10,
        tier_filters=None,
        k=None,
        threshold=None,
        rerank=False,
        include_graph=True,
    )
    assert rp.name == "balanced"
    assert rp.source == "stored"
    assert rp.semantic_weight == 1.0
    assert rp.tier_filters is None
    assert rp.rerank is False
    assert rp.include_graph is True


def test_resolved_profile_default_source():
    rp = ResolvedProfile(
        name="default",
        source="default",
        semantic_weight=0.5,
        graph_weight=0.5,
        recency_bias=60.0,
        min_score=0.4,
        max_results=20,
        tier_filters=["hot"],
        k=10,
        threshold=0.8,
        rerank=True,
        include_graph=False,
    )
    assert rp.source == "default"
    assert rp.tier_filters == ["hot"]
    assert rp.k == 10
    assert rp.threshold == 0.8
    assert rp.rerank is True
    assert rp.include_graph is False


def test_resolved_profile_frozen():
    rp = ResolvedProfile(
        name="default",
        source="default",
        semantic_weight=1.0,
        graph_weight=1.0,
        recency_bias=30.0,
        min_score=0.3,
        max_results=10,
        tier_filters=None,
        k=None,
        threshold=None,
        rerank=False,
        include_graph=True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        rp.source = "stored"  # type: ignore[misc]


def test_resolved_profile_slots():
    rp = ResolvedProfile(
        name="default",
        source="default",
        semantic_weight=1.0,
        graph_weight=1.0,
        recency_bias=30.0,
        min_score=0.3,
        max_results=10,
        tier_filters=None,
        k=None,
        threshold=None,
        rerank=False,
        include_graph=True,
    )
    assert not hasattr(rp, "__dict__")
