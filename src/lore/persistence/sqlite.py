"""SQLite Store implementation (Phase 3 of the solo-mode design).

Phase 3A — foundation only. The class is wired through `make_store()` for
sqlite:// URLs, opens a real connection pool with the WAL pragmas the design
calls for, and applies the `migrations_sqlite/` schema. Store-protocol method
implementations land in subsequent sub-phases (3C–3F); they currently raise
`NotImplementedError`.

Spec: docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlparse

from lore.persistence.exceptions import (
    BackendUnavailableError,
    ConfigError,
    StoreError,
)

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
        self._conn = conn  # bound-connection mode (used by tests)
        self._owned_conn: Optional[Any] = None  # owned-by-store mode
        self._closed = False

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
        return store

    @classmethod
    def from_connection(cls, conn) -> "SqliteStore":
        """Bind to an externally-owned aiosqlite connection (used by tests)."""
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

    def _acquire(self):
        """Return an async context manager yielding a usable connection.

        SQLite is a single-process backend; we don't need a real pool. The
        same connection is re-used.
        """
        return _SqliteConnCtx(self._conn or self._owned_conn)


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
    # MemoryOps
    "insert_memory", "get_memory", "update_memory", "delete_memory",
    "list_memories", "recall_memories", "expire_memories",
    "bump_access_counts", "enrich_memory_meta", "vote_memory",
    "list_memories_paginated", "list_memories_with_embeddings",
    "upsert_memory_with_embedding", "import_extracted_memory",
    # GraphOps
    "create_entity", "get_entity", "list_entities", "update_entity",
    "delete_entity", "merge_entities", "search_entities",
    "create_mention", "list_mentions_for_memory", "list_mentions_for_entity",
    "create_relationship", "get_relationship", "list_relationships",
    "list_pending_relationships", "approve_relationship",
    "reject_relationship", "bulk_review_relationships",
    "delete_relationship", "list_related_memories",
    "graph_stats", "list_topics", "topic_memory_count",
    "get_topic", "list_memories_for_topic", "search_text",
    # PolicyOps
    "list_profiles", "get_profile", "create_profile",
    "update_profile", "delete_profile", "resolve_profile_for_key",
    "set_default_profile",
    # WorkspaceOps
    "list_workspaces", "get_workspace", "get_workspace_by_slug",
    "create_workspace", "update_workspace", "delete_workspace",
    "list_workspace_members", "add_workspace_member", "remove_workspace_member",
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
    "list_drill_results", "get_latest_drill_for_org",
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
