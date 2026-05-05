"""Service tests for graph review workflow."""

from __future__ import annotations

import pytest

from lore.persistence import (
    NewEntity,
    NewMemory,
    NewRelationship,
)
from lore.persistence.exceptions import StoreNotFoundError
from lore.services.graph.review import (
    BulkReviewResult,
    ReviewActionResult,
    ReviewListing,
    _compute_risk_score,
    bulk_review,
    list_pending_reviews,
    review_relationship,
)

# ── Risk score unit tests (no DB needed) ─────────────────────


def test_compute_risk_score_perfect_low_risk():
    """Weight 1.0, max importance, no mentions, fresh — should be low risk."""
    rs = _compute_risk_score(
        weight=1.0,
        source_importance=1.0,
        source_mention_count=0,
        target_mention_count=0,
        age_hours=0.0,
    )
    assert rs.confidence_risk == 0.0
    assert rs.source_reliability == 0.0
    assert rs.entity_importance == 2.5  # max(0,0,1) * 2.5
    assert rs.staleness_risk == 0.0
    assert rs.total == 2.5


def test_compute_risk_score_worst_case():
    """Weight 0, importance 0, many mentions, very stale — should be high."""
    rs = _compute_risk_score(
        weight=0.0,
        source_importance=0.0,
        source_mention_count=20,
        target_mention_count=10,
        age_hours=336.0,  # 2 weeks
    )
    assert rs.confidence_risk == 40.0
    assert rs.source_reliability == 25.0
    assert rs.entity_importance == 25.0  # capped at 25
    assert rs.staleness_risk == 10.0  # capped at 10
    assert rs.total == 100.0


def test_compute_risk_score_default_importance():
    """source_importance None falls back to 0.5."""
    rs = _compute_risk_score(
        weight=1.0,
        source_importance=None,
        source_mention_count=0,
        target_mention_count=0,
        age_hours=0.0,
    )
    # imp defaults to 0.5 → reliability = (1-0.5)*25 = 12.5
    assert rs.source_reliability == 12.5


def test_compute_risk_score_clamps():
    """Extreme inputs should still produce values in expected ranges."""
    rs = _compute_risk_score(
        weight=2.0,  # >1, should clamp
        source_importance=2.0,  # >1, should clamp
        source_mention_count=1000,
        target_mention_count=1000,
        age_hours=10000.0,
    )
    assert rs.confidence_risk == 0.0
    assert rs.source_reliability == 0.0
    assert rs.entity_importance == 25.0
    assert rs.staleness_risk == 10.0


# ── Service tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_pending_reviews_returns_typed_listing(store):
    listing = await list_pending_reviews(store)
    assert isinstance(listing, ReviewListing)


@pytest.mark.asyncio
async def test_list_pending_reviews_filters_by_min_risk(store):
    a = await store.upsert_entity(NewEntity(name="lpr_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name="lpr_b", entity_type="topic"))
    # Add a high-risk relationship: weight=0 → 40 confidence_risk
    await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="uses",
            status="pending",
            weight=0.0,
        )
    )
    listing_no_filter = await list_pending_reviews(store)
    high_risk_count = sum(1 for p in listing_no_filter.pending if p.risk_score.total >= 30)
    assert high_risk_count >= 1
    listing_filtered = await list_pending_reviews(store, min_risk=200)  # impossibly high
    assert len(listing_filtered.pending) == 0


@pytest.mark.asyncio
async def test_list_pending_reviews_sorted_high_to_low_risk(store):
    a = await store.upsert_entity(NewEntity(name="srt_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name="srt_b", entity_type="topic"))
    # High weight → low risk
    await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="uses",
            status="pending",
            weight=1.0,
        )
    )
    # Low weight → high risk
    await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="depends_on",
            status="pending",
            weight=0.0,
        )
    )
    listing = await list_pending_reviews(store)
    if len(listing.pending) >= 2:
        scores = [p.risk_score.total for p in listing.pending]
        assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_list_pending_reviews_includes_memory_content(store):
    a = await store.upsert_entity(NewEntity(name="mc_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name="mc_b", entity_type="topic"))
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="memory backing the rel " * 20, embedding=[0.0] * 384)
    )
    await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="uses",
            status="pending",
            source_memory_id=m.id,
        )
    )
    listing = await list_pending_reviews(store)
    matching = [p for p in listing.pending if p.source_memory_id == m.id]
    assert len(matching) == 1
    assert matching[0].source_memory_content is not None
    assert len(matching[0].source_memory_content) <= 200


@pytest.mark.asyncio
async def test_review_relationship_invalid_action(store):
    with pytest.raises(ValueError):
        await review_relationship(store, "rel_x", action="archive")


@pytest.mark.asyncio
async def test_review_relationship_missing_raises(store):
    with pytest.raises(StoreNotFoundError):
        await review_relationship(store, "rel_missing", action="approve")


@pytest.mark.asyncio
async def test_review_relationship_approve(store):
    a = await store.upsert_entity(NewEntity(name="ra_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name="ra_b", entity_type="topic"))
    rel = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="uses",
            status="pending",
        )
    )
    res = await review_relationship(store, rel.id, action="approve")
    assert isinstance(res, ReviewActionResult)
    assert res.status == "approved"
    assert res.previous_status == "pending"


@pytest.mark.asyncio
async def test_review_relationship_reject_saves_pattern(store):
    a = await store.upsert_entity(NewEntity(name="rr_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name="rr_b", entity_type="topic"))
    rel = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="uses",
            status="pending",
        )
    )
    res = await review_relationship(
        store, rel.id, action="reject", reason="not relevant"
    )
    assert res.status == "rejected"
    # Idempotent: rejecting again should not crash
    # (relationship already rejected; rejected_pattern already exists; ON CONFLICT DO NOTHING)
    res2 = await review_relationship(store, rel.id, action="reject", reason="ditto")
    assert res2.status == "rejected"


@pytest.mark.asyncio
async def test_bulk_review_invalid_action(store):
    with pytest.raises(ValueError):
        await bulk_review(store, ["rel_x"], action="archive")


@pytest.mark.asyncio
async def test_bulk_review_empty_list(store):
    res = await bulk_review(store, [], action="approve")
    assert isinstance(res, BulkReviewResult)
    assert res.updated == 0


@pytest.mark.asyncio
async def test_bulk_review_approves_multiple(store):
    a = await store.upsert_entity(NewEntity(name="bm_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name="bm_b", entity_type="topic"))
    rels = []
    for rel_type in ("uses", "depends_on", "part_of"):
        r = await store.save_relationship(
            NewRelationship(
                source_entity_id=a.id,
                target_entity_id=b.id,
                rel_type=rel_type,
                status="pending",
            )
        )
        rels.append(r.id)
    res = await bulk_review(store, rels, action="approve")
    assert res.updated == 3


@pytest.mark.asyncio
async def test_bulk_review_tolerates_missing_ids(store):
    a = await store.upsert_entity(NewEntity(name="tol_a", entity_type="topic"))
    b = await store.upsert_entity(NewEntity(name="tol_b", entity_type="topic"))
    rel = await store.save_relationship(
        NewRelationship(
            source_entity_id=a.id,
            target_entity_id=b.id,
            rel_type="uses",
            status="pending",
        )
    )
    res = await bulk_review(store, [rel.id, "rel_missing"], action="approve")
    # 1 success + 1 missing (tolerated)
    assert res.updated == 1
