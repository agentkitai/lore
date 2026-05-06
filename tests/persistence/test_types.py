"""Tests for persistence-layer dataclasses."""

import dataclasses
from datetime import datetime, timezone

import pytest

from lore.persistence.types import (
    DailyStatRow,
    ExportedMemory,
    GraphStats,
    MemoryFilter,
    MemoryPatch,
    NewApiKey,
    NewConversationJob,
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
    RetrievalAnalyticsResult,
    ScoreDistributionBucket,
    ScoredMemory,
    StoredApiKey,
    StoredAuditEntry,
    StoredConversationJob,
    StoredEntity,
    StoredMember,
    StoredMemory,
    StoredMention,
    StoredProfile,
    StoredRecommendationConfig,
    StoredRelationship,
    StoredWorkspace,
    TimelineBucketRow,
    TopQueryRow,
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


# ── NewConversationJob ────────────────────────────────────────────


def test_new_conversation_job_defaults():
    ncj = NewConversationJob(
        org_id="org_1",
        message_count=3,
        messages_json='[{"role":"user","content":"hello"}]',
    )
    assert ncj.org_id == "org_1"
    assert ncj.message_count == 3
    assert ncj.messages_json == '[{"role":"user","content":"hello"}]'
    assert ncj.user_id is None
    assert ncj.session_id is None
    assert ncj.project is None


def test_new_conversation_job_all_fields():
    ncj = NewConversationJob(
        org_id="org_2",
        message_count=5,
        messages_json='[{"role":"assistant","content":"hi"}]',
        user_id="usr_01",
        session_id="sess_01",
        project="proj_alpha",
    )
    assert ncj.user_id == "usr_01"
    assert ncj.session_id == "sess_01"
    assert ncj.project == "proj_alpha"


def test_new_conversation_job_frozen():
    ncj = NewConversationJob(
        org_id="org_1",
        message_count=1,
        messages_json="[]",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ncj.org_id = "other"  # type: ignore[misc]


def test_new_conversation_job_slots():
    ncj = NewConversationJob(
        org_id="org_1",
        message_count=1,
        messages_json="[]",
    )
    assert not hasattr(ncj, "__dict__")


# ── StoredConversationJob ─────────────────────────────────────────


def test_stored_conversation_job_all_fields():
    now = datetime.now(timezone.utc)
    scj = StoredConversationJob(
        id="conv_01",
        org_id="org_1",
        status="completed",
        message_count=4,
        messages_json='[{"role":"user","content":"hello"}]',
        user_id="usr_01",
        session_id="sess_01",
        project="proj_alpha",
        memory_ids=["mem_01", "mem_02"],
        memories_extracted=2,
        duplicates_skipped=1,
        error=None,
        processing_time_ms=42,
        created_at=now,
        completed_at=now,
    )
    assert scj.id == "conv_01"
    assert scj.org_id == "org_1"
    assert scj.status == "completed"
    assert scj.message_count == 4
    assert scj.user_id == "usr_01"
    assert scj.session_id == "sess_01"
    assert scj.project == "proj_alpha"
    assert list(scj.memory_ids) == ["mem_01", "mem_02"]
    assert scj.memories_extracted == 2
    assert scj.duplicates_skipped == 1
    assert scj.error is None
    assert scj.processing_time_ms == 42
    assert scj.completed_at == now


def test_stored_conversation_job_optional_nulls():
    now = datetime.now(timezone.utc)
    scj = StoredConversationJob(
        id="conv_02",
        org_id="org_2",
        status="pending",
        message_count=2,
        messages_json="[]",
        user_id=None,
        session_id=None,
        project=None,
        memory_ids=[],
        memories_extracted=0,
        duplicates_skipped=0,
        error=None,
        processing_time_ms=0,
        created_at=now,
        completed_at=None,
    )
    assert scj.user_id is None
    assert scj.session_id is None
    assert scj.project is None
    assert scj.error is None
    assert scj.completed_at is None


def test_stored_conversation_job_frozen():
    now = datetime.now(timezone.utc)
    scj = StoredConversationJob(
        id="conv_03",
        org_id="org_1",
        status="pending",
        message_count=1,
        messages_json="[]",
        user_id=None,
        session_id=None,
        project=None,
        memory_ids=[],
        memories_extracted=0,
        duplicates_skipped=0,
        error=None,
        processing_time_ms=0,
        created_at=now,
        completed_at=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        scj.status = "completed"  # type: ignore[misc]


def test_stored_conversation_job_slots():
    now = datetime.now(timezone.utc)
    scj = StoredConversationJob(
        id="conv_04",
        org_id="org_1",
        status="pending",
        message_count=1,
        messages_json="[]",
        user_id=None,
        session_id=None,
        project=None,
        memory_ids=[],
        memories_extracted=0,
        duplicates_skipped=0,
        error=None,
        processing_time_ms=0,
        created_at=now,
        completed_at=None,
    )
    assert not hasattr(scj, "__dict__")


# ── ExportedMemory ────────────────────────────────────────────────


def test_exported_memory_defaults():
    now = datetime.now(timezone.utc)
    em = ExportedMemory(
        id="mem_exp_01",
        org_id="org_1",
        content="exported content",
        context=None,
        tags=(),
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )
    assert em.id == "mem_exp_01"
    assert em.org_id == "org_1"
    assert em.content == "exported content"
    assert em.context is None
    assert em.tags == ()
    assert em.confidence == 0.5
    assert em.source is None
    assert em.project is None
    assert em.embedding is None
    assert em.created_at == now
    assert em.updated_at == now
    assert em.expires_at is None
    assert em.upvotes == 0
    assert em.downvotes == 0
    assert em.meta == {}


def test_exported_memory_all_fields():
    now = datetime.now(timezone.utc)
    expires = datetime(2027, 1, 1, tzinfo=timezone.utc)
    em = ExportedMemory(
        id="mem_exp_02",
        org_id="org_2",
        content="full memory",
        context="some context",
        tags=("python", "backend"),
        confidence=0.9,
        source="conversation",
        project="proj_alpha",
        embedding=[0.1, 0.2, 0.3],
        created_at=now,
        updated_at=now,
        expires_at=expires,
        upvotes=5,
        downvotes=1,
        meta={"type": "lesson", "quality": "high"},
    )
    assert em.context == "some context"
    assert em.tags == ("python", "backend")
    assert em.confidence == 0.9
    assert em.source == "conversation"
    assert em.project == "proj_alpha"
    assert list(em.embedding) == [0.1, 0.2, 0.3]
    assert em.expires_at == expires
    assert em.upvotes == 5
    assert em.downvotes == 1
    assert em.meta == {"type": "lesson", "quality": "high"}


def test_exported_memory_frozen():
    now = datetime.now(timezone.utc)
    em = ExportedMemory(
        id="mem_exp_03",
        org_id="org_1",
        content="frozen test",
        context=None,
        tags=(),
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        em.content = "mutated"  # type: ignore[misc]


def test_exported_memory_slots():
    now = datetime.now(timezone.utc)
    em = ExportedMemory(
        id="mem_exp_04",
        org_id="org_1",
        content="slots test",
        context=None,
        tags=(),
        confidence=0.5,
        source=None,
        project=None,
        embedding=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
    )
    assert not hasattr(em, "__dict__")


# ── MemoryFilter extension ────────────────────────────────────────


def test_memory_filter_new_fields_default_none():
    f = MemoryFilter(org_id="org_1")
    assert f.project is None
    assert f.type is None
    assert f.tier is None
    assert f.limit is None
    assert f.include_expired is False
    assert f.text_query is None
    assert f.min_reputation is None


def test_memory_filter_with_text_query_and_min_reputation():
    f = MemoryFilter(
        org_id="org_1",
        project="proj_a",
        type="lesson",
        tier="hot",
        tags=("python",),
        limit=20,
        offset=5,
        include_expired=True,
        text_query="async patterns",
        min_reputation=3,
    )
    assert f.text_query == "async patterns"
    assert f.min_reputation == 3
    assert f.project == "proj_a"
    assert f.type == "lesson"
    assert f.tier == "hot"
    assert f.tags == ("python",)
    assert f.limit == 20
    assert f.offset == 5
    assert f.include_expired is True


# ── Dashboard slice dataclasses ───────────────────────────────────


# StoredAuditEntry


def test_stored_audit_entry_all_fields():
    now = datetime.now(timezone.utc)
    ae = StoredAuditEntry(
        id=1,
        org_id="org_1",
        workspace_id="ws_01",
        actor_id="usr_01",
        actor_type="user",
        action="memory.create",
        resource_type="memory",
        resource_id="mem_01",
        metadata={"key": "value"},
        ip_address="127.0.0.1",
        created_at=now,
    )
    assert ae.id == 1
    assert ae.org_id == "org_1"
    assert ae.workspace_id == "ws_01"
    assert ae.actor_id == "usr_01"
    assert ae.actor_type == "user"
    assert ae.action == "memory.create"
    assert ae.resource_type == "memory"
    assert ae.resource_id == "mem_01"
    assert ae.metadata == {"key": "value"}
    assert ae.ip_address == "127.0.0.1"
    assert ae.created_at == now


def test_stored_audit_entry_optional_nulls():
    now = datetime.now(timezone.utc)
    ae = StoredAuditEntry(
        id=2,
        org_id="org_1",
        workspace_id=None,
        actor_id="svc_01",
        actor_type="service",
        action="memory.expire",
        resource_type=None,
        resource_id=None,
        metadata={},
        ip_address=None,
        created_at=now,
    )
    assert ae.workspace_id is None
    assert ae.resource_type is None
    assert ae.resource_id is None
    assert ae.ip_address is None
    assert ae.metadata == {}


def test_stored_audit_entry_frozen():
    now = datetime.now(timezone.utc)
    ae = StoredAuditEntry(
        id=3,
        org_id="org_1",
        workspace_id=None,
        actor_id="usr_01",
        actor_type="user",
        action="memory.delete",
        resource_type=None,
        resource_id=None,
        metadata={},
        ip_address=None,
        created_at=now,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ae.action = "mutated"  # type: ignore[misc]


def test_stored_audit_entry_slots():
    now = datetime.now(timezone.utc)
    ae = StoredAuditEntry(
        id=4,
        org_id="org_1",
        workspace_id=None,
        actor_id="usr_01",
        actor_type="user",
        action="memory.read",
        resource_type=None,
        resource_id=None,
        metadata={},
        ip_address=None,
        created_at=now,
    )
    assert not hasattr(ae, "__dict__")


# ScoreDistributionBucket


def test_score_distribution_bucket_construction():
    b = ScoreDistributionBucket(bucket="0.7-0.8", count=12)
    assert b.bucket == "0.7-0.8"
    assert b.count == 12


def test_score_distribution_bucket_frozen():
    b = ScoreDistributionBucket(bucket="0.9-1.0", count=5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.count = 99  # type: ignore[misc]


def test_score_distribution_bucket_slots():
    b = ScoreDistributionBucket(bucket="0.5-0.6", count=3)
    assert not hasattr(b, "__dict__")


# TopQueryRow


def test_top_query_row_all_fields():
    tqr = TopQueryRow(query="recall python async", count=42, avg_score=0.87)
    assert tqr.query == "recall python async"
    assert tqr.count == 42
    assert tqr.avg_score == 0.87


def test_top_query_row_optional_avg_score():
    tqr = TopQueryRow(query="no results query", count=1, avg_score=None)
    assert tqr.avg_score is None


def test_top_query_row_frozen():
    tqr = TopQueryRow(query="test", count=1, avg_score=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        tqr.count = 99  # type: ignore[misc]


def test_top_query_row_slots():
    tqr = TopQueryRow(query="test", count=1, avg_score=None)
    assert not hasattr(tqr, "__dict__")


# DailyStatRow


def test_daily_stat_row_all_fields():
    dsr = DailyStatRow(date="2026-05-01", queries=100, avg_score=0.75, hit_rate=0.9)
    assert dsr.date == "2026-05-01"
    assert dsr.queries == 100
    assert dsr.avg_score == 0.75
    assert dsr.hit_rate == 0.9


def test_daily_stat_row_optional_avg_score():
    dsr = DailyStatRow(date="2026-05-02", queries=0, avg_score=None, hit_rate=0.0)
    assert dsr.avg_score is None
    assert dsr.hit_rate == 0.0


def test_daily_stat_row_frozen():
    dsr = DailyStatRow(date="2026-05-03", queries=5, avg_score=None, hit_rate=1.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        dsr.queries = 99  # type: ignore[misc]


def test_daily_stat_row_slots():
    dsr = DailyStatRow(date="2026-05-04", queries=5, avg_score=None, hit_rate=1.0)
    assert not hasattr(dsr, "__dict__")


# RetrievalAnalyticsResult


def test_retrieval_analytics_result_all_fields():
    buckets = [ScoreDistributionBucket(bucket="0.8-0.9", count=10)]
    top_queries = [TopQueryRow(query="foo", count=5, avg_score=0.9)]
    daily = [DailyStatRow(date="2026-05-01", queries=50, avg_score=0.8, hit_rate=0.95)]
    rar = RetrievalAnalyticsResult(
        total_queries=200,
        queries_with_results=180,
        queries_empty=20,
        avg_results_per_query=3.5,
        avg_score=0.78,
        avg_max_score=0.92,
        avg_latency_ms=45.0,
        p95_latency_ms=120.0,
        score_distribution=buckets,
        top_queries=top_queries,
        unique_memories_retrieved=55,
        total_memories=500,
        daily_stats=daily,
    )
    assert rar.total_queries == 200
    assert rar.queries_with_results == 180
    assert rar.queries_empty == 20
    assert rar.avg_results_per_query == 3.5
    assert rar.avg_score == 0.78
    assert rar.avg_max_score == 0.92
    assert rar.avg_latency_ms == 45.0
    assert rar.p95_latency_ms == 120.0
    assert list(rar.score_distribution) == buckets
    assert list(rar.top_queries) == top_queries
    assert rar.unique_memories_retrieved == 55
    assert rar.total_memories == 500
    assert list(rar.daily_stats) == daily


def test_retrieval_analytics_result_optional_nulls():
    rar = RetrievalAnalyticsResult(
        total_queries=0,
        queries_with_results=0,
        queries_empty=0,
        avg_results_per_query=0.0,
        avg_score=None,
        avg_max_score=None,
        avg_latency_ms=None,
        p95_latency_ms=None,
        score_distribution=[],
        top_queries=[],
        unique_memories_retrieved=0,
        total_memories=0,
        daily_stats=[],
    )
    assert rar.avg_score is None
    assert rar.avg_max_score is None
    assert rar.avg_latency_ms is None
    assert rar.p95_latency_ms is None
    assert list(rar.score_distribution) == []
    assert list(rar.top_queries) == []
    assert list(rar.daily_stats) == []


def test_retrieval_analytics_result_frozen():
    rar = RetrievalAnalyticsResult(
        total_queries=1,
        queries_with_results=1,
        queries_empty=0,
        avg_results_per_query=1.0,
        avg_score=None,
        avg_max_score=None,
        avg_latency_ms=None,
        p95_latency_ms=None,
        score_distribution=[],
        top_queries=[],
        unique_memories_retrieved=1,
        total_memories=10,
        daily_stats=[],
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        rar.total_queries = 99  # type: ignore[misc]


def test_retrieval_analytics_result_slots():
    rar = RetrievalAnalyticsResult(
        total_queries=1,
        queries_with_results=1,
        queries_empty=0,
        avg_results_per_query=1.0,
        avg_score=None,
        avg_max_score=None,
        avg_latency_ms=None,
        p95_latency_ms=None,
        score_distribution=[],
        top_queries=[],
        unique_memories_retrieved=1,
        total_memories=10,
        daily_stats=[],
    )
    assert not hasattr(rar, "__dict__")
