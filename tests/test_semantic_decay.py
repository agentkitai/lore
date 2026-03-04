"""Tests for F1 — Semantic Decay Scoring with type-specific half-lives."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

import numpy as np
import pytest

from lore import Lore
from lore.store.memory import MemoryStore
from lore.types import DECAY_HALF_LIVES

_DIM = 384


def _fixed_embed(text: str) -> List[float]:
    """Deterministic embedding: always the same vector regardless of text."""
    rng = np.random.RandomState(42)
    vec = rng.randn(_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _seeded_embed(text: str) -> List[float]:
    """Deterministic embedding seeded by text hash."""
    rng = np.random.RandomState(abs(hash(text)) % (2**31))
    vec = rng.randn(_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _make_lore(**kwargs) -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_seeded_embed, **kwargs)


class TestDecayHalfLivesConstant:
    """F1-S1: DECAY_HALF_LIVES dict in types.py."""

    def test_default_half_lives(self) -> None:
        assert DECAY_HALF_LIVES["code"] == 14
        assert DECAY_HALF_LIVES["note"] == 21
        assert DECAY_HALF_LIVES["lesson"] == 30
        assert DECAY_HALF_LIVES["convention"] == 60

    def test_unknown_type_defaults_to_30(self) -> None:
        lore = _make_lore()
        assert lore._half_lives.get("unknown_type") is None
        # Falls back to _half_life_days=30

    def test_half_lives_overridable_via_constructor(self) -> None:
        lore = Lore(
            store=MemoryStore(),
            embedding_fn=_seeded_embed,
            decay_half_lives={"code": 7, "custom": 90},
        )
        assert lore._half_lives["code"] == 7
        assert lore._half_lives["custom"] == 90
        # Others keep defaults
        assert lore._half_lives["convention"] == 60

    def test_each_type_produces_correct_half_life(self) -> None:
        lore = _make_lore()
        for type_name, expected_hl in DECAY_HALF_LIVES.items():
            assert lore._half_lives[type_name] == expected_hl


class TestWeightedAdditiveScoring:
    """F1-S2: Weighted additive scoring formula."""

    def test_formula_components(self) -> None:
        """Verify score = 0.7 * similarity + 0.3 * freshness."""
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)

        mid = lore.remember("test content", type="lesson", confidence=1.0)
        # Memory is brand new (age ~0), so freshness ≈ 1.0
        results = lore.recall("anything", limit=1)
        assert len(results) == 1
        score = results[0].score
        # With cosine=1.0 (same embed), confidence=1.0, vote_factor=1.0:
        # similarity = 1.0, freshness ≈ 1.0
        # score ≈ 0.7 * 1.0 + 0.3 * 1.0 = 1.0
        assert 0.95 < score <= 1.0

    def test_configurable_weights(self) -> None:
        """Custom similarity/freshness weights work."""
        store = MemoryStore()
        lore = Lore(
            store=store,
            embedding_fn=_fixed_embed,
            decay_similarity_weight=0.5,
            decay_freshness_weight=0.5,
        )
        mid = lore.remember("test content", type="lesson")
        results = lore.recall("anything", limit=1)
        assert len(results) == 1
        # With cosine=1.0, freshness≈1.0: score ≈ 0.5 + 0.5 = 1.0
        assert 0.95 < results[0].score <= 1.0

    def test_recent_lesson_outranks_old_same_similarity(self) -> None:
        """1-day-old memory with 0.6 sim outranks 60-day-old with 0.65 sim (type=code)."""
        store = MemoryStore()
        now = datetime.now(timezone.utc)

        # Create two different embeddings with known cosine similarities
        rng = np.random.RandomState(42)
        query_vec = rng.randn(_DIM).astype(np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)

        # Create vec1 that has ~0.6 cosine sim with query
        # Create vec2 that has ~0.65 cosine sim with query
        vec1 = query_vec.copy()
        noise1 = rng.randn(_DIM).astype(np.float32)
        vec1 = 0.6 * query_vec + 0.4 * noise1 / np.linalg.norm(noise1)
        vec1 = vec1 / np.linalg.norm(vec1)

        vec2 = query_vec.copy()
        noise2 = rng.randn(_DIM).astype(np.float32)
        vec2 = 0.65 * query_vec + 0.35 * noise2 / np.linalg.norm(noise2)
        vec2 = vec2 / np.linalg.norm(vec2)

        call_count = [0]
        vecs = [vec1.tolist(), vec2.tolist()]

        def _embed(text: str) -> List[float]:
            if text == "__query__":
                return query_vec.tolist()
            idx = call_count[0]
            call_count[0] += 1
            return vecs[idx]

        lore = Lore(store=store, embedding_fn=_embed)

        mid_recent = lore.remember("recent code fix", type="code", confidence=1.0)
        mid_old = lore.remember("old code fix", type="code", confidence=1.0)

        m_recent = store.get(mid_recent)
        m_old = store.get(mid_old)
        assert m_recent is not None and m_old is not None

        m_recent.created_at = (now - timedelta(days=1)).isoformat()
        m_old.created_at = (now - timedelta(days=60)).isoformat()
        store.save(m_recent)
        store.save(m_old)

        results = lore.recall("__query__", limit=10)
        scores = {r.memory.id: r.score for r in results}
        # Recent lesson should outrank old one despite lower similarity
        assert scores[mid_recent] > scores[mid_old]

    def test_type_specific_decay_code_vs_convention(self) -> None:
        """Code type (HL=14) decays faster than convention (HL=60)."""
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        now = datetime.now(timezone.utc)

        mid_code = lore.remember("code pattern", type="code")
        mid_conv = lore.remember("naming convention", type="convention")

        # Age both by 30 days
        for mid in [mid_code, mid_conv]:
            m = store.get(mid)
            assert m is not None
            m.created_at = (now - timedelta(days=30)).isoformat()
            store.save(m)

        results = lore.recall("anything", limit=10)
        scores = {r.memory.id: r.score for r in results}

        # Convention (HL=60, 30 days = ~0.707 freshness) should score
        # higher than code (HL=14, 30 days = ~0.228 freshness)
        assert scores[mid_conv] > scores[mid_code]

    def test_default_type_uses_default_half_life(self) -> None:
        """Memories with type 'general' (not in DECAY_HALF_LIVES) use default HL=30."""
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        now = datetime.now(timezone.utc)

        mid = lore.remember("general info", type="general")
        m = store.get(mid)
        assert m is not None
        m.created_at = (now - timedelta(days=30)).isoformat()
        store.save(m)

        results = lore.recall("anything", limit=1)
        assert len(results) == 1
        # At 30 days with HL=30: freshness = 0.5
        # similarity ≈ 1.0 (same embed)
        # score ≈ 0.7 * 1.0 + 0.3 * 0.5 = 0.85
        assert 0.80 < results[0].score < 0.90


class TestScoringBackwardCompatibility:
    """Ensure the new formula doesn't break existing behavior."""

    def test_older_memory_still_scores_lower(self) -> None:
        """Same test from existing test_decay_voting: older still loses."""
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)
        now = datetime.now(timezone.utc)

        mid1 = lore.remember("stripe 429 — backoff", confidence=0.9)
        mid2 = lore.remember("stripe 429 — backoff", confidence=0.9)

        m1 = store.get(mid1)
        m2 = store.get(mid2)
        assert m1 is not None and m2 is not None

        m1.created_at = (now - timedelta(days=1)).isoformat()
        m2.created_at = (now - timedelta(days=60)).isoformat()
        store.save(m1)
        store.save(m2)

        results = lore.recall("stripe rate limit", limit=10)
        scores = {r.memory.id: r.score for r in results}
        assert scores[mid1] > scores[mid2]

    def test_upvotes_still_boost_score(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)

        mid1 = lore.remember("stripe 429 — backoff", confidence=0.9)
        mid2 = lore.remember("stripe 429 — backoff", confidence=0.9)

        for _ in range(5):
            lore.upvote(mid1)

        results = lore.recall("stripe rate limit", limit=10)
        scores = {r.memory.id: r.score for r in results}
        assert scores[mid1] > scores[mid2]

    def test_downvotes_still_reduce_score(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fixed_embed)

        mid1 = lore.remember("stripe 429 — backoff", confidence=0.9)
        mid2 = lore.remember("stripe 429 — backoff", confidence=0.9)

        for _ in range(3):
            lore.downvote(mid2)

        results = lore.recall("stripe rate limit", limit=10)
        scores = {r.memory.id: r.score for r in results}
        assert scores[mid1] > scores[mid2]

    def test_vote_factor_clamped_at_0_1(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_seeded_embed)

        mid = lore.remember("test memory", confidence=0.9)
        for _ in range(100):
            lore.downvote(mid)

        results = lore.recall("test memory", limit=1)
        assert len(results) == 1
        assert results[0].score > 0

    def test_constructor_decay_half_life_days_still_works(self) -> None:
        """The old decay_half_life_days param is still respected as global default."""
        store = MemoryStore()
        lore = Lore(
            store=store,
            embedding_fn=_fixed_embed,
            decay_half_life_days=7,
        )
        assert lore._half_life_days == 7
