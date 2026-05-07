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
    IntegrityError,
    StoreError,
    StoreNotFoundError,
)
from lore.persistence.types import (
    DailyStatRow,
    ExportedMemory,
    MemoryFilter,
    MemoryPatch,
    NewApiKey,
    NewMember,
    NewMemory,
    NewProfile,
    NewRetrievalEvent,
    NewWorkspace,
    ProfilePatch,
    RecallParams,
    RetrievalAnalyticsResult,
    ScoreDistributionBucket,
    ScoredMemory,
    StoredApiKey,
    StoredMember,
    StoredMemory,
    StoredProfile,
    StoredWorkspace,
    TimeseriesPoint,
    TopQueryRow,
    WorkspacePatch,
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


# ── SLO metric SQL fragments (mirrors lore.persistence.postgres._METRIC_SQL) ──
#
# The percentile metrics (``p50_latency``, ``p95_latency``, ``p99_latency``,
# ``retrieval_latency_p95``) are computed via a CTE that ROW_NUMBERs over the
# ordered set and picks the row at ceil(N * pct). PG's ``percentile_cont``
# does linear interpolation between adjacent rows; the SQLite row-pick
# approximation can differ slightly on small samples — see the contract
# test ``test_compute_metric_value_p95_latency`` which uses a wide
# ``180.0 <= result <= 200.0`` tolerance band.
#
# NOTE: percentile_cont approximated via ROW_NUMBER() picking — see method
# docstrings for ``compute_metric_value`` / ``compute_metric_timeseries``.
_SQLITE_METRIC_SQL: dict[str, str] = {
    # Sentinel value "PCT::<fraction>" that the methods replace with the
    # appropriate CTE expression. Non-percentile metrics inline directly.
    "p50_latency": "PCT::0.50",
    "p95_latency": "PCT::0.95",
    "p99_latency": "PCT::0.99",
    "hit_rate": (
        "CAST(SUM(CASE WHEN results_count > 0 THEN 1 ELSE 0 END) AS REAL) "
        "/ MAX(COUNT(*), 1) AS value"
    ),
    "retrieval_latency_p95": "PCT::0.95",
    "retrieval_recall": (
        "CAST(SUM(CASE WHEN results_count > 0 THEN 1 ELSE 0 END) AS REAL) "
        "/ MAX(COUNT(*), 1) AS value"
    ),
    "uptime_pct": (
        "CAST(SUM(CASE WHEN query_time_ms IS NOT NULL THEN 1 ELSE 0 END) AS REAL) "
        "/ MAX(COUNT(*), 1) * 100.0 AS value"
    ),
}


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


def _row_to_member(row) -> StoredMember:
    """Translate a SQLite ``workspace_members`` row to ``StoredMember``."""
    return StoredMember(
        id=row["id"],
        workspace_id=row["workspace_id"],
        user_id=row["user_id"],
        role=row["role"],
        invited_at=_parse_iso(row["invited_at"]),
        accepted_at=_parse_iso(row["accepted_at"]),
    )


def _row_to_workspace(row) -> StoredWorkspace:
    """Translate a SQLite ``workspaces`` row to ``StoredWorkspace``."""
    settings_raw = row["settings"]
    if isinstance(settings_raw, str):
        settings = json.loads(settings_raw) if settings_raw else {}
    else:
        settings = settings_raw or {}
    return StoredWorkspace(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        slug=row["slug"],
        settings=dict(settings or {}),
        created_at=_parse_iso(row["created_at"]),
        archived_at=_parse_iso(row["archived_at"]),
    )


def _row_to_profile(row) -> StoredProfile:
    """Translate a SQLite ``retrieval_profiles`` row to ``StoredProfile``.

    Mirrors ``lore.persistence.postgres._row_to_profile`` but parses the
    JSON-encoded ``tier_filters`` TEXT column and the INTEGER 0/1 booleans.
    """
    tier_raw = row["tier_filters"]
    if isinstance(tier_raw, str):
        decoded = json.loads(tier_raw)
        tf: Optional[tuple] = tuple(decoded) if decoded is not None else None
    elif tier_raw is None:
        tf = None
    else:
        tf = tuple(tier_raw)
    return StoredProfile(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        semantic_weight=float(row["semantic_weight"]),
        graph_weight=float(row["graph_weight"]),
        recency_bias=float(row["recency_bias"]),
        tier_filters=tf,
        min_score=float(row["min_score"]),
        max_results=int(row["max_results"]),
        is_preset=bool(row["is_preset"]),
        k=row["k"],
        threshold=float(row["threshold"]) if row["threshold"] is not None else None,
        rerank=bool(row["rerank"]) if row["rerank"] is not None else False,
        include_graph=bool(row["include_graph"]) if row["include_graph"] is not None else True,
        created_at=_parse_iso(row["created_at"]),
        updated_at=_parse_iso(row["updated_at"]),
    )


def _row_to_api_key(row) -> StoredApiKey:
    """Translate a SQLite ``api_keys`` row to ``StoredApiKey``.

    Mirrors ``lore.persistence.postgres._row_to_api_key`` but parses ISO-8601
    TEXT timestamps and INTEGER 0/1 booleans.
    """
    return StoredApiKey(
        id=row["id"],
        org_id=row["org_id"],
        name=row["name"],
        key_hash=row["key_hash"],
        key_prefix=row["key_prefix"],
        project=row["project"],
        is_root=bool(row["is_root"]),
        workspace_id=row["workspace_id"],
        revoked_at=_parse_iso(row["revoked_at"]),
        created_at=_parse_iso(row["created_at"]),
        last_used_at=_parse_iso(row["last_used_at"]),
        role=row["role"],
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

    # ── AnalyticsOps (Phase 3E) ───────────────────────────────────────

    async def record_retrieval_event(self, event: "NewRetrievalEvent") -> None:
        """Insert a retrieval analytics event row.

        Mirrors ``PostgresStore.record_retrieval_event``: JSON-serialized
        ``scores`` / ``memory_ids`` arrays are stored as TEXT in SQLite
        instead of JSONB. ``created_at`` defaults to ``datetime('now')``
        via the column default.
        """
        async with self._acquire() as conn:
            await conn.execute(
                """
                INSERT INTO retrieval_events
                    (org_id, query, results_count, scores, memory_ids,
                     avg_score, max_score, min_score_threshold, query_time_ms,
                     project, format)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.org_id,
                    event.query,
                    event.results_count,
                    json.dumps(list(event.scores)),
                    json.dumps(list(event.memory_ids)),
                    event.avg_score,
                    event.max_score,
                    event.min_score_threshold,
                    event.query_time_ms,
                    event.project,
                    event.format,
                ),
            )
            await conn.commit()

    async def record_memory_access(
        self, org_id: str, memory_id: str
    ) -> Optional["StoredMemory"]:
        """Increment access counters and return the updated memory, or None.

        Mirrors ``PostgresStore.record_memory_access``: bumps
        ``access_count`` by 1, sets ``last_accessed_at = now()``, and
        recomputes ``importance_score`` from confidence, vote delta, and
        the (slightly damped) log of the new access count. Returns the
        updated row, or None if (id, org_id) does not match.

        SQLite has no ``UPDATE … RETURNING`` (added in 3.35; aiosqlite's
        wrapper doesn't expose it everywhere), so we issue an UPDATE and
        a SELECT inside the same connection. There is no risk of a
        concurrent writer interleaving since SQLite is single-writer.
        """
        sql_update = (
            "UPDATE memories SET "
            "access_count = COALESCE(access_count, 0) + 1, "
            "last_accessed_at = datetime('now'), "
            "importance_score = COALESCE(confidence, 1.0) "
            " * MAX(0.1, 1.0 + (COALESCE(upvotes, 0) - COALESCE(downvotes, 0)) * 0.1) "
            " * (1.0 + ln(COALESCE(access_count, 0) + 2) / ln(2) * 0.1), "
            "updated_at = datetime('now') "
            "WHERE id = ? AND org_id = ?"
        )
        async with self._acquire() as conn:
            cursor = await conn.execute(sql_update, (memory_id, org_id))
            updated = cursor.rowcount
            await cursor.close()
            await conn.commit()
            if not updated:
                return None
            async with conn.execute(
                f"SELECT {self._MEMORY_COLS} FROM memories "
                "WHERE id = ? AND org_id = ?",
                (memory_id, org_id),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_memory(row) if row else None

    async def list_recent_session_snapshots(
        self,
        org_id: str,
        *,
        project: Optional[str] = None,
        exclude_ids: Sequence[str] = (),
        limit: int = 3,
    ) -> Sequence["StoredMemory"]:
        """List the most recent session-snapshot memories for an org.

        Mirrors ``PostgresStore.list_recent_session_snapshots``:
        * ``meta->>'type' = 'session_snapshot'`` → SQLite
          ``json_extract(meta, '$.type') = 'session_snapshot'``.
        * ``created_at > now() - interval '24 hours'`` → SQLite
          ``created_at > datetime('now', '-24 hours')``.
        * ``id != ALL($N)`` → SQLite ``id NOT IN (?, ?, …)``.
        * Excludes already-expired rows (using a Python-side ISO-8601
          ``now()`` for shape parity with stored ``isoformat()`` values —
          see ``expire_memories`` for the rationale).
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        where: list[str] = [
            "org_id = ?",
            "(expires_at IS NULL OR expires_at > ?)",
            "json_extract(meta, '$.type') = 'session_snapshot'",
            "created_at > datetime('now', '-24 hours')",
        ]
        params: list[Any] = [org_id, now_iso]
        if project is not None:
            where.append("project = ?")
            params.append(project)
        if exclude_ids:
            placeholders = ",".join(["?"] * len(exclude_ids))
            where.append(f"id NOT IN ({placeholders})")
            params.extend(exclude_ids)
        params.append(limit)
        sql = (
            f"SELECT {self._MEMORY_COLS} FROM memories "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC LIMIT ?"
        )
        async with self._acquire() as conn:
            async with conn.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
        return tuple(_row_to_memory(r) for r in rows)

    async def compute_retrieval_analytics(
        self,
        *,
        org_id: str,
        days: int,
        project: Optional[str] = None,
    ) -> "RetrievalAnalyticsResult":
        """Compute aggregated retrieval analytics over the last ``days`` days.

        Mirrors ``PostgresStore.compute_retrieval_analytics``: issues seven
        small queries inside a single connection (summary, p95, score
        distribution, top queries, unique memories, total memories, daily
        stats). PG-specific bits translated:

        * ``now() - make_interval(days => $N)`` → ``datetime('now', '-N days')``
          interpolated as a literal (parameter binding does not work inside
          ``datetime(…)`` modifier strings).
        * ``percentile_cont(0.95) WITHIN GROUP (ORDER BY query_time_ms)`` →
          a CTE that ROW_NUMBERs over the ordered, non-null query_time_ms
          column and picks the row at ``CAST(N * 0.95 AS INTEGER)`` (no
          interpolation; small approximation tolerated by contract tests).
        * ``jsonb_array_elements_text(scores)`` / ``memory_ids`` → SQLite
          ``json_each(<col>)`` table-valued function (yields one row per
          array element with the element value in ``value``).
        * ``created_at::date`` → ``date(created_at)``.
        """
        # Build shared WHERE clause and params for retrieval_events queries.
        # ``datetime('now', '-N days')`` doesn't accept a bound parameter, so
        # the days value is interpolated as an int literal (validated by
        # ``int(days)`` to defuse SQL injection).
        days_int = int(days)
        where_parts = [
            "org_id = ?",
            f"created_at >= datetime('now', '-{days_int} days')",
        ]
        params: list[Any] = [org_id]
        if project is not None:
            where_parts.append("project = ?")
            params.append(project)
        where_sql = " AND ".join(where_parts)
        params_t = tuple(params)

        async with self._acquire() as conn:
            # ── Summary stats ──────────────────────────────────────
            async with conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_queries,
                    SUM(CASE WHEN results_count > 0 THEN 1 ELSE 0 END) AS queries_with_results,
                    SUM(CASE WHEN results_count = 0 THEN 1 ELSE 0 END) AS queries_empty,
                    AVG(CAST(results_count AS REAL)) AS avg_results,
                    AVG(avg_score) AS avg_score,
                    AVG(max_score) AS avg_max_score,
                    AVG(query_time_ms) AS avg_latency_ms
                FROM retrieval_events
                WHERE {where_sql}
                """,
                params_t,
            ) as cur:
                summary = await cur.fetchone()

            total = (summary["total_queries"] or 0) if summary else 0

            # ── P95 latency (CTE-based percentile pick) ────────────
            p95: Optional[float] = None
            if total > 0:
                async with conn.execute(
                    f"""
                    WITH ordered AS (
                        SELECT query_time_ms,
                               ROW_NUMBER() OVER (ORDER BY query_time_ms) AS rn,
                               COUNT(*) OVER () AS total
                        FROM retrieval_events
                        WHERE {where_sql} AND query_time_ms IS NOT NULL
                    )
                    SELECT query_time_ms FROM ordered
                    WHERE rn = MAX(1, CAST(total * 0.95 AS INTEGER))
                    LIMIT 1
                    """,
                    params_t,
                ) as cur:
                    p95_row = await cur.fetchone()
                if p95_row and p95_row["query_time_ms"] is not None:
                    p95 = round(float(p95_row["query_time_ms"]), 2)

            # ── Score distribution (jsonb_array_elements → json_each) ──
            async with conn.execute(
                f"""
                SELECT bucket, COUNT(*) AS cnt
                FROM (
                    SELECT
                        CASE
                            WHEN CAST(je.value AS REAL) < 0.3 THEN '0.0-0.3'
                            WHEN CAST(je.value AS REAL) < 0.5 THEN '0.3-0.5'
                            WHEN CAST(je.value AS REAL) < 0.7 THEN '0.5-0.7'
                            WHEN CAST(je.value AS REAL) < 0.9 THEN '0.7-0.9'
                            ELSE '0.9-1.0'
                        END AS bucket
                    FROM retrieval_events,
                         json_each(retrieval_events.scores) AS je
                    WHERE {where_sql}
                ) sub
                GROUP BY bucket
                ORDER BY bucket
                """,
                params_t,
            ) as cur:
                score_dist_rows = await cur.fetchall()

            buckets_order = ["0.0-0.3", "0.3-0.5", "0.5-0.7", "0.7-0.9", "0.9-1.0"]
            bucket_counts = {r["bucket"]: r["cnt"] for r in score_dist_rows}
            score_distribution = [
                ScoreDistributionBucket(bucket=b, count=bucket_counts.get(b, 0))
                for b in buckets_order
            ]

            # ── Top queries ────────────────────────────────────────
            async with conn.execute(
                f"""
                SELECT query, COUNT(*) AS cnt, AVG(avg_score) AS avg_s
                FROM retrieval_events
                WHERE {where_sql}
                GROUP BY query
                ORDER BY cnt DESC
                LIMIT 10
                """,
                params_t,
            ) as cur:
                top_rows = await cur.fetchall()
            top_queries = [
                TopQueryRow(
                    query=r["query"],
                    count=r["cnt"],
                    avg_score=round(float(r["avg_s"]), 4) if r["avg_s"] is not None else None,
                )
                for r in top_rows
            ]

            # ── Unique memories retrieved (json_each over memory_ids) ──
            async with conn.execute(
                f"""
                SELECT COUNT(DISTINCT je.value) AS unique_count
                FROM retrieval_events,
                     json_each(retrieval_events.memory_ids) AS je
                WHERE {where_sql}
                """,
                params_t,
            ) as cur:
                unique_row = await cur.fetchone()
            unique_memories = (unique_row["unique_count"] or 0) if unique_row else 0

            # ── Total memories (no date filter, ignores expired)
            mem_where_parts = ["org_id = ?"]
            mem_params: list[Any] = [org_id]
            if project is not None:
                mem_where_parts.append("project = ?")
                mem_params.append(project)
            mem_where_sql = " AND ".join(mem_where_parts)

            async with conn.execute(
                f"SELECT COUNT(*) AS total FROM memories WHERE {mem_where_sql}",
                tuple(mem_params),
            ) as cur:
                total_memories_row = await cur.fetchone()
            total_memories = (total_memories_row["total"] or 0) if total_memories_row else 0

            # ── Daily stats ────────────────────────────────────────
            async with conn.execute(
                f"""
                SELECT
                    date(created_at) AS day,
                    COUNT(*) AS queries,
                    AVG(avg_score) AS avg_s,
                    CAST(SUM(CASE WHEN results_count > 0 THEN 1 ELSE 0 END) AS REAL)
                        / MAX(COUNT(*), 1) AS hit_rate
                FROM retrieval_events
                WHERE {where_sql}
                GROUP BY day
                ORDER BY day DESC
                """,
                params_t,
            ) as cur:
                daily_rows = await cur.fetchall()
            daily_stats = [
                DailyStatRow(
                    date=str(r["day"]),
                    queries=r["queries"],
                    avg_score=round(float(r["avg_s"]), 4) if r["avg_s"] is not None else None,
                    hit_rate=round(float(r["hit_rate"]), 4) if r["hit_rate"] is not None else 0.0,
                )
                for r in daily_rows
            ]

        avg_score_v = summary["avg_score"] if summary else None
        avg_max_v = summary["avg_max_score"] if summary else None
        avg_lat_v = summary["avg_latency_ms"] if summary else None
        return RetrievalAnalyticsResult(
            total_queries=total,
            queries_with_results=(summary["queries_with_results"] or 0) if summary else 0,
            queries_empty=(summary["queries_empty"] or 0) if summary else 0,
            avg_results_per_query=round(float(summary["avg_results"] or 0), 2) if summary else 0.0,
            avg_score=round(float(avg_score_v), 4) if avg_score_v is not None else None,
            avg_max_score=round(float(avg_max_v), 4) if avg_max_v is not None else None,
            avg_latency_ms=round(float(avg_lat_v), 2) if avg_lat_v is not None else None,
            p95_latency_ms=p95,
            score_distribution=score_distribution,
            top_queries=top_queries,
            unique_memories_retrieved=unique_memories,
            total_memories=total_memories,
            daily_stats=daily_stats,
        )

    async def compute_metric_value(
        self,
        *,
        org_id: str,
        metric: str,
        window_minutes: int,
    ) -> Optional[float]:
        """Compute a single metric value over the last ``window_minutes``.

        Mirrors ``PostgresStore.compute_metric_value``. Percentile metrics
        (``p50_latency`` / ``p95_latency`` / ``p99_latency`` /
        ``retrieval_latency_p95``) take the CTE+ROW_NUMBER pick described
        on ``_SQLITE_METRIC_SQL``.

        Returns None if the window is empty (no rows match).
        """
        if metric not in _SQLITE_METRIC_SQL:
            raise ValueError(f"Unknown metric: {metric}")
        metric_sql = _SQLITE_METRIC_SQL[metric]
        # ``datetime('now', '-N minutes')`` doesn't accept a bound param,
        # so the int is interpolated after coercion.
        window = int(window_minutes)
        where_sql = (
            "org_id = ? AND created_at >= datetime('now', "
            f"'-{window} minutes')"
        )
        async with self._acquire() as conn:
            if metric_sql.startswith("PCT::"):
                pct = float(metric_sql.split("::", 1)[1])
                async with conn.execute(
                    f"""
                    WITH ordered AS (
                        SELECT query_time_ms,
                               ROW_NUMBER() OVER (ORDER BY query_time_ms) AS rn,
                               COUNT(*) OVER () AS total
                        FROM retrieval_events
                        WHERE {where_sql} AND query_time_ms IS NOT NULL
                    )
                    SELECT query_time_ms AS value FROM ordered
                    WHERE rn = MAX(1, CAST(total * ? AS INTEGER))
                    LIMIT 1
                    """,
                    (org_id, pct),
                ) as cur:
                    row = await cur.fetchone()
            else:
                # Non-percentile: hit_rate / retrieval_recall / uptime_pct.
                # COUNT(*) returns 0 (not NULL) on an empty set, so a guard
                # check using a separate COUNT distinguishes "0.0 because
                # the predicate filtered everything" from "no rows at all".
                async with conn.execute(
                    f"SELECT COUNT(*) AS n FROM retrieval_events WHERE {where_sql}",
                    (org_id,),
                ) as cur:
                    n_row = await cur.fetchone()
                if not n_row or (n_row["n"] or 0) == 0:
                    return None
                async with conn.execute(
                    f"SELECT {metric_sql} FROM retrieval_events WHERE {where_sql}",
                    (org_id,),
                ) as cur:
                    row = await cur.fetchone()
        if row and row["value"] is not None:
            return round(float(row["value"]), 4)
        return None

    async def compute_metric_timeseries(
        self,
        *,
        org_id: str,
        metric: str,
        window_hours: int,
        bucket_minutes: int,
    ) -> Sequence["TimeseriesPoint"]:
        """Compute a bucketed metric timeseries for SLO charts.

        Mirrors ``PostgresStore.compute_metric_timeseries``. Bucket math:
        truncate the unix epoch of ``created_at`` to a multiple of
        ``bucket_minutes * 60`` seconds, then convert back to a TEXT
        timestamp via ``datetime(<seconds>, 'unixepoch')``.

        Percentile metrics use the same CTE+ROW_NUMBER pick as
        ``compute_metric_value`` but per-bucket via ``GROUP BY bucket``
        in a sub-CTE — straightforward in SQL.
        """
        if metric not in _SQLITE_METRIC_SQL:
            raise ValueError(f"Unknown metric: {metric}")
        metric_sql = _SQLITE_METRIC_SQL[metric]
        hours = int(window_hours)
        bucket_secs = int(bucket_minutes) * 60
        bucket_expr = (
            f"datetime((CAST(strftime('%s', created_at) AS INTEGER) / "
            f"{bucket_secs}) * {bucket_secs}, 'unixepoch')"
        )
        where_sql = (
            f"org_id = ? AND created_at >= datetime('now', '-{hours} hours')"
        )

        async with self._acquire() as conn:
            if metric_sql.startswith("PCT::"):
                pct = float(metric_sql.split("::", 1)[1])
                # Per-bucket CTE: rank rows within each bucket, then pick
                # the row at MAX(1, CAST(bucket_total * pct AS INTEGER)).
                async with conn.execute(
                    f"""
                    WITH bucketed AS (
                        SELECT {bucket_expr} AS bucket,
                               query_time_ms,
                               ROW_NUMBER() OVER (
                                   PARTITION BY {bucket_expr}
                                   ORDER BY query_time_ms
                               ) AS rn,
                               COUNT(*) OVER (PARTITION BY {bucket_expr}) AS total
                        FROM retrieval_events
                        WHERE {where_sql} AND query_time_ms IS NOT NULL
                    )
                    SELECT bucket, query_time_ms AS value FROM bucketed
                    WHERE rn = MAX(1, CAST(total * ? AS INTEGER))
                    ORDER BY bucket
                    """,
                    (org_id, pct),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with conn.execute(
                    f"""
                    SELECT {bucket_expr} AS bucket, {metric_sql}
                    FROM retrieval_events
                    WHERE {where_sql}
                    GROUP BY bucket
                    ORDER BY bucket
                    """,
                    (org_id,),
                ) as cur:
                    rows = await cur.fetchall()

        return tuple(
            TimeseriesPoint(
                timestamp=_parse_iso(r["bucket"]),
                value=round(float(r["value"]), 4) if r["value"] is not None else None,
            )
            for r in rows
        )

    # ── PolicyOps (Phase 3F) ──────────────────────────────────────────

    _PROFILE_COLS = (
        "id, org_id, name, "
        "semantic_weight, graph_weight, recency_bias, "
        "tier_filters, min_score, max_results, is_preset, "
        "k, threshold, rerank, include_graph, "
        "created_at, updated_at"
    )

    async def get_profile(self, profile_id: str) -> Optional[StoredProfile]:
        """Return a profile by id, or None.

        Mirrors ``PostgresStore.get_profile``.
        """
        async with self._acquire() as conn:
            async with conn.execute(
                f"SELECT {self._PROFILE_COLS} FROM retrieval_profiles WHERE id = ?",
                (profile_id,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_profile(row) if row else None

    async def get_profile_by_name(
        self, org_id: str, name: str
    ) -> Optional[StoredProfile]:
        """Return the profile matching (org_id, name), or None.

        Mirrors ``PostgresStore.get_profile_by_name``.
        """
        async with self._acquire() as conn:
            async with conn.execute(
                f"SELECT {self._PROFILE_COLS} FROM retrieval_profiles "
                "WHERE name = ? AND org_id = ?",
                (name, org_id),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_profile(row) if row else None

    async def list_profiles(self, org_id: str) -> Sequence[StoredProfile]:
        """List org-owned + global profiles, ordered by name.

        Mirrors ``PostgresStore.list_profiles`` — matches rows where
        ``org_id = ? OR org_id = '__global__'``.
        """
        async with self._acquire() as conn:
            async with conn.execute(
                f"SELECT {self._PROFILE_COLS} FROM retrieval_profiles "
                "WHERE org_id = ? OR org_id = '__global__' "
                "ORDER BY name",
                (org_id,),
            ) as cur:
                rows = await cur.fetchall()
        return tuple(_row_to_profile(r) for r in rows)

    async def create_profile(self, profile: NewProfile) -> StoredProfile:
        """Insert a new profile; raises IntegrityError on (org_id, name) collision.

        Mirrors ``PostgresStore.create_profile``: generates a ``prof_<ULID>``
        id and returns the freshly inserted row as ``StoredProfile``.
        """
        profile_id = f"prof_{ULID()}"
        tier_filters_json = (
            json.dumps(list(profile.tier_filters))
            if profile.tier_filters is not None
            else None
        )
        async with self._acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO retrieval_profiles
                        (id, org_id, name,
                         semantic_weight, graph_weight, recency_bias,
                         tier_filters, min_score, max_results, is_preset,
                         k, threshold, rerank, include_graph)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        profile.org_id,
                        profile.name,
                        profile.semantic_weight,
                        profile.graph_weight,
                        profile.recency_bias,
                        tier_filters_json,
                        profile.min_score,
                        profile.max_results,
                        1 if profile.is_preset else 0,
                        profile.k,
                        profile.threshold,
                        1 if profile.rerank else 0,
                        1 if profile.include_graph else 0,
                    ),
                )
                await conn.commit()
            except aiosqlite.IntegrityError as e:
                raise IntegrityError(
                    f"Profile name {profile.name!r} already exists for org_id={profile.org_id!r}"
                ) from e
            async with conn.execute(
                f"SELECT {self._PROFILE_COLS} FROM retrieval_profiles WHERE id = ?",
                (profile_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:  # pragma: no cover
            raise StoreError("create_profile: row vanished after insert")
        return _row_to_profile(row)

    async def delete_profile(self, profile_id: str, org_id: str) -> bool:
        """Delete a profile scoped to (id, org_id); returns True if removed.

        Mirrors ``PostgresStore.delete_profile``.
        """
        async with self._acquire() as conn:
            cursor = await conn.execute(
                "DELETE FROM retrieval_profiles WHERE id = ? AND org_id = ?",
                (profile_id, org_id),
            )
            count = cursor.rowcount
            await cursor.close()
            await conn.commit()
        return count > 0

    async def update_profile(
        self, profile_id: str, patch: ProfilePatch
    ) -> Optional[StoredProfile]:
        """Apply a patch and return the updated row, or None if absent.

        Mirrors ``PostgresStore.update_profile``: builds a dynamic SET
        clause from non-None patch fields. Empty patches raise
        ``ValueError``. ``tier_filters`` is JSON-encoded; INTEGER 0/1
        booleans get coerced.
        """
        sets: list[str] = []
        params: list = []

        if patch.name is not None:
            params.append(patch.name)
            sets.append("name = ?")
        if patch.semantic_weight is not None:
            params.append(patch.semantic_weight)
            sets.append("semantic_weight = ?")
        if patch.graph_weight is not None:
            params.append(patch.graph_weight)
            sets.append("graph_weight = ?")
        if patch.recency_bias is not None:
            params.append(patch.recency_bias)
            sets.append("recency_bias = ?")
        if patch.tier_filters is not None:
            params.append(json.dumps(list(patch.tier_filters)))
            sets.append("tier_filters = ?")
        if patch.min_score is not None:
            params.append(patch.min_score)
            sets.append("min_score = ?")
        if patch.max_results is not None:
            params.append(patch.max_results)
            sets.append("max_results = ?")
        if patch.is_preset is not None:
            params.append(1 if patch.is_preset else 0)
            sets.append("is_preset = ?")
        if patch.k is not None:
            params.append(patch.k)
            sets.append("k = ?")
        if patch.threshold is not None:
            params.append(patch.threshold)
            sets.append("threshold = ?")
        if patch.rerank is not None:
            params.append(1 if patch.rerank else 0)
            sets.append("rerank = ?")
        if patch.include_graph is not None:
            params.append(1 if patch.include_graph else 0)
            sets.append("include_graph = ?")

        if not sets:
            raise ValueError(
                "update_profile called with empty patch — caller must ensure at least one field is set"
            )

        sets.append("updated_at = datetime('now')")
        params.append(profile_id)
        sql = (
            "UPDATE retrieval_profiles "
            f"SET {', '.join(sets)} "
            "WHERE id = ?"
        )
        async with self._acquire() as conn:
            cursor = await conn.execute(sql, params)
            updated = cursor.rowcount
            await cursor.close()
            await conn.commit()
            if not updated:
                return None
            async with conn.execute(
                f"SELECT {self._PROFILE_COLS} FROM retrieval_profiles WHERE id = ?",
                (profile_id,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_profile(row) if row else None

    async def resolve_profile_for_key(
        self, org_id: str, name: str
    ) -> Optional[StoredProfile]:
        """Resolve effective profile for (org_id, name).

        Mirrors ``PostgresStore.resolve_profile_for_key``: matches rows
        where ``name = ? AND (org_id = ? OR org_id = '__global__')``,
        ordered so the org-owned row wins on ties (returns the org-owned
        match if present, otherwise the ``__global__`` preset).
        """
        async with self._acquire() as conn:
            async with conn.execute(
                f"SELECT {self._PROFILE_COLS} FROM retrieval_profiles "
                "WHERE name = ? AND (org_id = ? OR org_id = '__global__') "
                "ORDER BY CASE WHEN org_id = ? THEN 0 ELSE 1 END "
                "LIMIT 1",
                (name, org_id, org_id),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_profile(row) if row else None

    # ── WorkspaceOps (Phase 3F) ───────────────────────────────────────

    _WORKSPACE_COLS = (
        "id, org_id, name, slug, settings, created_at, archived_at"
    )

    async def get_workspace(
        self, workspace_id: str, org_id: str
    ) -> Optional[StoredWorkspace]:
        """Return a workspace by (id, org_id), or None.

        Mirrors ``PostgresStore.get_workspace``.
        """
        async with self._acquire() as conn:
            async with conn.execute(
                f"SELECT {self._WORKSPACE_COLS} FROM workspaces "
                "WHERE id = ? AND org_id = ?",
                (workspace_id, org_id),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_workspace(row) if row else None

    async def list_workspaces(
        self, org_id: str, *, include_archived: bool = False
    ) -> Sequence[StoredWorkspace]:
        """List workspaces for an org; archived excluded by default.

        Mirrors ``PostgresStore.list_workspaces``.
        """
        if include_archived:
            sql = (
                f"SELECT {self._WORKSPACE_COLS} FROM workspaces "
                "WHERE org_id = ? ORDER BY name"
            )
            params: tuple = (org_id,)
        else:
            sql = (
                f"SELECT {self._WORKSPACE_COLS} FROM workspaces "
                "WHERE org_id = ? AND archived_at IS NULL ORDER BY name"
            )
            params = (org_id,)
        async with self._acquire() as conn:
            async with conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return tuple(_row_to_workspace(r) for r in rows)

    async def create_workspace(self, ws: NewWorkspace) -> StoredWorkspace:
        """Insert a new workspace; raises IntegrityError on (org_id, slug) collision.

        Mirrors ``PostgresStore.create_workspace``.
        """
        workspace_id = f"ws_{ULID()}"
        async with self._acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO workspaces (id, org_id, name, slug, settings)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        ws.org_id,
                        ws.name,
                        ws.slug,
                        json.dumps(dict(ws.settings)),
                    ),
                )
                await conn.commit()
            except aiosqlite.IntegrityError as e:
                raise IntegrityError(
                    f"Workspace slug {ws.slug!r} already exists for org_id={ws.org_id!r}"
                ) from e
            async with conn.execute(
                f"SELECT {self._WORKSPACE_COLS} FROM workspaces WHERE id = ?",
                (workspace_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:  # pragma: no cover
            raise StoreError("create_workspace: row vanished after insert")
        return _row_to_workspace(row)

    async def update_workspace(
        self, workspace_id: str, org_id: str, patch: WorkspacePatch
    ) -> Optional[StoredWorkspace]:
        """Apply a patch and return the updated row, or None if absent.

        Mirrors ``PostgresStore.update_workspace``: builds a dynamic SET
        clause from non-None patch fields. Empty patches raise ``ValueError``.
        """
        sets: list[str] = []
        params: list = []

        if patch.name is not None:
            params.append(patch.name)
            sets.append("name = ?")
        if patch.settings is not None:
            params.append(json.dumps(dict(patch.settings)))
            sets.append("settings = ?")

        if not sets:
            raise ValueError(
                "update_workspace called with empty patch — caller must ensure at least one field is set"
            )

        params.extend([workspace_id, org_id])
        sql = (
            "UPDATE workspaces "
            f"SET {', '.join(sets)} "
            "WHERE id = ? AND org_id = ?"
        )
        async with self._acquire() as conn:
            cursor = await conn.execute(sql, params)
            updated = cursor.rowcount
            await cursor.close()
            await conn.commit()
            if not updated:
                return None
            async with conn.execute(
                f"SELECT {self._WORKSPACE_COLS} FROM workspaces "
                "WHERE id = ? AND org_id = ?",
                (workspace_id, org_id),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_workspace(row) if row else None

    async def archive_workspace(self, workspace_id: str, org_id: str) -> bool:
        """Mark a workspace archived; returns True if a row transitioned.

        Mirrors ``PostgresStore.archive_workspace`` — the ``archived_at IS NULL``
        guard makes a no-op on already-archived workspaces and returns False.
        """
        async with self._acquire() as conn:
            cursor = await conn.execute(
                "UPDATE workspaces SET archived_at = datetime('now') "
                "WHERE id = ? AND org_id = ? AND archived_at IS NULL",
                (workspace_id, org_id),
            )
            count = cursor.rowcount
            await cursor.close()
            await conn.commit()
        return count > 0

    _MEMBER_COLS = (
        "id, workspace_id, user_id, role, invited_at, accepted_at"
    )

    async def add_workspace_member(self, member: NewMember) -> StoredMember:
        """Add a member; raises IntegrityError on FK violation (workspace_id).

        Mirrors ``PostgresStore.add_workspace_member``.
        """
        member_id = f"wsm_{ULID()}"
        async with self._acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO workspace_members (id, workspace_id, user_id, role)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        member_id,
                        member.workspace_id,
                        member.user_id,
                        member.role,
                    ),
                )
                await conn.commit()
            except aiosqlite.IntegrityError as e:
                raise IntegrityError(
                    f"workspace_id {member.workspace_id!r} does not exist"
                ) from e
            async with conn.execute(
                f"SELECT {self._MEMBER_COLS} FROM workspace_members WHERE id = ?",
                (member_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:  # pragma: no cover
            raise StoreError("add_workspace_member: row vanished after insert")
        return _row_to_member(row)

    async def list_workspace_members(
        self, workspace_id: str
    ) -> Sequence[StoredMember]:
        """List members of a workspace, ordered by invited_at ascending.

        Mirrors ``PostgresStore.list_workspace_members``.
        """
        async with self._acquire() as conn:
            async with conn.execute(
                f"SELECT {self._MEMBER_COLS} FROM workspace_members "
                "WHERE workspace_id = ? ORDER BY invited_at",
                (workspace_id,),
            ) as cur:
                rows = await cur.fetchall()
        return tuple(_row_to_member(r) for r in rows)

    async def update_workspace_member_role(
        self, workspace_id: str, user_id: str, role: str
    ) -> Optional[StoredMember]:
        """Update a member's role; returns the updated row, or None if absent.

        Mirrors ``PostgresStore.update_workspace_member_role``.
        """
        async with self._acquire() as conn:
            cursor = await conn.execute(
                "UPDATE workspace_members SET role = ? "
                "WHERE workspace_id = ? AND user_id = ?",
                (role, workspace_id, user_id),
            )
            updated = cursor.rowcount
            await cursor.close()
            await conn.commit()
            if not updated:
                return None
            async with conn.execute(
                f"SELECT {self._MEMBER_COLS} FROM workspace_members "
                "WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_member(row) if row else None

    async def remove_workspace_member(
        self, workspace_id: str, user_id: str
    ) -> bool:
        """Remove a member; returns True if a row was deleted.

        Mirrors ``PostgresStore.remove_workspace_member``.
        """
        async with self._acquire() as conn:
            cursor = await conn.execute(
                "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            )
            count = cursor.rowcount
            await cursor.close()
            await conn.commit()
        return count > 0

    # ── AuthOps (Phase 3G) ────────────────────────────────────────────

    _API_KEY_COLS = (
        "id, org_id, name, key_hash, key_prefix, project, is_root, "
        "workspace_id, revoked_at, created_at, last_used_at, role"
    )

    async def get_api_key(self, key_id: str) -> Optional[StoredApiKey]:
        """Return an API key by id, or None if absent.

        Mirrors ``PostgresStore.get_api_key``.
        """
        async with self._acquire() as conn:
            async with conn.execute(
                f"SELECT {self._API_KEY_COLS} FROM api_keys WHERE id = ?",
                (key_id,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_api_key(row) if row else None

    async def list_api_keys(self, org_id: str) -> Sequence[StoredApiKey]:
        """List all API keys for an org, ordered by created_at ASC.

        Mirrors ``PostgresStore.list_api_keys``.
        """
        async with self._acquire() as conn:
            async with conn.execute(
                f"SELECT {self._API_KEY_COLS} FROM api_keys "
                "WHERE org_id = ? ORDER BY created_at",
                (org_id,),
            ) as cur:
                rows = await cur.fetchall()
        return tuple(_row_to_api_key(r) for r in rows)

    async def create_api_key(self, key: NewApiKey) -> StoredApiKey:
        """Insert a new API key; returns the stored row.

        Mirrors ``PostgresStore.create_api_key``: caller-side ULID with a
        ``key_`` prefix, and SQLite's column DEFAULT supplies ``created_at``.
        """
        key_id = f"key_{ULID()}"
        async with self._acquire() as conn:
            await conn.execute(
                """
                INSERT INTO api_keys
                    (id, org_id, name, key_hash, key_prefix, project, is_root, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_id,
                    key.org_id,
                    key.name,
                    key.key_hash,
                    key.key_prefix,
                    key.project,
                    1 if key.is_root else 0,
                    key.workspace_id,
                ),
            )
            await conn.commit()
            async with conn.execute(
                f"SELECT {self._API_KEY_COLS} FROM api_keys WHERE id = ?",
                (key_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:  # pragma: no cover - defensive
            raise StoreError("create_api_key: row vanished after insert")
        return _row_to_api_key(row)

    async def revoke_api_key(self, key_id: str) -> Optional[StoredApiKey]:
        """Revoke an API key; returns the updated row, or None if absent / already revoked.

        SQLite has no ``UPDATE … RETURNING`` we rely on, so we issue an
        UPDATE and a follow-up SELECT inside the same connection — same
        single-writer guarantee as elsewhere in this module.
        """
        async with self._acquire() as conn:
            cursor = await conn.execute(
                "UPDATE api_keys SET revoked_at = datetime('now') "
                "WHERE id = ? AND revoked_at IS NULL",
                (key_id,),
            )
            updated = cursor.rowcount
            await cursor.close()
            await conn.commit()
            if not updated:
                return None
            async with conn.execute(
                f"SELECT {self._API_KEY_COLS} FROM api_keys WHERE id = ?",
                (key_id,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_api_key(row) if row else None

    async def count_active_root_keys(self, org_id: str) -> int:
        """Count active (non-revoked) root-level API keys for an org.

        Mirrors ``PostgresStore.count_active_root_keys`` — ``is_root`` is
        stored as INTEGER 1/0 in SQLite so the predicate uses ``= 1``.
        """
        async with self._acquire() as conn:
            async with conn.execute(
                "SELECT COUNT(*) AS cnt FROM api_keys "
                "WHERE org_id = ? AND is_root = 1 AND revoked_at IS NULL",
                (org_id,),
            ) as cur:
                row = await cur.fetchone()
        return int(row["cnt"]) if row else 0

    async def lookup_api_key_by_hash(self, key_hash: str) -> Optional[StoredApiKey]:
        """Return the API key matching a sha256 ``key_hash``, or None.

        Hot path: every authenticated request lands here on cache miss.
        Mirrors ``PostgresStore.lookup_api_key_by_hash``.
        """
        async with self._acquire() as conn:
            async with conn.execute(
                f"SELECT {self._API_KEY_COLS} FROM api_keys WHERE key_hash = ?",
                (key_hash,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_api_key(row) if row else None

    async def touch_api_key_last_used(self, key_id: str) -> None:
        """Bump ``last_used_at`` to now for an API key.

        Fire-and-forget: missing ids do not raise. Mirrors
        ``PostgresStore.touch_api_key_last_used``.
        """
        async with self._acquire() as conn:
            await conn.execute(
                "UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?",
                (key_id,),
            )
            await conn.commit()


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
    # PolicyOps — implemented in Phase 3F.
    # WorkspaceOps — implemented in Phase 3F.
    # AuthOps — implemented in Phase 3G.
    # AnalyticsOps — implemented in Phase 3E.
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
