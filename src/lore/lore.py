"""Main Lore class — entry point for the SDK."""

from __future__ import annotations

import os
import struct
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

import numpy as np
from ulid import ULID

from lore.embed.base import Embedder
from lore.embed.local import LocalEmbedder
from lore.redact.pipeline import RedactionPipeline
from lore.store.base import Store
from lore.store.sqlite import SqliteStore
from lore.types import Lesson, QueryResult

# Type alias for user-provided embedding functions
EmbeddingFn = Callable[[str], List[float]]

# Type for custom redaction patterns: (regex_string, label)
RedactPattern = Tuple[str, str]

_EMBEDDING_DIM = 384


def _serialize_embedding(vec: List[float]) -> bytes:
    """Serialize a float list to bytes (float32)."""
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize_embedding(data: bytes) -> np.ndarray:
    """Deserialize bytes to numpy array (float32)."""
    count = len(data) // 4
    return np.array(struct.unpack(f"{count}f", data), dtype=np.float32)


class _FnEmbedder(Embedder):
    """Wraps a user-provided embedding function as an Embedder."""

    def __init__(self, fn: EmbeddingFn) -> None:
        self._fn = fn

    def embed(self, text: str) -> List[float]:
        return self._fn(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self._fn(t) for t in texts]


class Lore:
    """Cross-agent memory library.

    Usage::

        lore = Lore()
        lesson_id = lore.publish(problem="...", resolution="...")
        results = lore.query("how to handle rate limits")
    """

    def __init__(
        self,
        project: Optional[str] = None,
        db_path: Optional[str] = None,
        store: Optional[Store] = None,
        embedding_fn: Optional[EmbeddingFn] = None,
        embedder: Optional[Embedder] = None,
        redact: bool = True,
        redact_patterns: Optional[List[RedactPattern]] = None,
    ) -> None:
        self.project = project

        # Redaction pipeline
        self._redact_enabled = redact
        if redact:
            self._redactor = RedactionPipeline(
                custom_patterns=redact_patterns,
            )
        else:
            self._redactor = None

        if store is not None:
            self._store = store
        else:
            if db_path is None:
                db_path = os.path.join(
                    os.path.expanduser("~"), ".lore", "default.db"
                )
            self._store = SqliteStore(db_path)

        # Resolve embedder: explicit embedder > embedding_fn > default local
        if embedder is not None:
            self._embedder = embedder
        elif embedding_fn is not None:
            self._embedder = _FnEmbedder(embedding_fn)
        else:
            self._embedder = LocalEmbedder()

    def close(self) -> None:
        """Close underlying store if it supports closing."""
        if hasattr(self._store, "close"):
            self._store.close()  # type: ignore[attr-defined]

    def __enter__(self) -> "Lore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def publish(
        self,
        problem: str,
        resolution: str,
        context: Optional[str] = None,
        tags: Optional[List[str]] = None,
        confidence: float = 0.5,
        source: Optional[str] = None,
        project: Optional[str] = None,
    ) -> str:
        """Publish a new lesson. Returns the lesson ID (ULID)."""
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {confidence}"
            )

        # Redact sensitive data before storage
        if self._redactor is not None:
            problem = self._redactor.run(problem)
            resolution = self._redactor.run(resolution)
            if context is not None:
                context = self._redactor.run(context)

        # Build text for embedding
        embed_text = f"{problem} {resolution}"
        if context:
            embed_text = f"{embed_text} {context}"
        embedding_vec = self._embedder.embed(embed_text)
        embedding_bytes = _serialize_embedding(embedding_vec)

        now = _utc_now_iso()
        lesson = Lesson(
            id=str(ULID()),
            problem=problem,
            resolution=resolution,
            context=context,
            tags=tags or [],
            confidence=confidence,
            source=source,
            project=project or self.project,
            embedding=embedding_bytes,
            created_at=now,
            updated_at=now,
        )
        self._store.save(lesson)
        return lesson.id

    def query(
        self,
        text: str,
        tags: Optional[List[str]] = None,
        limit: int = 5,
        min_confidence: float = 0.0,
    ) -> List[QueryResult]:
        """Query lessons by semantic similarity, optionally filtered by tags.

        Returns a list of QueryResult ordered by descending similarity score.
        """
        # Get all candidates (scope to project if set)
        all_lessons = self._store.list(project=self.project)

        # Filter by tags
        if tags:
            tag_set = set(tags)
            all_lessons = [
                l for l in all_lessons
                if tag_set.issubset(set(l.tags))
            ]

        # Filter by min_confidence
        if min_confidence > 0.0:
            all_lessons = [
                l for l in all_lessons if l.confidence >= min_confidence
            ]

        # Filter out lessons without embeddings
        candidates = [l for l in all_lessons if l.embedding]
        if not candidates:
            return []

        # Embed query
        query_vec = np.array(
            self._embedder.embed(text), dtype=np.float32
        )

        # Vectorized cosine similarity
        embeddings = np.array(
            [_deserialize_embedding(l.embedding) for l in candidates],  # type: ignore[arg-type]
            dtype=np.float32,
        )
        # Normalize (embeddings should already be normalized, but be safe)
        query_norm = query_vec / max(np.linalg.norm(query_vec), 1e-9)
        emb_norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        emb_norms = np.clip(emb_norms, 1e-9, None)
        embeddings_normed = embeddings / emb_norms

        scores = embeddings_normed @ query_norm

        # Build results sorted by score descending
        results = [
            QueryResult(lesson=candidates[i], score=float(scores[i]))
            for i in range(len(candidates))
        ]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def get(self, lesson_id: str) -> Optional[Lesson]:
        """Get a lesson by ID."""
        return self._store.get(lesson_id)

    def list(
        self,
        project: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Lesson]:
        """List lessons, optionally filtered by project."""
        return self._store.list(project=project, limit=limit)

    def delete(self, lesson_id: str) -> bool:
        """Delete a lesson by ID."""
        return self._store.delete(lesson_id)


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
