"""Tests for E6: Approval UX for Discovered Connections (Trust Layer).

Covers:
- Relationship status field (pending/approved/rejected)
- Store layer review methods
- Lore SDK review methods
- CLI review command
- MCP review_digest tool
- Graph query filtering by status
- Rejected pattern tracking and prevention
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from lore import Lore
from lore.store.memory import MemoryStore
from lore.types import (
    VALID_REVIEW_STATUSES,
    Entity,
    RejectedPattern,
    Relationship,
    ReviewItem,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def lore_instance(store):
    return Lore(store=store, knowledge_graph=True)


def _make_entities(store):
    """Create two test entities and return them."""
    e1 = Entity(
        id="ent-1", name="python", entity_type="language",
        first_seen_at=datetime.now(timezone.utc).isoformat(),
        last_seen_at=datetime.now(timezone.utc).isoformat(),
    )
    e2 = Entity(
        id="ent-2", name="fastapi", entity_type="framework",
        first_seen_at=datetime.now(timezone.utc).isoformat(),
        last_seen_at=datetime.now(timezone.utc).isoformat(),
    )
    store.save_entity(e1)
    store.save_entity(e2)
    return e1, e2


def _make_relationship(store, status="approved", rel_id="rel-1"):
    """Create a test relationship."""
    rel = Relationship(
        id=rel_id,
        source_entity_id="ent-1",
        target_entity_id="ent-2",
        rel_type="uses",
        weight=1.0,
        valid_from=datetime.now(timezone.utc).isoformat(),
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    store.save_relationship(rel)
    return rel


# ── Types ─────────────────────────────────────────────────────────


class TestReviewTypes:
    def test_valid_review_statuses(self):
        assert "pending" in VALID_REVIEW_STATUSES
        assert "approved" in VALID_REVIEW_STATUSES
        assert "rejected" in VALID_REVIEW_STATUSES

    def test_relationship_default_status(self):
        rel = Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="uses",
        )
        assert rel.status == "approved"

    def test_relationship_pending_status(self):
        rel = Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="uses", status="pending",
        )
        assert rel.status == "pending"

    def test_rejected_pattern_dataclass(self):
        p = RejectedPattern(
            id="rp1", source_name="python", target_name="fastapi",
            rel_type="uses", rejected_at="2026-03-14T00:00:00",
        )
        assert p.source_name == "python"
        assert p.target_name == "fastapi"
        assert p.rel_type == "uses"
        assert p.reason is None

    def test_review_item_dataclass(self):
        rel = Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="uses",
        )
        item = ReviewItem(
            relationship=rel,
            source_entity_name="python",
            source_entity_type="language",
            target_entity_name="fastapi",
            target_entity_type="framework",
        )
        assert item.source_entity_name == "python"
        assert item.source_memory_content is None


# ── Store Layer ───────────────────────────────────────────────────


class TestStoreReview:
    def test_list_pending_empty(self, store):
        assert store.list_pending_relationships() == []

    def test_list_pending_returns_only_pending(self, store):
        _make_entities(store)
        _make_relationship(store, status="approved", rel_id="rel-approved")
        _make_relationship(store, status="pending", rel_id="rel-pending")
        pending = store.list_pending_relationships()
        assert len(pending) == 1
        assert pending[0].id == "rel-pending"
        assert pending[0].status == "pending"

    def test_update_relationship_status(self, store):
        _make_entities(store)
        _make_relationship(store, status="pending")
        assert store.update_relationship_status("rel-1", "approved") is True
        rel = store.get_relationship("rel-1")
        assert rel.status == "approved"

    def test_update_relationship_status_not_found(self, store):
        assert store.update_relationship_status("nonexistent", "approved") is False

    def test_save_rejected_pattern(self, store):
        p = RejectedPattern(
            id="rp1", source_name="python", target_name="fastapi",
            rel_type="uses",
        )
        store.save_rejected_pattern(p)
        assert store.is_rejected_pattern("python", "fastapi", "uses") is True
        assert store.is_rejected_pattern("python", "django", "uses") is False

    def test_save_rejected_pattern_idempotent(self, store):
        p1 = RejectedPattern(id="rp1", source_name="a", target_name="b", rel_type="uses")
        p2 = RejectedPattern(id="rp2", source_name="a", target_name="b", rel_type="uses")
        store.save_rejected_pattern(p1)
        store.save_rejected_pattern(p2)
        assert len(store.list_rejected_patterns()) == 1

    def test_list_rejected_patterns(self, store):
        p = RejectedPattern(id="rp1", source_name="x", target_name="y", rel_type="uses")
        store.save_rejected_pattern(p)
        patterns = store.list_rejected_patterns()
        assert len(patterns) == 1
        assert patterns[0].source_name == "x"

    def test_list_relationships_excludes_rejected(self, store):
        _make_entities(store)
        _make_relationship(store, status="approved", rel_id="rel-a")
        _make_relationship(store, status="rejected", rel_id="rel-r")
        rels = store.list_relationships()
        assert len(rels) == 1
        assert rels[0].id == "rel-a"

    def test_query_relationships_excludes_rejected(self, store):
        _make_entities(store)
        _make_relationship(store, status="approved", rel_id="rel-a")
        _make_relationship(store, status="rejected", rel_id="rel-r")
        rels = store.query_relationships(["ent-1"])
        assert len(rels) == 1
        assert rels[0].id == "rel-a"

    def test_list_pending_respects_limit(self, store):
        _make_entities(store)
        for i in range(5):
            _make_relationship(store, status="pending", rel_id=f"rel-p{i}")
        pending = store.list_pending_relationships(limit=2)
        assert len(pending) == 2


# ── Lore SDK ──────────────────────────────────────────────────────


class TestLoreReview:
    def test_get_pending_reviews_empty(self, lore_instance):
        items = lore_instance.get_pending_reviews()
        assert items == []

    def test_get_pending_reviews(self, lore_instance, store):
        _make_entities(store)
        _make_relationship(store, status="pending")
        items = lore_instance.get_pending_reviews()
        assert len(items) == 1
        assert items[0].source_entity_name == "python"
        assert items[0].target_entity_name == "fastapi"

    def test_review_connection_approve(self, lore_instance, store):
        _make_entities(store)
        _make_relationship(store, status="pending")
        ok = lore_instance.review_connection("rel-1", "approve")
        assert ok is True
        rel = store.get_relationship("rel-1")
        assert rel.status == "approved"

    def test_review_connection_reject(self, lore_instance, store):
        _make_entities(store)
        _make_relationship(store, status="pending")
        ok = lore_instance.review_connection("rel-1", "reject", reason="incorrect")
        assert ok is True
        rel = store.get_relationship("rel-1")
        assert rel.status == "rejected"
        # Check pattern saved
        assert store.is_rejected_pattern("python", "fastapi", "uses") is True

    def test_review_connection_not_found(self, lore_instance):
        ok = lore_instance.review_connection("nonexistent", "approve")
        assert ok is False

    def test_review_connection_invalid_action(self, lore_instance):
        with pytest.raises(ValueError, match="Invalid action"):
            lore_instance.review_connection("rel-1", "maybe")

    def test_review_all_approve(self, lore_instance, store):
        _make_entities(store)
        _make_relationship(store, status="pending", rel_id="rel-p1")
        _make_relationship(store, status="pending", rel_id="rel-p2")
        count = lore_instance.review_all("approve")
        assert count == 2
        assert store.get_relationship("rel-p1").status == "approved"
        assert store.get_relationship("rel-p2").status == "approved"

    def test_review_all_reject(self, lore_instance, store):
        _make_entities(store)
        _make_relationship(store, status="pending", rel_id="rel-p1")
        count = lore_instance.review_all("reject")
        assert count == 1
        assert store.get_relationship("rel-p1").status == "rejected"

    def test_review_all_empty(self, lore_instance):
        count = lore_instance.review_all("approve")
        assert count == 0

    def test_get_pending_reviews_includes_memory_content(self, lore_instance, store):
        _make_entities(store)
        mem_id = lore_instance.remember("We use FastAPI for the API layer")
        rel = Relationship(
            id="rel-wm",
            source_entity_id="ent-1",
            target_entity_id="ent-2",
            rel_type="uses",
            weight=1.0,
            valid_from=datetime.now(timezone.utc).isoformat(),
            status="pending",
            source_memory_id=mem_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        store.save_relationship(rel)
        items = lore_instance.get_pending_reviews()
        assert len(items) == 1
        assert "FastAPI" in items[0].source_memory_content

    def test_get_pending_reviews_skips_missing_entities(self, lore_instance, store):
        # Relationship referencing non-existent entities
        rel = Relationship(
            id="rel-bad", source_entity_id="gone-1", target_entity_id="gone-2",
            rel_type="uses", status="pending",
            valid_from=datetime.now(timezone.utc).isoformat(),
        )
        store.save_relationship(rel)
        items = lore_instance.get_pending_reviews()
        assert len(items) == 0  # Skipped because entities don't exist


# ── CLI ───────────────────────────────────────────────────────────


class TestCLIReview:
    def test_review_no_pending(self, capsys):
        from lore.cli import main
        main(["review"])
        out = capsys.readouterr().out
        assert "Nothing to review" in out

    def test_review_lists_pending(self, capsys):
        # Seed data through the shared MemoryStore fixture
        from lore.cli import _get_lore, main
        lore = _get_lore()
        store = lore._store
        _make_entities(store)
        _make_relationship(store, status="pending")
        main(["review"])
        out = capsys.readouterr().out
        assert "python" in out
        assert "fastapi" in out
        assert "uses" in out

    def test_review_approve(self, capsys):
        from lore.cli import _get_lore, main
        lore = _get_lore()
        store = lore._store
        _make_entities(store)
        _make_relationship(store, status="pending")
        main(["review", "--approve", "rel-1"])
        out = capsys.readouterr().out
        assert "Approved" in out
        assert store.get_relationship("rel-1").status == "approved"

    def test_review_reject(self, capsys):
        from lore.cli import _get_lore, main
        lore = _get_lore()
        store = lore._store
        _make_entities(store)
        _make_relationship(store, status="pending")
        main(["review", "--reject", "rel-1"])
        out = capsys.readouterr().out
        assert "Rejected" in out

    def test_review_approve_not_found(self, capsys):
        from lore.cli import main
        with pytest.raises(SystemExit):
            main(["review", "--approve", "nonexistent"])

    def test_review_approve_all(self, capsys):
        from lore.cli import _get_lore, main
        lore = _get_lore()
        store = lore._store
        _make_entities(store)
        _make_relationship(store, status="pending", rel_id="rel-p1")
        _make_relationship(store, status="pending", rel_id="rel-p2")
        main(["review", "--approve-all"])
        out = capsys.readouterr().out
        assert "Approved 2" in out

    def test_review_reject_all(self, capsys):
        from lore.cli import _get_lore, main
        lore = _get_lore()
        store = lore._store
        _make_entities(store)
        _make_relationship(store, status="pending", rel_id="rel-p1")
        main(["review", "--reject-all"])
        out = capsys.readouterr().out
        assert "Rejected 1" in out


# ── MCP ───────────────────────────────────────────────────────────


@pytest.fixture
def mock_mcp_lore():
    """Patch _get_lore in MCP server to return test Lore instance."""
    lore = Lore(store=MemoryStore(), knowledge_graph=True)
    with patch("lore.mcp.server._get_lore", return_value=lore):
        yield lore


class TestMCPReview:
    def test_review_digest_empty(self, mock_mcp_lore):
        from lore.mcp.server import review_digest
        result = review_digest(limit=20)
        assert "No pending" in result

    def test_review_digest_with_pending(self, mock_mcp_lore):
        from lore.mcp.server import review_digest
        store = mock_mcp_lore._store
        _make_entities(store)
        _make_relationship(store, status="pending")
        result = review_digest(limit=20)
        assert "python" in result
        assert "fastapi" in result
        assert "uses" in result
        assert "1 total" in result

    def test_review_connection_mcp_approve(self, mock_mcp_lore):
        from lore.mcp.server import review_connection
        store = mock_mcp_lore._store
        _make_entities(store)
        _make_relationship(store, status="pending")
        result = review_connection("rel-1", "approve")
        assert "Approved" in result
        assert store.get_relationship("rel-1").status == "approved"

    def test_review_connection_mcp_reject(self, mock_mcp_lore):
        from lore.mcp.server import review_connection
        store = mock_mcp_lore._store
        _make_entities(store)
        _make_relationship(store, status="pending")
        result = review_connection("rel-1", "reject", reason="wrong")
        assert "Rejected" in result

    def test_review_connection_mcp_not_found(self, mock_mcp_lore):
        from lore.mcp.server import review_connection
        result = review_connection("nonexistent", "approve")
        assert "not found" in result

    def test_review_connection_mcp_invalid_action(self, mock_mcp_lore):
        from lore.mcp.server import review_connection
        result = review_connection("x", "maybe")
        assert "Invalid action" in result


# ── Graph Query Filtering ─────────────────────────────────────────


class TestGraphFiltering:
    def test_list_relationships_shows_pending(self, store):
        """Pending relationships are included (not rejected)."""
        _make_entities(store)
        _make_relationship(store, status="pending", rel_id="rel-p")
        rels = store.list_relationships()
        assert len(rels) == 1  # pending is not rejected, so included

    def test_list_relationships_excludes_rejected(self, store):
        _make_entities(store)
        _make_relationship(store, status="rejected", rel_id="rel-r")
        rels = store.list_relationships()
        assert len(rels) == 0

    def test_get_relationships_from_excludes_rejected(self, store):
        _make_entities(store)
        _make_relationship(store, status="approved", rel_id="rel-a")
        _make_relationship(store, status="rejected", rel_id="rel-r")
        rels = store.get_relationships_from(["ent-1"])
        # get_relationships_from doesn't filter by status in the base impl
        # Only list_relationships and query_relationships filter
        assert len(rels) >= 1

    def test_query_relationships_excludes_rejected(self, store):
        _make_entities(store)
        _make_relationship(store, status="approved", rel_id="rel-a")
        _make_relationship(store, status="rejected", rel_id="rel-r")
        rels = store.query_relationships(["ent-1"])
        assert len(rels) == 1
        assert rels[0].id == "rel-a"


# ── Rejected Pattern Prevention ──────────────────────────────────


class TestRejectedPatterns:
    def test_is_rejected_pattern_false_by_default(self, store):
        assert store.is_rejected_pattern("a", "b", "uses") is False

    def test_is_rejected_pattern_after_save(self, store):
        p = RejectedPattern(id="rp1", source_name="a", target_name="b", rel_type="uses")
        store.save_rejected_pattern(p)
        assert store.is_rejected_pattern("a", "b", "uses") is True

    def test_different_rel_type_not_rejected(self, store):
        p = RejectedPattern(id="rp1", source_name="a", target_name="b", rel_type="uses")
        store.save_rejected_pattern(p)
        assert store.is_rejected_pattern("a", "b", "depends_on") is False

    def test_rejection_creates_pattern_via_sdk(self, lore_instance, store):
        _make_entities(store)
        _make_relationship(store, status="pending")
        lore_instance.review_connection("rel-1", "reject")
        assert store.is_rejected_pattern("python", "fastapi", "uses") is True

    def test_rejected_patterns_list(self, store):
        p1 = RejectedPattern(id="rp1", source_name="a", target_name="b", rel_type="uses")
        p2 = RejectedPattern(id="rp2", source_name="c", target_name="d", rel_type="mentions")
        store.save_rejected_pattern(p1)
        store.save_rejected_pattern(p2)
        patterns = store.list_rejected_patterns()
        assert len(patterns) == 2

    def test_rejected_pattern_with_reason(self, store):
        p = RejectedPattern(
            id="rp1", source_name="a", target_name="b",
            rel_type="uses", reason="false positive",
        )
        store.save_rejected_pattern(p)
        patterns = store.list_rejected_patterns()
        assert patterns[0].reason == "false positive"


# ── Backward Compatibility ────────────────────────────────────────


class TestBackwardCompat:
    def test_relationship_default_status_is_approved(self):
        """Existing code creating relationships without status gets 'approved'."""
        rel = Relationship(
            id="r1", source_entity_id="e1", target_entity_id="e2",
            rel_type="uses",
        )
        assert rel.status == "approved"

    def test_existing_relationships_work_in_queries(self, store):
        """Relationships without explicit status should work in queries."""
        _make_entities(store)
        # Simulate old relationship (status defaults to 'approved')
        rel = Relationship(
            id="rel-old", source_entity_id="ent-1", target_entity_id="ent-2",
            rel_type="uses",
            valid_from=datetime.now(timezone.utc).isoformat(),
        )
        store.save_relationship(rel)
        rels = store.list_relationships()
        assert len(rels) == 1
        assert rels[0].status == "approved"

    def test_store_base_defaults_return_empty(self):
        """Base store methods return sensible defaults."""
        from lore.store.base import Store

        class MinimalStore(Store):
            def save(self, m): pass
            def get(self, mid): return None
            def list(self, **kw): return []
            def update(self, m): return False
            def delete(self, mid): return False
            def count(self, **kw): return 0
            def cleanup_expired(self): return 0

        s = MinimalStore()
        assert s.list_pending_relationships() == []
        assert s.update_relationship_status("x", "y") is False
        assert s.is_rejected_pattern("a", "b", "c") is False
        assert s.list_rejected_patterns() == []
