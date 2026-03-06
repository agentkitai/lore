"""Near-duplicate detection for ingested content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from lore.ingest.adapters.base import NormalizedMessage
    from lore.store.base import Store
    from lore.types import Memory


@dataclass
class DedupResult:
    is_duplicate: bool
    duplicate_of: Optional[str] = None
    similarity: float = 0.0
    strategy: str = ""  # "exact_id" | "content_similarity"


class Deduplicator:
    """Two-strategy deduplication: exact source ID match + content similarity."""

    def __init__(
        self,
        store: "Store",
        embedder: object,
        threshold: float = 0.95,
    ):
        self.store = store
        self.embedder = embedder
        self.threshold = threshold

    def check(
        self,
        normalized: "NormalizedMessage",
        adapter_name: str,
        project: Optional[str] = None,
    ) -> DedupResult:
        """Check if content is a near-duplicate of an existing memory.

        Strategy 1: Exact source message ID match.
        Strategy 2: Content embedding similarity.
        """
        # Strategy 1: Exact source message ID
        if normalized.source_message_id:
            existing = self._find_by_source_id(
                normalized.source_message_id, adapter_name, project
            )
            if existing:
                return DedupResult(
                    is_duplicate=True,
                    duplicate_of=existing.id,
                    similarity=1.0,
                    strategy="exact_id",
                )

        # Strategy 2: Content similarity
        if not normalized.content.strip():
            return DedupResult(is_duplicate=False)

        embedding = self.embedder.embed(normalized.content)
        similar = self.store.search(
            embedding=embedding,
            project=project,
            limit=5,
            min_confidence=0.0,
        )
        for result in similar:
            if result.score >= self.threshold:
                return DedupResult(
                    is_duplicate=True,
                    duplicate_of=result.memory.id,
                    similarity=result.score,
                    strategy="content_similarity",
                )

        return DedupResult(is_duplicate=False)

    def _find_by_source_id(
        self, source_message_id: str, adapter_name: str, project: Optional[str]
    ) -> Optional["Memory"]:
        """Search for existing memory with matching source_info.source_message_id."""
        candidates = self.store.list(project=project, limit=100)
        for mem in candidates:
            si = (mem.metadata or {}).get("source_info", {})
            if (
                si.get("source_message_id") == source_message_id
                and si.get("adapter") == adapter_name
            ):
                return mem
        return None
