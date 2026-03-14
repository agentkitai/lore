"""Obsidian-compatible Markdown export renderer.

Generates a directory structure with YAML-frontmatter memory files,
entity files with backlinks and wikilinks, and a relationship table.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from lore.export.serializers import (
    memory_to_filename,
    slugify,
)
from lore.store.base import Store
from lore.types import (
    Entity,
    EntityMention,
    ExportFilter,
    ExportResult,
    Fact,
    Memory,
)


def _yaml_frontmatter(fields: Dict[str, Any]) -> str:
    """Render a dict as YAML frontmatter (simple, no dependencies)."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        elif isinstance(value, list):
            lines.append(f"{key}: [{', '.join(repr(v) for v in value)}]")
        else:
            # Quote strings that contain special YAML chars
            sv = str(value)
            if any(c in sv for c in ":#{}[]|>!&*?,") or sv.startswith(("'", '"')):
                lines.append(f'{key}: "{sv}"')
            else:
                lines.append(f"{key}: {sv}")
    lines.append("---")
    return "\n".join(lines)


class MarkdownRenderer:
    """Renders Lore data as Obsidian-compatible Markdown files."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def render(
        self,
        output_dir: str,
        filters: Optional[ExportFilter] = None,
        include_embeddings: bool = False,
    ) -> ExportResult:
        start = time.monotonic()
        filters = filters or ExportFilter()
        out = Path(output_dir)

        # ── Fetch data ──
        memories = self._store.list(
            project=filters.project,
            type=filters.type,
            tier=filters.tier,
            include_archived=True,
            since=filters.since,
        )
        memories.sort(key=lambda m: m.created_at)
        memory_ids = [m.id for m in memories]

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

        # Scope graph data
        if is_filtered:
            memory_id_set: Set[str] = set(memory_ids)
            all_mentions = [m for m in all_mentions if m.memory_id in memory_id_set]
            mentioned_entity_ids = {m.entity_id for m in all_mentions}
            entities = [e for e in entities if e.id in mentioned_entity_ids]
            entity_id_set = {e.id for e in entities}
            all_relationships = [
                r for r in all_relationships
                if r.source_entity_id in entity_id_set and r.target_entity_id in entity_id_set
            ]

        # Build lookup maps
        entity_map: Dict[str, Entity] = {e.id: e for e in entities}
        facts_by_memory: Dict[str, List[Fact]] = {}
        for f in facts:
            facts_by_memory.setdefault(f.memory_id, []).append(f)
        mentions_by_memory: Dict[str, List[EntityMention]] = {}
        for m in all_mentions:
            mentions_by_memory.setdefault(m.memory_id, []).append(m)
        mentions_by_entity: Dict[str, List[EntityMention]] = {}
        for m in all_mentions:
            mentions_by_entity.setdefault(m.entity_id, []).append(m)
        memory_map: Dict[str, Memory] = {m.id: m for m in memories}

        # ── Write memory files ──
        for mem in memories:
            type_dir = out / "memories" / mem.type
            type_dir.mkdir(parents=True, exist_ok=True)

            fm = {
                "id": mem.id,
                "type": mem.type,
                "tier": mem.tier,
                "project": mem.project,
                "tags": mem.tags or [],
                "confidence": mem.confidence,
                "importance_score": mem.importance_score,
                "upvotes": mem.upvotes,
                "downvotes": mem.downvotes,
                "created_at": mem.created_at,
                "updated_at": mem.updated_at,
                "source": mem.source,
            }
            content_parts = [_yaml_frontmatter(fm), "", mem.content]

            # Facts section
            mem_facts = facts_by_memory.get(mem.id, [])
            if mem_facts:
                content_parts.append("")
                content_parts.append("## Facts")
                content_parts.append("| Subject | Predicate | Object |")
                content_parts.append("|---------|-----------|--------|")
                for f in mem_facts:
                    content_parts.append(f"| {f.subject} | {f.predicate} | {f.object} |")

            # Entities section with wikilinks
            mem_mentions = mentions_by_memory.get(mem.id, [])
            if mem_mentions:
                content_parts.append("")
                content_parts.append("## Entities")
                for mention in mem_mentions:
                    ent = entity_map.get(mention.entity_id)
                    if ent:
                        content_parts.append(f"- [[{ent.name}]]")

            content_parts.append("")  # trailing newline
            filename = memory_to_filename(mem)
            (type_dir / filename).write_text("\n".join(content_parts), encoding="utf-8")

        # ── Write entity files ──
        entities_dir = out / "entities"
        entities_dir.mkdir(parents=True, exist_ok=True)

        for entity in entities:
            fm = {
                "id": entity.id,
                "entity_type": entity.entity_type,
                "aliases": entity.aliases or [],
                "mention_count": entity.mention_count,
                "first_seen_at": entity.first_seen_at,
            }
            parts = [_yaml_frontmatter(fm), "", f"# {entity.name}"]

            # Mentioned In section
            ent_mentions = mentions_by_entity.get(entity.id, [])
            if ent_mentions:
                parts.append("")
                parts.append("## Mentioned In")
                for mention in ent_mentions:
                    mem = memory_map.get(mention.memory_id)
                    if mem:
                        slug = memory_to_filename(mem).replace(".md", "")
                        preview = mem.content[:60].replace("\n", " ")
                        parts.append(f"- [[{slug}]] — {preview}")

            # Relationships table
            ent_rels = [
                r for r in all_relationships
                if r.source_entity_id == entity.id or r.target_entity_id == entity.id
            ]
            if ent_rels:
                parts.append("")
                parts.append("## Relationships")
                parts.append("| Direction | Type | Entity |")
                parts.append("|-----------|------|--------|")
                for rel in ent_rels:
                    if rel.source_entity_id == entity.id:
                        target = entity_map.get(rel.target_entity_id)
                        if target:
                            parts.append(f"| → | {rel.rel_type} | [[{target.name}]] |")
                    else:
                        source = entity_map.get(rel.source_entity_id)
                        if source:
                            parts.append(f"| ← | {rel.rel_type} | [[{source.name}]] |")

            parts.append("")
            safe_name = slugify(entity.name, max_length=100)
            (entities_dir / f"{safe_name}.md").write_text(
                "\n".join(parts), encoding="utf-8",
            )

        # ── Write graph/relationships.md ──
        graph_dir = out / "graph"
        graph_dir.mkdir(parents=True, exist_ok=True)
        rel_parts = ["# Relationships", ""]
        if all_relationships:
            rel_parts.append("| Source | Type | Target | Weight |")
            rel_parts.append("|--------|------|--------|--------|")
            for rel in all_relationships:
                src = entity_map.get(rel.source_entity_id)
                tgt = entity_map.get(rel.target_entity_id)
                src_name = f"[[{src.name}]]" if src else rel.source_entity_id
                tgt_name = f"[[{tgt.name}]]" if tgt else rel.target_entity_id
                rel_parts.append(
                    f"| {src_name} | {rel.rel_type} | {tgt_name} | {rel.weight:.2f} |"
                )
        else:
            rel_parts.append("No relationships exported.")
        rel_parts.append("")
        (graph_dir / "relationships.md").write_text("\n".join(rel_parts), encoding="utf-8")

        # ── Write _export_meta.md ──
        meta_parts = [
            "# Export Metadata",
            "",
            f"- **Exported at:** {datetime.now(timezone.utc).isoformat()}",
            "- **Lore version:** 0.9.5",
            f"- **Memories:** {len(memories)}",
            f"- **Entities:** {len(entities)}",
            f"- **Relationships:** {len(all_relationships)}",
            f"- **Facts:** {len(facts)}",
        ]
        if filters.project:
            meta_parts.append(f"- **Filter — project:** {filters.project}")
        if filters.type:
            meta_parts.append(f"- **Filter — type:** {filters.type}")
        if filters.tier:
            meta_parts.append(f"- **Filter — tier:** {filters.tier}")
        if filters.since:
            meta_parts.append(f"- **Filter — since:** {filters.since}")
        meta_parts.append("")
        (out / "_export_meta.md").write_text("\n".join(meta_parts), encoding="utf-8")

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ExportResult(
            path=str(out),
            format="markdown",
            memories=len(memories),
            entities=len(entities),
            relationships=len(all_relationships),
            entity_mentions=len(all_mentions),
            facts=len(facts),
            duration_ms=elapsed_ms,
        )
