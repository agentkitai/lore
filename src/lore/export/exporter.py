"""Core JSON export engine.

Fetches all data from the store, applies filters, sorts deterministically,
serializes via serializers, computes content hash, and writes JSON to file.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

from lore.export.schema import EXPORT_SCHEMA_VERSION, compute_content_hash
from lore.export.serializers import (
    conflict_to_dict,
    consolidation_log_to_dict,
    entity_mention_to_dict,
    entity_to_dict,
    fact_to_dict,
    memory_to_dict,
    relationship_to_dict,
)
from lore.store.base import Store
from lore.types import ExportFilter, ExportResult

_LORE_VERSION = "0.9.5"


def _get_lore_version() -> str:
    """Best-effort version discovery."""
    try:
        from importlib.metadata import version
        return version("lore-sdk")
    except Exception:
        return _LORE_VERSION


class Exporter:
    """JSON (and orchestrator for Markdown) export engine."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def export(
        self,
        output: Optional[str] = None,
        filters: Optional[ExportFilter] = None,
        include_embeddings: bool = False,
        pretty: bool = False,
    ) -> ExportResult:
        """Export all data to a JSON file.

        Returns an ExportResult with path, counts, hash, and duration.
        """
        start = time.monotonic()
        filters = filters or ExportFilter()

        # ── Fetch memories (include archived) ──
        memories = self._store.list(
            project=filters.project,
            type=filters.type,
            tier=filters.tier,
            include_archived=True,
            since=filters.since,
        )
        # Sort deterministically by created_at ascending
        memories.sort(key=lambda m: m.created_at)
        memory_ids = [m.id for m in memories]

        # ── Fetch graph data (tolerate missing tables) ──
        is_filtered = bool(filters.project or filters.type or filters.tier or filters.since)
        mid_filter = memory_ids if is_filtered else None

        try:
            entities = self._store.list_entities(limit=100000)
        except Exception:
            entities = []
        try:
            all_relationships = self._store.list_relationships(
                include_expired=True, limit=100000,
            )
        except Exception:
            all_relationships = []
        try:
            all_mentions = self._store.list_all_entity_mentions(memory_ids=mid_filter)
        except Exception:
            all_mentions = []
        try:
            facts = self._store.list_all_facts(memory_ids=mid_filter)
        except Exception:
            facts = []
        try:
            conflicts = self._store.list_all_conflicts()
        except Exception:
            conflicts = []
        try:
            consolidation_logs = self._store.list_all_consolidation_logs()
        except Exception:
            consolidation_logs = []

        # ── Scope graph data to exported memories when filtering ──
        if is_filtered:
            memory_id_set: Set[str] = set(memory_ids)
            # Keep only mentions referencing exported memories
            all_mentions = [m for m in all_mentions if m.memory_id in memory_id_set]
            # Keep only entities that have mentions in exported memories
            mentioned_entity_ids = {m.entity_id for m in all_mentions}
            entities = [e for e in entities if e.id in mentioned_entity_ids]
            entity_id_set = {e.id for e in entities}
            # Keep only relationships where both ends exist
            all_relationships = [
                r for r in all_relationships
                if r.source_entity_id in entity_id_set and r.target_entity_id in entity_id_set
            ]

        # ── Sort deterministically ──
        entities.sort(key=lambda e: e.name.lower())
        all_relationships.sort(
            key=lambda r: (r.source_entity_id, r.target_entity_id, r.rel_type)
        )
        all_mentions.sort(key=lambda m: (m.entity_id, m.memory_id))
        facts.sort(key=lambda f: (f.memory_id, f.extracted_at))
        conflicts.sort(key=lambda c: c.resolved_at)
        consolidation_logs.sort(key=lambda c: c.created_at)

        # ── Serialize ──
        data: Dict[str, Any] = {
            "memories": [memory_to_dict(m, include_embedding=include_embeddings) for m in memories],
            "entities": [entity_to_dict(e) for e in entities],
            "relationships": [relationship_to_dict(r) for r in all_relationships],
            "entity_mentions": [entity_mention_to_dict(m) for m in all_mentions],
            "facts": [fact_to_dict(f) for f in facts],
            "conflicts": [conflict_to_dict(c) for c in conflicts],
            "consolidation_logs": [consolidation_log_to_dict(c) for c in consolidation_logs],
        }

        content_hash = compute_content_hash(data)

        # Build filter dict for envelope
        applied_filters: Dict[str, str] = {}
        if filters.project:
            applied_filters["project"] = filters.project
        if filters.type:
            applied_filters["type"] = filters.type
        if filters.tier:
            applied_filters["tier"] = filters.tier
        if filters.since:
            applied_filters["since"] = filters.since

        envelope: Dict[str, Any] = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "lore_version": _get_lore_version(),
            "content_hash": content_hash,
            "filters": applied_filters,
            "counts": {
                "memories": len(memories),
                "entities": len(entities),
                "relationships": len(all_relationships),
                "entity_mentions": len(all_mentions),
                "facts": len(facts),
                "conflicts": len(conflicts),
                "consolidation_logs": len(consolidation_logs),
            },
            "data": data,
        }

        # ── Write to file ──
        if output is None:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
            output = f"./lore-export-{ts}.json"

        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        indent = 2 if pretty else None
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(envelope, f, ensure_ascii=False, sort_keys=False, indent=indent)

        elapsed_ms = int((time.monotonic() - start) * 1000)

        return ExportResult(
            path=str(output_path),
            format="json",
            memories=len(memories),
            entities=len(entities),
            relationships=len(all_relationships),
            entity_mentions=len(all_mentions),
            facts=len(facts),
            conflicts=len(conflicts),
            consolidation_logs=len(consolidation_logs),
            content_hash=content_hash,
            duration_ms=elapsed_ms,
        )
