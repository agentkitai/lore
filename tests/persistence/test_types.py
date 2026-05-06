"""Tests for persistence-layer dataclasses."""

import dataclasses
from datetime import datetime, timezone

import pytest

from lore.persistence.types import (
    GraphStats,
    MemoryFilter,
    MemoryPatch,
    NewApiKey,
    NewEntity,
    NewMember,
    NewMemory,
    NewMention,
    NewProfile,
    NewRecommendationFeedback,
    NewRelationship,
    NewRetrievalEvent,
    NewWorkspace,
    PendingRelationshipRow,
    ProfilePatch,
    RecallParams,
    RecommendationCandidate,
    ResolvedProfile,
    ScoredMemory,
    StoredApiKey,
    StoredEntity,
    StoredMember,
    StoredMemory,
    StoredMention,
    StoredProfile,
    StoredRecommendationConfig,
    StoredRelationship,
    StoredWorkspace,
    TimelineBucketRow,
    WorkspacePatch,
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


# Identity dataclass tests — Workspace


def test_new_workspace_defaults():
    nw = NewWorkspace(org_id="org_1", name="Acme", slug="acme")
    assert nw.org_id == "org_1"
    assert nw.name == "Acme"
    assert nw.slug == "acme"
    assert nw.settings == {}


def test_new_workspace_all_fields():
    nw = NewWorkspace(
        org_id="org_2",
        name="Beta Corp",
        slug="beta-corp",
        settings={"timezone": "UTC", "theme": "dark"},
    )
    assert nw.settings == {"timezone": "UTC", "theme": "dark"}


def test_new_workspace_frozen():
    nw = NewWorkspace(org_id="org_1", name="X", slug="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        nw.name = "Y"  # type: ignore[misc]


def test_new_workspace_slots():
    nw = NewWorkspace(org_id="org_1", name="X", slug="x")
    assert not hasattr(nw, "__dict__")


def test_stored_workspace_round_trip():
    now = datetime.now(timezone.utc)
    sw = StoredWorkspace(
        id="ws_01",
        org_id="org_1",
        name="Acme",
        slug="acme",
        settings={"theme": "light"},
        created_at=now,
        archived_at=None,
    )
    assert sw.id == "ws_01"
    assert sw.org_id == "org_1"
    assert sw.slug == "acme"
    assert sw.settings == {"theme": "light"}
    assert sw.created_at == now
    assert sw.archived_at is None


def test_stored_workspace_frozen():
    now = datetime.now(timezone.utc)
    sw = StoredWorkspace(
        id="ws_02",
        org_id="org_1",
        name="Acme",
        slug="acme",
        settings={},
        created_at=now,
        archived_at=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sw.name = "Other"  # type: ignore[misc]


def test_stored_workspace_slots():
    now = datetime.now(timezone.utc)
    sw = StoredWorkspace(
        id="ws_03",
        org_id="org_1",
        name="Acme",
        slug="acme",
        settings={},
        created_at=now,
        archived_at=None,
    )
    assert not hasattr(sw, "__dict__")


def test_workspace_patch_all_none():
    wp = WorkspacePatch()
    assert wp.name is None
    assert wp.settings is None


def test_workspace_patch_partial():
    wp = WorkspacePatch(name="Renamed")
    assert wp.name == "Renamed"
    assert wp.settings is None


def test_workspace_patch_frozen():
    wp = WorkspacePatch(name="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        wp.name = "y"  # type: ignore[misc]


def test_workspace_patch_slots():
    wp = WorkspacePatch()
    assert not hasattr(wp, "__dict__")


# Identity dataclass tests — Member


def test_new_member_defaults():
    nm = NewMember(workspace_id="ws_01", user_id="usr_01")
    assert nm.workspace_id == "ws_01"
    assert nm.user_id == "usr_01"
    assert nm.role == "writer"


def test_new_member_all_fields():
    nm = NewMember(workspace_id="ws_02", user_id="usr_02", role="admin")
    assert nm.role == "admin"


def test_new_member_frozen():
    nm = NewMember(workspace_id="ws_01", user_id="usr_01")
    with pytest.raises(dataclasses.FrozenInstanceError):
        nm.role = "admin"  # type: ignore[misc]


def test_new_member_slots():
    nm = NewMember(workspace_id="ws_01", user_id="usr_01")
    assert not hasattr(nm, "__dict__")


def test_stored_member_round_trip():
    now = datetime.now(timezone.utc)
    sm = StoredMember(
        id="mbr_01",
        workspace_id="ws_01",
        user_id="usr_01",
        role="writer",
        invited_at=now,
        accepted_at=None,
    )
    assert sm.id == "mbr_01"
    assert sm.workspace_id == "ws_01"
    assert sm.user_id == "usr_01"
    assert sm.role == "writer"
    assert sm.invited_at == now
    assert sm.accepted_at is None


def test_stored_member_accepted():
    now = datetime.now(timezone.utc)
    sm = StoredMember(
        id="mbr_02",
        workspace_id="ws_01",
        user_id=None,
        role="reader",
        invited_at=now,
        accepted_at=now,
    )
    assert sm.user_id is None
    assert sm.accepted_at == now


def test_stored_member_frozen():
    now = datetime.now(timezone.utc)
    sm = StoredMember(
        id="mbr_03",
        workspace_id="ws_01",
        user_id="usr_01",
        role="writer",
        invited_at=now,
        accepted_at=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sm.role = "admin"  # type: ignore[misc]


def test_stored_member_slots():
    now = datetime.now(timezone.utc)
    sm = StoredMember(
        id="mbr_04",
        workspace_id="ws_01",
        user_id="usr_01",
        role="writer",
        invited_at=now,
        accepted_at=None,
    )
    assert not hasattr(sm, "__dict__")


# Identity dataclass tests — ApiKey


def test_new_api_key_defaults():
    nak = NewApiKey(
        org_id="org_1",
        name="My Key",
        key_hash="abc123",
        key_prefix="lore_",
    )
    assert nak.org_id == "org_1"
    assert nak.name == "My Key"
    assert nak.key_hash == "abc123"
    assert nak.key_prefix == "lore_"
    assert nak.project is None
    assert nak.is_root is False
    assert nak.workspace_id is None


def test_new_api_key_all_fields():
    nak = NewApiKey(
        org_id="org_2",
        name="Root Key",
        key_hash="deadbeef",
        key_prefix="lore_r_",
        project="proj_a",
        is_root=True,
        workspace_id="ws_01",
    )
    assert nak.project == "proj_a"
    assert nak.is_root is True
    assert nak.workspace_id == "ws_01"


def test_new_api_key_frozen():
    nak = NewApiKey(
        org_id="org_1",
        name="Key",
        key_hash="h",
        key_prefix="p_",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        nak.name = "Other"  # type: ignore[misc]


def test_new_api_key_slots():
    nak = NewApiKey(
        org_id="org_1",
        name="Key",
        key_hash="h",
        key_prefix="p_",
    )
    assert not hasattr(nak, "__dict__")


def test_stored_api_key_round_trip():
    now = datetime.now(timezone.utc)
    sak = StoredApiKey(
        id="key_01",
        org_id="org_1",
        name="Prod Key",
        key_hash="abc123",
        key_prefix="lore_",
        project=None,
        is_root=False,
        workspace_id=None,
        revoked_at=None,
        created_at=now,
        last_used_at=None,
    )
    assert sak.id == "key_01"
    assert sak.org_id == "org_1"
    assert sak.name == "Prod Key"
    assert sak.key_hash == "abc123"
    assert sak.is_root is False
    assert sak.revoked_at is None
    assert sak.created_at == now
    assert sak.last_used_at is None


def test_stored_api_key_revoked():
    now = datetime.now(timezone.utc)
    sak = StoredApiKey(
        id="key_02",
        org_id="org_1",
        name="Old Key",
        key_hash="xyz",
        key_prefix="lore_",
        project="proj_b",
        is_root=True,
        workspace_id="ws_02",
        revoked_at=now,
        created_at=now,
        last_used_at=now,
    )
    assert sak.project == "proj_b"
    assert sak.is_root is True
    assert sak.workspace_id == "ws_02"
    assert sak.revoked_at == now
    assert sak.last_used_at == now


def test_stored_api_key_frozen():
    now = datetime.now(timezone.utc)
    sak = StoredApiKey(
        id="key_03",
        org_id="org_1",
        name="Key",
        key_hash="h",
        key_prefix="p_",
        project=None,
        is_root=False,
        workspace_id=None,
        revoked_at=None,
        created_at=now,
        last_used_at=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sak.name = "Mutated"  # type: ignore[misc]


def test_stored_api_key_slots():
    now = datetime.now(timezone.utc)
    sak = StoredApiKey(
        id="key_04",
        org_id="org_1",
        name="Key",
        key_hash="h",
        key_prefix="p_",
        project=None,
        is_root=False,
        workspace_id=None,
        revoked_at=None,
        created_at=now,
        last_used_at=None,
    )
    assert not hasattr(sak, "__dict__")


# ── NewRetrievalEvent ─────────────────────────────────────────────


def test_new_retrieval_event_defaults():
    nre = NewRetrievalEvent(
        org_id="org_1",
        query="test query",
        results_count=3,
        scores=[0.9, 0.8, 0.7],
        memory_ids=["m1", "m2", "m3"],
        avg_score=None,
        max_score=None,
        min_score_threshold=None,
        query_time_ms=None,
    )
    assert nre.org_id == "org_1"
    assert nre.query == "test query"
    assert nre.results_count == 3
    assert nre.scores == [0.9, 0.8, 0.7]
    assert nre.memory_ids == ["m1", "m2", "m3"]
    assert nre.avg_score is None
    assert nre.max_score is None
    assert nre.min_score_threshold is None
    assert nre.query_time_ms is None
    assert nre.project is None
    assert nre.format is None


def test_new_retrieval_event_all_fields():
    nre = NewRetrievalEvent(
        org_id="org_2",
        query="full query",
        results_count=2,
        scores=[0.95, 0.85],
        memory_ids=["ma", "mb"],
        avg_score=0.9,
        max_score=0.95,
        min_score_threshold=0.5,
        query_time_ms=42.5,
        project="proj_a",
        format="json",
    )
    assert nre.avg_score == 0.9
    assert nre.max_score == 0.95
    assert nre.min_score_threshold == 0.5
    assert nre.query_time_ms == 42.5
    assert nre.project == "proj_a"
    assert nre.format == "json"


def test_new_retrieval_event_frozen():
    nre = NewRetrievalEvent(
        org_id="org_1",
        query="q",
        results_count=0,
        scores=[],
        memory_ids=[],
        avg_score=None,
        max_score=None,
        min_score_threshold=None,
        query_time_ms=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        nre.query = "mutated"  # type: ignore[misc]


def test_new_retrieval_event_slots():
    nre = NewRetrievalEvent(
        org_id="org_1",
        query="q",
        results_count=0,
        scores=[],
        memory_ids=[],
        avg_score=None,
        max_score=None,
        min_score_threshold=None,
        query_time_ms=None,
    )
    assert not hasattr(nre, "__dict__")


# ── RecommendationCandidate ───────────────────────────────────────


def test_recommendation_candidate_defaults():
    now = datetime.now(timezone.utc)
    rc = RecommendationCandidate(
        id="mem_01",
        content="some content",
        embedding=[0.1] * 384,
        metadata={"type": "lesson"},
        created_at=now,
        access_count=0,
        last_accessed_at=None,
    )
    assert rc.id == "mem_01"
    assert rc.content == "some content"
    assert len(rc.embedding) == 384
    assert rc.metadata == {"type": "lesson"}
    assert rc.created_at == now
    assert rc.access_count == 0
    assert rc.last_accessed_at is None


def test_recommendation_candidate_all_fields():
    now = datetime.now(timezone.utc)
    earlier = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rc = RecommendationCandidate(
        id="mem_02",
        content="another memory",
        embedding=[0.5] * 512,
        metadata={"project": "proj_a", "score": 0.9},
        created_at=earlier,
        access_count=7,
        last_accessed_at=now,
    )
    assert rc.id == "mem_02"
    assert len(rc.embedding) == 512
    assert rc.metadata["project"] == "proj_a"
    assert rc.access_count == 7
    assert rc.last_accessed_at == now


def test_recommendation_candidate_frozen():
    now = datetime.now(timezone.utc)
    rc = RecommendationCandidate(
        id="mem_03",
        content="frozen test",
        embedding=[0.0],
        metadata={},
        created_at=now,
        access_count=0,
        last_accessed_at=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        rc.content = "mutated"  # type: ignore[misc]


def test_recommendation_candidate_slots():
    now = datetime.now(timezone.utc)
    rc = RecommendationCandidate(
        id="mem_04",
        content="slots test",
        embedding=[0.0],
        metadata={},
        created_at=now,
        access_count=0,
        last_accessed_at=None,
    )
    assert not hasattr(rc, "__dict__")


# ── StoredRecommendationConfig ────────────────────────────────────


def test_stored_recommendation_config_defaults():
    now = datetime.now(timezone.utc)
    src = StoredRecommendationConfig(
        id="cfg_01",
        workspace_id=None,
        agent_id=None,
        aggressiveness=0.5,
        enabled=True,
        max_suggestions=3,
        cooldown_minutes=60,
        updated_at=now,
    )
    assert src.id == "cfg_01"
    assert src.workspace_id is None
    assert src.agent_id is None
    assert src.aggressiveness == 0.5
    assert src.enabled is True
    assert src.max_suggestions == 3
    assert src.cooldown_minutes == 60
    assert src.updated_at == now


def test_stored_recommendation_config_all_fields():
    now = datetime.now(timezone.utc)
    src = StoredRecommendationConfig(
        id="cfg_02",
        workspace_id="ws_01",
        agent_id="agent_01",
        aggressiveness=0.9,
        enabled=False,
        max_suggestions=10,
        cooldown_minutes=30,
        updated_at=now,
    )
    assert src.workspace_id == "ws_01"
    assert src.agent_id == "agent_01"
    assert src.aggressiveness == 0.9
    assert src.enabled is False
    assert src.max_suggestions == 10
    assert src.cooldown_minutes == 30


def test_stored_recommendation_config_frozen():
    now = datetime.now(timezone.utc)
    src = StoredRecommendationConfig(
        id="cfg_03",
        workspace_id=None,
        agent_id=None,
        aggressiveness=0.5,
        enabled=True,
        max_suggestions=3,
        cooldown_minutes=60,
        updated_at=now,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        src.enabled = False  # type: ignore[misc]


def test_stored_recommendation_config_slots():
    now = datetime.now(timezone.utc)
    src = StoredRecommendationConfig(
        id="cfg_04",
        workspace_id=None,
        agent_id=None,
        aggressiveness=0.5,
        enabled=True,
        max_suggestions=3,
        cooldown_minutes=60,
        updated_at=now,
    )
    assert not hasattr(src, "__dict__")


# ── NewRecommendationFeedback ─────────────────────────────────────


def test_new_recommendation_feedback_defaults():
    nrf = NewRecommendationFeedback(
        org_id="org_1",
        memory_id="mem_01",
        actor_id="usr_01",
        feedback="positive",
    )
    assert nrf.org_id == "org_1"
    assert nrf.memory_id == "mem_01"
    assert nrf.actor_id == "usr_01"
    assert nrf.feedback == "positive"
    assert nrf.workspace_id is None
    assert nrf.signal == "manual"
    assert nrf.context_hash is None


def test_new_recommendation_feedback_all_fields():
    nrf = NewRecommendationFeedback(
        org_id="org_2",
        memory_id="mem_02",
        actor_id="usr_02",
        feedback="negative",
        workspace_id="ws_01",
        signal="implicit",
        context_hash="abc123",
    )
    assert nrf.feedback == "negative"
    assert nrf.workspace_id == "ws_01"
    assert nrf.signal == "implicit"
    assert nrf.context_hash == "abc123"


def test_new_recommendation_feedback_frozen():
    nrf = NewRecommendationFeedback(
        org_id="org_1",
        memory_id="mem_01",
        actor_id="usr_01",
        feedback="positive",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        nrf.feedback = "negative"  # type: ignore[misc]


def test_new_recommendation_feedback_slots():
    nrf = NewRecommendationFeedback(
        org_id="org_1",
        memory_id="mem_01",
        actor_id="usr_01",
        feedback="positive",
    )
    assert not hasattr(nrf, "__dict__")
