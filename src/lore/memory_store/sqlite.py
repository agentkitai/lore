"""SQLite store implementation for Lore local mode."""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from lore.memory_store.base import Store
from lore.types import Memory, SearchResult, StoreStats

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'note',
    source      TEXT,
    project     TEXT,
    tags        TEXT NOT NULL DEFAULT '[]',
    metadata    TEXT NOT NULL DEFAULT '{}',
    embedding   BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    expires_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
"""

# Time decay constant matching server: exp(-0.005 * age_days)
_DECAY_LAMBDA = 0.005


def _serialize_embedding(vec: List[float]) -> bytes:
    """Serialize a float list to bytes (float32)."""
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize_embedding(data: bytes) -> np.ndarray:
    """Deserialize bytes to numpy array (float32)."""
    count = len(data) // 4
    return np.array(struct.unpack(f"{count}f", data), dtype=np.float32)


class SqliteStore(Store):
    """SQLite-backed memory store for local MCP mode."""

    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def save(self, memory: Memory) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, content, type, source, project, tags, metadata,
                embedding, created_at, updated_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory.id,
                memory.content,
                memory.type,
                memory.source,
                memory.project,
                json.dumps(memory.tags),
                json.dumps(memory.metadata),
                memory.embedding,
                memory.created_at,
                memory.updated_at,
                memory.expires_at,
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

    def search(
        self,
        embedding: List[float],
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 5,
    ) -> List[SearchResult]:
        """Client-side cosine similarity search with time decay."""
        now = datetime.now(timezone.utc)

        # Get all candidates
        query = "SELECT * FROM memories WHERE 1=1"
        params: List[Any] = []

        if project is not None:
            query += " AND project = ?"
            params.append(project)

        if type is not None:
            query += " AND type = ?"
            params.append(type)

        query += " ORDER BY created_at DESC"
        rows = self._conn.execute(query, params).fetchall()

        candidates = []
        for row in rows:
            m = self._row_to_memory(row)
            # Skip expired
            if m.expires_at:
                try:
                    exp = datetime.fromisoformat(m.expires_at)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp <= now:
                        continue
                except ValueError:
                    pass
            # Must have embedding
            if not m.embedding:
                continue
            # Tag filter
            if tags and not set(tags).issubset(set(m.tags)):
                continue
            candidates.append(m)

        if not candidates:
            return []

        query_arr = np.array(embedding, dtype=np.float32)
        query_norm = query_arr / max(np.linalg.norm(query_arr), 1e-9)

        embeddings = np.array(
            [_deserialize_embedding(m.embedding) for m in candidates],  # type: ignore[arg-type]
            dtype=np.float32,
        )
        emb_norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        emb_norms = np.clip(emb_norms, 1e-9, None)
        embeddings_normed = embeddings / emb_norms

        cosine_scores = embeddings_normed @ query_norm

        results: List[SearchResult] = []
        for i, m in enumerate(candidates):
            age_days = (
                now - datetime.fromisoformat(m.created_at).replace(tzinfo=timezone.utc)
            ).total_seconds() / 86400.0
            time_decay = math.exp(-_DECAY_LAMBDA * age_days)
            score = float(cosine_scores[i]) * time_decay
            results.append(SearchResult(memory=m, score=round(max(score, 0.0), 6)))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def list(
        self,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        include_expired: bool = False,
    ) -> Tuple[List[Memory], int]:
        now = datetime.now(timezone.utc)
        query = "SELECT * FROM memories WHERE 1=1"
        count_query = "SELECT COUNT(*) FROM memories WHERE 1=1"
        params: List[Any] = []

        if project is not None:
            query += " AND project = ?"
            count_query += " AND project = ?"
            params.append(project)

        if type is not None:
            query += " AND type = ?"
            count_query += " AND type = ?"
            params.append(type)

        total = self._conn.execute(count_query, params).fetchone()[0]

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params_with_page = list(params) + [limit, offset]

        rows = self._conn.execute(query, params_with_page).fetchall()
        memories = []
        for row in rows:
            m = self._row_to_memory(row)
            # Skip expired unless include_expired is True
            if not include_expired and m.expires_at:
                try:
                    exp = datetime.fromisoformat(m.expires_at)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp <= now:
                        continue
                except ValueError:
                    pass
            # Tag filter
            if tags and not set(tags).issubset(set(m.tags)):
                continue
            memories.append(m)

        return memories, total

    def delete(self, memory_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM memories WHERE id = ?", (memory_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_by_filter(
        self,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
    ) -> int:
        query = "DELETE FROM memories WHERE 1=1"
        params: List[Any] = []

        if type is not None:
            query += " AND type = ?"
            params.append(type)

        if project is not None:
            query += " AND project = ?"
            params.append(project)

        cursor = self._conn.execute(query, params)
        self._conn.commit()
        return cursor.rowcount

    def delete_expired(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        self._conn.commit()
        return cursor.rowcount

    def stats(self, project: Optional[str] = None) -> StoreStats:
        # Build WHERE clause excluding expired memories
        conditions = ["(expires_at IS NULL OR expires_at > ?)"]
        params: List[Any] = [datetime.now(timezone.utc).isoformat()]

        if project is not None:
            conditions.append("project = ?")
            params.append(project)

        where = " WHERE " + " AND ".join(conditions)

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM memories{where}", params
        ).fetchone()[0]

        type_rows = self._conn.execute(
            f"SELECT type, COUNT(*) as cnt FROM memories{where} GROUP BY type ORDER BY cnt DESC",
            params,
        ).fetchall()

        project_rows = self._conn.execute(
            f"SELECT COALESCE(project, '(unscoped)') as project, COUNT(*) as cnt "
            f"FROM memories{where} GROUP BY project ORDER BY cnt DESC",
            params,
        ).fetchall()

        dates = self._conn.execute(
            f"SELECT MIN(created_at) as oldest, MAX(created_at) as newest FROM memories{where}",
            params,
        ).fetchone()

        return StoreStats(
            total_count=total,
            count_by_type={row["type"]: row["cnt"] for row in type_rows},
            count_by_project={row["project"]: row["cnt"] for row in project_rows},
            oldest_memory=dates["oldest"] if dates else None,
            newest_memory=dates["newest"] if dates else None,
        )

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        tags_raw = row["tags"]
        tags: List[str] = json.loads(tags_raw) if tags_raw else []
        metadata_raw = row["metadata"]
        metadata: Dict[str, Any] = json.loads(metadata_raw) if metadata_raw else {}
        return Memory(
            id=row["id"],
            content=row["content"],
            type=row["type"],
            source=row["source"],
            project=row["project"],
            tags=tags,
            metadata=metadata,
            embedding=row["embedding"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
