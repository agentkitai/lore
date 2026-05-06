"""Contract tests for the PolicyOps slice of Store — get_profile / get_profile_by_name.

These tests run against every Store implementation (Phase 1A: Postgres only).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from lore.persistence import IntegrityError, NewProfile, Store, StoredProfile


# ── helpers ────────────────────────────────────────────────────────────────────


async def _insert_profile(
    store,
    *,
    org_id: str = "test-org",
    name: str = "test-profile-1",
    semantic_weight: float = 0.8,
    graph_weight: float = 0.5,
    recency_bias: float = 14.0,
    tier_filters=None,
    min_score: float = 0.25,
    max_results: int = 5,
    is_preset: bool = False,
    k=None,
    threshold=None,
    rerank: bool = False,
    include_graph: bool = True,
) -> str:
    """Insert a retrieval_profile row via raw SQL and return its id."""
    profile_id = f"prof_{uuid.uuid4().hex[:16]}"
    conn = store._conn
    await conn.execute(
        """
        INSERT INTO retrieval_profiles (
            id, org_id, name,
            semantic_weight, graph_weight, recency_bias,
            tier_filters, min_score, max_results, is_preset,
            k, threshold, rerank, include_graph,
            created_at, updated_at
        ) VALUES (
            $1, $2, $3,
            $4, $5, $6,
            $7, $8, $9, $10,
            $11, $12, $13, $14,
            NOW(), NOW()
        )
        """,
        profile_id,
        org_id,
        name,
        semantic_weight,
        graph_weight,
        recency_bias,
        list(tier_filters) if tier_filters is not None else None,
        min_score,
        max_results,
        is_preset,
        k,
        threshold,
        rerank,
        include_graph,
    )
    return profile_id


# ── get_profile tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_profile_round_trip(store: Store):
    profile_id = await _insert_profile(
        store,
        org_id="org-gp",
        name="test-profile-rt",
        semantic_weight=0.75,
        graph_weight=0.4,
        recency_bias=7.0,
        tier_filters=["tier1", "tier2"],
        min_score=0.35,
        max_results=8,
        is_preset=False,
        k=20,
        threshold=0.6,
        rerank=True,
        include_graph=False,
    )

    result = await store.get_profile(profile_id)

    assert result is not None
    assert isinstance(result, StoredProfile)
    assert result.id == profile_id
    assert result.org_id == "org-gp"
    assert result.name == "test-profile-rt"
    assert result.semantic_weight == pytest.approx(0.75)
    assert result.graph_weight == pytest.approx(0.4)
    assert result.recency_bias == pytest.approx(7.0)
    assert tuple(result.tier_filters) == ("tier1", "tier2")
    assert result.min_score == pytest.approx(0.35)
    assert result.max_results == 8
    assert result.is_preset is False
    assert result.k == 20
    assert result.threshold == pytest.approx(0.6)
    assert result.rerank is True
    assert result.include_graph is False
    assert isinstance(result.created_at, datetime)
    assert isinstance(result.updated_at, datetime)


@pytest.mark.asyncio
async def test_get_profile_returns_none_when_missing(store: Store):
    result = await store.get_profile("prof_does_not_exist")
    assert result is None


# ── get_profile_by_name tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_profile_by_name_round_trip(store: Store):
    profile_id = await _insert_profile(
        store,
        org_id="org-bn",
        name="test-profile-byname",
        semantic_weight=0.9,
        graph_weight=0.6,
    )

    result = await store.get_profile_by_name("org-bn", "test-profile-byname")

    assert result is not None
    assert isinstance(result, StoredProfile)
    assert result.id == profile_id
    assert result.org_id == "org-bn"
    assert result.name == "test-profile-byname"
    assert result.semantic_weight == pytest.approx(0.9)
    assert result.graph_weight == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_get_profile_by_name_returns_none_when_missing(store: Store):
    result = await store.get_profile_by_name("org-missing", "no-such-profile")
    assert result is None


@pytest.mark.asyncio
async def test_get_profile_by_name_is_case_sensitive(store: Store):
    await _insert_profile(
        store,
        org_id="org-cs",
        name="foo",
    )

    # Exact match works
    found = await store.get_profile_by_name("org-cs", "foo")
    assert found is not None

    # Different case must not match
    not_found = await store.get_profile_by_name("org-cs", "FOO")
    assert not_found is None

    not_found2 = await store.get_profile_by_name("org-cs", "Foo")
    assert not_found2 is None


@pytest.mark.asyncio
async def test_get_profile_by_name_org_isolation(store: Store):
    await _insert_profile(
        store,
        org_id="org_a",
        name="shared-name",
    )

    # Correct org finds it
    found = await store.get_profile_by_name("org_a", "shared-name")
    assert found is not None
    assert found.org_id == "org_a"

    # Different org returns None
    not_found = await store.get_profile_by_name("org_b", "shared-name")
    assert not_found is None


# ── create_profile tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_profile_round_trip(store: Store):
    new_profile = NewProfile(
        org_id="org-cp",
        name="test-create-rt",
        semantic_weight=0.7,
        graph_weight=0.3,
        recency_bias=10.0,
        tier_filters=("episodic", "semantic"),
        min_score=0.2,
        max_results=12,
        is_preset=False,
        k=15,
        threshold=0.55,
        rerank=True,
        include_graph=False,
    )

    created = await store.create_profile(new_profile)

    assert isinstance(created, StoredProfile)
    assert created.id.startswith("prof_")
    assert created.org_id == "org-cp"
    assert created.name == "test-create-rt"
    assert created.semantic_weight == pytest.approx(0.7)
    assert created.graph_weight == pytest.approx(0.3)
    assert created.recency_bias == pytest.approx(10.0)
    assert tuple(created.tier_filters) == ("episodic", "semantic")
    assert created.min_score == pytest.approx(0.2)
    assert created.max_results == 12
    assert created.is_preset is False
    assert created.k == 15
    assert created.threshold == pytest.approx(0.55)
    assert created.rerank is True
    assert created.include_graph is False
    assert isinstance(created.created_at, datetime)
    assert isinstance(created.updated_at, datetime)

    # Verify round-trip via get_profile
    fetched = await store.get_profile(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == created.name


@pytest.mark.asyncio
async def test_create_profile_uniqueness_collision(store: Store):
    new_profile = NewProfile(
        org_id="org-collision",
        name="duplicate-name",
        semantic_weight=0.8,
        graph_weight=0.5,
        recency_bias=14.0,
    )

    await store.create_profile(new_profile)

    with pytest.raises(IntegrityError):
        await store.create_profile(new_profile)


# ── list_profiles tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_profiles_returns_org_and_globals(store: Store):
    # Create one org-owned profile
    unique_name = f"test-orgowned-{uuid.uuid4().hex[:8]}"
    new_profile = NewProfile(
        org_id="solo",
        name=unique_name,
        semantic_weight=0.6,
        graph_weight=0.4,
        recency_bias=7.0,
    )
    await store.create_profile(new_profile)

    results = await store.list_profiles("solo")

    # Should include the 3 migration-013 globals + our org-owned one = 4 total
    assert len(results) == 4

    names = [r.name for r in results]
    assert unique_name in names
    assert "coding" in names
    assert "incident-response" in names
    assert "research" in names

    # Results are sorted by name
    assert names == sorted(names)

    # All org_ids are either "solo" or "__global__"
    for r in results:
        assert r.org_id in ("solo", "__global__")


@pytest.mark.asyncio
async def test_list_profiles_org_isolation(store: Store):
    # Create a profile under org_a
    unique_name = f"org-a-only-{uuid.uuid4().hex[:8]}"
    new_profile = NewProfile(
        org_id="org_a",
        name=unique_name,
        semantic_weight=0.5,
        graph_weight=0.5,
        recency_bias=14.0,
    )
    await store.create_profile(new_profile)

    # List for org_b should only return globals (3 presets), not org_a's profile
    results = await store.list_profiles("org_b")

    assert len(results) == 3
    names = [r.name for r in results]
    assert unique_name not in names
    for r in results:
        assert r.org_id == "__global__"
