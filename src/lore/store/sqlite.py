"""SQLite store implementation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from lore.store.base import Store
from lore.types import Lesson

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS lessons (
    id          TEXT PRIMARY KEY,
    problem     TEXT NOT NULL,
    resolution  TEXT NOT NULL,
    context     TEXT,
    tags        TEXT,
    confidence  REAL DEFAULT 0.5,
    source      TEXT,
    project     TEXT,
    embedding   BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    expires_at  TEXT,
    upvotes     INTEGER DEFAULT 0,
    downvotes   INTEGER DEFAULT 0,
    meta        TEXT
);
CREATE INDEX IF NOT EXISTS idx_lessons_project ON lessons(project);
CREATE INDEX IF NOT EXISTS idx_lessons_tags ON lessons(tags);
CREATE INDEX IF NOT EXISTS idx_lessons_created ON lessons(created_at);
"""


class SqliteStore(Store):
    """SQLite-backed lesson store."""

    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def save(self, lesson: Lesson) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO lessons
               (id, problem, resolution, context, tags, confidence, source,
                project, embedding, created_at, updated_at, expires_at,
                upvotes, downvotes, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lesson.id,
                lesson.problem,
                lesson.resolution,
                lesson.context,
                json.dumps(lesson.tags),
                lesson.confidence,
                lesson.source,
                lesson.project,
                lesson.embedding,
                lesson.created_at,
                lesson.updated_at,
                lesson.expires_at,
                lesson.upvotes,
                lesson.downvotes,
                json.dumps(lesson.meta) if lesson.meta is not None else None,
            ),
        )
        self._conn.commit()

    def get(self, lesson_id: str) -> Optional[Lesson]:
        row = self._conn.execute(
            "SELECT * FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_lesson(row)

    def list(
        self,
        project: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Lesson]:
        query = "SELECT * FROM lessons"
        params: List[Any] = []
        if project is not None:
            query += " WHERE project = ?"
            params.append(project)
        query += " ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_lesson(r) for r in rows]

    def update(self, lesson: Lesson) -> bool:
        cursor = self._conn.execute(
            """UPDATE lessons SET
               problem=?, resolution=?, context=?, tags=?, confidence=?,
               source=?, project=?, embedding=?, updated_at=?,
               expires_at=?, upvotes=?, downvotes=?, meta=?
               WHERE id=?""",
            (
                lesson.problem,
                lesson.resolution,
                lesson.context,
                json.dumps(lesson.tags),
                lesson.confidence,
                lesson.source,
                lesson.project,
                lesson.embedding,
                lesson.updated_at,
                lesson.expires_at,
                lesson.upvotes,
                lesson.downvotes,
                json.dumps(lesson.meta) if lesson.meta is not None else None,
                lesson.id,
            ),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete(self, lesson_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM lessons WHERE id = ?", (lesson_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_lesson(row: sqlite3.Row) -> Lesson:
        tags_raw = row["tags"]
        tags: List[str] = json.loads(tags_raw) if tags_raw else []
        meta_raw = row["meta"]
        meta: Optional[Dict[str, Any]] = (
            json.loads(meta_raw) if meta_raw else None
        )
        return Lesson(
            id=row["id"],
            problem=row["problem"],
            resolution=row["resolution"],
            context=row["context"],
            tags=tags,
            confidence=row["confidence"],
            source=row["source"],
            project=row["project"],
            embedding=row["embedding"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            upvotes=row["upvotes"],
            downvotes=row["downvotes"],
            meta=meta,
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
