"""JSON import engine with deduplication, hash verification, and embedding regeneration."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from lore.export.schema import validate_schema_version, verify_content_hash
from lore.export.serializers import (
    dict_to_conflict,
    dict_to_consolidation_log,
    dict_to_entity,
    dict_to_entity_mention,
    dict_to_fact,
    dict_to_memory,
    dict_to_relationship,
)
from lore.store.base import Store
from lore.types import ImportResult

logger = logging.getLogger(__name__)


class Importer:
    """JSON import engine with dedup, hash verification, and re-embedding."""

    def __init__(
        self,
        store: Store,
        embedder: Any = None,
        redaction_pipeline: Any = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._redaction = redaction_pipeline

    def import_file(
        self,
        file_path: str,
        overwrite: bool = False,
        project_override: Optional[str] = None,
        dry_run: bool = False,
    ) -> ImportResult:
        start = time.monotonic()
        result = ImportResult()

        # ── Read and parse JSON ──
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Import file not found: {file_path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                export_data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in import file: {e}") from e

        # ── Validate schema version ──
        schema_version = export_data.get("schema_version", 1)
        validate_schema_version(schema_version)

        # ── Verify content hash ──
        verify_content_hash(export_data)

        data = export_data.get("data", {})

        # ── Import memories ──
        memories_data = data.get("memories", [])
        result.total = len(memories_data)
        imported_memory_ids: List[str] = []
        needs_embedding: List[str] = []

        for md in memories_data:
            if not md.get("id") or not md.get("content"):
                result.errors += 1
                result.warnings.append(
                    f"Skipped record missing id or content: {md.get('id', '<no id>')}"
                )
                continue

            if project_override:
                md["project"] = project_override

            memory = dict_to_memory(md)

            if dry_run:
                existing = self._store.get(memory.id)
                if existing:
                    if overwrite:
                        result.overwritten += 1
                    else:
                        result.skipped += 1
                else:
                    result.imported += 1
                continue

            existing = self._store.get(memory.id)
            if existing:
                if overwrite:
                    self._store.save(memory)
                    result.overwritten += 1
                    imported_memory_ids.append(memory.id)
                    if memory.embedding is None:
                        needs_embedding.append(memory.id)
                else:
                    result.skipped += 1
                    continue
            else:
                self._store.save(memory)
                result.imported += 1
                imported_memory_ids.append(memory.id)
                if memory.embedding is None:
                    needs_embedding.append(memory.id)

        if dry_run:
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result

        # ── Import entities ──
        entity_ids_in_store = set()
        for ed in data.get("entities", []):
            try:
                entity = dict_to_entity(ed)
                existing = self._store.get_entity(entity.id)
                if existing and not overwrite:
                    entity_ids_in_store.add(entity.id)
                    continue
                self._store.save_entity(entity)
                entity_ids_in_store.add(entity.id)
            except Exception as e:
                result.warnings.append(f"Entity import failed ({ed.get('id', '?')}): {e}")

        # ── Import facts ──
        for fd in data.get("facts", []):
            try:
                fact = dict_to_fact(fd)
                self._store.save_fact(fact)
            except Exception as e:
                result.warnings.append(f"Fact import failed ({fd.get('id', '?')}): {e}")

        # ── Import relationships ──
        for rd in data.get("relationships", []):
            try:
                rel = dict_to_relationship(rd)
                # Check both entity ends exist
                if rel.source_entity_id not in entity_ids_in_store:
                    result.warnings.append(
                        f"Orphaned relationship {rel.id}: source entity "
                        f"{rel.source_entity_id} not found, skipping"
                    )
                    continue
                if rel.target_entity_id not in entity_ids_in_store:
                    result.warnings.append(
                        f"Orphaned relationship {rel.id}: target entity "
                        f"{rel.target_entity_id} not found, skipping"
                    )
                    continue
                self._store.save_relationship(rel)
            except Exception as e:
                result.warnings.append(f"Relationship import failed ({rd.get('id', '?')}): {e}")

        # ── Import entity mentions ──
        memory_id_set = set(imported_memory_ids) | {
            md["id"] for md in memories_data if md.get("id")
        }
        for emd in data.get("entity_mentions", []):
            try:
                mention = dict_to_entity_mention(emd)
                if mention.entity_id not in entity_ids_in_store:
                    result.warnings.append(
                        f"Orphaned mention {mention.id}: entity "
                        f"{mention.entity_id} not found, skipping"
                    )
                    continue
                if mention.memory_id not in memory_id_set:
                    result.warnings.append(
                        f"Orphaned mention {mention.id}: memory "
                        f"{mention.memory_id} not found, skipping"
                    )
                    continue
                self._store.save_entity_mention(mention)
            except Exception as e:
                result.warnings.append(f"Mention import failed ({emd.get('id', '?')}): {e}")

        # ── Import conflicts ──
        for cd in data.get("conflicts", []):
            try:
                conflict = dict_to_conflict(cd)
                self._store.save_conflict(conflict)
            except Exception as e:
                result.warnings.append(f"Conflict import failed ({cd.get('id', '?')}): {e}")

        # ── Import consolidation logs ──
        for cld in data.get("consolidation_logs", []):
            try:
                log_entry = dict_to_consolidation_log(cld)
                self._store.save_consolidation_log(log_entry)
            except Exception as e:
                result.warnings.append(f"Consolidation log import failed ({cld.get('id', '?')}): {e}")

        # ── Re-embed memories without embeddings ──
        if self._embedder and needs_embedding:
            for mid in needs_embedding:
                try:
                    mem = self._store.get(mid)
                    if mem and mem.embedding is None:
                        embed_text = mem.content
                        if mem.context:
                            embed_text = f"{mem.content}\n{mem.context}"
                        vec = self._embedder.embed(embed_text)
                        import struct
                        mem.embedding = struct.pack(f"{len(vec)}f", *vec)
                        self._store.update(mem)
                        result.embeddings_regenerated += 1
                except Exception as e:
                    result.warnings.append(f"Embedding failed for {mid}: {e}")

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result
