"""SQLite Store implementation (Phase 3 of the solo-mode design).

Phase 3A — foundation: lifecycle + WAL pragmas + sqlite-vec extension load
+ migration runner. Phase 3B — vec0 virtual table for embeddings + a
transactional helper enforcing the `memories` ⇆ `memory_vectors` invariant
in a single `BEGIN IMMEDIATE … COMMIT`. Phase 3C — first three MemoryOps
methods (`insert_memory`, `get_memory`, `delete_memory`) wired through that
transactional pair. Remaining MemoryOps + AnalyticsOps + the other six Store
slices stay as `NotImplementedError` stubs pending 3D–3F.

Spec: docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from ulid import ULID

from lore.persistence.exceptions import (
    BackendUnavailableError,
    ConfigError,
    StoreError,
    StoreNotFoundError,
)
from lore.persistence.types import (
    ExportedMemory,
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    ScoredMemory,
    StoredMemory,
)

# Embedding dimension is fixed at 384 across the codebase
# (see migrations/001_initial.sql and lore.embed defaults).
EMBED_DIM = 384

try:  # pragma: no cover - optional dep
    import aiosqlite
except ImportError:  # pragma: no cover
    aiosqlite = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dep
    import sqlite_vec  # noqa: F401
    HAS_SQLITE_VEC = True
except ImportError:  # pragma: no cover
    HAS_SQLITE_VEC = False

logger = logging.getLogger(__name__)


# Default migrations directory (sibling of migrations/), resolved at runtime
# via lore.persistence.sqlite._migrations_dir() so tests can override it.
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "migrations_sqlite"


def _migrations_dir() -> Path:
    override = os.environ.get("LORE_MIGRATIONS_SQLITE_DIR")
    return Path(override) if override else _DEFAULT_MIGRATIONS_DIR


def _resolve_db_path(database_url: str) -> str:
    """Convert a sqlite:/// URL to a filesystem path.

    `sqlite:///path/to/db` and `sqlite:////absolute/path` both supported.
    `sqlite:///:memory:` returns the literal `:memory:` for in-process DBs.
    """
    parsed = urlparse(database_url)
    if parsed.scheme != "sqlite":
        raise ConfigError(f"Not a sqlite URL: {database_url!r}")
    # urlparse splits sqlite:////abs into netloc='', path='/abs'; sqlite:///rel
    # gives netloc='', path='/rel' — the leading slash needs trimming for
    # rel paths but kept for absolute. Fall back to the raw string after the
    # scheme to keep this robust.
    raw = database_url[len("sqlite://"):]
    if raw.startswith("/:memory:") or raw == "/:memory:":
        return ":memory:"
    if raw.startswith("//"):
        # sqlite:////abs/path  -> /abs/path
        return raw[1:]
    if raw.startswith("/"):
        # sqlite:///rel/path   -> rel/path  (relative to CWD)
        candidate = raw[1:]
        # If the trimmed path begins with ~/, expand it.
        if candidate.startswith("~"):
            return str(Path(candidate).expanduser())
        return candidate
    return raw


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a SQLite TEXT timestamp into an aware UTC ``datetime``.

    SQLite ``datetime('now')`` produces ``"YYYY-MM-DD HH:MM:SS"`` (space
    separator, no timezone). ``datetime.fromisoformat`` handles both space
    and ``T`` separators in 3.11+, but the result is naïve. We attach UTC
    explicitly to mirror Postgres' ``TIMESTAMPTZ now()`` returning aware
    UTC datetimes.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _decode_vec_to_json(value) -> Optional[list[float]]:
    """Decode a sqlite-vec ``vec_to_json`` output into a list of floats.

    ``vec_to_json`` produces a JSON-array string like ``"[0.1,0.2,...]"``.
    Returns ``None`` if the input is None / empty (e.g. LEFT JOIN miss).
    """
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if not value:
        return None
    parsed = json.loads(value)
    return [float(x) for x in parsed]


def _row_to_exported(row, embedding: Optional[list[float]]) -> ExportedMemory:
    """Translate a memories row + decoded embedding into ``ExportedMemory``.

    Mirrors PostgresStore's ``_row_to_exported_memory`` but takes the
    embedding as a separate argument since SQLite stores it in the
    ``memory_vectors`` virtual table joined externally.
    """
    tags_raw = row["tags"]
    tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
    meta_raw = row["meta"]
    meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
    raw_context = row["context"]
    return ExportedMemory(
        id=row["id"],
        org_id=row["org_id"],
        content=row["content"],
        context=raw_context if raw_context else None,
        tags=tuple(tags or ()),
        confidence=float(row["confidence"]) if row["confidence"] is not None else 0.5,
        source=row["source"],
        project=row["project"],
        embedding=embedding,
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
        expires_at=_parse_iso(row["expires_at"]),
        upvotes=row["upvotes"] or 0,
        downvotes=row["downvotes"] or 0,
        meta=dict(meta or {}),
    )


def _row_to_memory(row) -> StoredMemory:
    """Translate a SQLite ``memories`` row to ``StoredMemory``.

    Mirrors ``lore.persistence.postgres._row_to_stored`` but parses TEXT-as-JSON
    columns (``tags``, ``meta``) and ISO-8601 TEXT timestamps. ``aiosqlite.Row``
    supports both index-by-column-name and ``dict(row)`` access.
    """
    tags_raw = row["tags"]
    tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
    meta_raw = row["meta"]
    meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
    raw_context = row["context"]
    return StoredMemory(
        id=row["id"],
        org_id=row["org_id"],
        content=row["content"],
        context=raw_context if raw_context else None,
        tags=tuple(tags or ()),
        confidence=float(row["confidence"]) if row["confidence"] is not None else 0.5,
        source=row["source"],
        project=row["project"],
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
        expires_at=_parse_iso(row["expires_at"]),
        upvotes=row["upvotes"] or 0,
        downvotes=row["downvotes"] or 0,
        meta=dict(meta or {}),
        importance_score=float(row["importance_score"]) if row["importance_score"] is not None else 1.0,
        access_count=row["access_count"] or 0,
        last_accessed_at=_parse_iso(row["last_accessed_at"]),
    )


class SqliteStore:
    """Store implementation backed by SQLite + sqlite-vec.

    Phase 3A wires up:
      * Connection management with WAL pragmas (journal_mode=WAL,
        synchronous=NORMAL, busy_timeout=5000, foreign_keys=ON).
      * sqlite-vec extension load on every connection.
      * Migration runner: applies migrations_sqlite/*.sql in order and tracks
        applied versions via a `schema_migrations` table.

    Per-method Store-protocol implementations land in 3C–3F.
    """

    def __init__(self, *, db_path: str, conn: Optional[Any] = None):
        if aiosqlite is None:
            raise BackendUnavailableError(
                "aiosqlite is not installed. Install with: pip install lore-sdk[solo]"
            )
        if not HAS_SQLITE_VEC:
            raise BackendUnavailableError(
                "sqlite-vec is not installed. Install with: pip install lore-sdk[solo]"
            )
        self._db_path = db_path
        self._bound_conn = conn  # bound-connection mode (used by tests)
        self._owned_conn: Optional[Any] = None  # owned-by-store mode
        self._closed = False

    @property
    def _conn(self):
        """Return the active connection (bound or owned).

        Mirrors PostgresStore.from_connection's ``_conn`` attribute so contract
        tests and other callers can use ``store._conn.execute(...)`` regardless
        of whether the SqliteStore is bound to an externally-owned connection
        or owns its own. Returns None if both are unset (closed store).
        """
        return self._bound_conn or self._owned_conn

    # ── Lifecycle ──────────────────────────────────────────────────────

    @classmethod
    async def open(cls, database_url: str) -> "SqliteStore":
        """Open a SqliteStore from a sqlite:// URL, applying migrations."""
        db_path = _resolve_db_path(database_url)
        if db_path != ":memory:":
            parent = Path(db_path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)
        store = cls(db_path=db_path)
        store._owned_conn = await store._open_connection(db_path)
        await store._apply_migrations(store._owned_conn)
        await store._init_vec_tables(store._owned_conn)
        return store

    @classmethod
    def from_connection(cls, conn) -> "SqliteStore":
        """Bind to an externally-owned aiosqlite connection (used by tests).

        The provided connection is exposed via ``store._conn`` (and through
        ``_acquire()`` / ``transaction()``) for parity with PostgresStore's
        bound-mode shape.
        """
        return cls(db_path=":bound:", conn=conn)

    async def _open_connection(self, db_path: str):
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        # WAL + reasonable concurrency defaults.
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        # sqlite-vec extension load. aiosqlite proxies load_extension to the
        # connection's worker thread, which is where the underlying sqlite3
        # connection lives.
        try:
            await conn.enable_load_extension(True)
            await conn.load_extension(sqlite_vec.loadable_path())
            await conn.enable_load_extension(False)
        except Exception as exc:  # pragma: no cover - depends on platform
            await conn.close()
            raise BackendUnavailableError(
                f"Failed to load sqlite-vec extension: {exc}. "
                "On some platforms you need a Python build with extension "
                "loading enabled. See sqlite-vec install notes."
            ) from exc
        return conn

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owned_conn is not None:
            await self._owned_conn.close()
            self._owned_conn = None

    # ── Migrations ─────────────────────────────────────────────────────

    _MIGRATION_FILE_RE = re.compile(r"^(\d{3})_.+\.sql$")

    async def _apply_migrations(self, conn) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await conn.commit()

        migrations_dir = _migrations_dir()
        if not migrations_dir.exists():
            logger.warning(
                "SqliteStore: migrations directory does not exist: %s "
                "(no migrations applied)",
                migrations_dir,
            )
            return

        applied: set[str] = set()
        async with conn.execute("SELECT version FROM schema_migrations") as cur:
            async for row in cur:
                applied.add(row["version"])

        files = sorted(
            (p for p in migrations_dir.iterdir() if p.is_file() and p.suffix == ".sql"),
            key=lambda p: p.name,
        )
        for path in files:
            m = self._MIGRATION_FILE_RE.match(path.name)
            if not m:
                logger.debug("Skipping non-migration file: %s", path.name)
                continue
            version = m.group(1)
            if version in applied:
                continue
            sql = path.read_text()
            try:
                await conn.executescript(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)",
                    (version,),
                )
                await conn.commit()
            except Exception as exc:
                raise StoreError(
                    f"Failed to apply SQLite migration {path.name}: {exc}"
                ) from exc
            logger.info("Applied SQLite migration %s", path.name)

    # ── Vector layer (Phase 3B) ────────────────────────────────────────

    async def _init_vec_tables(self, conn) -> None:
        """Create the `memory_vectors` vec0 virtual table.

        The vec0 table stores the embedding vector keyed by `memory_rowid`,
        which mirrors the `memories.rowid` integer the underlying base
        table assigns. Inserts go in pairs inside a single transaction
        (see `transaction()`) so a `memories` row never exists without its
        matching vector and vice versa.

        ``distance_metric=cosine`` selects cosine distance so the recall
        ranking matches PG's ``embedding <=> $vec`` operator (also cosine).
        Cosine distance is in ``[0, 2]`` for arbitrary vectors and in
        ``[0, 1]`` for the typical case of normalized embeddings — the
        recall path computes ``score = 1 - distance`` to mirror PG's
        ``(1 - (embedding <=> $vec))`` similarity expression.

        Not migration-versioned because vec0 is provider-specific to the
        SQLite backend and not part of the cross-dialect schema contract.
        Idempotent thanks to `IF NOT EXISTS`.
        """
        await conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors USING vec0(
                memory_rowid INTEGER PRIMARY KEY,
                embedding FLOAT[{EMBED_DIM}] distance_metric=cosine
            )
            """
        )
        await conn.commit()

    @contextlib.asynccontextmanager
    async def transaction(self):
        """`BEGIN IMMEDIATE … COMMIT` (or ROLLBACK on exception).

        Use for any write that touches BOTH `memories` and `memory_vectors`
        — or any other multi-table invariant. `BEGIN IMMEDIATE` acquires a
        write lock up-front so we don't get stuck in a deferred-to-immediate
        upgrade if a concurrent read is open.

        Yields the connection so the caller can chain executes inside the
        transaction without re-acquiring it.
        """
        conn = self._conn
        if conn is None:
            raise StoreError("SqliteStore connection is closed")
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            with contextlib.suppress(Exception):
                await conn.rollback()
            raise
        else:
            await conn.commit()

    def _acquire(self):
        """Return an async context manager yielding a usable connection.

        SQLite is a single-process backend; we don't need a real pool. The
        same connection is re-used.
        """
        return _SqliteConnCtx(self._conn)

    # ── MemoryOps: insert, get, delete (Phase 3C) ─────────────────────

    async def insert_memory(self, memory: "NewMemory") -> "StoredMemory":
        """Insert a memory + its embedding inside a single transaction.

        Mirrors ``PostgresStore.insert_memory``: generates an id, encodes
        JSON columns, and returns the freshly inserted row as ``StoredMemory``.
        The vec0 ``memory_vectors`` companion row is inserted in the same
        transaction so the pair invariant holds — see Phase 3B's
        ``transaction()`` helper.
        """
        memory_id = f"mem_{ULID()}"
        async with self.transaction() as tx:
            cursor = await tx.execute(
                """
                INSERT INTO memories
                    (id, org_id, content, context, tags, confidence, source,
                     project, expires_at, meta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    memory.org_id,
                    memory.content,
                    memory.context or "",  # NOT NULL in PG schema; mirror
                    json.dumps(list(memory.tags)),
                    memory.confidence,
                    memory.source,
                    memory.project,
                    memory.expires_at.isoformat() if memory.expires_at else None,
                    json.dumps(dict(memory.meta)),
                ),
            )
            rowid = cursor.lastrowid
            await cursor.close()

            await tx.execute(
                "INSERT INTO memory_vectors(memory_rowid, embedding) VALUES (?, ?)",
                (rowid, repr(list(memory.embedding))),
            )

            async with tx.execute(
                """
                SELECT id, org_id, content, context, tags, confidence, source,
                       project, created_at, updated_at, expires_at, upvotes,
                       downvotes, meta, importance_score, access_count,
                       last_accessed_at
                FROM memories WHERE rowid = ?
                """,
                (rowid,),
            ) as cur:
                row = await cur.fetchone()

        if row is None:  # pragma: no cover - defensive
            raise StoreError(f"insert_memory: row {rowid} disappeared after insert")
        return _row_to_memory(row)

    async def get_memory(self, org_id: str, memory_id: str) -> Optional["StoredMemory"]:
        """Fetch a memory by ``(id, org_id)``; excludes already-expired rows.

        Mirrors PostgresStore: an expired row is invisible to ``get_memory``
        even though it physically still lives in the table until the next
        ``expire_memories`` sweep.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self._acquire() as conn:
            async with conn.execute(
                """
                SELECT id, org_id, content, context, tags, confidence, source,
                       project, created_at, updated_at, expires_at, upvotes,
                       downvotes, meta, importance_score, access_count,
                       last_accessed_at
                FROM memories
                WHERE id = ?
                  AND org_id = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (memory_id, org_id, now_iso),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_memory(row) if row else None

    async def delete_memory(self, org_id: str, memory_id: str) -> bool:
        """Delete a memory and its companion vector inside one transaction.

        The vec0 row is keyed by ``memory_vectors.memory_rowid`` which equals
        the base table's rowid. We resolve the rowid up-front, then delete
        the vector first followed by the base row (vec0 has no FK so order
        is informational only — both succeed or both roll back).
        """
        async with self.transaction() as tx:
            async with tx.execute(
                "SELECT rowid FROM memories WHERE id = ? AND org_id = ?",
                (memory_id, org_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return False
            rowid = row["rowid"]
            await tx.execute(
                "DELETE FROM memory_vectors WHERE memory_rowid = ?", (rowid,)
            )
            cursor = await tx.execute(
                "DELETE FROM memories WHERE id = ? AND org_id = ?",
                (memory_id, org_id),
            )
            deleted = cursor.rowcount
            await cursor.close()
        return deleted == 1

    # ── MemoryOps: rest of the slice (Phase 3D) ───────────────────────

    _MEMORY_COLS = (
        "id, org_id, content, context, tags, confidence, source, "
        "project, created_at, updated_at, expires_at, upvotes, "
        "downvotes, meta, importance_score, access_count, last_accessed_at"
    )

    async def update_memory(
        self,
        org_id: str,
        memory_id: str,
        patch: "MemoryPatch",
    ) -> "StoredMemory":
        """Apply a ``MemoryPatch`` and return the updated row.

        Builds a dynamic UPDATE based on which fields the patch sets. Mirrors
        ``PostgresStore.update_memory``: the row must exist and not be
        expired, otherwise raises ``StoreNotFoundError``.

        ``MemoryPatch`` does not carry an embedding field, so this method
        never touches ``memory_vectors`` (and therefore doesn't need to wrap
        in ``transaction()``).
        """
        sets: list[str] = []
        params: list[Any] = []
        if patch.content is not None:
            sets.append("content = ?")
            params.append(patch.content)
        if patch.context is not None:
            sets.append("context = ?")
            params.append(patch.context)
        if patch.tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(list(patch.tags)))
        if patch.confidence is not None:
            sets.append("confidence = ?")
            params.append(patch.confidence)
        if patch.source is not None:
            sets.append("source = ?")
            params.append(patch.source)
        if patch.project is not None:
            sets.append("project = ?")
            params.append(patch.project)
        if patch.expires_at is not None:
            sets.append("expires_at = ?")
            params.append(patch.expires_at.isoformat())
        if patch.meta is not None:
            sets.append("meta = ?")
            params.append(json.dumps(dict(patch.meta)))

        if not sets:
            existing = await self.get_memory(org_id, memory_id)
            if existing is None:
                raise StoreNotFoundError("memories", memory_id)
            return existing

        sets.append("updated_at = datetime('now')")
        now_iso = datetime.now(timezone.utc).isoformat()
        sql = (
            "UPDATE memories "
            f"SET {', '.join(sets)} "
            "WHERE id = ? AND org_id = ? "
            "  AND (expires_at IS NULL OR expires_at > ?)"
        )
        params.extend([memory_id, org_id, now_iso])

        async with self._acquire() as conn:
            cursor = await conn.execute(sql, tuple(params))
            updated = cursor.rowcount
            await cursor.close()
            await conn.commit()
            if updated == 0:
                raise StoreNotFoundError("memories", memory_id)
            async with conn.execute(
                f"SELECT {self._MEMORY_COLS} FROM memories "
                "WHERE id = ? AND org_id = ?",
                (memory_id, org_id),
            ) as cur:
                row = await cur.fetchone()
        if row is None:  # pragma: no cover - should not happen post-update
            raise StoreNotFoundError("memories", memory_id)
        return _row_to_memory(row)

    def _build_memory_filter_clauses(
        self,
        filter: "MemoryFilter",
        *,
        text_query: bool = False,
        min_reputation: bool = False,
        alias: str = "",
    ) -> tuple[list[str], list[Any]]:
        """Translate a ``MemoryFilter`` into a SQLite WHERE clause + params.

        Mirrors ``PostgresStore``'s building of ``where``/``params`` in
        ``list_memories`` / ``list_memories_paginated``. Tags translate
        from PG's ``tags @> $N::jsonb`` ("contains all of") into a
        SQLite ``json_each``-based EXISTS subquery for each requested tag.

        ``text_query`` and ``min_reputation`` flags are used by the
        paginated/exported variants which expose those filters; the basic
        ``list_memories`` doesn't pass them.

        ``alias`` is an optional table alias prefix (e.g. ``"m"``) used by
        ``list_memories_with_embeddings`` whose SELECT joins ``memory_vectors``
        and so needs every column reference qualified.
        """
        prefix = f"{alias}." if alias else ""
        where: list[str] = [f"{prefix}org_id = ?"]
        params: list[Any] = [filter.org_id]
        if filter.project is not None:
            where.append(f"{prefix}project = ?")
            params.append(filter.project)
        if filter.type is not None:
            # PG: meta->>'type' = $N. SQLite: json_extract(meta, '$.type').
            where.append(f"json_extract({prefix}meta, '$.type') = ?")
            params.append(filter.type)
        if filter.tier is not None:
            where.append(f"json_extract({prefix}meta, '$.tier') = ?")
            params.append(filter.tier)
        if filter.tags:
            # PG: tags @> '["a","b"]'::jsonb (contains-all semantics).
            # SQLite: AND'd EXISTS (SELECT 1 FROM json_each(tags) WHERE value=?)
            for tag in filter.tags:
                where.append(
                    f"EXISTS (SELECT 1 FROM json_each({prefix}tags) "
                    "WHERE value = ?)"
                )
                params.append(tag)
        if filter.since is not None:
            where.append(f"{prefix}created_at >= ?")
            params.append(filter.since.isoformat())
        if filter.until is not None:
            where.append(f"{prefix}created_at < ?")
            params.append(filter.until.isoformat())
        if text_query and filter.text_query is not None:
            where.append(f"({prefix}content LIKE ? OR {prefix}context LIKE ?)")
            pat = f"%{filter.text_query}%"
            params.extend([pat, pat])
        if min_reputation and filter.min_reputation is not None:
            where.append(f"{prefix}reputation_score >= ?")
            params.append(filter.min_reputation)
        if not filter.include_expired:
            now_iso = datetime.now(timezone.utc).isoformat()
            where.append(f"({prefix}expires_at IS NULL OR {prefix}expires_at > ?)")
            params.append(now_iso)
        return where, params

    async def list_memories(
        self, filter: "MemoryFilter"
    ) -> Sequence["StoredMemory"]:
        """List memories matching the filter, ordered by ``created_at`` DESC."""
        where, params = self._build_memory_filter_clauses(filter)
        sql = (
            f"SELECT {self._MEMORY_COLS} FROM memories "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC"
        )
        if filter.limit is not None:
            sql += " LIMIT ?"
            params.append(filter.limit)
        if filter.offset:
            sql += " OFFSET ?"
            params.append(filter.offset)
        async with self._acquire() as conn:
            async with conn.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
        return [_row_to_memory(r) for r in rows]

    async def expire_memories(self) -> int:
        """Delete rows with ``expires_at < now()`` plus their vec0 companions.

        SQLite has no ``DELETE … RETURNING`` cascade across vec0, and vec0
        has no FK, so we resolve victim rowids inside the same
        ``BEGIN IMMEDIATE`` transaction, delete the vec0 rows, then the
        base rows. Returns the number of base-table rows removed.

        ``expires_at`` is stored as Python-side ``isoformat()`` (with ``T``
        separator and ``+00:00`` suffix) by ``insert_memory``, while
        SQLite's ``datetime('now')`` returns ``"YYYY-MM-DD HH:MM:SS"``.
        Lexicographic comparison between those two TEXT shapes is unsafe
        (``"T" > " "`` makes any ISO timestamp sort *after* the SQLite shape
        of the same wall-clock time). To keep parity with PG's
        ``expires_at < now()`` semantics we substitute a Python-generated
        ``isoformat()`` for ``now()`` so both sides of the comparison share
        the same format.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self.transaction() as tx:
            async with tx.execute(
                "SELECT rowid FROM memories "
                "WHERE expires_at IS NOT NULL "
                "  AND expires_at < ?",
                (now_iso,),
            ) as cur:
                rows = await cur.fetchall()
            if not rows:
                return 0
            rowids = [r["rowid"] for r in rows]
            placeholders = ",".join(["?"] * len(rowids))
            await tx.execute(
                f"DELETE FROM memory_vectors WHERE memory_rowid IN ({placeholders})",
                tuple(rowids),
            )
            cursor = await tx.execute(
                f"DELETE FROM memories WHERE rowid IN ({placeholders})",
                tuple(rowids),
            )
            deleted = cursor.rowcount
            await cursor.close()
        return int(deleted) if deleted is not None else 0

    async def bump_access_counts(
        self,
        org_id: str,
        memory_ids: Sequence[str],
    ) -> None:
        """Atomically bump access_count + last_accessed_at + importance_score.

        Mirrors ``PostgresStore.bump_access_counts``: increments
        ``access_count``, sets ``last_accessed_at = now()``, and recomputes
        ``importance_score`` from confidence, vote delta, and the (slightly
        damped) log of the new access count.

        Translation notes:
        * PG ``GREATEST(0.1, x)`` → SQLite ``MAX(0.1, x)``.
        * SQLite has ``ln`` since 3.35; the formula matches PG verbatim.
        * Cross-org isolation is preserved by the WHERE clause.
        """
        if not memory_ids:
            return
        placeholders = ",".join(["?"] * len(memory_ids))
        sql = (
            "UPDATE memories SET "
            "access_count = COALESCE(access_count, 0) + 1, "
            "last_accessed_at = datetime('now'), "
            "importance_score = COALESCE(confidence, 1.0) "
            " * MAX(0.1, 1.0 + (COALESCE(upvotes, 0) - COALESCE(downvotes, 0)) * 0.1) "
            " * (1.0 + ln(COALESCE(access_count, 0) + 2) / ln(2) * 0.1) "
            f"WHERE id IN ({placeholders}) AND org_id = ?"
        )
        params: list[Any] = list(memory_ids)
        params.append(org_id)
        async with self._acquire() as conn:
            await conn.execute(sql, tuple(params))
            await conn.commit()

    async def enrich_memory_meta(
        self,
        memory_id: str,
        enrichment_data: "Mapping[str, Any]",
    ) -> None:
        """Set ``meta.enrichment = enrichment_data``.

        PG: ``jsonb_set(COALESCE(meta, '{}'), '{enrichment}', $2)`` — sets
        the ``enrichment`` key to the supplied JSON value, replacing any
        prior value at that key. SQLite: ``json_set(meta, '$.enrichment', json(?))``
        has the same effect (sets a single key in a flat dict).

        Note (PG vs SQLite parity): PG's ``jsonb_set`` writes the full
        passed value at the path; SQLite's ``json_set`` likewise writes
        the value at the single path key. Behavior is identical for the
        single ``$.enrichment`` write the service layer performs. We do
        NOT use ``json_patch`` here — that does an RFC 7396 merge which
        differs from PG's ``jsonb_set`` semantics for nested maps.
        """
        sql = (
            "UPDATE memories SET "
            "meta = json_set(COALESCE(meta, '{}'), '$.enrichment', json(?)), "
            "updated_at = datetime('now') "
            "WHERE id = ?"
        )
        async with self._acquire() as conn:
            await conn.execute(
                sql,
                (json.dumps(dict(enrichment_data)), memory_id),
            )
            await conn.commit()

    async def list_memories_paginated(
        self,
        filter: "MemoryFilter",
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, Sequence["StoredMemory"]]:
        """Two-query paginated list (COUNT + SELECT) — mirrors PG path."""
        where, params = self._build_memory_filter_clauses(
            filter, text_query=True, min_reputation=True,
        )
        where_sql = " AND ".join(where)
        count_sql = f"SELECT COUNT(*) AS n FROM memories WHERE {where_sql}"
        select_sql = (
            f"SELECT {self._MEMORY_COLS} FROM memories "
            f"WHERE {where_sql} "
            "ORDER BY created_at DESC "
            "LIMIT ? OFFSET ?"
        )
        async with self._acquire() as conn:
            async with conn.execute(count_sql, tuple(params)) as cur:
                count_row = await cur.fetchone()
            total = int(count_row["n"]) if count_row else 0
            async with conn.execute(
                select_sql, tuple(params) + (limit, offset)
            ) as cur:
                rows = await cur.fetchall()
        return (total, tuple(_row_to_memory(r) for r in rows))

    async def list_memories_with_embeddings(
        self,
        filter: "MemoryFilter",
    ) -> Sequence["ExportedMemory"]:
        """Bulk export — JOIN to ``memory_vectors`` to surface the embedding.

        Mirrors ``PostgresStore.list_memories_with_embeddings``: no LIMIT,
        ordered by ``created_at`` ASC, includes the embedding column. The
        SQLite embedding lives in the vec0 virtual table; we LEFT JOIN
        through ``memory_rowid`` and use ``vec_to_json`` to convert the
        binary vector back to a JSON-array string we then parse.

        ``LEFT JOIN`` so memories without an embedding (the vec0 row was
        deleted out-of-band, or the row was inserted via a path that
        skipped the pair invariant) surface with ``embedding=None`` —
        same shape PG returns for a NULL embedding column.
        """
        where, params = self._build_memory_filter_clauses(
            filter, text_query=True, min_reputation=True, alias="m",
        )
        where_sql = " AND ".join(where)
        # ``vec_to_json(NULL)`` errors with "Input must have type BLOB or
        # TEXT" — guard with CASE so LEFT JOIN misses surface as NULL.
        sql = (
            "SELECT m.id, m.org_id, m.content, m.context, m.tags, "
            "m.confidence, m.source, m.project, m.created_at, m.updated_at, "
            "m.expires_at, m.upvotes, m.downvotes, m.meta, "
            "CASE WHEN v.embedding IS NULL THEN NULL "
            "     ELSE vec_to_json(v.embedding) END AS embedding_json "
            "FROM memories m "
            "LEFT JOIN memory_vectors v ON v.memory_rowid = m.rowid "
            f"WHERE {where_sql} "
            "ORDER BY m.created_at"
        )
        async with self._acquire() as conn:
            async with conn.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
        return tuple(
            _row_to_exported(r, _decode_vec_to_json(r["embedding_json"]))
            for r in rows
        )

    async def recall_by_embedding(
        self,
        params: "RecallParams",
    ) -> Sequence["ScoredMemory"]:
        """Vec0 KNN ⨯ memories JOIN ⨯ score-decay ⨯ min_score filter.

        Mirrors PG's ``recall_by_embedding``:

        ``score = (1 - cosine_distance) * importance_score
                  * 0.5 ^ ( min(days_since_created, days_since_last_accessed)
                            / half_life_days )``

        Translation notes:
        * PG's ``embedding <=> $vec`` (cosine distance) → vec0's
          ``distance`` column with ``distance_metric=cosine``. Both yield
          the same metric; ``similarity = 1 - distance``.
        * The vec0 ``MATCH`` operator only allows the LIMIT to come
          through the virtual table's own ``k = ?`` constraint, so we
          fetch top-K from vec0 first, then JOIN ``memories`` and apply
          downstream filters (org, project, expiry, min_score). We
          slightly over-fetch from vec0 (max(k, limit*4)) to leave room
          for the WHERE-clause filters to drop candidates without
          starving the final result.
        * SQLite has no ``EXTRACT(EPOCH FROM …)``; we use
          ``(julianday('now') - julianday(col))`` which yields days as
          a float. ``LEAST`` → ``MIN``. ``power(0.5, x)`` → SQLite's
          ``pow(0.5, x)`` (alias since 3.35).
        """
        # Over-fetch from vec0 since post-filtering may drop candidates.
        # 4x the limit is a generous floor; clamp to a sane upper bound.
        k = max(params.limit, 1) * 4
        # Build the post-vec0 WHERE clauses (PG path: org, project, expiry).
        # Uses the same shape as ``_build_memory_filter_clauses`` for the
        # subset of filters ``RecallParams`` actually exposes.
        where: list[str] = ["m.org_id = ?"]
        sql_params: list[Any] = [params.org_id]
        if params.project is not None:
            where.append("m.project = ?")
            sql_params.append(params.project)
        if params.exclude_expired:
            now_iso = datetime.now(timezone.utc).isoformat()
            where.append("(m.expires_at IS NULL OR m.expires_at > ?)")
            sql_params.append(now_iso)

        # SQLite quirks: vec0's k must be a literal integer in some
        # builds; passing it as a parameter is supported via the rowid
        # virtual constraint syntax. We thread it as a bind param.
        sql = f"""
            SELECT
                m.id, m.org_id, m.content, m.context, m.tags, m.confidence,
                m.source, m.project, m.created_at, m.updated_at, m.expires_at,
                m.upvotes, m.downvotes, m.meta, m.importance_score,
                m.access_count, m.last_accessed_at,
                v.distance AS distance,
                (
                    (1.0 - v.distance)
                    * COALESCE(m.importance_score, 1.0)
                    * pow(
                        0.5,
                        MIN(
                            julianday('now') - julianday(m.created_at),
                            COALESCE(
                                julianday('now') - julianday(m.last_accessed_at),
                                julianday('now') - julianday(m.created_at)
                            )
                        ) / {float(params.half_life_days)}
                      )
                ) AS score
            FROM memory_vectors v
            JOIN memories m ON m.rowid = v.memory_rowid
            WHERE v.embedding MATCH ?
              AND v.k = ?
              AND {' AND '.join(where)}
              AND (1.0 - v.distance) >= ?
            ORDER BY score DESC
            LIMIT ?
        """
        bind: list[Any] = [
            repr(list(params.query_vec)),
            k,
            *sql_params,
            params.min_score,
            params.limit,
        ]
        async with self._acquire() as conn:
            async with conn.execute(sql, tuple(bind)) as cur:
                rows = await cur.fetchall()

        scored: list[ScoredMemory] = []
        for r in rows:
            sm = _row_to_memory(r)
            scored.append(
                ScoredMemory(
                    id=sm.id,
                    org_id=sm.org_id,
                    content=sm.content,
                    context=sm.context,
                    tags=sm.tags,
                    confidence=sm.confidence,
                    source=sm.source,
                    project=sm.project,
                    created_at=sm.created_at,
                    updated_at=sm.updated_at,
                    expires_at=sm.expires_at,
                    upvotes=sm.upvotes,
                    downvotes=sm.downvotes,
                    meta=sm.meta,
                    importance_score=sm.importance_score,
                    access_count=sm.access_count,
                    last_accessed_at=sm.last_accessed_at,
                    score=float(r["score"]),
                )
            )
        return scored

    async def upsert_memory_with_embedding(
        self,
        *,
        memory_id: str,
        org_id: str,
        content: str,
        context: Optional[str],
        tags: Sequence[str],
        confidence: float,
        source: Optional[str],
        project: Optional[str],
        embedding: Optional[Sequence[float]],
        expires_at: Optional[datetime],
        upvotes: int,
        downvotes: int,
        meta: "Mapping[str, Any]",
    ) -> bool:
        """Idempotent INSERT … ON CONFLICT (id) DO UPDATE … (org-guarded).

        Returns ``True`` when a brand-new row was inserted, ``False`` when
        an existing row was updated *or* when the supplied ``org_id``
        doesn't match the existing row (PG silently no-ops; we mirror
        that). The vec0 companion is upserted in the same transaction
        whether we inserted or updated; ``None`` embeddings yield no
        ``memory_vectors`` row, matching PG's NULL-embedding shape.

        Translation notes:
        * PG returns ``(xmax = 0) AS inserted`` from the upsert to detect
          the insert vs update case. SQLite's ``ON CONFLICT (id) DO UPDATE``
          doesn't expose that — we resolve it by checking up-front whether
          the row exists, then doing the upsert.
        * Org-guard: if a row with this id exists in another org, we
          silently no-op (the update WHERE filters by org) — same as PG.
        """
        encoded_tags = json.dumps(list(tags))
        encoded_meta = json.dumps(dict(meta))
        safe_context = context if context is not None else ""
        expires_iso = expires_at.isoformat() if expires_at is not None else None
        embedding_repr = repr(list(embedding)) if embedding is not None else None

        async with self.transaction() as tx:
            async with tx.execute(
                "SELECT id, org_id, rowid FROM memories WHERE id = ?",
                (memory_id,),
            ) as cur:
                existing = await cur.fetchone()
            if existing is None:
                # Pure insert: write the base row, then the vec0 companion
                # if an embedding was supplied.
                cursor = await tx.execute(
                    """
                    INSERT INTO memories
                        (id, org_id, content, context, tags, confidence,
                         source, project, created_at, updated_at, expires_at,
                         upvotes, downvotes, meta)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'),
                            datetime('now'), ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        org_id,
                        content,
                        safe_context,
                        encoded_tags,
                        confidence,
                        source,
                        project,
                        expires_iso,
                        upvotes,
                        downvotes,
                        encoded_meta,
                    ),
                )
                rowid = cursor.lastrowid
                await cursor.close()
                if embedding_repr is not None:
                    await tx.execute(
                        "INSERT INTO memory_vectors(memory_rowid, embedding) "
                        "VALUES (?, ?)",
                        (rowid, embedding_repr),
                    )
                return True
            # Existing row: silent no-op if the supplied org_id mismatches.
            if existing["org_id"] != org_id:
                return False
            # Otherwise, update in place (same id, same org).
            cursor = await tx.execute(
                """
                UPDATE memories SET
                    content = ?,
                    context = ?,
                    tags = ?,
                    confidence = ?,
                    source = ?,
                    project = ?,
                    updated_at = datetime('now'),
                    expires_at = ?,
                    upvotes = ?,
                    downvotes = ?,
                    meta = ?
                WHERE id = ? AND org_id = ?
                """,
                (
                    content,
                    safe_context,
                    encoded_tags,
                    confidence,
                    source,
                    project,
                    expires_iso,
                    upvotes,
                    downvotes,
                    encoded_meta,
                    memory_id,
                    org_id,
                ),
            )
            await cursor.close()
            rowid = existing["rowid"]
            # Refresh the vec0 companion to match the new embedding (if any).
            await tx.execute(
                "DELETE FROM memory_vectors WHERE memory_rowid = ?",
                (rowid,),
            )
            if embedding_repr is not None:
                await tx.execute(
                    "INSERT INTO memory_vectors(memory_rowid, embedding) "
                    "VALUES (?, ?)",
                    (rowid, embedding_repr),
                )
            return False

    async def import_extracted_memory(
        self,
        *,
        memory_id: str,
        org_id: str,
        content: str,
        context: str,
        tags: "Sequence[str]",
        source: str,
        meta: "Mapping[str, Any]",
        confidence: float,
    ) -> bool:
        """INSERT … ON CONFLICT (id) DO NOTHING; returns True if inserted.

        Used by the conversation-extraction pipeline to deduplicate by
        caller-supplied ID. PG version does not include an embedding
        column (it stays NULL); we mirror by inserting the base row only,
        no vec0 companion. Subsequent ``upsert_memory_with_embedding``
        from the embed-and-store pipeline will fill in the vec0 row.
        """
        encoded_tags = json.dumps(list(tags))
        encoded_meta = json.dumps(dict(meta))
        async with self._acquire() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO memories
                    (id, org_id, content, context, tags, source, meta,
                     confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    memory_id,
                    org_id,
                    content,
                    context,
                    encoded_tags,
                    source,
                    encoded_meta,
                    confidence,
                ),
            )
            inserted = cursor.rowcount == 1
            await cursor.close()
            await conn.commit()
        return inserted

    async def vote_memory(
        self,
        org_id: str,
        memory_id: str,
        *,
        direction: str,
    ) -> "StoredMemory":
        """Increment ``upvotes`` or ``downvotes``; mirrors PG signature.

        ``direction`` is ``'up'`` or ``'down'``; anything else raises
        ``ValueError`` (matches PG's ``ValueError``). Raises
        ``StoreNotFoundError`` if the memory doesn't exist.
        """
        if direction == "up":
            column = "upvotes"
        elif direction == "down":
            column = "downvotes"
        else:
            raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
        sql = (
            f"UPDATE memories SET {column} = COALESCE({column}, 0) + 1, "
            "updated_at = datetime('now') "
            "WHERE id = ? AND org_id = ?"
        )
        async with self._acquire() as conn:
            cursor = await conn.execute(sql, (memory_id, org_id))
            updated = cursor.rowcount
            await cursor.close()
            await conn.commit()
            if updated == 0:
                raise StoreNotFoundError("memories", memory_id)
            async with conn.execute(
                f"SELECT {self._MEMORY_COLS} FROM memories "
                "WHERE id = ? AND org_id = ?",
                (memory_id, org_id),
            ) as cur:
                row = await cur.fetchone()
        if row is None:  # pragma: no cover
            raise StoreNotFoundError("memories", memory_id)
        return _row_to_memory(row)


class _SqliteConnCtx:
    """Trivial async context manager around an aiosqlite connection.

    Mirrors the shape of asyncpg's pool.acquire() so call sites can use the
    same `async with self._acquire() as conn:` pattern across both stores.
    """

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        if self._conn is None:
            raise StoreError("SqliteStore connection is closed")
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


# ── Stub Store-protocol surface ───────────────────────────────────────
# All Store methods are wired here as NotImplementedError stubs so that
# Phase 3A can be merged without falsely advertising a complete backend.
# Each stub is filled in by the matching sub-phase (3C–3F). The list is
# intentionally exhaustive so static type checkers see the full surface.

def _stub(method_name: str):
    async def _impl(self, *args, **kwargs):  # pragma: no cover - stub
        raise NotImplementedError(
            f"SqliteStore.{method_name}() is not implemented yet "
            "(scheduled for a future Phase 3 sub-phase)."
        )
    _impl.__name__ = method_name
    return _impl


_STUBBED_METHODS: Sequence[str] = (
    # MemoryOps — Phase 3C implemented insert/get/delete; Phase 3D fills
    # in the remaining 11 methods above. No MemoryOps stubs remain.
    # GraphOps
    "get_entity", "get_entity_by_name", "list_entities", "upsert_entity",
    "update_entity_counts", "delete_entity",
    "get_mentions_for_memory", "get_mentions_for_entity", "save_mention",
    "count_memories_for_entity",
    "get_relationship", "get_active_relationship",
    "list_relationships_for_entity", "save_relationship",
    "update_relationship_status", "update_relationship_weight",
    "expire_relationship", "list_pending_relationships",
    "save_rejected_pattern", "query_relationships",
    "get_graph_stats", "get_timeline_buckets", "get_memories_by_entities",
    "search_memories_text",
    # PolicyOps
    "get_profile", "get_profile_by_name", "list_profiles", "create_profile",
    "update_profile", "delete_profile", "resolve_profile_for_key",
    # WorkspaceOps
    "get_workspace", "list_workspaces", "create_workspace",
    "update_workspace", "archive_workspace",
    "add_workspace_member", "list_workspace_members",
    "update_workspace_member_role", "remove_workspace_member",
    # AuthOps
    "get_api_key", "list_api_keys", "create_api_key", "revoke_api_key",
    "count_active_root_keys", "lookup_api_key_by_hash",
    "touch_api_key_last_used",
    # AnalyticsOps
    "record_retrieval_event", "record_memory_access",
    "list_recent_session_snapshots", "compute_retrieval_analytics",
    "compute_metric_value", "compute_metric_timeseries",
    # RecommendationOps
    "get_recommendation_config", "upsert_recommendation_config",
    "record_recommendation_feedback",
    "list_candidate_memories_for_recommendation",
    # ConversationOps
    "create_conversation_job", "get_conversation_job",
    "mark_conversation_job_processing", "complete_conversation_job",
    "fail_conversation_job",
    # AuditOps
    "query_audit_log",
    # RetentionOps
    "list_retention_policies", "get_retention_policy",
    "create_retention_policy", "update_retention_policy",
    "delete_retention_policy", "get_latest_snapshot_for_policy",
    "count_snapshots_for_policy", "record_drill_result",
    "list_drill_results_for_policy", "get_latest_drill_result",
    # SloOps
    "list_slo_definitions", "get_slo_definition",
    "create_slo_definition", "update_slo_definition",
    "delete_slo_definition", "list_slo_alerts", "record_slo_alert",
    # SharingOps
    "get_or_init_sharing_config", "update_sharing_config",
    "list_agent_sharing_configs", "upsert_agent_sharing_config",
    "list_deny_rules", "create_deny_rule", "delete_deny_rule",
    "list_audit_events", "record_audit_event", "get_sharing_stats",
    "purge_sharing", "rate_lesson",
)

for _name in _STUBBED_METHODS:
    setattr(SqliteStore, _name, _stub(_name))
