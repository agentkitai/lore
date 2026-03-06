"""SQLite store implementation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from lore.store.base import Store
from lore.types import (
    ConflictEntry,
    ConsolidationLogEntry,
    Entity,
    EntityMention,
    Fact,
    Memory,
    Relationship,
)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    type        TEXT DEFAULT 'general',
    tier        TEXT DEFAULT 'long',
    context     TEXT,
    tags        TEXT,
    metadata    TEXT,
    source      TEXT,
    project     TEXT,
    embedding   BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    ttl         INTEGER,
    expires_at  TEXT,
    confidence  REAL DEFAULT 1.0,
    upvotes     INTEGER DEFAULT 0,
    downvotes   INTEGER DEFAULT 0,
    importance_score REAL DEFAULT 1.0,
    access_count INTEGER DEFAULT 0,
    last_accessed_at TEXT,
    archived INTEGER DEFAULT 0,
    consolidated_into TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_memories_project_tier ON memories(project, tier);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance_score);
CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories(last_accessed_at);
CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived);
"""

_FACT_SCHEMA = """\
CREATE TABLE IF NOT EXISTS facts (
    id              TEXT PRIMARY KEY,
    memory_id       TEXT NOT NULL,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    object          TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    extracted_at    TEXT NOT NULL,
    invalidated_by  TEXT,
    invalidated_at  TEXT,
    metadata        TEXT,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_facts_memory ON facts(memory_id);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate ON facts(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(id) WHERE invalidated_by IS NULL;

CREATE TABLE IF NOT EXISTS conflict_log (
    id              TEXT PRIMARY KEY,
    new_memory_id   TEXT NOT NULL,
    old_fact_id     TEXT NOT NULL,
    new_fact_id     TEXT,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    old_value       TEXT NOT NULL,
    new_value       TEXT NOT NULL,
    resolution      TEXT NOT NULL,
    resolved_at     TEXT NOT NULL,
    metadata        TEXT
);
CREATE INDEX IF NOT EXISTS idx_conflict_log_memory ON conflict_log(new_memory_id);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolution ON conflict_log(resolution);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolved ON conflict_log(resolved_at);
"""

_MIGRATION_SQL = """\
CREATE TABLE memories AS SELECT
    id,
    (problem || '\n' || resolution) AS content,
    'lesson' AS type,
    context,
    tags,
    meta AS metadata,
    source,
    project,
    embedding,
    created_at,
    updated_at,
    NULL AS ttl,
    expires_at,
    confidence,
    upvotes,
    downvotes
FROM lessons;
DROP TABLE lessons;
"""


_GRAPH_SCHEMA = """\
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    aliases         TEXT DEFAULT '[]',
    description     TEXT,
    metadata        TEXT,
    mention_count   INTEGER DEFAULT 1,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_mention_count ON entities(mention_count DESC);

CREATE TABLE IF NOT EXISTS relationships (
    id                  TEXT PRIMARY KEY,
    source_entity_id    TEXT NOT NULL,
    target_entity_id    TEXT NOT NULL,
    rel_type            TEXT NOT NULL,
    weight              REAL DEFAULT 1.0,
    properties          TEXT,
    source_fact_id      TEXT,
    source_memory_id    TEXT,
    valid_from          TEXT NOT NULL,
    valid_until         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (source_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (target_entity_id) REFERENCES entities(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_active ON relationships(source_entity_id) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(rel_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_unique_edge ON relationships(source_entity_id, target_entity_id, rel_type) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_rel_temporal ON relationships(valid_from, valid_until);

CREATE TABLE IF NOT EXISTS entity_mentions (
    id              TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL,
    memory_id       TEXT NOT NULL,
    mention_type    TEXT DEFAULT 'explicit',
    confidence      REAL DEFAULT 1.0,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_em_entity ON entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_em_memory ON entity_mentions(memory_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_em_unique ON entity_mentions(entity_id, memory_id);
"""


_CONSOLIDATION_LOG_SCHEMA = """\
CREATE TABLE IF NOT EXISTS consolidation_log (
    id                      TEXT PRIMARY KEY,
    consolidated_memory_id  TEXT NOT NULL,
    original_memory_ids     TEXT NOT NULL,
    strategy                TEXT NOT NULL,
    model_used              TEXT,
    original_count          INTEGER NOT NULL,
    created_at              TEXT NOT NULL,
    metadata                TEXT
);
CREATE INDEX IF NOT EXISTS idx_clog_memory
    ON consolidation_log(consolidated_memory_id);
CREATE INDEX IF NOT EXISTS idx_clog_created
    ON consolidation_log(created_at);
"""


class SqliteStore(Store):
    """SQLite-backed memory store."""

    def __init__(self, db_path: str, knowledge_graph: bool = False) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._maybe_migrate()
        # If table exists, add columns before schema (which creates indexes on them)
        if self._table_exists("memories"):
            self._maybe_add_context_column()
            self._maybe_add_tier_column()
            self._maybe_add_importance_columns()
            self._maybe_add_consolidation_columns()
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._maybe_create_fact_tables()
        self._maybe_create_consolidation_log_table()
        if knowledge_graph:
            self._maybe_create_graph_tables()

    def _maybe_migrate(self) -> None:
        """Auto-migrate lessons table to memories table if needed."""
        tables = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "lessons" in tables and "memories" not in tables:
            self._conn.executescript(_MIGRATION_SQL)
            self._conn.executescript(
                "CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);\n"
                "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);\n"
                "CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);"
            )

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def _maybe_add_context_column(self) -> None:
        """Add context column to existing memories table if missing."""
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "context" not in cols:
            self._conn.execute("ALTER TABLE memories ADD COLUMN context TEXT")
            self._conn.commit()

    def _maybe_add_tier_column(self) -> None:
        """Add tier column to existing memories table if missing."""
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "tier" not in cols:
            self._conn.execute(
                "ALTER TABLE memories ADD COLUMN tier TEXT DEFAULT 'long'"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_project_tier ON memories(project, tier)"
            )
            self._conn.commit()

    def _maybe_add_importance_columns(self) -> None:
        """Add importance scoring columns to existing memories table if missing."""
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        migrations = []
        if "importance_score" not in cols:
            migrations.append(
                "ALTER TABLE memories ADD COLUMN importance_score REAL DEFAULT 1.0"
            )
        if "access_count" not in cols:
            migrations.append(
                "ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0"
            )
        if "last_accessed_at" not in cols:
            migrations.append(
                "ALTER TABLE memories ADD COLUMN last_accessed_at TEXT"
            )
        for sql in migrations:
            self._conn.execute(sql)
        if migrations:
            self._conn.executescript(
                "CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance_score);\n"
                "CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories(last_accessed_at);"
            )
            self._conn.commit()

    def _maybe_add_consolidation_columns(self) -> None:
        """Add archived and consolidated_into columns if missing."""
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        migrations = []
        if "archived" not in cols:
            migrations.append(
                "ALTER TABLE memories ADD COLUMN archived INTEGER DEFAULT 0"
            )
        if "consolidated_into" not in cols:
            migrations.append(
                "ALTER TABLE memories ADD COLUMN consolidated_into TEXT"
            )
        for sql in migrations:
            self._conn.execute(sql)
        if migrations:
            self._conn.executescript(
                "CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived);"
            )
            self._conn.commit()

    def _maybe_create_consolidation_log_table(self) -> None:
        """Create consolidation_log table if it doesn't exist."""
        self._conn.executescript(_CONSOLIDATION_LOG_SCHEMA)

    def save(self, memory: Memory) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, content, type, tier, context, tags, metadata, source,
                project, embedding, created_at, updated_at,
                ttl, expires_at, confidence, upvotes, downvotes,
                importance_score, access_count, last_accessed_at,
                archived, consolidated_into)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory.id,
                memory.content,
                memory.type,
                memory.tier,
                memory.context,
                json.dumps(memory.tags),
                json.dumps(memory.metadata) if memory.metadata is not None else None,
                memory.source,
                memory.project,
                memory.embedding,
                memory.created_at,
                memory.updated_at,
                memory.ttl,
                memory.expires_at,
                memory.confidence,
                memory.upvotes,
                memory.downvotes,
                memory.importance_score,
                memory.access_count,
                memory.last_accessed_at,
                int(memory.archived),
                memory.consolidated_into,
            ),
        )
        self._conn.commit()

    def get(self, memory_id: str) -> Optional[Memory]:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_memory(row)

    def list(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
        limit: Optional[int] = None,
        include_archived: bool = False,
    ) -> List[Memory]:
        query = "SELECT * FROM memories"
        params: List[Any] = []
        conditions: List[str] = []
        if not include_archived:
            conditions.append("archived = 0")
        if project is not None:
            conditions.append("project = ?")
            params.append(project)
        if type is not None:
            conditions.append("type = ?")
            params.append(type)
        if tier is not None:
            conditions.append("tier = ?")
            params.append(tier)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def update(self, memory: Memory) -> bool:
        cursor = self._conn.execute(
            """UPDATE memories SET
               content=?, type=?, tier=?, context=?, tags=?, metadata=?, source=?,
               project=?, embedding=?, updated_at=?,
               ttl=?, expires_at=?, confidence=?, upvotes=?, downvotes=?,
               importance_score=?, access_count=?, last_accessed_at=?,
               archived=?, consolidated_into=?
               WHERE id=?""",
            (
                memory.content,
                memory.type,
                memory.tier,
                memory.context,
                json.dumps(memory.tags),
                json.dumps(memory.metadata) if memory.metadata is not None else None,
                memory.source,
                memory.project,
                memory.embedding,
                memory.updated_at,
                memory.ttl,
                memory.expires_at,
                memory.confidence,
                memory.upvotes,
                memory.downvotes,
                memory.importance_score,
                memory.access_count,
                memory.last_accessed_at,
                int(memory.archived),
                memory.consolidated_into,
                memory.id,
            ),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete(self, memory_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM memories WHERE id = ?", (memory_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def count(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> int:
        query = "SELECT COUNT(*) FROM memories"
        params: List[Any] = []
        conditions: List[str] = []
        if project is not None:
            conditions.append("project = ?")
            params.append(project)
        if type is not None:
            conditions.append("type = ?")
            params.append(type)
        if tier is not None:
            conditions.append("tier = ?")
            params.append(tier)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        row = self._conn.execute(query, params).fetchone()
        return row[0]

    def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        self._conn.commit()
        return cursor.rowcount

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        tags_raw = row["tags"]
        tags: List[str] = json.loads(tags_raw) if tags_raw else []
        metadata_raw = row["metadata"]
        metadata: Optional[Dict[str, Any]] = (
            json.loads(metadata_raw) if metadata_raw else None
        )
        keys = row.keys()
        return Memory(
            id=row["id"],
            content=row["content"],
            type=row["type"] or "general",
            tier=row["tier"] if "tier" in keys else "long",
            context=row["context"],
            tags=tags,
            metadata=metadata,
            source=row["source"],
            project=row["project"],
            embedding=row["embedding"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            ttl=row["ttl"],
            expires_at=row["expires_at"],
            confidence=row["confidence"],
            upvotes=row["upvotes"],
            downvotes=row["downvotes"],
            importance_score=row["importance_score"] if "importance_score" in keys else 1.0,
            access_count=row["access_count"] if "access_count" in keys else 0,
            last_accessed_at=row["last_accessed_at"] if "last_accessed_at" in keys else None,
            archived=bool(row["archived"]) if "archived" in keys else False,
            consolidated_into=row["consolidated_into"] if "consolidated_into" in keys else None,
        )

    # ------------------------------------------------------------------
    # Fact tables
    # ------------------------------------------------------------------

    def _maybe_create_fact_tables(self) -> None:
        """Create facts and conflict_log tables if they don't exist."""
        self._conn.executescript(_FACT_SCHEMA)

    # ------------------------------------------------------------------
    # Fact + conflict CRUD
    # ------------------------------------------------------------------

    def save_fact(self, fact: Fact) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO facts
               (id, memory_id, subject, predicate, object, confidence,
                extracted_at, invalidated_by, invalidated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fact.id,
                fact.memory_id,
                fact.subject,
                fact.predicate,
                fact.object,
                fact.confidence,
                fact.extracted_at,
                fact.invalidated_by,
                fact.invalidated_at,
                json.dumps(fact.metadata) if fact.metadata is not None else None,
            ),
        )
        self._conn.commit()

    def get_facts(self, memory_id: str) -> List[Fact]:
        rows = self._conn.execute(
            "SELECT * FROM facts WHERE memory_id = ? ORDER BY extracted_at",
            (memory_id,),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_active_facts(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        limit: int = 50,
    ) -> List[Fact]:
        query = "SELECT * FROM facts WHERE invalidated_by IS NULL"
        params: List[Any] = []
        if subject is not None:
            query += " AND subject = ?"
            params.append(subject.strip().lower())
        if predicate is not None:
            query += " AND predicate = ?"
            params.append(predicate.strip().lower())
        query += " ORDER BY extracted_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def invalidate_fact(self, fact_id: str, invalidated_by: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE facts SET invalidated_by = ?, invalidated_at = ?
               WHERE id = ? AND invalidated_by IS NULL""",
            (invalidated_by, now, fact_id),
        )
        self._conn.commit()

    def save_conflict(self, entry: ConflictEntry) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO conflict_log
               (id, new_memory_id, old_fact_id, new_fact_id, subject, predicate,
                old_value, new_value, resolution, resolved_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.new_memory_id,
                entry.old_fact_id,
                entry.new_fact_id,
                entry.subject,
                entry.predicate,
                entry.old_value,
                entry.new_value,
                entry.resolution,
                entry.resolved_at,
                json.dumps(entry.metadata) if entry.metadata is not None else None,
            ),
        )
        self._conn.commit()

    def list_conflicts(
        self,
        resolution: Optional[str] = None,
        limit: int = 20,
    ) -> List[ConflictEntry]:
        query = "SELECT * FROM conflict_log"
        params: List[Any] = []
        if resolution is not None:
            query += " WHERE resolution = ?"
            params.append(resolution)
        query += " ORDER BY resolved_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_conflict(r) for r in rows]

    @staticmethod
    def _row_to_fact(row: sqlite3.Row) -> Fact:
        metadata_raw = row["metadata"]
        metadata: Optional[Dict[str, Any]] = (
            json.loads(metadata_raw) if metadata_raw else None
        )
        return Fact(
            id=row["id"],
            memory_id=row["memory_id"],
            subject=row["subject"],
            predicate=row["predicate"],
            object=row["object"],
            confidence=row["confidence"],
            extracted_at=row["extracted_at"],
            invalidated_by=row["invalidated_by"],
            invalidated_at=row["invalidated_at"],
            metadata=metadata,
        )

    @staticmethod
    def _row_to_conflict(row: sqlite3.Row) -> ConflictEntry:
        metadata_raw = row["metadata"]
        metadata: Optional[Dict[str, Any]] = (
            json.loads(metadata_raw) if metadata_raw else None
        )
        return ConflictEntry(
            id=row["id"],
            new_memory_id=row["new_memory_id"],
            old_fact_id=row["old_fact_id"],
            new_fact_id=row["new_fact_id"],
            subject=row["subject"],
            predicate=row["predicate"],
            old_value=row["old_value"],
            new_value=row["new_value"],
            resolution=row["resolution"],
            resolved_at=row["resolved_at"],
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Graph tables
    # ------------------------------------------------------------------

    def _maybe_create_graph_tables(self) -> None:
        """Create graph tables if they don't exist."""
        self._conn.executescript(_GRAPH_SCHEMA)

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def save_entity(self, entity: Entity) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO entities
               (id, name, entity_type, aliases, description, metadata,
                mention_count, first_seen_at, last_seen_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entity.id, entity.name, entity.entity_type,
                json.dumps(entity.aliases),
                entity.description,
                json.dumps(entity.metadata) if entity.metadata else None,
                entity.mention_count, entity.first_seen_at, entity.last_seen_at,
                entity.created_at, entity.updated_at,
            ),
        )
        self._conn.commit()

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        row = self._conn.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        return self._row_to_entity(row) if row else None

    def get_entity_by_name(self, name: str) -> Optional[Entity]:
        row = self._conn.execute(
            "SELECT * FROM entities WHERE name = ?", (name,)
        ).fetchone()
        return self._row_to_entity(row) if row else None

    def get_entity_by_alias(self, alias: str) -> Optional[Entity]:
        row = self._conn.execute(
            """SELECT * FROM entities
               WHERE id IN (
                   SELECT e.id FROM entities e, json_each(e.aliases) AS a
                   WHERE a.value = ?
               )""",
            (alias,),
        ).fetchone()
        return self._row_to_entity(row) if row else None

    def update_entity(self, entity: Entity) -> None:
        self._conn.execute(
            """UPDATE entities SET
               name=?, entity_type=?, aliases=?, description=?, metadata=?,
               mention_count=?, first_seen_at=?, last_seen_at=?, updated_at=?
               WHERE id=?""",
            (
                entity.name, entity.entity_type,
                json.dumps(entity.aliases),
                entity.description,
                json.dumps(entity.metadata) if entity.metadata else None,
                entity.mention_count, entity.first_seen_at, entity.last_seen_at,
                entity.updated_at, entity.id,
            ),
        )
        self._conn.commit()

    def delete_entity(self, entity_id: str) -> None:
        self._conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        self._conn.commit()

    def list_entities(
        self,
        entity_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Entity]:
        query = "SELECT * FROM entities"
        params: List[Any] = []
        if entity_type:
            query += " WHERE entity_type = ?"
            params.append(entity_type)
        query += " ORDER BY mention_count DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_entity(r) for r in rows]

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> Entity:
        aliases_raw = row["aliases"]
        aliases = json.loads(aliases_raw) if aliases_raw else []
        metadata_raw = row["metadata"]
        metadata = json.loads(metadata_raw) if metadata_raw else None
        return Entity(
            id=row["id"],
            name=row["name"],
            entity_type=row["entity_type"],
            aliases=aliases,
            description=row["description"],
            metadata=metadata,
            mention_count=row["mention_count"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Relationship CRUD
    # ------------------------------------------------------------------

    def save_relationship(self, rel: Relationship) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO relationships
               (id, source_entity_id, target_entity_id, rel_type, weight,
                properties, source_fact_id, source_memory_id,
                valid_from, valid_until, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rel.id, rel.source_entity_id, rel.target_entity_id,
                rel.rel_type, rel.weight,
                json.dumps(rel.properties) if rel.properties else None,
                rel.source_fact_id, rel.source_memory_id,
                rel.valid_from, rel.valid_until,
                rel.created_at, rel.updated_at,
            ),
        )
        self._conn.commit()

    def get_relationship(self, rel_id: str) -> Optional[Relationship]:
        row = self._conn.execute(
            "SELECT * FROM relationships WHERE id = ?", (rel_id,)
        ).fetchone()
        return self._row_to_relationship(row) if row else None

    def get_active_relationship(
        self, source_id: str, target_id: str, rel_type: str
    ) -> Optional[Relationship]:
        row = self._conn.execute(
            """SELECT * FROM relationships
               WHERE source_entity_id = ? AND target_entity_id = ?
               AND rel_type = ? AND valid_until IS NULL""",
            (source_id, target_id, rel_type),
        ).fetchone()
        return self._row_to_relationship(row) if row else None

    def get_relationship_by_fact(self, fact_id: str) -> Optional[Relationship]:
        row = self._conn.execute(
            "SELECT * FROM relationships WHERE source_fact_id = ? AND valid_until IS NULL",
            (fact_id,),
        ).fetchone()
        return self._row_to_relationship(row) if row else None

    def update_relationship(self, rel: Relationship) -> None:
        self._conn.execute(
            """UPDATE relationships SET
               source_entity_id=?, target_entity_id=?, rel_type=?, weight=?,
               properties=?, source_fact_id=?, source_memory_id=?,
               valid_from=?, valid_until=?, updated_at=?
               WHERE id=?""",
            (
                rel.source_entity_id, rel.target_entity_id,
                rel.rel_type, rel.weight,
                json.dumps(rel.properties) if rel.properties else None,
                rel.source_fact_id, rel.source_memory_id,
                rel.valid_from, rel.valid_until,
                rel.updated_at, rel.id,
            ),
        )
        self._conn.commit()

    def delete_relationship(self, rel_id: str) -> None:
        self._conn.execute("DELETE FROM relationships WHERE id = ?", (rel_id,))
        self._conn.commit()

    def get_relationships_from(
        self, entity_ids: List[str], active_only: bool = True
    ) -> List[Relationship]:
        if not entity_ids:
            return []
        placeholders = ",".join("?" * len(entity_ids))
        query = f"SELECT * FROM relationships WHERE source_entity_id IN ({placeholders})"
        params: List[Any] = list(entity_ids)
        if active_only:
            query += " AND valid_until IS NULL"
        query += " ORDER BY weight DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_relationship(r) for r in rows]

    def get_relationships_to(
        self, entity_ids: List[str], active_only: bool = True
    ) -> List[Relationship]:
        if not entity_ids:
            return []
        placeholders = ",".join("?" * len(entity_ids))
        query = f"SELECT * FROM relationships WHERE target_entity_id IN ({placeholders})"
        params: List[Any] = list(entity_ids)
        if active_only:
            query += " AND valid_until IS NULL"
        query += " ORDER BY weight DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_relationship(r) for r in rows]

    def list_relationships(
        self,
        entity_id: Optional[str] = None,
        rel_type: Optional[str] = None,
        include_expired: bool = False,
        limit: int = 100,
    ) -> List[Relationship]:
        query = "SELECT * FROM relationships"
        params: List[Any] = []
        conditions: List[str] = []
        if entity_id:
            conditions.append("(source_entity_id = ? OR target_entity_id = ?)")
            params.extend([entity_id, entity_id])
        if rel_type:
            conditions.append("rel_type = ?")
            params.append(rel_type)
        if not include_expired:
            conditions.append("valid_until IS NULL")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY weight DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_relationship(r) for r in rows]

    def query_relationships(
        self,
        entity_ids: List[str],
        direction: str = "both",
        active_only: bool = True,
        at_time: Optional[str] = None,
        rel_types: Optional[List[str]] = None,
    ) -> List[Relationship]:
        if not entity_ids:
            return []
        placeholders = ",".join("?" * len(entity_ids))
        params: List[Any] = []
        clauses: List[str] = []

        if direction == "outbound":
            clauses.append(f"source_entity_id IN ({placeholders})")
            params.extend(entity_ids)
        elif direction == "inbound":
            clauses.append(f"target_entity_id IN ({placeholders})")
            params.extend(entity_ids)
        else:
            clauses.append(
                f"(source_entity_id IN ({placeholders}) OR target_entity_id IN ({placeholders}))"
            )
            params.extend(entity_ids)
            params.extend(entity_ids)

        if active_only and not at_time:
            clauses.append("valid_until IS NULL")

        if at_time:
            clauses.append("valid_from <= ?")
            clauses.append("(valid_until IS NULL OR valid_until >= ?)")
            params.extend([at_time, at_time])

        if rel_types:
            type_ph = ",".join("?" * len(rel_types))
            clauses.append(f"rel_type IN ({type_ph})")
            params.extend(rel_types)

        where = " AND ".join(clauses)
        query = f"SELECT * FROM relationships WHERE {where} ORDER BY weight DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_relationship(r) for r in rows]

    @staticmethod
    def _row_to_relationship(row: sqlite3.Row) -> Relationship:
        props_raw = row["properties"]
        properties = json.loads(props_raw) if props_raw else None
        return Relationship(
            id=row["id"],
            source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"],
            rel_type=row["rel_type"],
            weight=row["weight"],
            properties=properties,
            source_fact_id=row["source_fact_id"],
            source_memory_id=row["source_memory_id"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Entity Mention CRUD
    # ------------------------------------------------------------------

    def save_entity_mention(self, mention: EntityMention) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO entity_mentions
               (id, entity_id, memory_id, mention_type, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                mention.id, mention.entity_id, mention.memory_id,
                mention.mention_type, mention.confidence, mention.created_at,
            ),
        )
        self._conn.commit()

    def get_entity_mentions_for_memory(self, memory_id: str) -> List[EntityMention]:
        rows = self._conn.execute(
            "SELECT * FROM entity_mentions WHERE memory_id = ?", (memory_id,)
        ).fetchall()
        return [self._row_to_entity_mention(r) for r in rows]

    def get_entity_mentions_for_entity(self, entity_id: str) -> List[EntityMention]:
        rows = self._conn.execute(
            "SELECT * FROM entity_mentions WHERE entity_id = ?", (entity_id,)
        ).fetchall()
        return [self._row_to_entity_mention(r) for r in rows]

    def transfer_entity_mentions(self, from_id: str, to_id: str) -> None:
        # Delete mentions that would violate unique constraint, then update rest
        self._conn.execute(
            """DELETE FROM entity_mentions
               WHERE entity_id = ? AND memory_id IN (
                   SELECT memory_id FROM entity_mentions WHERE entity_id = ?
               )""",
            (from_id, to_id),
        )
        self._conn.execute(
            "UPDATE entity_mentions SET entity_id = ? WHERE entity_id = ?",
            (to_id, from_id),
        )
        self._conn.commit()

    def transfer_entity_relationships(self, from_id: str, to_id: str) -> None:
        self._conn.execute(
            "UPDATE relationships SET source_entity_id = ? WHERE source_entity_id = ?",
            (to_id, from_id),
        )
        self._conn.execute(
            "UPDATE relationships SET target_entity_id = ? WHERE target_entity_id = ?",
            (to_id, from_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_entity_mention(row: sqlite3.Row) -> EntityMention:
        return EntityMention(
            id=row["id"],
            entity_id=row["entity_id"],
            memory_id=row["memory_id"],
            mention_type=row["mention_type"],
            confidence=row["confidence"],
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Consolidation Log CRUD
    # ------------------------------------------------------------------

    def save_consolidation_log(self, entry: ConsolidationLogEntry) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO consolidation_log
               (id, consolidated_memory_id, original_memory_ids, strategy,
                model_used, original_count, created_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.consolidated_memory_id,
                json.dumps(entry.original_memory_ids),
                entry.strategy,
                entry.model_used,
                entry.original_count,
                entry.created_at,
                json.dumps(entry.metadata) if entry.metadata is not None else None,
            ),
        )
        self._conn.commit()

    def get_consolidation_log(
        self,
        limit: int = 50,
        project: Optional[str] = None,
    ) -> List[ConsolidationLogEntry]:
        query = "SELECT * FROM consolidation_log ORDER BY created_at DESC LIMIT ?"
        rows = self._conn.execute(query, (limit,)).fetchall()
        return [self._row_to_consolidation_log(r) for r in rows]

    @staticmethod
    def _row_to_consolidation_log(row: sqlite3.Row) -> ConsolidationLogEntry:
        ids_raw = row["original_memory_ids"]
        original_ids = json.loads(ids_raw) if ids_raw else []
        metadata_raw = row["metadata"]
        metadata = json.loads(metadata_raw) if metadata_raw else None
        return ConsolidationLogEntry(
            id=row["id"],
            consolidated_memory_id=row["consolidated_memory_id"],
            original_memory_ids=original_ids,
            strategy=row["strategy"],
            model_used=row["model_used"],
            original_count=row["original_count"],
            created_at=row["created_at"],
            metadata=metadata,
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
