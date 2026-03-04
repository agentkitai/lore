"""SQLite store implementation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from lore.store.base import Store
from lore.types import Memory

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    type        TEXT DEFAULT 'general',
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
    downvotes   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
"""

_MIGRATION_SQL = """\
CREATE TABLE memories AS SELECT
    id,
    (problem || '\n' || resolution) AS content,
    'lesson' AS type,
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
        self._conn.executescript(_SCHEMA)

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
            # Re-create indexes on the migrated table
            self._conn.executescript(
                "CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);\n"
                "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);\n"
                "CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);"
            )

    def save(self, memory: Memory) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, content, type, tags, metadata, source,
                project, embedding, created_at, updated_at,
                ttl, expires_at, confidence, upvotes, downvotes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory.id,
                memory.content,
                memory.type,
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
               content=?, type=?, tags=?, metadata=?, source=?,
               project=?, embedding=?, updated_at=?,
               ttl=?, expires_at=?, confidence=?, upvotes=?, downvotes=?
               WHERE id=?""",
            (
                memory.content,
                memory.type,
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
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        row = self._conn.execute(query, params).fetchone()
        return row[0]

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        tags_raw = row["tags"]
        tags: List[str] = json.loads(tags_raw) if tags_raw else []
        metadata_raw = row["metadata"]
        metadata: Optional[Dict[str, Any]] = (
            json.loads(metadata_raw) if metadata_raw else None
        )
        return Memory(
            id=row["id"],
            content=row["content"],
            type=row["type"] or "general",
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
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
