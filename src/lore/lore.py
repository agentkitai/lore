"""Main Lore class — entry point for the SDK."""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
from ulid import ULID

from lore.embed.base import Embedder
from lore.embed.local import LocalEmbedder
from lore.exceptions import LessonNotFoundError
from lore.memory_store.base import Store as MemoryStore
from lore.memory_store.sqlite import SqliteStore as MemorySqliteStore
from lore.prompt import as_prompt as _as_prompt
from lore.redact.pipeline import RedactionPipeline
from lore.store.base import Store
from lore.store.sqlite import SqliteStore
from lore.types import Lesson, Memory, QueryResult, SearchResult, StoreStats

# Type alias for user-provided embedding functions
EmbeddingFn = Callable[[str], List[float]]

# Type for custom redaction patterns: (regex_string, label)
RedactPattern = Tuple[str, str]

_EMBEDDING_DIM = 384
_DEFAULT_HALF_LIFE_DAYS = 30


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

        if isinstance(store, str) and store == "remote":
            if not api_url or not api_key:
                raise ValueError(
                    "api_url and api_key are required when store='remote'"
                )
            from lore.store.remote import RemoteStore
            self._store: Store = RemoteStore(api_url=api_url, api_key=api_key)
            from lore.memory_store.remote import RemoteStore as MemoryRemoteStore
            self._memory_store: MemoryStore = MemoryRemoteStore(
                api_url=api_url, api_key=api_key,
            )
        elif isinstance(store, Store):
            self._store = store
            # If user passed a custom lesson store, memory store uses default
            if db_path is None:
                db_path = os.path.join(
                    os.path.expanduser("~"), ".lore", "default.db"
                )
            self._memory_store = MemorySqliteStore(db_path)
        elif store is not None:
            raise ValueError(f"store must be a Store instance or 'remote', got {store!r}")
        else:
            if db_path is None:
                db_path = os.path.join(
                    os.path.expanduser("~"), ".lore", "default.db"
                )
            self._store = SqliteStore(db_path)
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
        if hasattr(self._store, "close"):
            self._store.close()  # type: ignore[attr-defined]
        if hasattr(self._memory_store, "close"):
            self._memory_store.close()  # type: ignore[attr-defined]

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

        Returns a list of QueryResult ordered by descending score
        (cosine similarity * decay).
        """
        # Embed query text
        query_vec = self._embedder.embed(text)

        # For remote stores, delegate search to the server
        from lore.store.remote import RemoteStore
        if isinstance(self._store, RemoteStore):
            return self._query_remote(query_vec, tags=tags, limit=limit)

        return self._query_local(query_vec, tags=tags, limit=limit, min_confidence=min_confidence)

    def _query_remote(
        self,
        query_vec: List[float],
        tags: Optional[List[str]] = None,
        limit: int = 5,
    ) -> List[QueryResult]:
        """Delegate semantic search to the remote Lore server."""
        from lore.store.remote import RemoteStore, _response_to_lesson
        assert isinstance(self._store, RemoteStore)
        raw_results = self._store.search(
            embedding=query_vec,
            limit=limit,
            tags=tags,
            project=self.project,
        )
        results: List[QueryResult] = []
        for item in raw_results:
            score = item.get("score", 0.0)
            lesson = _response_to_lesson(item)
            results.append(QueryResult(lesson=lesson, score=float(score)))
        return results

    def _query_local(
        self,
        query_vec: List[float],
        tags: Optional[List[str]] = None,
        limit: int = 5,
        min_confidence: float = 0.0,
    ) -> List[QueryResult]:
        """Client-side semantic search for local (SQLite) stores."""
        now = datetime.now(timezone.utc)

        # Get all candidates (scope to project if set)
        all_lessons = self._store.list(project=self.project)

        # Filter expired lessons
        all_lessons = [
            l for l in all_lessons
            if l.expires_at is None
            or datetime.fromisoformat(l.expires_at) > now
        ]

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

        query_arr = np.array(query_vec, dtype=np.float32)

        # Vectorized cosine similarity
        embeddings = np.array(
            [_deserialize_embedding(l.embedding) for l in candidates],  # type: ignore[arg-type]
            dtype=np.float32,
        )
        # Normalize (embeddings should already be normalized, but be safe)
        query_norm = query_arr / max(np.linalg.norm(query_arr), 1e-9)
        emb_norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        emb_norms = np.clip(emb_norms, 1e-9, None)
        embeddings_normed = embeddings / emb_norms

        cosine_scores = embeddings_normed @ query_norm

        # Apply decay: score *= confidence * time_factor * vote_factor
        results: List[QueryResult] = []
        for i, lesson in enumerate(candidates):
            age_days = (
                now - datetime.fromisoformat(lesson.created_at)
            ).total_seconds() / 86400.0
            time_factor = 0.5 ** (age_days / self._half_life_days)
            vote_factor = 1.0 + (lesson.upvotes - lesson.downvotes) * 0.1
            vote_factor = max(vote_factor, 0.1)
            decay = lesson.confidence * time_factor * vote_factor
            final_score = float(cosine_scores[i]) * decay
            results.append(QueryResult(lesson=lesson, score=final_score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def upvote(self, lesson_id: str) -> None:
        """Increment upvotes for a lesson."""
        lesson = self._store.get(lesson_id)
        if lesson is None:
            raise LessonNotFoundError(lesson_id)
        lesson.upvotes += 1
        lesson.updated_at = _utc_now_iso()
        self._store.update(lesson)

    def downvote(self, lesson_id: str) -> None:
        """Increment downvotes for a lesson."""
        lesson = self._store.get(lesson_id)
        if lesson is None:
            raise LessonNotFoundError(lesson_id)
        lesson.downvotes += 1
        lesson.updated_at = _utc_now_iso()
        self._store.update(lesson)

    def as_prompt(
        self,
        lessons: List[QueryResult],
        max_tokens: int = 1000,
    ) -> str:
        """Format query results for system prompt injection."""
        return _as_prompt(lessons, max_tokens=max_tokens)

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

    def export_lessons(
        self,
        path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Export all lessons as JSON-serializable dicts.

        If *path* is given, writes ``{"version": 1, "lessons": [...]}``
        to that file and returns the lesson list.
        """
        lessons = self._store.list(project=self.project)
        serialized: List[Dict[str, Any]] = []
        for lesson in lessons:
            d = asdict(lesson)
            # embedding is bytes — drop it from export (not portable)
            d.pop("embedding", None)
            serialized.append(d)

        if path is not None:
            payload: Dict[str, Any] = {
                "version": 1,
                "lessons": serialized,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        return serialized

    def import_lessons(
        self,
        path: Optional[str] = None,
        data: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> int:
        """Import lessons from a file or data structure.

        Accepts either the wrapped format ``{"version": 1, "lessons": [...]}``
        or a raw list of lesson dicts.  Skips duplicates (by ID).

        Returns the number of lessons actually imported.
        """
        if path is not None:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

        if data is None:
            raise ValueError("Either path or data must be provided")

        # Unwrap versioned format
        if isinstance(data, dict):
            lessons_raw: List[Dict[str, Any]] = data.get("lessons", [])
        else:
            lessons_raw = data

        # Gather existing IDs for duplicate check
        existing_ids = {l.id for l in self._store.list()}

        imported = 0
        for item in lessons_raw:
            lid = item.get("id")
            if lid and lid in existing_ids:
                continue

            # Re-embed for vector search
            embed_text = f"{item.get('problem', '')} {item.get('resolution', '')}"
            ctx = item.get("context")
            if ctx:
                embed_text = f"{embed_text} {ctx}"
            embedding_vec = self._embedder.embed(embed_text)
            embedding_bytes = _serialize_embedding(embedding_vec)

            lesson = Lesson(
                id=item.get("id", str(ULID())),
                problem=item.get("problem", ""),
                resolution=item.get("resolution", ""),
                context=item.get("context"),
                tags=item.get("tags", []),
                confidence=item.get("confidence", 0.5),
                source=item.get("source"),
                project=item.get("project"),
                embedding=embedding_bytes,
                created_at=item.get("created_at", _utc_now_iso()),
                updated_at=item.get("updated_at", _utc_now_iso()),
                expires_at=item.get("expires_at"),
                upvotes=item.get("upvotes", 0),
                downvotes=item.get("downvotes", 0),
                meta=item.get("meta"),
            )
            self._store.save(lesson)
            existing_ids.add(lesson.id)
            imported += 1

        return imported

    # ------------------------------------------------------------------
    # New Memory API (remember / recall / forget / list_memories / stats)
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
    ) -> str:
        """Store a memory. Returns the memory ID."""
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
        return memory.id

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
    ) -> Tuple[List[Memory], int]:
        """List memories with optional filters. Returns (memories, total)."""
        effective_project = project or self.project
        return self._memory_store.list(
            type=type,
            tags=tags,
            project=effective_project,
            limit=limit,
            offset=offset,
        )

    def memory_stats(
        self,
        project: Optional[str] = None,
    ) -> StoreStats:
        """Get aggregate statistics for the memory store."""
        effective_project = project or self.project
        return self._memory_store.stats(project=effective_project)

    def get_memory(self, memory_id: str) -> Optional[Memory]:
        """Get a single memory by ID."""
        return self._memory_store.get(memory_id)


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
