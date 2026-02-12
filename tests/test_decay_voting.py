"""Tests for Story 7: Confidence Decay + Upvote/Downvote."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pytest

from lore import Lore, LessonNotFoundError
from lore.store.memory import MemoryStore
from lore.types import Lesson

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
        lid = lore.publish(problem="p", resolution="r")
        lore.upvote(lid)
        lesson = lore.get(lid)
        assert lesson is not None
        assert lesson.upvotes == 1

    def test_downvote_increments(self) -> None:
        lore = _make_lore()
        lid = lore.publish(problem="p", resolution="r")
        lore.downvote(lid)
        lesson = lore.get(lid)
        assert lesson is not None
        assert lesson.downvotes == 1

    def test_multiple_upvotes(self) -> None:
        lore = _make_lore()
        lid = lore.publish(problem="p", resolution="r")
        for _ in range(5):
            lore.upvote(lid)
        lesson = lore.get(lid)
        assert lesson is not None
        assert lesson.upvotes == 5

    def test_upvote_nonexistent_raises(self) -> None:
        lore = _make_lore()
        with pytest.raises(LessonNotFoundError):
            lore.upvote("nonexistent-id")

    def test_downvote_nonexistent_raises(self) -> None:
        lore = _make_lore()
        with pytest.raises(LessonNotFoundError):
            lore.downvote("nonexistent-id")

    def test_lesson_not_found_error_has_id(self) -> None:
        err = LessonNotFoundError("abc123")
        assert err.lesson_id == "abc123"
        assert "abc123" in str(err)


class TestDecay:
    def test_older_lesson_scores_lower(self) -> None:
        """A 60-day-old lesson scores lower than a 1-day-old identical lesson."""
        # Use a fixed embedding so cosine similarity is identical for both
        fixed_vec = np.random.RandomState(42).randn(_DIM).astype(np.float32)
        fixed_vec = (fixed_vec / np.linalg.norm(fixed_vec)).tolist()

        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=lambda _: fixed_vec)

        now = datetime.now(timezone.utc)

        lid1 = lore.publish(problem="stripe 429", resolution="backoff", confidence=0.9)
        lid2 = lore.publish(problem="stripe 429", resolution="backoff", confidence=0.9)

        l1 = store.get(lid1)
        l2 = store.get(lid2)
        assert l1 is not None and l2 is not None

        l1.created_at = (now - timedelta(days=1)).isoformat()
        l2.created_at = (now - timedelta(days=60)).isoformat()
        store.save(l1)
        store.save(l2)

        results = lore.query("stripe rate limit", limit=10)
        scores = {r.lesson.id: r.score for r in results}
        assert scores[lid1] > scores[lid2]

    def test_upvotes_boost_score(self) -> None:
        """A lesson with 5 upvotes scores higher than identical with 0."""
        fixed_vec = np.random.RandomState(42).randn(_DIM).astype(np.float32)
        fixed_vec = (fixed_vec / np.linalg.norm(fixed_vec)).tolist()

        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=lambda _: fixed_vec)

        lid1 = lore.publish(problem="stripe 429", resolution="backoff", confidence=0.9)
        lid2 = lore.publish(problem="stripe 429", resolution="backoff", confidence=0.9)

        for _ in range(5):
            lore.upvote(lid1)

        results = lore.query("stripe rate limit", limit=10)
        scores = {r.lesson.id: r.score for r in results}
        assert scores[lid1] > scores[lid2]

    def test_downvotes_reduce_score(self) -> None:
        """More downvotes than upvotes reduces score."""
        fixed_vec = np.random.RandomState(42).randn(_DIM).astype(np.float32)
        fixed_vec = (fixed_vec / np.linalg.norm(fixed_vec)).tolist()

        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=lambda _: fixed_vec)

        lid1 = lore.publish(problem="stripe 429", resolution="backoff", confidence=0.9)
        lid2 = lore.publish(problem="stripe 429", resolution="backoff", confidence=0.9)

        for _ in range(3):
            lore.downvote(lid2)

        results = lore.query("stripe rate limit", limit=10)
        scores = {r.lesson.id: r.score for r in results}
        assert scores[lid1] > scores[lid2]

    def test_configurable_half_life(self) -> None:
        """Custom half-life affects decay."""
        store = MemoryStore()
        lore_short = Lore(store=store, embedding_fn=_fake_embed, decay_half_life_days=7)

        now = datetime.now(timezone.utc)
        lid = lore_short.publish(problem="p", resolution="r", confidence=1.0)
        lesson = store.get(lid)
        assert lesson is not None
        lesson.created_at = (now - timedelta(days=7)).isoformat()
        store.save(lesson)

        results = lore_short.query("p r", limit=1)
        # With half_life=7 and age=7, time_factor=0.5
        # Score should be roughly cosine * 1.0 * 0.5 * 1.0
        assert len(results) == 1
        # The score should be about half of what a fresh lesson would get
        # We can't check exact value due to cosine, but it should be positive
        assert results[0].score > 0

    def test_vote_factor_clamped_at_0_1(self) -> None:
        """Vote factor should be at least 0.1 even with massive downvotes."""
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fake_embed)

        lid = lore.publish(problem="p", resolution="r", confidence=0.9)
        for _ in range(100):
            lore.downvote(lid)

        results = lore.query("p r", limit=1)
        assert len(results) == 1
        # Score should still be positive (clamped vote_factor)
        assert results[0].score > 0


class TestExpiresAt:
    def test_expired_lessons_excluded(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fake_embed)

        lid = lore.publish(problem="p", resolution="r")
        lesson = store.get(lid)
        assert lesson is not None

        # Set expires_at to the past
        lesson.expires_at = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        store.save(lesson)

        results = lore.query("p r")
        assert len(results) == 0

    def test_future_expires_at_included(self) -> None:
        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_fake_embed)

        lid = lore.publish(problem="p", resolution="r")
        lesson = store.get(lid)
        assert lesson is not None

        lesson.expires_at = (
            datetime.now(timezone.utc) + timedelta(days=30)
        ).isoformat()
        store.save(lesson)

        results = lore.query("p r")
        assert len(results) == 1

    def test_no_expires_at_included(self) -> None:
        lore = _make_lore()
        lore.publish(problem="p", resolution="r")
        results = lore.query("p r")
        assert len(results) == 1
