"""Tests for the embedding engine."""

from __future__ import annotations

import time
from typing import List

import pytest

from lore import Lore
from lore.embed.base import Embedder
from lore.embed.local import LocalEmbedder
from lore.store.memory import MemoryStore

_EMBEDDING_DIM = 384


class TestEmbedderABC:
    """Test that Embedder is a proper ABC."""

    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            Embedder()  # type: ignore[abstract]

    def test_subclass_must_implement(self) -> None:
        class Incomplete(Embedder):
            def embed(self, text: str) -> List[float]:
                return []

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]


class TestLocalEmbedder:
    """Tests for LocalEmbedder (requires model download on first run)."""

    @pytest.fixture(scope="class")
    def embedder(self) -> LocalEmbedder:
        return LocalEmbedder()

    def test_embed_returns_384_floats(self, embedder: LocalEmbedder) -> None:
        result = embedder.embed("hello world")
        assert len(result) == _EMBEDDING_DIM
        assert all(isinstance(x, float) for x in result)

    def test_identical_text_identical_vectors(
        self, embedder: LocalEmbedder
    ) -> None:
        a = embedder.embed("hello world")
        b = embedder.embed("hello world")
        assert a == b

    def test_different_text_different_vectors(
        self, embedder: LocalEmbedder
    ) -> None:
        a = embedder.embed("hello world")
        b = embedder.embed("quantum physics research")
        assert a != b

    def test_embed_batch(self, embedder: LocalEmbedder) -> None:
        results = embedder.embed_batch(["hello", "world"])
        assert len(results) == 2
        assert all(len(v) == _EMBEDDING_DIM for v in results)

    def test_embed_batch_empty(self, embedder: LocalEmbedder) -> None:
        assert embedder.embed_batch([]) == []

    def test_embed_performance(self, embedder: LocalEmbedder) -> None:
        """Single sentence embedding should take < 200ms on CPU."""
        embedder.embed("warmup")
        start = time.perf_counter()
        embedder.embed("Stripe API returns 429 after 100 requests per minute")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 200, f"Embedding took {elapsed_ms:.1f}ms (>200ms)"


class TestCustomEmbeddingFn:
    """Test that Lore accepts a custom embedding function."""

    def test_custom_fn_used(self) -> None:
        calls: List[str] = []

        def fake_embed(text: str) -> List[float]:
            calls.append(text)
            return [0.1] * _EMBEDDING_DIM

        lore = Lore(store=MemoryStore(), embedding_fn=fake_embed)
        lore.remember("test content")
        assert len(calls) == 1
        assert "test content" in calls[0]
