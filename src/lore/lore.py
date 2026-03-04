"""Main Lore class — entry point for the SDK."""

from __future__ import annotations

import os
import re
import struct
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from ulid import ULID

from lore.embed.base import Embedder
from lore.embed.local import LocalEmbedder
from lore.memory_store.base import Store as MemoryStore
from lore.memory_store.sqlite import SqliteStore as MemorySqliteStore
from lore.redact.pipeline import RedactionPipeline
from lore.types import Memory, SearchResult, StoreStats

# Type alias for user-provided embedding functions
EmbeddingFn = Callable[[str], List[float]]

# Type for custom redaction patterns: (regex_string, label)
RedactPattern = Tuple[str, str]

_EMBEDDING_DIM = 384


def _parse_ttl(ttl: Optional[str]) -> Optional[str]:
    """Parse a TTL string (e.g. '7d', '1h', '30m') into an ISO expires_at."""
    if not ttl:
        return None
    match = re.match(r"^(\d+)([smhdw])$", ttl.strip().lower())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    delta_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    expires = datetime.now(timezone.utc) + timedelta(**{delta_map[unit]: value})
    return expires.isoformat()


def _serialize_embedding(vec: List[float]) -> bytes:
    """Serialize a float list to bytes (float32)."""
    return struct.pack(f"{len(vec)}f", *vec)


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
        memory = lore.remember("API rate limit is 100 req/min")
        results = lore.recall("rate limits")
    """

    def __init__(
        self,
        project: Optional[str] = None,
        db_path: Optional[str] = None,
        embedding_fn: Optional[EmbeddingFn] = None,
        embedder: Optional[Embedder] = None,
        redact: bool = True,
        redact_patterns: Optional[List[RedactPattern]] = None,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
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

        if api_url and api_key:
            from lore.memory_store.remote import RemoteStore as MemoryRemoteStore
            self._memory_store: MemoryStore = MemoryRemoteStore(
                api_url=api_url, api_key=api_key,
            )
        else:
            if db_path is None:
                db_path = os.path.join(
                    os.path.expanduser("~"), ".lore", "default.db"
                )
            self._memory_store = MemorySqliteStore(db_path)

        # Resolve embedder: explicit embedder > embedding_fn > default local
        if embedder is not None:
            self._embedder = embedder
        elif embedding_fn is not None:
            self._embedder = _FnEmbedder(embedding_fn)
        else:
            self._embedder = LocalEmbedder()

    def close(self) -> None:
        """Close underlying stores if they support closing."""
        if hasattr(self._memory_store, "close"):
            self._memory_store.close()  # type: ignore[attr-defined]

    def __enter__(self) -> "Lore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Memory API (remember / recall / forget / list_memories / stats)
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        type: str = "note",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        project: Optional[str] = None,
        source: Optional[str] = None,
        ttl: Optional[str] = None,
    ) -> Memory:
        """Store a memory. Returns the Memory object (without embedding)."""
        effective_project = project or self.project
        expires_at = _parse_ttl(ttl)

        embedding_vec = self._embedder.embed(content)
        embedding_bytes = _serialize_embedding(embedding_vec)

        now = _utc_now_iso()
        memory = Memory(
            id=str(ULID()),
            content=content,
            type=type,
            source=source,
            project=effective_project,
            tags=tags or [],
            metadata=metadata or {},
            embedding=embedding_bytes,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        self._memory_store.save(memory)
        return replace(memory, embedding=None)

    def recall(
        self,
        query_text: str,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 5,
    ) -> List[SearchResult]:
        """Semantic search over memories. Returns results sorted by score."""
        effective_project = project or self.project
        query_vec = self._embedder.embed(query_text)
        return self._memory_store.search(
            embedding=query_vec,
            type=type,
            tags=tags,
            project=effective_project,
            limit=limit,
        )

    def forget(
        self,
        id: Optional[str] = None,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
    ) -> int:
        """Delete memories. By ID (returns 1/0), or by filter (returns count)."""
        if id:
            return 1 if self._memory_store.delete(id) else 0
        return self._memory_store.delete_by_filter(
            type=type, tags=tags, project=project,
        )

    def list_memories(
        self,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        include_expired: bool = False,
    ) -> Tuple[List[Memory], int]:
        """List memories with optional filters. Returns (memories, total)."""
        effective_project = project or self.project
        return self._memory_store.list(
            type=type,
            tags=tags,
            project=effective_project,
            limit=limit,
            offset=offset,
            include_expired=include_expired,
        )

    def memory_stats(
        self,
        project: Optional[str] = None,
    ) -> StoreStats:
        """Get aggregate statistics for the memory store."""
        effective_project = project or self.project
        return self._memory_store.stats(project=effective_project)

    def stats(
        self,
        project: Optional[str] = None,
    ) -> StoreStats:
        """Get aggregate statistics for the memory store.

        Convenience alias for :meth:`memory_stats`.
        """
        return self.memory_stats(project=project)

    def get_memory(self, memory_id: str) -> Optional[Memory]:
        """Get a single memory by ID."""
        return self._memory_store.get(memory_id)


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
