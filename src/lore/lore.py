"""Main Lore class — entry point for the SDK."""

from __future__ import annotations

import os
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
from ulid import ULID

from lore.embed.base import Embedder
from lore.embed.local import LocalEmbedder
from lore.exceptions import MemoryNotFoundError
from lore.redact.pipeline import RedactionPipeline
from lore.store.base import Store
from lore.store.sqlite import SqliteStore
from lore.types import Memory, RecallResult

# Type alias for user-provided embedding functions
EmbeddingFn = Callable[[str], List[float]]

# Type for custom redaction patterns: (regex_string, label)
RedactPattern = Tuple[str, str]

_EMBEDDING_DIM = 384
_DEFAULT_HALF_LIFE_DAYS = 30


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
        memory_id = lore.remember("Always use exponential backoff for rate limits")
        results = lore.recall("how to handle rate limits")
    """

    def __init__(
        self,
        project: Optional[str] = None,
        db_path: Optional[str] = None,
        store: Optional[Union[Store, str]] = None,
        embedding_fn: Optional[EmbeddingFn] = None,
        embedder: Optional[Embedder] = None,
        redact: bool = True,
        redact_patterns: Optional[List[RedactPattern]] = None,
        decay_half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.project = project
        self._half_life_days = decay_half_life_days

        # Redaction pipeline
        self._redact_enabled = redact
        if redact:
            self._redactor = RedactionPipeline(
                custom_patterns=redact_patterns,
            )
        else:
            self._redactor = None

        if isinstance(store, str) and store != "remote":
            raise ValueError(f"store must be a Store instance or 'remote', got {store!r}")
        if isinstance(store, str) and store == "remote":
            if not api_url or not api_key:
                raise ValueError(
                    "api_url and api_key are required when store='remote'"
                )
            # Remote store deferred to Phase B — raise clear error
            raise ValueError(
                "Remote store is not supported in this version. "
                "Use a local store instead."
            )
        elif isinstance(store, Store):
            self._store: Store = store
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

    def remember(
        self,
        content: str,
        *,
        type: str = "general",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        project: Optional[str] = None,
        ttl: Optional[int] = None,
        confidence: float = 1.0,
    ) -> str:
        """Store a memory. Returns the memory ID (ULID)."""
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {confidence}"
            )

        # Redact sensitive data before storage
        if self._redactor is not None:
            content = self._redactor.run(content)

        # Compute embedding
        embedding_vec = self._embedder.embed(content)
        embedding_bytes = _serialize_embedding(embedding_vec)

        now = _utc_now_iso()

        # Compute expires_at from ttl
        expires_at = None
        if ttl is not None:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=ttl)
            ).isoformat()

        memory = Memory(
            id=str(ULID()),
            content=content,
            type=type,
            tags=tags or [],
            metadata=metadata,
            source=source,
            project=project or self.project,
            embedding=embedding_bytes,
            created_at=now,
            updated_at=now,
            ttl=ttl,
            expires_at=expires_at,
            confidence=confidence,
        )
        self._store.save(memory)
        return memory.id

    def recall(
        self,
        query: str,
        *,
        tags: Optional[List[str]] = None,
        type: Optional[str] = None,
        limit: int = 5,
        min_confidence: float = 0.0,
    ) -> List[RecallResult]:
        """Semantic search for memories.

        Returns a list of RecallResult ordered by descending score.
        """
        query_vec = self._embedder.embed(query)
        return self._recall_local(
            query_vec, tags=tags, type=type, limit=limit,
            min_confidence=min_confidence,
        )

    def _recall_local(
        self,
        query_vec: List[float],
        *,
        tags: Optional[List[str]] = None,
        type: Optional[str] = None,
        limit: int = 5,
        min_confidence: float = 0.0,
    ) -> List[RecallResult]:
        """Client-side semantic search for local stores."""
        now = datetime.now(timezone.utc)

        # Get all candidates (scope to project if set, optionally by type)
        all_memories = self._store.list(project=self.project, type=type)

        # Filter expired memories
        all_memories = [
            m for m in all_memories
            if m.expires_at is None
            or datetime.fromisoformat(m.expires_at) > now
        ]

        # Filter by tags
        if tags:
            tag_set = set(tags)
            all_memories = [
                m for m in all_memories
                if tag_set.issubset(set(m.tags))
            ]

        # Filter by min_confidence
        if min_confidence > 0.0:
            all_memories = [
                m for m in all_memories if m.confidence >= min_confidence
            ]

        # Filter out memories without embeddings
        candidates = [m for m in all_memories if m.embedding]
        if not candidates:
            return []

        query_arr = np.array(query_vec, dtype=np.float32)

        # Vectorized cosine similarity
        embeddings = np.array(
            [_deserialize_embedding(m.embedding) for m in candidates],  # type: ignore[arg-type]
            dtype=np.float32,
        )
        query_norm = query_arr / max(np.linalg.norm(query_arr), 1e-9)
        emb_norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        emb_norms = np.clip(emb_norms, 1e-9, None)
        embeddings_normed = embeddings / emb_norms

        cosine_scores = embeddings_normed @ query_norm

        # Apply decay: score *= confidence * time_factor * vote_factor
        results: List[RecallResult] = []
        for i, memory in enumerate(candidates):
            age_days = (
                now - datetime.fromisoformat(memory.created_at)
            ).total_seconds() / 86400.0
            time_factor = 0.5 ** (age_days / self._half_life_days)
            vote_factor = 1.0 + (memory.upvotes - memory.downvotes) * 0.1
            vote_factor = max(vote_factor, 0.1)
            decay = memory.confidence * time_factor * vote_factor
            final_score = float(cosine_scores[i]) * decay
            results.append(RecallResult(memory=memory, score=final_score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def forget(self, memory_id: str) -> bool:
        """Delete a memory by ID. Returns True if it existed."""
        return self._store.delete(memory_id)

    def get(self, memory_id: str) -> Optional[Memory]:
        """Get a memory by ID."""
        return self._store.get(memory_id)

    def list_memories(
        self,
        *,
        project: Optional[str] = None,
        type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Memory]:
        """List memories with optional filters."""
        return self._store.list(project=project, type=type, limit=limit)

    def stats(self, project: Optional[str] = None) -> Dict[str, Any]:
        """Return memory statistics."""
        all_memories = self._store.list(project=project)
        if not all_memories:
            return {"total": 0, "by_type": {}, "oldest": None, "newest": None}

        by_type: Dict[str, int] = {}
        for m in all_memories:
            by_type[m.type] = by_type.get(m.type, 0) + 1

        # Memories are sorted by created_at desc, so newest is first
        return {
            "total": len(all_memories),
            "by_type": by_type,
            "oldest": all_memories[-1].created_at,
            "newest": all_memories[0].created_at,
        }

    def upvote(self, memory_id: str) -> None:
        """Increment upvotes for a memory."""
        memory = self._store.get(memory_id)
        if memory is None:
            raise MemoryNotFoundError(memory_id)
        memory.upvotes += 1
        memory.updated_at = _utc_now_iso()
        self._store.update(memory)

    def downvote(self, memory_id: str) -> None:
        """Increment downvotes for a memory."""
        memory = self._store.get(memory_id)
        if memory is None:
            raise MemoryNotFoundError(memory_id)
        memory.downvotes += 1
        memory.updated_at = _utc_now_iso()
        self._store.update(memory)


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
