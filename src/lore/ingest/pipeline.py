"""Ingestion pipeline — orchestrates normalize -> dedup -> remember."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lore.ingest.adapters.base import NormalizedMessage, SourceAdapter
from lore.ingest.dedup import DedupResult, Deduplicator

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    status: str
    memory_id: Optional[str] = None
    duplicate_of: Optional[str] = None
    similarity: float = 0.0
    dedup_strategy: str = ""
    enriched: bool = False
    tracking_id: Optional[str] = None
    error: Optional[str] = None


class IngestionPipeline:
    """Full ingestion pipeline from raw payload to stored memory."""

    def __init__(
        self,
        lore: object,
        deduplicator: Deduplicator,
        default_dedup_mode: str = "reject",
        auto_enrich: bool = True,
    ):
        self.lore = lore
        self.deduplicator = deduplicator
        self.default_dedup_mode = default_dedup_mode
        self.auto_enrich = auto_enrich

    def ingest(
        self,
        adapter: SourceAdapter,
        payload: dict,
        *,
        project: Optional[str] = None,
        dedup_mode: Optional[str] = None,
        enrich: Optional[bool] = None,
        extra_tags: Optional[List[str]] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> IngestResult:
        """Run full ingestion pipeline for a single item."""
        mode = dedup_mode or self.default_dedup_mode
        should_enrich = enrich if enrich is not None else self.auto_enrich

        # Stage 1: Normalize
        normalized = adapter.normalize(payload)

        # Stage 2: Validate
        if not normalized.content or not normalized.content.strip():
            return IngestResult(status="failed", error="Content is empty after normalization")

        # Stage 3: Dedup
        if mode != "allow":
            dedup = self.deduplicator.check(normalized, adapter.adapter_name, project)
            if dedup.is_duplicate:
                if mode == "reject":
                    return IngestResult(
                        status="duplicate_rejected",
                        duplicate_of=dedup.duplicate_of,
                        similarity=dedup.similarity,
                        dedup_strategy=dedup.strategy,
                    )
                elif mode == "skip":
                    return IngestResult(
                        status="duplicate_skipped",
                        duplicate_of=dedup.duplicate_of,
                        similarity=dedup.similarity,
                        dedup_strategy=dedup.strategy,
                    )
                elif mode == "merge":
                    self._merge_source_info(dedup.duplicate_of, normalized, adapter.adapter_name)
                    return IngestResult(
                        status="duplicate_merged",
                        duplicate_of=dedup.duplicate_of,
                        similarity=dedup.similarity,
                        dedup_strategy=dedup.strategy,
                    )

        # Stage 4: Build source_info metadata
        source_info = self._build_source_info(normalized, adapter.adapter_name)
        metadata = dict(extra_metadata) if extra_metadata else {}
        metadata["source_info"] = source_info

        # Stage 5: Delegate to lore.remember()
        tags = list(normalized.tags or [])
        if extra_tags:
            tags.extend(extra_tags)

        try:
            memory_id = self.lore.remember(
                content=normalized.content,
                type=normalized.memory_type,
                tier="long",
                tags=tags,
                metadata=metadata,
                source=adapter.adapter_name,
                project=project,
            )
        except Exception as e:
            logger.error("Ingestion storage failed: %s", e, exc_info=True)
            return IngestResult(status="failed", error=str(e))

        return IngestResult(
            status="ingested",
            memory_id=memory_id,
            enriched=should_enrich and getattr(self.lore, '_enrichment_pipeline', None) is not None,
        )

    def ingest_batch(
        self,
        items: List[dict],
        adapter: SourceAdapter,
        *,
        project: Optional[str] = None,
        dedup_mode: Optional[str] = None,
        enrich: Optional[bool] = None,
    ) -> List[IngestResult]:
        """Ingest a batch of items. Returns per-item results."""
        results = []
        for item in items:
            result = self.ingest(
                adapter=adapter,
                payload=item,
                project=project,
                dedup_mode=dedup_mode,
                enrich=enrich,
            )
            results.append(result)
        return results

    def _build_source_info(self, normalized: NormalizedMessage, adapter_name: str) -> dict:
        return {
            "adapter": adapter_name,
            "channel": normalized.channel,
            "user": normalized.user,
            "original_timestamp": normalized.timestamp,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "source_message_id": normalized.source_message_id,
            "raw_format": normalized.raw_format,
        }

    def _merge_source_info(
        self, memory_id: str, normalized: NormalizedMessage, adapter_name: str
    ) -> None:
        """Append source_info to existing memory for multi-source attribution."""
        memory = self.lore._store.get(memory_id)
        if not memory:
            return
        meta = dict(memory.metadata) if memory.metadata else {}
        existing_sources = meta.get("additional_sources", [])
        existing_sources.append(self._build_source_info(normalized, adapter_name))
        meta["additional_sources"] = existing_sources
        memory.metadata = meta
        self.lore._store.update(memory)
