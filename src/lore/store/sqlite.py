"""SQLite store implementation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from lore.store.base import Store
from lore.types import ConflictEntry, Fact, Memory

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
    last_accessed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_memories_project_tier ON memories(project, tier);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance_score);
CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories(last_accessed_at);
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


class SqliteStore(Store):
    """SQLite-backed memory store."""

    def __init__(self, db_path: str) -> None:
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
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._maybe_create_fact_tables()

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

    def save(self, memory: Memory) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, content, type, tier, context, tags, metadata, source,
                project, embedding, created_at, updated_at,
                ttl, expires_at, confidence, upvotes, downvotes,
                importance_score, access_count, last_accessed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    ) -> List[Memory]:
        query = "SELECT * FROM memories"
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
               importance_score=?, access_count=?, last_accessed_at=?
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

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
