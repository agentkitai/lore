#!/usr/bin/env python3
"""Backfill knowledge graph (entities, entity_mentions, relationships) from enriched memories.

Reads meta->'enrichment'->'entities' from all enriched memories in Postgres
and creates proper Entity + EntityMention + co-occurrence Relationship rows.

Usage:
    python3 scripts/backfill_graph.py
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timezone

import asyncpg
from ulid import ULID

DB_DSN = "postgresql://lore:lore@localhost:5432/lore"

VALID_ENTITY_TYPES = {
    "person", "tool", "platform", "concept", "project",
    "organization", "language", "framework", "service", "other",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_name(raw: str) -> str:
    name = raw.strip().lower()
    name = " ".join(name.split())
    name = name.rstrip(".,;:!?")
    return name


async def main():
    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=3)

    # --- Stats before ---
    async with pool.acquire() as conn:
        mem_count = await conn.fetchval("SELECT count(*) FROM memories")
        ent_count = await conn.fetchval("SELECT count(*) FROM entities")
        rel_count = await conn.fetchval("SELECT count(*) FROM relationships")
        em_count = await conn.fetchval("SELECT count(*) FROM entity_mentions")
        print(f"Before: {mem_count} memories, {ent_count} entities, {em_count} mentions, {rel_count} relationships")

    # --- Fetch enriched memories ---
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, meta
            FROM memories
            WHERE meta->'enrichment'->'entities' IS NOT NULL
              AND jsonb_array_length(meta->'enrichment'->'entities') > 0
            ORDER BY created_at
        """)
    print(f"Found {len(rows)} memories with enrichment entities")

    # Entity cache: normalized_name -> entity_id
    entity_cache: dict[str, str] = {}
    # Pre-load existing entities
    async with pool.acquire() as conn:
        existing = await conn.fetch("SELECT id, name FROM entities")
        for e in existing:
            entity_cache[e["name"]] = e["id"]
    print(f"Pre-loaded {len(entity_cache)} existing entities")

    entities_created = 0
    mentions_created = 0
    relationships_created = 0
    skipped_memories = 0

    for i, row in enumerate(rows):
        memory_id = row["id"]
        meta = row["meta"] if isinstance(row["meta"], dict) else json.loads(row["meta"])
        enrichment = meta.get("enrichment", {})
        raw_entities = enrichment.get("entities", [])

        if not raw_entities:
            skipped_memories += 1
            continue

        # Resolve entities for this memory
        memory_entity_ids: list[str] = []

        async with pool.acquire() as conn:
            for raw in raw_entities:
                name = normalize_name(raw.get("name", ""))
                if not name:
                    continue
                entity_type = raw.get("type", "concept")
                if entity_type not in VALID_ENTITY_TYPES:
                    entity_type = "other"

                now = utc_now()

                if name in entity_cache:
                    entity_id = entity_cache[name]
                    # Update last_seen and mention_count
                    await conn.execute("""
                        UPDATE entities
                        SET mention_count = mention_count + 1,
                            last_seen_at = $1,
                            updated_at = $1,
                            entity_type = CASE
                                WHEN entity_type = 'concept' AND $2 != 'concept' THEN $2
                                ELSE entity_type
                            END
                        WHERE id = $3
                    """, now, entity_type, entity_id)
                else:
                    # Create new entity
                    entity_id = str(ULID())
                    await conn.execute("""
                        INSERT INTO entities (id, name, entity_type, aliases, mention_count,
                                              first_seen_at, last_seen_at, created_at, updated_at)
                        VALUES ($1, $2, $3, '[]'::jsonb, 1, $4, $4, $4, $4)
                    """, entity_id, name, entity_type, now)
                    entity_cache[name] = entity_id
                    entities_created += 1

                # Create entity mention (skip if already exists)
                mention_id = str(ULID())
                try:
                    await conn.execute("""
                        INSERT INTO entity_mentions (id, entity_id, memory_id, mention_type, confidence, created_at)
                        VALUES ($1, $2, $3, 'explicit', 1.0, $4)
                        ON CONFLICT (entity_id, memory_id) DO NOTHING
                    """, mention_id, entity_id, memory_id, now)
                    mentions_created += 1
                except asyncpg.ForeignKeyViolationError:
                    # Memory might have been deleted
                    continue

                memory_entity_ids.append(entity_id)

            # Create co-occurrence relationships between entities in this memory
            if len(memory_entity_ids) >= 2:
                now = utc_now()
                for j, eid1 in enumerate(memory_entity_ids):
                    for eid2 in memory_entity_ids[j + 1:]:
                        for source_id, target_id in [(eid1, eid2), (eid2, eid1)]:
                            rel_id = str(ULID())
                            try:
                                await conn.execute("""
                                    INSERT INTO relationships
                                        (id, source_entity_id, target_entity_id, rel_type,
                                         weight, source_memory_id, valid_from, created_at, updated_at)
                                    VALUES ($1, $2, $3, 'co_occurs_with', 0.3, $4, $5, $5, $5)
                                    ON CONFLICT (source_entity_id, target_entity_id, rel_type)
                                        WHERE valid_until IS NULL
                                    DO UPDATE SET
                                        weight = LEAST(1.0, relationships.weight + 0.05),
                                        updated_at = $5
                                """, rel_id, source_id, target_id, memory_id, now)
                                relationships_created += 1
                            except asyncpg.ForeignKeyViolationError:
                                continue

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i + 1}/{len(rows)} memories processed "
                  f"({entities_created} entities, {mentions_created} mentions, {relationships_created} rels)")

    print(f"\nDone! Processed {len(rows)} memories ({skipped_memories} skipped)")
    print(f"  Entities created: {entities_created}")
    print(f"  Mentions created: {mentions_created}")
    print(f"  Relationships created: {relationships_created}")

    # --- Stats after ---
    async with pool.acquire() as conn:
        ent_count = await conn.fetchval("SELECT count(*) FROM entities")
        rel_count = await conn.fetchval("SELECT count(*) FROM relationships")
        em_count = await conn.fetchval("SELECT count(*) FROM entity_mentions")
        print(f"\nAfter: {ent_count} entities, {em_count} mentions, {rel_count} relationships")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
