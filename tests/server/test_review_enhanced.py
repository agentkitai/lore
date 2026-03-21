"""Tests for Enhanced Review with Risk Scoring (F5)."""

from __future__ import annotations

import pytest

from lore.types import ReviewItem, Relationship


class TestReviewDecisionType:
    def test_review_decision_dataclass(self):
        from lore.types import ReviewDecision
        decision = ReviewDecision(
            id="dec-1",
            relationship_id="rel-1",
            action="approve",
            reviewer_id="user-1",
            notes="Looks correct",
        )
        assert decision.action == "approve"
        assert decision.notes == "Looks correct"

    def test_review_decision_reject(self):
        from lore.types import ReviewDecision
        decision = ReviewDecision(
            id="dec-2",
            relationship_id="rel-2",
            action="reject",
            notes="Spurious connection",
        )
        assert decision.action == "reject"


class TestReviewModels:
    def test_review_action_request(self):
        from lore.server.routes.review import ReviewActionRequest
        req = ReviewActionRequest(action="approve", reason="verified")
        assert req.action == "approve"

    def test_bulk_review_request(self):
        from lore.server.routes.review import BulkReviewRequest
        req = BulkReviewRequest(
            action="approve",
            ids=["rel-1", "rel-2"],
            reason="Bulk verified",
        )
        assert len(req.ids) == 2


class TestReviewItemResponse:
    def test_review_item_response(self):
        from lore.server.routes.review import ReviewItemResponse
        item = ReviewItemResponse(
            id="rel-1",
            source_entity={"id": "e1", "name": "Python", "entity_type": "language"},
            target_entity={"id": "e2", "name": "FastAPI", "entity_type": "framework"},
            rel_type="uses",
            weight=0.8,
        )
        assert item.rel_type == "uses"
        assert item.source_entity["name"] == "Python"
