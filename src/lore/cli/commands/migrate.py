"""``lore migrate`` — bidirectional data migration between two Store backends.

Phase 5 of the SQLite solo-mode design. This is the one place in the codebase
that legitimately uses raw SQL outside of ``lore.persistence.{postgres,sqlite}``
because we need bit-exact row copies — server-generated IDs and timestamps
must survive the migration unchanged. The Store protocol's ``create_*``
methods all generate fresh IDs/timestamps and would break that invariant.

Wire format follows the spec: stream rows table-by-table in dependency
order, batch INSERTs of ``--batch-size`` (default 500), with JSON columns
encoded as text strings, booleans coerced to 0/1, and timestamps preserved
as ISO-8601. The ``memories`` ⇆ ``memory_vectors`` pair is handled
specially on the SQLite target side (vec0 row inserted in the same
``BEGIN IMMEDIATE`` as the base row).

Resumability: a state file at ``~/.lore/migrate-state.json`` tracks
``{table: rows_copied}`` keyed by the (source, target) URL pair. On
``--continue``, tables whose target row count already matches the source
are skipped wholesale.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Embedding dimension is fixed at 384 across the codebase
# (see migrations/001_initial.sql and lore.embed defaults). The migrate
# command compares both sides defensively so a future model swap is
# detected before any copy starts.
EMBED_DIM = 384

DEFAULT_BATCH_SIZE = 500

# Migration order: parents before children so FK targets exist when
# children are inserted. Mirrors the table list in the Phase 5 spec.
TABLE_ORDER: tuple[str, ...] = (
    "orgs",
    "users",
    "workspaces",
    "workspace_members",
    "api_keys",
    "retrieval_profiles",
    "memories",
    "entities",
    "entity_mentions",
    "relationships",
    "rejected_patterns",
    "review_decisions",
    "retention_policies",
    "snapshot_metadata",
    "restore_drill_results",
    "slo_definitions",
    "slo_alerts",
    "sharing_config",
    "agent_sharing_config",
    "deny_list_rules",
    "sharing_audit",
    "audit_log",
    "retrieval_events",
    "recommendation_config",
    "recommendation_feedback",
    "conversation_jobs",
)

# Tables whose ``id`` is an autoincrement integer (not a ULID); on the
# target side we let the engine assign new ids unless the source id is
# preserved as a regular insert. We still INSERT with the source id so
# FK references stay valid; the autoincrement counter advances on demand.
INTEGER_ID_TABLES = frozenset({"audit_log", "retrieval_events", "slo_alerts"})

# JSONB / JSON columns by table — values may arrive from PG as Python
# dict/list (asyncpg auto-decodes) or as already-encoded strings; SQLite
# always returns strings. The target write path normalizes to a JSON
# string on the SQLite side and to a Python value on the PG side.
JSON_COLUMNS: dict[str, frozenset[str]] = {
    "memories": frozenset({"tags", "meta", "quality_signals"}),
    "entities": frozenset({"aliases", "metadata"}),
    "relationships": frozenset({"properties"}),
    "agent_sharing_config": frozenset({"categories"}),
    "audit_log": frozenset({"metadata"}),
    "conversation_jobs": frozenset(),  # message_ids/messages_json are TEXT
    "retention_policies": frozenset({"retention_window"}),
    "slo_alerts": frozenset({"dispatched_to"}),
    "slo_definitions": frozenset({"alert_channels"}),
    "retrieval_events": frozenset({"scores", "memory_ids"}),
    "workspaces": frozenset({"settings"}),
}

# Boolean columns by table — PG returns Python bool, SQLite stores 0/1.
# We coerce both directions so round-trips are stable.
BOOL_COLUMNS: dict[str, frozenset[str]] = {
    "api_keys": frozenset({"is_root"}),
    "deny_list_rules": frozenset({"is_regex"}),
    "agent_sharing_config": frozenset({"enabled"}),
    "sharing_config": frozenset({"enabled", "human_review_enabled"}),
    "snapshot_metadata": frozenset({"encrypted"}),
    "retention_policies": frozenset({"encryption_required", "is_active"}),
    "slo_definitions": frozenset({"enabled"}),
    "recommendation_config": frozenset({"enabled"}),
    "retrieval_profiles": frozenset({"is_preset", "rerank", "include_graph"}),
}


def _state_path() -> Path:
    return Path.home() / ".lore" / "migrate-state.json"


def _state_key(src_url: str, tgt_url: str) -> str:
    raw = f"{src_url}\n{tgt_url}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _load_state(src_url: str, tgt_url: str) -> dict[str, int]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        all_state = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(all_state.get(_state_key(src_url, tgt_url), {}))


def _save_state(src_url: str, tgt_url: str, state: dict[str, int]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    all_state: dict[str, dict[str, int]]
    if p.exists():
        try:
            all_state = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            all_state = {}
    else:
        all_state = {}
    all_state[_state_key(src_url, tgt_url)] = state
    p.write_text(json.dumps(all_state, indent=2))


def _scheme(url: str) -> str:
    return urlparse(url).scheme.lower()


def _is_pg(url: str) -> bool:
    return _scheme(url) in ("postgres", "postgresql")


def _is_sqlite(url: str) -> bool:
    return _scheme(url) == "sqlite"


# ── Schema-version probes ──────────────────────────────────────────────


_MIGRATION_FILE_RE = re.compile(r"^(\d{3})_.+\.sql$")


def _migration_versions_for_url(url: str) -> set[str]:
    """Return the set of migration version numbers expected for a URL.

    Both backends ship parallel migration trees: ``migrations/`` for PG and
    ``migrations_sqlite/`` for SQLite. We compute the *expected* version
    set from the on-disk migrations directory (relative to the package
    root) — this is what a fresh Store would apply when opened. The
    actual applied set is queried separately from each side's
    ``schema_migrations`` table (when present).
    """
    project_root = Path(__file__).parent.parent.parent.parent.parent
    sub = "migrations" if _is_pg(url) else "migrations_sqlite"
    mdir = project_root / sub
    if not mdir.exists():
        return set()
    out: set[str] = set()
    for p in mdir.iterdir():
        m = _MIGRATION_FILE_RE.match(p.name)
        if m:
            out.add(m.group(1))
    return out


async def _applied_versions_pg(conn) -> Optional[set[str]]:
    """Return the applied schema-migrations set for a Postgres connection.

    Returns ``None`` if the table is absent — Postgres' ``run_migrations``
    in ``lore/server/db.py`` doesn't currently track applied versions, so
    a fresh Postgres test DB will not have this table.
    """
    row = await conn.fetchrow(
        "SELECT to_regclass('public.schema_migrations') AS t"
    )
    if row is None or row["t"] is None:
        return None
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {r["version"] for r in rows}


async def _applied_versions_sqlite(conn) -> Optional[set[str]]:
    """Return the applied schema-migrations set for an aiosqlite connection."""
    async with conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='schema_migrations'"
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    out: set[str] = set()
    async with conn.execute("SELECT version FROM schema_migrations") as cur:
        async for r in cur:
            out.add(r[0] if not hasattr(r, "keys") else r["version"])
    return out


# ── Type adaptation ────────────────────────────────────────────────────


def _adapt_for_sqlite(table: str, col: str, value: Any) -> Any:
    """Coerce a PG-shaped value into something aiosqlite can bind."""
    if value is None:
        return None
    json_cols = JSON_COLUMNS.get(table, frozenset())
    bool_cols = BOOL_COLUMNS.get(table, frozenset())
    if col in json_cols:
        if isinstance(value, str):
            return value
        return json.dumps(value)
    if col in bool_cols:
        return 1 if value else 0
    if col == "tier_filters" and table == "retrieval_profiles":
        # PG: TEXT[] (asyncpg returns list); SQLite: stored as TEXT JSON.
        if isinstance(value, list):
            return json.dumps(value)
        return value
    if col == "ip_address":
        return str(value) if value else None
    # Timestamps: asyncpg returns datetime objects. SQLite stores TEXT
    # ISO-8601 (the schema uses ``datetime('now')`` defaults). Convert
    # via .isoformat() for stable round-tripping.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _adapt_for_pg(table: str, col: str, value: Any) -> Any:
    """Coerce a SQLite-shaped value into something asyncpg can bind."""
    if value is None:
        return None
    json_cols = JSON_COLUMNS.get(table, frozenset())
    bool_cols = BOOL_COLUMNS.get(table, frozenset())
    if col in json_cols:
        # PG jsonb expects either a JSON string or a Python dict/list;
        # we'll bind as a JSON string and cast with ``::jsonb`` in SQL.
        if isinstance(value, str):
            return value
        return json.dumps(value)
    if col in bool_cols:
        return bool(value)
    if col == "tier_filters" and table == "retrieval_profiles":
        # SQLite stores JSON; PG expects TEXT[].
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return value
    return value


# ── Per-backend table I/O ──────────────────────────────────────────────


async def _list_columns_pg(conn, table: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=$1 "
        "ORDER BY ordinal_position",
        table,
    )
    return [r["column_name"] for r in rows]


async def _list_columns_sqlite(conn, table: str) -> list[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return [r["name"] for r in rows]


async def _table_exists_pg(conn, table: str) -> bool:
    row = await conn.fetchrow(
        "SELECT to_regclass($1) AS t",
        f"public.{table}",
    )
    return bool(row and row["t"] is not None)


async def _table_exists_sqlite(conn, table: str) -> bool:
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ) as cur:
        return (await cur.fetchone()) is not None


async def _row_count_pg(conn, table: str) -> int:
    row = await conn.fetchrow(f"SELECT COUNT(*) AS c FROM {table}")
    return int(row["c"])


async def _row_count_sqlite(conn, table: str) -> int:
    async with conn.execute(f"SELECT COUNT(*) AS c FROM {table}") as cur:
        row = await cur.fetchone()
    return int(row["c"])


# ── Memories: special-case for embedding column / vec0 pair ────────────


async def _read_memories_pg(conn, batch_size: int) -> Iterable[list[dict]]:
    """Yield batches of ``memories`` rows from PG, including the embedding."""
    cols = await _list_columns_pg(conn, "memories")
    select = (
        ", ".join(c if c != "embedding" else "embedding::text AS embedding"
                  for c in cols)
    )
    async with conn.transaction():
        cur = await conn.cursor(f"SELECT {select} FROM memories ORDER BY created_at, id")
        while True:
            rows = await cur.fetch(batch_size)
            if not rows:
                return
            yield [_pg_memory_row_to_dict(r) for r in rows]


def _pg_memory_row_to_dict(row) -> dict:
    d: dict = {}
    for k in row.keys():
        v = row[k]
        if k == "embedding" and isinstance(v, str) and v:
            stripped = v.strip("[]")
            d[k] = (
                [float(x) for x in stripped.split(",")] if stripped else None
            )
        elif k == "embedding":
            d[k] = list(v) if v else None
        else:
            d[k] = v
    return d


async def _read_memories_sqlite(conn, batch_size: int) -> Iterable[list[dict]]:
    """Yield batches of ``memories`` rows from SQLite, including the embedding."""
    sql = (
        "SELECT m.*, "
        "CASE WHEN v.embedding IS NULL THEN NULL "
        "     ELSE vec_to_json(v.embedding) END AS embedding_json "
        "FROM memories m LEFT JOIN memory_vectors v "
        "ON v.memory_rowid = m.rowid "
        "ORDER BY m.created_at, m.id"
    )
    batch: list[dict] = []
    async with conn.execute(sql) as cur:
        async for r in cur:
            d = {k: r[k] for k in r.keys() if k != "embedding_json"}
            ej = r["embedding_json"]
            if ej:
                try:
                    d["embedding"] = json.loads(ej)
                except json.JSONDecodeError:
                    d["embedding"] = None
            else:
                d["embedding"] = None
            batch.append(d)
            if len(batch) >= batch_size:
                yield batch
                batch = []
    if batch:
        yield batch


# ── Generic table copy ─────────────────────────────────────────────────


async def _read_generic_pg(
    conn, table: str, batch_size: int,
) -> Iterable[list[dict]]:
    cols = await _list_columns_pg(conn, table)
    select = ", ".join(cols)
    async with conn.transaction():
        cur = await conn.cursor(f"SELECT {select} FROM {table} ORDER BY 1")
        while True:
            rows = await cur.fetch(batch_size)
            if not rows:
                return
            yield [{c: r[c] for c in cols} for r in rows]


async def _read_generic_sqlite(
    conn, table: str, batch_size: int,
) -> Iterable[list[dict]]:
    cols = await _list_columns_sqlite(conn, table)
    select = ", ".join(cols)
    sql = f"SELECT {select} FROM {table} ORDER BY 1"
    batch: list[dict] = []
    async with conn.execute(sql) as cur:
        async for r in cur:
            batch.append({c: r[c] for c in cols})
            if len(batch) >= batch_size:
                yield batch
                batch = []
    if batch:
        yield batch


async def _write_batch_sqlite(
    conn,
    table: str,
    rows: list[dict],
    *,
    target_cols: Sequence[str],
) -> None:
    """Insert a batch into a SQLite table, adapting types as needed.

    ``INSERT OR IGNORE`` makes the path idempotent: a fresh target DB
    that's been auto-bootstrapped with the ``solo`` org / first key
    won't collide on the source's matching rows, and ``--continue``
    re-runs are safe to repeat. The spec's ``all-or-nothing per table
    batch`` constraint still holds because we wrap each batch in a
    transaction; duplicates are dropped silently by SQLite.
    """
    cols = [c for c in target_cols if c != "embedding"]
    placeholders = ", ".join("?" for _ in cols)
    sql = (
        f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    payload = []
    for r in rows:
        payload.append(
            tuple(_adapt_for_sqlite(table, c, r.get(c)) for c in cols)
        )
    await conn.executemany(sql, payload)


async def _write_batch_pg(
    conn,
    table: str,
    rows: list[dict],
    *,
    target_cols: Sequence[str],
) -> None:
    """Insert a batch into a Postgres table, adapting types as needed.

    JSONB columns are bound as JSON strings and cast with ``::jsonb`` in
    the INSERT to avoid asyncpg's strict type matching.
    """
    cols = [c for c in target_cols if c != "embedding"]
    json_cols = JSON_COLUMNS.get(table, frozenset())
    placeholders = []
    i = 1
    for c in cols:
        if c in json_cols:
            placeholders.append(f"${i}::jsonb")
        else:
            placeholders.append(f"${i}")
        i += 1
    # ``ON CONFLICT DO NOTHING`` mirrors the SQLite ``OR IGNORE`` path so
    # the bootstrap-row collision (solo org / first key) and a ``--continue``
    # re-run are both no-ops on the duplicate rows. PG requires a target
    # column for the conflict — when ``id`` is not part of the schema we
    # fall back to omitting the clause.
    on_conflict = " ON CONFLICT DO NOTHING" if "id" in cols else ""
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)})"
        f"{on_conflict}"
    )
    for r in rows:
        params = [_adapt_for_pg(table, c, r.get(c)) for c in cols]
        await conn.execute(sql, *params)


async def _write_memories_sqlite(
    conn,
    rows: list[dict],
    *,
    target_cols: Sequence[str],
    re_embed: bool,
    embedder: Any,
) -> None:
    """Insert ``memories`` rows + their vec0 companions in one transaction.

    Mirrors ``SqliteStore.transaction()``: the BEGIN IMMEDIATE wrapper is
    started by the caller; this helper just emits the row + vec INSERTs.
    """
    base_cols = [c for c in target_cols if c != "embedding"]
    placeholders = ", ".join("?" for _ in base_cols)
    sql_base = (
        f"INSERT OR IGNORE INTO memories ({', '.join(base_cols)}) "
        f"VALUES ({placeholders})"
    )
    sql_vec = (
        "INSERT INTO memory_vectors(memory_rowid, embedding) VALUES (?, ?)"
    )
    for r in rows:
        cur = await conn.execute(
            sql_base,
            tuple(_adapt_for_sqlite("memories", c, r.get(c)) for c in base_cols),
        )
        # ``cur.rowcount == 0`` indicates ``OR IGNORE`` dropped a duplicate;
        # the existing row already owns a ``memory_vectors`` companion so
        # we must not double-insert.
        inserted = (cur.rowcount or 0) > 0
        rowid = cur.lastrowid
        await cur.close()
        if not inserted:
            continue
        emb = r.get("embedding")
        if re_embed and embedder is not None:
            content = r.get("content") or ""
            try:
                emb = list(embedder.embed(content))
            except Exception as exc:
                logger.warning(
                    "re-embed failed for memory %s: %s", r.get("id"), exc
                )
                emb = None
        if emb:
            await conn.execute(sql_vec, (rowid, repr(list(emb))))


async def _write_memories_pg(
    conn,
    rows: list[dict],
    *,
    target_cols: Sequence[str],
    re_embed: bool,
    embedder: Any,
) -> None:
    """Insert ``memories`` rows into Postgres including the embedding column."""
    json_cols = JSON_COLUMNS.get("memories", frozenset())
    placeholders = []
    cols = list(target_cols)
    if "embedding" not in cols:
        cols.append("embedding")
    i = 1
    for c in cols:
        if c == "embedding":
            placeholders.append(f"${i}::vector")
        elif c in json_cols:
            placeholders.append(f"${i}::jsonb")
        else:
            placeholders.append(f"${i}")
        i += 1
    sql = (
        f"INSERT INTO memories ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)}) "
        "ON CONFLICT DO NOTHING"
    )
    for r in rows:
        emb = r.get("embedding")
        if re_embed and embedder is not None:
            content = r.get("content") or ""
            try:
                emb = list(embedder.embed(content))
            except Exception as exc:
                logger.warning(
                    "re-embed failed for memory %s: %s", r.get("id"), exc
                )
                emb = None
        params: list = []
        for c in cols:
            if c == "embedding":
                if emb is None:
                    params.append(None)
                else:
                    params.append(json.dumps(list(emb)))
            else:
                params.append(_adapt_for_pg("memories", c, r.get(c)))
        await conn.execute(sql, *params)


# ── Top-level orchestration ────────────────────────────────────────────


async def _open_raw_pg(url: str):
    import asyncpg

    conn = await asyncpg.connect(url)
    return conn


async def _open_raw_sqlite(url: str):
    import aiosqlite
    import sqlite_vec

    from lore.persistence.sqlite import _resolve_db_path

    path = _resolve_db_path(url)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.enable_load_extension(True)
    await conn.load_extension(sqlite_vec.loadable_path())
    await conn.enable_load_extension(False)
    return conn


@contextlib.asynccontextmanager
async def _open_pair(src_url: str, tgt_url: str):
    """Open + bootstrap migrations on both endpoints, yield raw conns."""
    # Bootstrap target first so all expected tables exist (uses the
    # high-level Store.open path which runs migrations idempotently).
    from lore.persistence.factory import make_store

    src_store = await make_store(src_url)
    tgt_store = await make_store(tgt_url)
    src_raw = None
    tgt_raw = None
    try:
        # Acquire a raw connection on each side. We bypass the Store
        # protocol for inserts so IDs/timestamps survive bit-exact;
        # the high-level Store is only used for migration bootstrap.
        if _is_pg(src_url):
            src_raw = await _open_raw_pg(src_url)
        else:
            src_raw = await _open_raw_sqlite(src_url)
        if _is_pg(tgt_url):
            tgt_raw = await _open_raw_pg(tgt_url)
        else:
            tgt_raw = await _open_raw_sqlite(tgt_url)
        yield src_raw, tgt_raw
    finally:
        if src_raw is not None:
            await src_raw.close()
        if tgt_raw is not None:
            await tgt_raw.close()
        await src_store.close()
        await tgt_store.close()


async def _detect_embedding_dim(url: str, conn) -> Optional[int]:
    """Return the embedding dim used by ``url`` (or None if no rows)."""
    if _is_pg(url):
        row = await conn.fetchrow(
            "SELECT embedding::text AS e FROM memories "
            "WHERE embedding IS NOT NULL LIMIT 1"
        )
        if row is None or not row["e"]:
            return None
        stripped = row["e"].strip("[]")
        if not stripped:
            return None
        return len(stripped.split(","))
    # SQLite: read one vec0 row via vec_to_json
    async with conn.execute(
        "SELECT vec_to_json(embedding) AS e FROM memory_vectors LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    if row is None or not row["e"]:
        return None
    try:
        v = json.loads(row["e"])
    except json.JSONDecodeError:
        return None
    return len(v) if isinstance(v, list) else None


async def _migrate_table(
    table: str,
    *,
    src_url: str,
    tgt_url: str,
    src_conn,
    tgt_conn,
    batch_size: int,
    dry_run: bool,
    re_embed: bool,
    embedder: Any,
) -> tuple[int, int]:
    """Migrate one table; returns (source_count, written_count)."""
    src_is_pg = _is_pg(src_url)
    tgt_is_pg = _is_pg(tgt_url)

    src_exists = (
        await _table_exists_pg(src_conn, table)
        if src_is_pg else await _table_exists_sqlite(src_conn, table)
    )
    tgt_exists = (
        await _table_exists_pg(tgt_conn, table)
        if tgt_is_pg else await _table_exists_sqlite(tgt_conn, table)
    )
    if not src_exists or not tgt_exists:
        logger.info(
            "skipping %s (src_exists=%s tgt_exists=%s)",
            table, src_exists, tgt_exists,
        )
        return 0, 0

    src_count = (
        await _row_count_pg(src_conn, table)
        if src_is_pg else await _row_count_sqlite(src_conn, table)
    )
    if src_count == 0:
        return 0, 0

    target_cols_fn = (
        _list_columns_pg if tgt_is_pg else _list_columns_sqlite
    )
    target_cols = await target_cols_fn(tgt_conn, table)
    src_cols_fn = (
        _list_columns_pg if src_is_pg else _list_columns_sqlite
    )
    src_cols = await src_cols_fn(src_conn, table)
    # Intersection: only copy columns present on both sides. Drops
    # backend-only columns (e.g. PG's ``embedding`` is handled separately).
    shared_cols = [c for c in target_cols if c in src_cols]

    written = 0
    if dry_run:
        return src_count, src_count

    if table == "memories":
        reader = (
            _read_memories_pg(src_conn, batch_size)
            if src_is_pg else _read_memories_sqlite(src_conn, batch_size)
        )
        async for batch in reader:
            try:
                if tgt_is_pg:
                    async with tgt_conn.transaction():
                        await _write_memories_pg(
                            tgt_conn, batch,
                            target_cols=shared_cols,
                            re_embed=re_embed,
                            embedder=embedder,
                        )
                else:
                    await tgt_conn.execute("BEGIN IMMEDIATE")
                    try:
                        await _write_memories_sqlite(
                            tgt_conn, batch,
                            target_cols=shared_cols,
                            re_embed=re_embed,
                            embedder=embedder,
                        )
                        await tgt_conn.commit()
                    except BaseException:
                        with contextlib.suppress(Exception):
                            await tgt_conn.rollback()
                        raise
            except Exception as exc:
                first_id = batch[0].get("id") if batch else "?"
                logger.error(
                    "migrate %s batch failed at id=%s: %s",
                    table, first_id, exc,
                )
                raise
            written += len(batch)
        return src_count, written

    reader = (
        _read_generic_pg(src_conn, table, batch_size)
        if src_is_pg else _read_generic_sqlite(src_conn, table, batch_size)
    )
    async for batch in reader:
        try:
            if tgt_is_pg:
                async with tgt_conn.transaction():
                    await _write_batch_pg(
                        tgt_conn, table, batch, target_cols=shared_cols,
                    )
            else:
                await tgt_conn.execute("BEGIN IMMEDIATE")
                try:
                    await _write_batch_sqlite(
                        tgt_conn, table, batch, target_cols=shared_cols,
                    )
                    await tgt_conn.commit()
                except BaseException:
                    with contextlib.suppress(Exception):
                        await tgt_conn.rollback()
                    raise
        except Exception as exc:
            first_id = batch[0].get("id") if batch else "?"
            logger.error(
                "migrate %s batch failed at id=%s: %s",
                table, first_id, exc,
            )
            raise
        written += len(batch)
    return src_count, written


async def _run_migrate(args: argparse.Namespace) -> int:
    src_url = args.src
    tgt_url = args.tgt
    batch_size = args.batch_size or DEFAULT_BATCH_SIZE
    dry_run = bool(args.dry_run)
    re_embed = bool(args.re_embed)
    cont = bool(getattr(args, "continue_run", False))

    if _scheme(src_url) not in ("postgres", "postgresql", "sqlite"):
        print(
            f"Unsupported source scheme: {_scheme(src_url)!r}",
            file=sys.stderr,
        )
        return 2
    if _scheme(tgt_url) not in ("postgres", "postgresql", "sqlite"):
        print(
            f"Unsupported target scheme: {_scheme(tgt_url)!r}",
            file=sys.stderr,
        )
        return 2

    state = _load_state(src_url, tgt_url) if cont else {}

    async with _open_pair(src_url, tgt_url) as (src_conn, tgt_conn):
        # ── Schema-version compatibility ──────────────────────────────
        # The expected version set is derived from on-disk migration
        # files for each scheme; the actual applied set is queried from
        # ``schema_migrations`` if the table exists. We require the
        # *applied* sets to be a superset of the expected — i.e. both
        # endpoints have at minimum every version we know about.
        src_applied = (
            await _applied_versions_pg(src_conn)
            if _is_pg(src_url)
            else await _applied_versions_sqlite(src_conn)
        )
        tgt_applied = (
            await _applied_versions_pg(tgt_conn)
            if _is_pg(tgt_url)
            else await _applied_versions_sqlite(tgt_conn)
        )
        # Only enforce equality when both sides expose
        # ``schema_migrations``. When one side doesn't, we skip the
        # cross-version check and rely on per-table existence guards.
        if src_applied is not None and tgt_applied is not None:
            if src_applied != tgt_applied:
                missing_in_target = sorted(src_applied - tgt_applied)
                missing_in_source = sorted(tgt_applied - src_applied)
                print(
                    "schema-version mismatch — refusing to migrate.",
                    file=sys.stderr,
                )
                if missing_in_target:
                    print(
                        f"  missing on target: {missing_in_target}",
                        file=sys.stderr,
                    )
                if missing_in_source:
                    print(
                        f"  missing on source: {missing_in_source}",
                        file=sys.stderr,
                    )
                return 3

        # ── Embedding dim compatibility ──────────────────────────────
        src_dim = await _detect_embedding_dim(src_url, src_conn)
        tgt_dim = await _detect_embedding_dim(tgt_url, tgt_conn)
        if src_dim is not None and tgt_dim is not None and src_dim != tgt_dim:
            if not re_embed:
                print(
                    f"embedding-dim mismatch (src={src_dim}, tgt={tgt_dim}) — "
                    "pass --re-embed to regenerate embeddings on the target.",
                    file=sys.stderr,
                )
                return 4
        embedder = None
        if re_embed and not dry_run:
            try:
                from lore.embed.local import LocalEmbedder

                embedder = LocalEmbedder()
            except Exception as exc:
                print(f"failed to load embedder: {exc}", file=sys.stderr)
                return 5

        # ── Table-by-table migration ─────────────────────────────────
        totals: dict[str, tuple[int, int]] = {}
        for table in TABLE_ORDER:
            if cont and table in state:
                # Skip tables already fully copied.
                tgt_count = (
                    await _row_count_pg(tgt_conn, table)
                    if _is_pg(tgt_url)
                    else await _row_count_sqlite(tgt_conn, table)
                )
                if tgt_count >= state.get(table, 0):
                    print(f"  skip (resume): {table} [{tgt_count} rows]")
                    totals[table] = (tgt_count, 0)
                    continue
            src_count, written = await _migrate_table(
                table,
                src_url=src_url,
                tgt_url=tgt_url,
                src_conn=src_conn,
                tgt_conn=tgt_conn,
                batch_size=batch_size,
                dry_run=dry_run,
                re_embed=re_embed,
                embedder=embedder,
            )
            totals[table] = (src_count, written)
            if not dry_run:
                state[table] = written
                _save_state(src_url, tgt_url, state)
            verb = "would copy" if dry_run else "copied"
            print(f"  {table}: {verb} {written}/{src_count} rows")

        # ── Row-count validation ─────────────────────────────────────
        if not dry_run:
            mismatches: list[str] = []
            for table, (src_count, _written) in totals.items():
                tgt_count = (
                    await _row_count_pg(tgt_conn, table)
                    if _is_pg(tgt_url)
                    else await _row_count_sqlite(tgt_conn, table)
                )
                if tgt_count < src_count:
                    mismatches.append(
                        f"{table}: src={src_count} tgt={tgt_count}"
                    )
            if mismatches:
                print(
                    "row-count validation failed:", file=sys.stderr,
                )
                for m in mismatches:
                    print(f"  {m}", file=sys.stderr)
                return 6

    print(
        "migration complete"
        if not dry_run else "dry run — no changes written"
    )
    return 0


def cmd_migrate(args: argparse.Namespace) -> None:
    """Entry point invoked by ``lore migrate``."""
    rc = asyncio.run(_run_migrate(args))
    if rc != 0:
        sys.exit(rc)
