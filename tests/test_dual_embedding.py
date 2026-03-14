"""Tests for F3 — Dual Embedding Routing."""

from __future__ import annotations

import time
from typing import List

import pytest

from lore import Lore
from lore.embed.base import Embedder
from lore.embed.router import EmbeddingRouter, detect_content_type
from lore.store.memory import MemoryStore

_DIM = 384


# ---------------------------------------------------------------------------
# F3-S1: Content type detection heuristic
# ---------------------------------------------------------------------------


class TestDetectContentType:
    """Test detect_content_type heuristic accuracy."""

    CODE_SNIPPETS = [
        # Python function
        'def hello():\n    print("hi")\n    return True',
        # JavaScript
        "const x = (a) => {\n  return a + 1;\n};",
        # Class definition
        "class Foo:\n    def __init__(self):\n        self.x = 1",
        # Import statements
        "import os\nimport sys\nfrom pathlib import Path",
        # Fenced code block
        "```python\ndef foo():\n    pass\n```",
        # TypeScript with arrow functions
        "function handleClick(event: Event) {\n  event.preventDefault();\n}",
        # Go-like code
        "func main() {\n  fmt.Println(\"hello\")\n}",
        # Code with operators
        "const result = items.filter(x => x.active).map(x => x.name);",
    ]

    PROSE_SNIPPETS = [
        "Always use exponential backoff when retrying API calls.",
        "The database migration failed because of a missing column.",
        "Remember to update the README before releasing version 2.0.",
        "Use Redis for caching hot paths in the application layer.",
        "The team decided to adopt TypeScript for better type safety.",
        "Performance degraded after the last deploy due to N+1 queries.",
        "Consider using connection pooling for PostgreSQL connections.",
        "The CI pipeline takes too long; we should parallelize the tests.",
    ]

    @pytest.mark.parametrize("snippet", CODE_SNIPPETS)
    def test_detects_code(self, snippet: str) -> None:
        assert detect_content_type(snippet) == "code"

    @pytest.mark.parametrize("snippet", PROSE_SNIPPETS)
    def test_detects_prose(self, snippet: str) -> None:
        assert detect_content_type(snippet) == "prose"

    def test_performance(self) -> None:
        """Should be <0.1ms per call."""
        text = "def foo():\n    return 42\n"
        detect_content_type(text)  # warmup
        start = time.perf_counter()
        for _ in range(1000):
            detect_content_type(text)
        elapsed_ms = (time.perf_counter() - start) * 1000  # total ms
        per_call_ms = elapsed_ms / 1000
        assert per_call_ms < 0.1, f"Per call: {per_call_ms:.4f}ms (> 0.1ms)"

    def test_empty_string(self) -> None:
        assert detect_content_type("") == "prose"

    def test_mixed_content_with_code_fence(self) -> None:
        text = "Here is how to fix it:\n```python\ndef fix():\n    pass\n```"
        assert detect_content_type(text) == "code"


# ---------------------------------------------------------------------------
# Fake embedders for unit tests
# ---------------------------------------------------------------------------


class FakeProseEmbedder(Embedder):
    """Returns a vector with 0.1 in all dims."""

    def embed(self, text: str) -> List[float]:
        return [0.1] * _DIM

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [[0.1] * _DIM for _ in texts]


class FakeCodeEmbedder(Embedder):
    """Returns a vector with 0.9 in all dims."""

    def embed(self, text: str) -> List[float]:
        return [0.9] * _DIM

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [[0.9] * _DIM for _ in texts]


# ---------------------------------------------------------------------------
# F3-S3: EmbeddingRouter
# ---------------------------------------------------------------------------


class TestEmbeddingRouter:
    @pytest.fixture()
    def router(self) -> EmbeddingRouter:
        return EmbeddingRouter(
            prose_embedder=FakeProseEmbedder(),
            code_embedder=FakeCodeEmbedder(),
        )

    def test_routes_prose(self, router: EmbeddingRouter) -> None:
        vec = router.embed("Always use exponential backoff for retries.")
        assert vec[0] == pytest.approx(0.1)
        assert router.last_embed_model == "prose"

    def test_routes_code(self, router: EmbeddingRouter) -> None:
        vec = router.embed('def foo():\n    x = bar()\n    return x\n')
        assert vec[0] == pytest.approx(0.9)
        assert router.last_embed_model == "code"

    def test_embed_batch_groups(self, router: EmbeddingRouter) -> None:
        texts = [
            "Always retry on 429.",
            'def bar():\n    x = baz()\n    return x\n',
        ]
        results = router.embed_batch(texts)
        assert len(results) == 2
        # Prose snippet → 0.1 vector
        assert results[0][0] == pytest.approx(0.1)
        # Code snippet → 0.9 vector
        assert results[1][0] == pytest.approx(0.9)

    def test_embed_batch_empty(self, router: EmbeddingRouter) -> None:
        assert router.embed_batch([]) == []

    def test_embed_query_dual(self, router: EmbeddingRouter) -> None:
        vecs = router.embed_query_dual("search query")
        assert "prose" in vecs
        assert "code" in vecs
        assert vecs["prose"][0] == pytest.approx(0.1)
        assert vecs["code"][0] == pytest.approx(0.9)

    def test_fallback_when_no_code_embedder(self) -> None:
        router = EmbeddingRouter(prose_embedder=FakeProseEmbedder())
        # Code text should still work — just uses prose embedder
        vec = router.embed('def foo():\n    x = bar()\n    return x\n')
        assert vec[0] == pytest.approx(0.1)

    def test_implements_embedder_protocol(self, router: EmbeddingRouter) -> None:
        assert isinstance(router, Embedder)


# ---------------------------------------------------------------------------
# F3-S3 / S4: Lore integration with dual embedding
# ---------------------------------------------------------------------------


class TestLoreDualEmbedding:
    """Integration tests using fake embedders to verify metadata tracking
    and dual query matching."""

    @pytest.fixture()
    def lore_dual(self) -> Lore:
        prose = FakeProseEmbedder()
        code = FakeCodeEmbedder()
        router = EmbeddingRouter(prose_embedder=prose, code_embedder=code)
        return Lore(

            store=MemoryStore(),
            embedder=router,
            redact=False,
        )

    def test_remember_stores_embed_model_prose(self, lore_dual: Lore) -> None:
        mid = lore_dual.remember("Always use retries for flaky networks.")
        mem = lore_dual.get(mid)
        assert mem is not None
        assert mem.metadata is not None
        assert mem.metadata["embed_model"] == "prose"

    def test_remember_stores_embed_model_code(self, lore_dual: Lore) -> None:
        mid = lore_dual.remember('def handler():\n    x = process()\n    return x\n')
        mem = lore_dual.get(mid)
        assert mem is not None
        assert mem.metadata is not None
        assert mem.metadata["embed_model"] == "code"

    def test_dual_embedding_false_by_default(self) -> None:
        lore = Lore(

            store=MemoryStore(),
            embedding_fn=lambda t: [0.5] * _DIM,
            redact=False,
        )
        mid = lore.remember("test")
        mem = lore.get(mid)
        assert mem is not None
        # No embed_model when not using router
        assert mem.metadata is None or "embed_model" not in (mem.metadata or {})

    def test_recall_matches_by_embed_model(self, lore_dual: Lore) -> None:
        # Store both prose and code memories
        lore_dual.remember("Always cache database queries.")
        lore_dual.remember('def cache_query():\n    val = redis.get(key)\n    return val\n')
        results = lore_dual.recall("caching strategy", limit=10)
        assert len(results) == 2
        # Both should have scores (no crash from dual matching)
        assert all(r.score > 0 for r in results)

    def test_legacy_memories_without_embed_model(self, lore_dual: Lore) -> None:
        """Memories without embed_model metadata should be treated as prose."""
        # Manually store a memory without embed_model
        import struct
        from datetime import datetime, timezone

        from ulid import ULID

        from lore.types import Memory

        vec = [0.5] * _DIM
        emb_bytes = struct.pack(f"{_DIM}f", *vec)
        mem = Memory(
            id=str(ULID()),
            content="legacy memory",
            embedding=emb_bytes,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            metadata=None,  # No embed_model
        )
        lore_dual._store.save(mem)

        results = lore_dual.recall("legacy", limit=5)
        assert len(results) >= 1
        # Should not crash — treated as prose


# ---------------------------------------------------------------------------
# F3-S5: Reindex
# ---------------------------------------------------------------------------


class TestReindex:
    def test_reindex_updates_embed_model(self) -> None:
        prose = FakeProseEmbedder()
        code = FakeCodeEmbedder()
        router = EmbeddingRouter(prose_embedder=prose, code_embedder=code)

        # First create memories with a plain embedder (no embed_model set)
        lore = Lore(

            store=MemoryStore(),
            embedding_fn=lambda t: [0.5] * _DIM,
            redact=False,
        )
        mid_prose = lore.remember("Always use retries for flaky networks.")
        mid_code = lore.remember('def handler():\n    x = process()\n    return x\n')

        # Switch to router and reindex
        lore._embedder = router
        updated = lore.reindex()
        assert updated == 2

        m1 = lore.get(mid_prose)
        m2 = lore.get(mid_code)
        assert m1 is not None and m1.metadata is not None
        assert m1.metadata["embed_model"] == "prose"
        assert m2 is not None and m2.metadata is not None
        assert m2.metadata["embed_model"] == "code"

    def test_reindex_dry_run(self) -> None:
        prose = FakeProseEmbedder()
        code = FakeCodeEmbedder()
        router = EmbeddingRouter(prose_embedder=prose, code_embedder=code)

        lore = Lore(

            store=MemoryStore(),
            embedding_fn=lambda t: [0.5] * _DIM,
            redact=False,
        )
        lore.remember("Test memory")
        lore._embedder = router

        updated = lore.reindex(dry_run=True)
        assert updated == 1

        # Memory should NOT be changed
        mems = lore.list_memories()
        assert mems[0].metadata is None or "embed_model" not in (mems[0].metadata or {})

    def test_reindex_idempotent(self) -> None:
        prose = FakeProseEmbedder()
        code = FakeCodeEmbedder()
        router = EmbeddingRouter(prose_embedder=prose, code_embedder=code)
        lore = Lore(store=MemoryStore(), embedder=router, redact=False)
        lore.remember("Always use retries.")

        # First reindex
        lore.reindex()
        # Second reindex — nothing should change
        updated = lore.reindex()
        assert updated == 0

    def test_reindex_progress_callback(self) -> None:
        router = EmbeddingRouter(
            prose_embedder=FakeProseEmbedder(),
            code_embedder=FakeCodeEmbedder(),
        )
        lore = Lore(

            store=MemoryStore(),
            embedding_fn=lambda t: [0.5] * _DIM,
            redact=False,
        )
        lore.remember("mem1")
        lore.remember("mem2")
        lore._embedder = router

        calls: list[tuple[int, int]] = []
        lore.reindex(progress_fn=lambda done, total: calls.append((done, total)))
        assert len(calls) == 2
        assert calls[-1] == (2, 2)
