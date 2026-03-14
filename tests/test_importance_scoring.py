"""Tests for F5 — Importance Scoring + Adaptive Decay.

Covers:
- compute_importance: default, upvotes, downvotes floor, access log, combined
- time_adjusted_importance: fresh, one half-life, last_accessed recency
- resolve_half_life: tier+type, tier default, no tier, overrides
- decay_factor: boundary conditions
- Integration: access tracking, multiplicative scoring, cleanup, backward compat
"""

from __future__ import annotations

import math
import warnings
from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np

from lore import Lore
from lore.importance import (
    compute_importance,
    decay_factor,
    resolve_half_life,
    time_adjusted_importance,
)
from lore.store.memory import MemoryStore
from lore.types import DECAY_HALF_LIVES, TIER_DECAY_HALF_LIVES, Memory

_DIM = 384


def _fixed_embed(text: str) -> List[float]:
    rng = np.random.RandomState(42)
    vec = rng.randn(_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _make_memory(**kwargs) -> Memory:
    defaults = dict(
        id="test-id",
        content="test content",
        type="lesson",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        confidence=1.0,
    )
    defaults.update(kwargs)
    return Memory(**defaults)


# ──────────────────────────────────────────────────────────────
# Unit tests: compute_importance
# ──────────────────────────────────────────────────────────────


class TestComputeImportance:
    def test_default(self) -> None:
        mem = _make_memory()
        assert compute_importance(mem) == 1.0

    def test_upvotes(self) -> None:
        mem = _make_memory(upvotes=5, downvotes=0, access_count=0)
        result = compute_importance(mem)
        # vote_factor = 1.0 + 5*0.1 = 1.5, access_factor = 1.0
        assert abs(result - 1.5) < 0.01

    def test_downvotes_floor(self) -> None:
        mem = _make_memory(upvotes=0, downvotes=10, access_count=0)
        result = compute_importance(mem)
        # vote_factor = max(0.1, 1.0 - 10*0.1) = max(0.1, 0.0) = 0.1
        assert abs(result - 0.1) < 0.01

    def test_access_log(self) -> None:
        mem = _make_memory(access_count=10)
        result = compute_importance(mem)
        # access_factor = 1.0 + log2(11) * 0.1 ≈ 1.346
        expected = 1.0 * 1.0 * (1.0 + math.log2(11) * 0.1)
        assert abs(result - expected) < 0.01

    def test_combined(self) -> None:
        mem = _make_memory(upvotes=5, downvotes=0, access_count=10, confidence=1.0)
        result = compute_importance(mem)
        vote_factor = 1.5
        access_factor = 1.0 + math.log2(11) * 0.1
        expected = 1.0 * vote_factor * access_factor
        assert abs(result - expected) < 0.01

    def test_low_confidence(self) -> None:
        mem = _make_memory(confidence=0.5)
        result = compute_importance(mem)
        assert abs(result - 0.5) < 0.01


# ──────────────────────────────────────────────────────────────
# Unit tests: time_adjusted_importance
# ──────────────────────────────────────────────────────────────


class TestTimeAdjustedImportance:
    def test_fresh(self) -> None:
        now = datetime.utcnow()
        mem = _make_memory(created_at=now.isoformat(), importance_score=1.0)
        tai = time_adjusted_importance(mem, 30.0, now=now)
        assert abs(tai - 1.0) < 0.01

    def test_one_half_life(self) -> None:
        now = datetime.utcnow()
        created = now - timedelta(days=30)
        mem = _make_memory(created_at=created.isoformat(), importance_score=1.0)
        tai = time_adjusted_importance(mem, 30.0, now=now)
        assert abs(tai - 0.5) < 0.01

    def test_last_accessed_recency(self) -> None:
        now = datetime.utcnow()
        created = now - timedelta(days=30)
        last_accessed = now - timedelta(days=1)
        mem = _make_memory(
            created_at=created.isoformat(),
            last_accessed_at=last_accessed.isoformat(),
            importance_score=1.0,
        )
        tai = time_adjusted_importance(mem, 30.0, now=now)
        # Should use age=1 day (min of 30 and 1), so decay ≈ 0.977
        assert tai > 0.95


# ──────────────────────────────────────────────────────────────
# Unit tests: resolve_half_life
# ──────────────────────────────────────────────────────────────


class TestResolveHalfLife:
    def test_tier_type(self) -> None:
        assert resolve_half_life("long", "convention") == 60.0

    def test_tier_default(self) -> None:
        assert resolve_half_life("working", "unknown_type") == 1

    def test_no_tier(self) -> None:
        result = resolve_half_life(None, "lesson")
        assert result == 30.0  # Falls back to "long" tier

    def test_overrides(self) -> None:
        overrides = {("short", "code"): 3.0}
        result = resolve_half_life("short", "code", overrides=overrides)
        assert result == 3.0

    def test_nonexistent_tier(self) -> None:
        result = resolve_half_life("nonexistent_tier", "note")
        # Falls to DECAY_HALF_LIVES (long tier alias)
        assert result == DECAY_HALF_LIVES.get("note", 30.0)


# ──────────────────────────────────────────────────────────────
# Unit tests: decay_factor
# ──────────────────────────────────────────────────────────────


class TestDecayFactor:
    def test_age_zero(self) -> None:
        assert decay_factor(0, 30) == 1.0

    def test_very_large_age(self) -> None:
        result = decay_factor(300, 30)
        assert result < 0.001

    def test_one_half_life(self) -> None:
        result = decay_factor(30, 30)
        assert abs(result - 0.5) < 0.01


# ──────────────────────────────────────────────────────────────
# Integration tests
# ──────────────────────────────────────────────────────────────


class TestRecallAccessTracking:
    def test_recall_updates_access_count(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        mid = lore.remember("test memory")
        mem = store.get(mid)
        assert mem is not None
        assert mem.access_count == 0

        lore.recall("anything", limit=1)
        mem = store.get(mid)
        assert mem is not None
        assert mem.access_count == 1

    def test_recall_sets_last_accessed(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        mid = lore.remember("test memory")
        mem = store.get(mid)
        assert mem is not None
        assert mem.last_accessed_at is None

        lore.recall("anything", limit=1)
        mem = store.get(mid)
        assert mem is not None
        assert mem.last_accessed_at is not None

    def test_recall_recomputes_importance(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        mid = lore.remember("test memory")

        # Upvote to change importance
        for _ in range(3):
            lore.upvote(mid)

        mem_before = store.get(mid)
        assert mem_before is not None
        score_before = mem_before.importance_score

        lore.recall("anything", limit=1)
        mem_after = store.get(mid)
        assert mem_after is not None
        # access_count increased, so importance should increase
        assert mem_after.importance_score > score_before


class TestMultiplicativeScoring:
    def test_higher_importance_ranks_higher(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)

        mid_a = lore.remember("test A")
        mid_b = lore.remember("test B")

        # Upvote A to give it higher importance
        for _ in range(5):
            lore.upvote(mid_a)

        results = lore.recall("anything", limit=10)
        scores = {r.memory.id: r.score for r in results}
        assert scores[mid_a] > scores[mid_b]

    def test_working_tier_decays_faster(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        now = datetime.now(timezone.utc)

        mid_working = lore.remember("working tier", type="lesson")
        mid_long = lore.remember("long tier", type="lesson")

        m_w = store.get(mid_working)
        m_l = store.get(mid_long)
        assert m_w is not None and m_l is not None

        m_w.tier = "working"
        m_l.tier = "long"
        age = (now - timedelta(days=5)).isoformat()
        m_w.created_at = age
        m_l.created_at = age
        store.save(m_w)
        store.save(m_l)

        results = lore.recall("anything", limit=10)
        scores = {r.memory.id: r.score for r in results}
        # Working tier lesson HL=3, long tier lesson HL=30
        # At 5 days: working decay ≈ 0.5^(5/3) ≈ 0.31, long decay ≈ 0.5^(5/30) ≈ 0.89
        assert scores[mid_long] > scores[mid_working]


class TestUpvoteUpdatesImportance:
    def test_upvote_increases_importance(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        mid = lore.remember("test memory")

        mem = store.get(mid)
        assert mem is not None
        assert mem.importance_score == 1.0

        lore.upvote(mid)
        mem = store.get(mid)
        assert mem is not None
        assert mem.importance_score > 1.0

    def test_downvote_decreases_importance(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        mid = lore.remember("test memory")

        for _ in range(3):
            lore.downvote(mid)

        mem = store.get(mid)
        assert mem is not None
        # vote_factor = max(0.1, 1.0 - 0.3) = 0.7
        assert abs(mem.importance_score - 0.7) < 0.01


class TestCleanupImportance:
    def test_removes_low_importance(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        now = datetime.now(timezone.utc)

        mid = lore.remember("old memory", type="lesson")
        mem = store.get(mid)
        assert mem is not None
        # Age it 150 days with HL=30 → TAI ≈ 0.031
        mem.created_at = (now - timedelta(days=150)).isoformat()
        store.save(mem)

        count = lore.cleanup_expired(importance_threshold=0.05)
        assert count >= 1
        assert store.get(mid) is None

    def test_preserves_important(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        now = datetime.now(timezone.utc)

        mid = lore.remember("important memory", type="lesson")
        mem = store.get(mid)
        assert mem is not None
        # High importance through upvotes
        mem.upvotes = 10
        mem.importance_score = compute_importance(mem)
        mem.created_at = (now - timedelta(days=150)).isoformat()
        store.save(mem)

        lore.cleanup_expired(importance_threshold=0.05)
        # TAI ≈ 2.0 * 0.031 = 0.063 > 0.05
        assert store.get(mid) is not None


class TestBackwardCompat:
    def test_decay_half_lives_alias(self) -> None:
        assert DECAY_HALF_LIVES is TIER_DECAY_HALF_LIVES["long"]
        assert DECAY_HALF_LIVES["code"] == 14
        assert DECAY_HALF_LIVES["convention"] == 60

    def test_deprecated_params_warn(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Lore(

                store=MemoryStore(),
                embedding_fn=_fixed_embed,
                decay_similarity_weight=0.5,
                decay_freshness_weight=0.5,
            )
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "multiplicative" in str(w[0].message)

    def test_no_warning_for_defaults(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Lore(store=MemoryStore(), embedding_fn=_fixed_embed)
            deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_warnings) == 0


class TestRecalculateImportance:
    def test_recalculate(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)

        mid = lore.remember("test memory")
        # Manually set stale importance
        mem = store.get(mid)
        assert mem is not None
        mem.upvotes = 5
        mem.importance_score = 0.5  # stale
        store.save(mem)

        count = lore.recalculate_importance()
        assert count == 1

        mem = store.get(mid)
        assert mem is not None
        assert mem.importance_score > 1.0  # recomputed correctly
