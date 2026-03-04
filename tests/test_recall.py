"""Tests for semantic recall."""

from __future__ import annotations

import time
from typing import List

import numpy as np

from lore import Lore, RecallResult
from lore.store.memory import MemoryStore

_DIM = 384


def _fake_embed(text: str) -> List[float]:
    """Deterministic fake embedder: hash text to a normalized vector."""
    rng = np.random.RandomState(abs(hash(text)) % (2**31))
    vec = rng.randn(_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _make_lore() -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_fake_embed)


class TestRecall:
    def test_recall_returns_recall_results(self) -> None:
        lore = _make_lore()
        lore.remember("stripe 429 — use backoff")
        results = lore.recall("stripe rate limit")
        assert len(results) >= 1
        assert isinstance(results[0], RecallResult)
        assert isinstance(results[0].score, float)
        assert "stripe 429" in results[0].memory.content

    def test_recall_empty_store(self) -> None:
        lore = _make_lore()
        results = lore.recall("anything")
        assert results == []

    def test_recall_with_tags_filter(self) -> None:
        lore = _make_lore()
        lore.remember("stripe issue", tags=["stripe"])
        lore.remember("openai issue", tags=["openai"])
        results = lore.recall("test", tags=["stripe"])
        assert len(results) == 1
        assert results[0].memory.tags == ["stripe"]

    def test_recall_with_type_filter(self) -> None:
        lore = _make_lore()
        lore.remember("a lesson", type="lesson")
        lore.remember("a fact", type="fact")
        results = lore.recall("test", type="lesson")
        assert len(results) == 1
        assert results[0].memory.type == "lesson"

    def test_recall_with_limit(self) -> None:
        lore = _make_lore()
        for i in range(10):
            lore.remember(f"memory {i}")
        results = lore.recall("memory", limit=3)
        assert len(results) == 3

    def test_recall_with_min_confidence(self) -> None:
        lore = _make_lore()
        lore.remember("low confidence", confidence=0.2)
        lore.remember("high confidence", confidence=0.8)
        results = lore.recall("test", min_confidence=0.5)
        assert len(results) == 1
        assert results[0].memory.confidence >= 0.5

    def test_recall_scores_reasonable(self) -> None:
        lore = _make_lore()
        for i in range(5):
            lore.remember(f"memory {i}")
        results = lore.recall("memory")
        for r in results:
            assert -2.0 <= r.score <= 2.0

    def test_recall_results_sorted_by_score(self) -> None:
        lore = _make_lore()
        for i in range(10):
            lore.remember(f"memory {i}")
        results = lore.recall("memory", limit=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_recall_tag_subset_filter(self) -> None:
        """Tags filter requires ALL specified tags to be present."""
        lore = _make_lore()
        lore.remember("mem1", tags=["a", "b"])
        lore.remember("mem2", tags=["a"])
        results = lore.recall("test", tags=["a", "b"])
        assert len(results) == 1
        assert "b" in results[0].memory.tags

    def test_remember_stores_embedding(self) -> None:
        lore = _make_lore()
        mid = lore.remember("test memory")
        memory = lore.get(mid)
        assert memory is not None
        assert memory.embedding is not None
        assert len(memory.embedding) == _DIM * 4  # float32

    def test_recall_performance_1000_memories(self) -> None:
        """Recall over 1000 memories should complete in < 500ms."""
        lore = _make_lore()
        for i in range(1000):
            lore.remember(f"memory about topic {i}")

        start = time.perf_counter()
        results = lore.recall("test query", limit=5)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(results) == 5
        assert elapsed_ms < 500, f"Recall took {elapsed_ms:.1f}ms (>500ms)"
