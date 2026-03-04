"""Main Lore class — entry point for the SDK."""

from __future__ import annotations

import json
import os
import struct
import time
import warnings
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
from lore.types import Memory, MemoryStats, RecallResult

# Type alias for user-provided embedding functions
EmbeddingFn = Callable[[str], List[float]]

# Type for custom redaction patterns: (regex_string, label)
RedactPattern = Tuple[str, str]

_EMBEDDING_DIM = 384
_DEFAULT_HALF_LIFE_DAYS = 30
_CLEANUP_INTERVAL_SECONDS = 60


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
        self._last_cleanup: float = 0.0
        self._last_cleanup_count: int = 0

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

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        *,
        type: str = "general",
        context: Optional[str] = None,
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
            if context:
                context = self._redactor.run(context)

        # Compute embedding
        embed_text = f"{content} {context}" if context else content
        embedding_vec = self._embedder.embed(embed_text)
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
            context=context,
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
        Triggers lazy cleanup of expired memories.
        """
        self._maybe_cleanup_expired()
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
        """List memories with optional filters. Excludes expired memories."""
        now = datetime.now(timezone.utc)
        memories = self._store.list(project=project, type=type, limit=None)
        memories = [
            m for m in memories
            if m.expires_at is None
            or datetime.fromisoformat(m.expires_at) > now
        ]
        if limit is not None:
            memories = memories[:limit]
        return memories

    def stats(self, project: Optional[str] = None) -> MemoryStats:
        """Return memory statistics."""
        all_memories = self._store.list(project=project)
        if not all_memories:
            return MemoryStats(
                total=0,
                expired_cleaned=self._last_cleanup_count,
            )

        by_type: Dict[str, int] = {}
        for m in all_memories:
            by_type[m.type] = by_type.get(m.type, 0) + 1

        # Memories are sorted by created_at desc, so newest is first
        return MemoryStats(
            total=len(all_memories),
            by_type=by_type,
            oldest=all_memories[-1].created_at,
            newest=all_memories[0].created_at,
            expired_cleaned=self._last_cleanup_count,
        )

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

    # ------------------------------------------------------------------
    # TTL Cleanup
    # ------------------------------------------------------------------

    def _maybe_cleanup_expired(self) -> None:
        """Run cleanup_expired at most once per 60 seconds."""
        now = time.monotonic()
        if now - self._last_cleanup >= _CLEANUP_INTERVAL_SECONDS:
            self._last_cleanup = now
            self._last_cleanup_count = self._store.cleanup_expired()

    # ------------------------------------------------------------------
    # Deprecated methods (backward compat with pre-0.3 API)
    # ------------------------------------------------------------------

    def publish(
        self,
        problem: str,
        resolution: str,
        *,
        context: Optional[str] = None,
        tags: Optional[List[str]] = None,
        confidence: float = 0.5,
        source: Optional[str] = None,
        project: Optional[str] = None,
    ) -> str:
        """Deprecated: use remember() instead."""
        warnings.warn("publish() is deprecated, use remember()", DeprecationWarning, stacklevel=2)
        content = f"{problem}\n\n{resolution}"
        return self.remember(
            content, type="lesson", context=context, tags=tags,
            confidence=confidence, source=source, project=project,
        )

    def query(
        self,
        text: str,
        *,
        tags: Optional[List[str]] = None,
        limit: int = 5,
        min_confidence: float = 0.0,
    ) -> List[RecallResult]:
        """Deprecated: use recall() instead."""
        warnings.warn("query() is deprecated, use recall()", DeprecationWarning, stacklevel=2)
        return self.recall(text, tags=tags, limit=limit, min_confidence=min_confidence)

    def delete(self, memory_id: str) -> bool:
        """Deprecated: use forget() instead."""
        warnings.warn("delete() is deprecated, use forget()", DeprecationWarning, stacklevel=2)
        return self.forget(memory_id)

    def export_lessons(self, path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Deprecated: export memories as JSON-serializable dicts."""
        warnings.warn("export_lessons() is deprecated", DeprecationWarning, stacklevel=2)
        memories = self._store.list()
        data = []
        for m in memories:
            d: Dict[str, Any] = {
                "id": m.id,
                "content": m.content,
                "type": m.type,
                "tags": m.tags,
                "confidence": m.confidence,
                "source": m.source,
                "project": m.project,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
                "upvotes": m.upvotes,
                "downvotes": m.downvotes,
            }
            if m.metadata:
                d["metadata"] = m.metadata
            if m.context:
                d["context"] = m.context
            data.append(d)
        if path:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        return data

    def import_lessons(
        self,
        path: Optional[str] = None,
        data: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Deprecated: import memories from file or data. Skips duplicates."""
        warnings.warn("import_lessons() is deprecated", DeprecationWarning, stacklevel=2)
        if path:
            with open(path) as f:
                data = json.load(f)
        if not data:
            return 0
        count = 0
        for d in data:
            mid = d.get("id", str(ULID()))
            if self._store.get(mid) is not None:
                continue
            memory = Memory(
                id=mid,
                content=d.get("content", ""),
                type=d.get("type", "general"),
                context=d.get("context"),
                tags=d.get("tags", []),
                metadata=d.get("metadata"),
                confidence=d.get("confidence", 1.0),
                source=d.get("source"),
                project=d.get("project"),
                created_at=d.get("created_at", _utc_now_iso()),
                updated_at=d.get("updated_at", _utc_now_iso()),
                upvotes=d.get("upvotes", 0),
                downvotes=d.get("downvotes", 0),
            )
            self._store.save(memory)
            count += 1
        return count


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
