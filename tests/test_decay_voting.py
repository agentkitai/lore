"""Tests for confidence decay + upvote/downvote."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pytest

from lore import Lore, MemoryNotFoundError
from lore.store.memory import MemoryStore

_DIM = 384


def _fake_embed(text: str) -> List[float]:
    rng = np.random.RandomState(abs(hash(text)) % (2**31))
    vec = rng.randn(_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _make_lore(**kwargs) -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_fake_embed, **kwargs)


class TestUpvoteDownvote:
    def test_upvote_increments(self) -> None:
        lore = _make_lore()
        mid = lore.remember("test memory")
        lore.upvote(mid)
        memory = lore.get(mid)
        assert memory is not None
        assert memory.upvotes == 1

    def test_downvote_increments(self) -> None:
        lore = _make_lore()
        mid = lore.remember("test memory")
        lore.downvote(mid)
        memory = lore.get(mid)
        assert memory is not None
        assert memory.downvotes == 1

    def test_multiple_upvotes(self) -> None:
        lore = _make_lore()
        mid = lore.remember("test memory")
        for _ in range(5):
            lore.upvote(mid)
        memory = lore.get(mid)
        assert memory is not None
        assert memory.upvotes == 5

    def test_upvote_nonexistent_raises(self) -> None:
        lore = _make_lore()
        with pytest.raises(MemoryNotFoundError):
            lore.upvote("nonexistent-id")

    def test_downvote_nonexistent_raises(self) -> None:
        lore = _make_lore()
        with pytest.raises(MemoryNotFoundError):
            lore.downvote("nonexistent-id")

    def test_memory_not_found_error_has_id(self) -> None:
        err = MemoryNotFoundError("abc123")
        assert err.memory_id == "abc123"
        assert "abc123" in str(err)


class TestDecay:
    def test_older_memory_scores_lower(self) -> None:
        """A 60-day-old memory scores lower than a 1-day-old identical memory."""
        fixed_vec = np.random.RandomState(42).randn(_DIM).astype(np.float32)
        fixed_vec = (fixed_vec / np.linalg.norm(fixed_vec)).tolist()

        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=lambda _: fixed_vec)

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

    def test_upvotes_boost_score(self) -> None:
        """A memory with 5 upvotes scores higher than identical with 0."""
        fixed_vec = np.random.RandomState(42).randn(_DIM).astype(np.float32)
        fixed_vec = (fixed_vec / np.linalg.norm(fixed_vec)).tolist()

        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=lambda _: fixed_vec)

        mid1 = lore.remember("stripe 429 — backoff", confidence=0.9)
        mid2 = lore.remember("stripe 429 — backoff", confidence=0.9)

        for _ in range(5):
            lore.upvote(mid1)

        results = lore.recall("stripe rate limit", limit=10)
        scores = {r.memory.id: r.score for r in results}
        assert scores[mid1] > scores[mid2]

    def test_downvotes_reduce_score(self) -> None:
        """More downvotes than upvotes reduces score."""
        fixed_vec = np.random.RandomState(42).randn(_DIM).astype(np.float32)
        fixed_vec = (fixed_vec / np.linalg.norm(fixed_vec)).tolist()

        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=lambda _: fixed_vec)

        mid1 = lore.remember("stripe 429 — backoff", confidence=0.9)
        mid2 = lore.remember("stripe 429 — backoff", confidence=0.9)

        for _ in range(3):
            lore.downvote(mid2)

        results = lore.recall("stripe rate limit", limit=10)
        scores = {r.memory.id: r.score for r in results}
        assert scores[mid1] > scores[mid2]

    def test_configurable_half_life(self) -> None:
        """Custom half-life affects decay."""
        store = MemoryStore()
        lore_short = Lore(store=store, embedding_fn=_fake_embed, decay_half_life_days=7)

        now = datetime.now(timezone.utc)
        mid = lore_short.remember("test memory", confidence=1.0)
        memory = store.get(mid)
        assert memory is not None
        memory.created_at = (now - timedelta(days=7)).isoformat()
        store.save(memory)

        results = lore_short.recall("test memory", limit=1)
        assert len(results) == 1
        assert results[0].score > 0

    def test_vote_factor_clamped_at_0_1(self) -> None:
        """Vote factor should be at least 0.1 even with massive downvotes."""
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fake_embed)

        mid = lore.remember("test memory", confidence=0.9)
        for _ in range(100):
            lore.downvote(mid)

        results = lore.recall("test memory", limit=1)
        assert len(results) == 1
        assert results[0].score > 0


class TestExpiresAt:
    def test_expired_memories_excluded(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fake_embed)

        mid = lore.remember("test memory")
        memory = store.get(mid)
        assert memory is not None

        memory.expires_at = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        store.save(memory)

        results = lore.recall("test memory")
        assert len(results) == 0

    def test_future_expires_at_included(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fake_embed)

        mid = lore.remember("test memory")
        memory = store.get(mid)
        assert memory is not None

        memory.expires_at = (
            datetime.now(timezone.utc) + timedelta(days=30)
        ).isoformat()
        store.save(memory)

        results = lore.recall("test memory")
        assert len(results) == 1

    def test_no_expires_at_included(self) -> None:
        lore = _make_lore()
        lore.remember("test memory")
        results = lore.recall("test memory")
        assert len(results) == 1

    def test_ttl_sets_expires_at(self) -> None:
        lore = _make_lore()
        mid = lore.remember("ephemeral", ttl=3600)
        memory = lore.get(mid)
        assert memory is not None
        assert memory.expires_at is not None
        expires = datetime.fromisoformat(memory.expires_at)
        now = datetime.now(timezone.utc)
        diff = (expires - now).total_seconds()
        assert 3500 < diff < 3700
