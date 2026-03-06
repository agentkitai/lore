"""Main Lore class — entry point for the SDK."""

from __future__ import annotations

import os
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
from ulid import ULID

from lore.embed.base import Embedder
from lore.embed.local import LocalEmbedder, make_code_embedder
from lore.embed.router import EmbeddingRouter
from lore.exceptions import MemoryNotFoundError, SecretBlockedError
from lore.redact.pipeline import RedactionPipeline
from lore.store.base import Store
from lore.store.sqlite import SqliteStore
from lore.importance import (
    compute_importance,
    resolve_half_life,
    time_adjusted_importance,
)
from lore.types import (
    DECAY_HALF_LIVES,
    VALID_MEMORY_TYPES,
    Memory,
    MemoryStats,
    RecallResult,
)

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
        security_scan_levels: Optional[List[int]] = None,
        security_action_overrides: Optional[Dict[str, str]] = None,
        decay_half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
        decay_half_lives: Optional[Dict[str, float]] = None,
        decay_similarity_weight: float = 0.7,
        decay_freshness_weight: float = 0.3,
        dual_embedding: bool = False,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        importance_threshold: float = 0.05,
        decay_config: Optional[Dict[Tuple[str, str], float]] = None,
    ) -> None:
        self.project = project
        self._half_life_days = decay_half_life_days
        self._half_lives: Dict[str, float] = {**DECAY_HALF_LIVES}
        if decay_half_lives:
            self._half_lives.update(decay_half_lives)
        self._importance_threshold = importance_threshold
        self._decay_config = decay_config
        self._last_cleanup: float = 0.0
        self._last_cleanup_count: int = 0

        # Deprecation warnings for removed additive weights
        if decay_similarity_weight != 0.7 or decay_freshness_weight != 0.3:
            import warnings
            warnings.warn(
                "decay_similarity_weight and decay_freshness_weight are deprecated "
                "and ignored. Scoring now uses multiplicative model: "
                "score = cosine_similarity * time_adjusted_importance. "
                "Remove these parameters. They will be deleted in v0.7.0.",
                DeprecationWarning,
                stacklevel=2,
            )

        # Redaction pipeline
        self._redact_enabled = redact
        if redact:
            self._redactor = RedactionPipeline(
                custom_patterns=redact_patterns,
                security_scan_levels=security_scan_levels,
                security_action_overrides=security_action_overrides,  # type: ignore[arg-type]
            )
        else:
            self._redactor = None

        if isinstance(store, str) and store != "remote":
            raise ValueError(f"store must be a Store instance or 'remote', got {store!r}")
        if isinstance(store, str) and store == "remote":
            from lore.store.http import HttpStore
            self._store: Store = HttpStore(api_url=api_url, api_key=api_key)
        elif isinstance(store, Store):
            self._store: Store = store
        else:
            if db_path is None:
                db_path = os.path.join(
                    os.path.expanduser("~"), ".lore", "default.db"
                )
            self._store = SqliteStore(db_path)

        # Resolve embedder: explicit embedder > embedding_fn > default local
        self._dual_embedding = dual_embedding
        if embedder is not None:
            self._embedder = embedder
        elif embedding_fn is not None:
            self._embedder = _FnEmbedder(embedding_fn)
        elif dual_embedding:
            prose = LocalEmbedder()
            code = make_code_embedder(fallback=prose)
            self._embedder = EmbeddingRouter(prose_embedder=prose, code_embedder=code)
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
        if not type or not isinstance(type, str) or not type.strip():
            raise ValueError("type must be a non-empty string")
        if type not in VALID_MEMORY_TYPES:
            raise ValueError(
                f"invalid memory type {type!r}, "
                f"must be one of: {', '.join(sorted(VALID_MEMORY_TYPES))}"
            )
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {confidence}"
            )

        # Security scan and redact before storage
        if self._redactor is not None:
            scan = self._redactor.scan(content)
            if scan.action == "block":
                raise SecretBlockedError(scan.blocked_types[0])
            content = scan.masked_text()
            if context:
                ctx_scan = self._redactor.scan(context)
                if ctx_scan.action == "block":
                    raise SecretBlockedError(ctx_scan.blocked_types[0])
                context = ctx_scan.masked_text()

        # Compute embedding
        embed_text = f"{content} {context}" if context else content
        embedding_vec = self._embedder.embed(embed_text)
        embedding_bytes = _serialize_embedding(embedding_vec)

        # Track which embedding model was used
        if isinstance(self._embedder, EmbeddingRouter):
            meta = dict(metadata) if metadata else {}
            meta["embed_model"] = self._embedder.last_embed_model
            metadata = meta

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
        check_freshness: bool = False,
        repo_path: Optional[str] = None,
    ) -> List[RecallResult]:
        """Semantic search for memories.

        Returns a list of RecallResult ordered by descending score.
        Triggers lazy cleanup of expired memories.

        Args:
            check_freshness: If True, attach staleness info to each result.
                Requires repo_path.
            repo_path: Path to git repo for freshness checks.
        """
        self._maybe_cleanup_expired()

        # Dual embedding: embed query with both models
        query_vecs: Optional[Dict[str, List[float]]] = None
        if isinstance(self._embedder, EmbeddingRouter):
            query_vecs = self._embedder.embed_query_dual(query)
            # Use prose vec as the default for _recall_local's main path
            query_vec = query_vecs["prose"]
        else:
            query_vec = self._embedder.embed(query)

        # Remote store: delegate search to server
        if hasattr(self._store, 'search'):
            results = self._store.search(
                embedding=query_vec,
                tags=tags,
                project=self.project,
                limit=limit,
                min_confidence=min_confidence,
            )
        else:
            results = self._recall_local(
                query_vec, tags=tags, type=type, limit=limit,
                min_confidence=min_confidence,
                query_vecs=query_vecs,
            )

        if check_freshness and repo_path:
            from lore.freshness.detector import FreshnessDetector

            detector = FreshnessDetector(repo_path)
            for r in results:
                r.staleness = detector.check(r.memory)

        return results

    def _recall_local(
        self,
        query_vec: List[float],
        *,
        tags: Optional[List[str]] = None,
        type: Optional[str] = None,
        limit: int = 5,
        min_confidence: float = 0.0,
        query_vecs: Optional[Dict[str, List[float]]] = None,
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

        # Pre-compute normalised query vectors
        query_arr = np.array(query_vec, dtype=np.float32)
        query_norm = query_arr / max(float(np.linalg.norm(query_arr)), 1e-9)

        code_query_norm: Optional[np.ndarray] = None
        if query_vecs and "code" in query_vecs:
            code_arr = np.array(query_vecs["code"], dtype=np.float32)
            code_query_norm = code_arr / max(float(np.linalg.norm(code_arr)), 1e-9)

        # Vectorized cosine similarity
        embeddings = np.array(
            [_deserialize_embedding(m.embedding) for m in candidates],  # type: ignore[arg-type]
            dtype=np.float32,
        )
        emb_norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        emb_norms = np.clip(emb_norms, 1e-9, None)
        embeddings_normed = embeddings / emb_norms

        cosine_prose = embeddings_normed @ query_norm
        cosine_code: Optional[np.ndarray] = None
        if code_query_norm is not None:
            cosine_code = embeddings_normed @ code_query_norm

        # Multiplicative scoring: cosine_similarity * time_adjusted_importance
        results: List[RecallResult] = []
        for i, memory in enumerate(candidates):
            # Pick cosine score matching the model that embedded this memory
            if cosine_code is not None:
                embed_model = (memory.metadata or {}).get("embed_model", "prose")
                cosine_score = float(
                    cosine_code[i] if embed_model == "code" else cosine_prose[i]
                )
            else:
                cosine_score = float(cosine_prose[i])

            half_life = resolve_half_life(
                getattr(memory, "tier", None),
                memory.type,
                overrides=self._decay_config,
            )
            tai = time_adjusted_importance(memory, half_life, now=now)
            final_score = cosine_score * tai
            results.append(RecallResult(memory=memory, score=final_score))

        results.sort(key=lambda r: r.score, reverse=True)
        top_results = results[:limit]

        # Access tracking: update returned memories
        access_now = _utc_now_iso()
        for r in top_results:
            memory = r.memory
            memory.access_count += 1
            memory.last_accessed_at = access_now
            memory.importance_score = compute_importance(memory)
            self._store.update(memory)

        return top_results

    def as_prompt(
        self,
        query: str,
        *,
        format: str = "xml",
        max_tokens: Optional[int] = None,
        max_chars: Optional[int] = None,
        limit: int = 10,
        tags: Optional[List[str]] = None,
        type: Optional[str] = None,
        min_score: float = 0.0,
        include_metadata: bool = False,
        project: Optional[str] = None,
    ) -> str:
        """Export memories formatted for LLM context injection.

        Calls recall() internally, then formats results using PromptFormatter.
        Returns a formatted string ready for prompt injection, or "" if no matches.
        """
        from lore.prompt.formatter import PromptFormatter

        # Allow project override for the recall call
        orig_project = self.project
        if project is not None:
            self.project = project
        try:
            results = self.recall(query, tags=tags, type=type, limit=limit)
        finally:
            self.project = orig_project

        formatter = PromptFormatter()
        return formatter.format(
            query,
            results,
            format=format,
            max_tokens=max_tokens,
            max_chars=max_chars,
            min_score=min_score,
            include_metadata=include_metadata,
        )

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
        """Increment upvotes for a memory and recompute importance."""
        if hasattr(self._store, 'upvote'):
            self._store.upvote(memory_id)
            return
        memory = self._store.get(memory_id)
        if memory is None:
            raise MemoryNotFoundError(memory_id)
        memory.upvotes += 1
        memory.importance_score = compute_importance(memory)
        memory.updated_at = _utc_now_iso()
        self._store.update(memory)

    def downvote(self, memory_id: str) -> None:
        """Increment downvotes for a memory and recompute importance."""
        if hasattr(self._store, 'downvote'):
            self._store.downvote(memory_id)
            return
        memory = self._store.get(memory_id)
        if memory is None:
            raise MemoryNotFoundError(memory_id)
        memory.downvotes += 1
        memory.importance_score = compute_importance(memory)
        memory.updated_at = _utc_now_iso()
        self._store.update(memory)

    # ------------------------------------------------------------------
    # Reindexing
    # ------------------------------------------------------------------

    def reindex(
        self,
        *,
        dry_run: bool = False,
        progress_fn: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """Re-embed all memories using the current embedder.

        When the embedder is an :class:`EmbeddingRouter`, each memory is
        classified as code/prose and re-embedded with the matching model.
        The ``embed_model`` metadata field is updated accordingly.

        Returns the number of memories updated.
        """
        all_memories = self._store.list()
        total = len(all_memories)
        updated = 0

        for idx, memory in enumerate(all_memories):
            embed_text = (
                f"{memory.content} {memory.context}"
                if memory.context
                else memory.content
            )
            new_vec = self._embedder.embed(embed_text)
            new_bytes = _serialize_embedding(new_vec)

            # Determine embed_model tag
            new_model: Optional[str] = None
            if isinstance(self._embedder, EmbeddingRouter):
                new_model = self._embedder.last_embed_model

            old_model = (memory.metadata or {}).get("embed_model")
            embedding_changed = memory.embedding != new_bytes
            model_changed = new_model is not None and old_model != new_model

            if not embedding_changed and not model_changed:
                if progress_fn:
                    progress_fn(idx + 1, total)
                continue

            if not dry_run:
                memory.embedding = new_bytes
                if new_model is not None:
                    meta = dict(memory.metadata) if memory.metadata else {}
                    meta["embed_model"] = new_model
                    memory.metadata = meta
                memory.updated_at = _utc_now_iso()
                self._store.update(memory)

            updated += 1
            if progress_fn:
                progress_fn(idx + 1, total)

        return updated

    # ------------------------------------------------------------------
    # TTL Cleanup
    # ------------------------------------------------------------------

    def cleanup_expired(self, importance_threshold: Optional[float] = None) -> int:
        """Remove expired memories AND memories below importance threshold."""
        threshold = importance_threshold if importance_threshold is not None else self._importance_threshold
        now = datetime.now(timezone.utc)
        count = 0

        # Phase 1: TTL/expiry cleanup
        count += self._store.cleanup_expired()

        # Phase 2: Importance-based cleanup
        all_memories = self._store.list(limit=10000)
        to_delete = []
        for memory in all_memories:
            half_life = resolve_half_life(
                getattr(memory, "tier", None),
                memory.type,
                overrides=self._decay_config,
            )
            tai = time_adjusted_importance(memory, half_life, now=now)
            if tai < threshold:
                to_delete.append(memory.id)

        for memory_id in to_delete:
            self._store.delete(memory_id)
            count += 1

        return count

    def recalculate_importance(self, project: Optional[str] = None) -> int:
        """Recompute importance_score for all memories. Returns count updated."""
        memories = self._store.list(project=project, limit=100000)
        count = 0
        for memory in memories:
            new_score = compute_importance(memory)
            if memory.importance_score != new_score:
                memory.importance_score = new_score
                self._store.update(memory)
                count += 1
        return count

    def _maybe_cleanup_expired(self) -> None:
        """Run cleanup_expired at most once per 60 seconds."""
        now = time.monotonic()
        if now - self._last_cleanup >= _CLEANUP_INTERVAL_SECONDS:
            self._last_cleanup = now
            self._last_cleanup_count = self.cleanup_expired()



def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
