# Phase 3A — SQLite Solo Mode Foundation

**Spec:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`

**Goal:** Wire the second backend's foundation. After 3A, `make_store("sqlite:///./test.db")` returns a working `SqliteStore` whose schema is fully migrated. All Store-protocol methods raise `NotImplementedError`; per-slice implementations land in 3C–3F.

## Scope

In:
- `SqliteStore` skeleton (lifecycle: open, close, connection management with WAL pragmas + sqlite-vec extension load + migration runner).
- `make_store(database_url)` URL-scheme dispatch to `SqliteStore.open()`.
- `migrations_sqlite/` directory with translated SQL files for every existing Postgres migration (skip 002, 003 — they don't exist).
- CI parity guard `scripts/check_migrations_parity.py` (every Postgres migration must have a SQLite sibling).
- CI: routes-no-SQL guard + parity guard wired into `.github/workflows/ci.yml`.
- `pyproject.toml` `[solo]` extra: `aiosqlite`, `sqlite-vec`.
- Smoke test: open empty SqliteStore, verify schema is applied, close.

Out (deferred to later sub-phases):
- Per-method implementations (3C–3F).
- `memory_vectors` virtual table + transactional pair invariant (3B).
- Bootstrap (auto-create org_id="solo", api key file) (3G).
- Typed exception parity (`StoreBusy` retries, etc.) (3G).
- Contract suite parameterization across both backends (3C — added when first slice implements).

## Files

### New
- `src/lore/persistence/sqlite.py` — SqliteStore class
- `migrations_sqlite/001_initial.sql` … `migrations_sqlite/019_recommendation_config_null_safe_unique.sql` — 17 files mirroring `migrations/`
- `scripts/check_migrations_parity.py` — CI guard
- `tests/persistence/test_sqlite_smoke.py` — open/close/idempotency
- `docs/superpowers/plans/2026-05-06-phase-3a-sqlite-foundation.md` — this plan

### Modified
- `src/lore/persistence/factory.py` — `make_store` dispatches sqlite:// to SqliteStore.open()
- `pyproject.toml` — `[solo]` extra
- `.github/workflows/ci.yml` — guards + (optionally) install `[solo]` for sqlite tests
- `CHANGELOG.md`, `docs/architecture.md` — Phase 3 unlocks.

## Tasks

**T1 — `[solo]` extra + SqliteStore skeleton**

`pyproject.toml`: `[project.optional-dependencies] solo = ["aiosqlite>=0.19", "sqlite-vec>=0.1.0"]`.

`src/lore/persistence/sqlite.py`:
- Optional imports of `aiosqlite` and `sqlite_vec` (raise `BackendUnavailableError` from `__init__` if missing).
- `_resolve_db_path(database_url)` parses sqlite:/// URLs (`sqlite:///rel/path`, `sqlite:////abs/path`, `sqlite:///:memory:`).
- `SqliteStore.open(database_url)` classmethod: resolve path, mkdir parent, open connection, apply pragmas (`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`), load sqlite-vec, run migrations, return store.
- `SqliteStore.close()` closes the owned connection.
- `_acquire()` returns an async context manager yielding the connection (mirrors PG signature).
- `_apply_migrations()` reads `migrations_sqlite/*.sql` in lexical order, skips already-applied versions tracked in `schema_migrations(version, applied_at)`, executes via `executescript`, commits per file.
- All Store-protocol methods are stubbed via a small loop that `setattr`s `_stub(name)` onto the class. NotImplementedError with a clear message naming the method.

Commit: `feat(persistence): SqliteStore skeleton + connection management`

**T2 — `make_store()` URL dispatch**

`src/lore/persistence/factory.py` sqlite branch instantiates `SqliteStore.open(database_url)`. Postgres branch unchanged.

Commit: `feat(persistence): factory dispatches sqlite:// to SqliteStore`

**T3 — Translate 17 PG migrations to migrations_sqlite/**

Mechanical translation per spec table:
- `JSONB` → `TEXT` (queried via `json_extract`/`json_each` later).
- `TIMESTAMPTZ` → `TEXT` (ISO-8601; default via `(datetime('now'))`).
- `vector(384)` columns → drop the column entirely (vector storage moves to a separate `memory_vectors` vec0 virtual table in Phase 3B).
- `DO $$ ... $$` blocks → straight `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`. SQLite migrations are unconditional.
- HNSW indexes → drop (sqlite-vec handles vector indexing).
- `gen_random_uuid()` → caller-side ULID (matches the `f"prefix_{ULID()}"` pattern already used by PostgresStore writes).
- `now()` → `datetime('now')`.
- `pg_indexes` introspection → not needed.
- `BIGSERIAL` / `SERIAL` → `INTEGER PRIMARY KEY AUTOINCREMENT` (only if appearing).
- `BOOLEAN` → `INTEGER` (0/1).
- `BYTEA` → `BLOB`.
- Foreign keys: keep, with `ON DELETE CASCADE` where the PG version has it. SQLite enforces these only when `PRAGMA foreign_keys=ON` (we set that).
- Constraint `CHECK` clauses translate verbatim where possible.
- `INSERT ... ON CONFLICT (col1, col2) DO NOTHING/UPDATE SET ...` syntax matches between dialects but careful with SQLite's restriction that the conflict target must match an actual UNIQUE constraint or PRIMARY KEY.
- COALESCE-based expression indexes (migration 019) translate as expression indexes in SQLite too.

Each `migrations_sqlite/NNN_*.sql` file should match the version number of its sibling exactly. The descriptive filename suffix may differ.

Commit: `feat(migrations_sqlite): translate Postgres schema for SQLite backend`

**T4 — CI parity guard**

`scripts/check_migrations_parity.py` enumerates both directories, compares the set of leading 3-digit version prefixes. Exit non-zero if any version is missing on either side.

Add `python scripts/check_migrations_parity.py` and `python scripts/check_routes_no_sql.py` as CI steps in `.github/workflows/ci.yml` (the lint job).

Commit: `chore(ci): add migrations parity + routes-no-SQL guards to CI`

**T5 — Smoke test**

`tests/persistence/test_sqlite_smoke.py`:
- `test_open_empty_db_runs_migrations`: open a temp file, verify `schema_migrations` table has all expected versions.
- `test_open_is_idempotent`: open twice, verify no duplicate-application errors.
- `test_make_store_dispatches_sqlite_url`: factory returns a SqliteStore for sqlite:/// URL.
- `test_method_stubs_raise_not_implemented`: pick one stubbed method, assert NotImplementedError.

Skip via `pytest.importorskip("sqlite_vec")` so contributors without sqlite-vec installed don't see false failures.

Commit: `test(persistence): SqliteStore smoke tests`

**T6 — Docs**

CHANGELOG: Phase 3A entry.
architecture.md: new section "Backends" called out Phase 3 unlocks.

Commit: `docs: document Phase 3A SqliteStore foundation`

**T7 — Final verification**

- `pytest tests/` — all green
- `ruff check src/ tests/` — clean
- `python3 scripts/check_routes_no_sql.py` — 22 OK
- `python3 scripts/check_migrations_parity.py` — N versions OK
- `pip install -e ".[solo]"` succeeds
- `python -c "import lore.persistence.sqlite"` succeeds (import-time validation)

## Known risks

- `sqlite-vec` extension load uses `aiosqlite._conn` (the underlying sqlite3 connection). This is an internal API but stable across aiosqlite 0.18+. If aiosqlite changes the attribute, the load call needs updating.
- Some PG migrations may have constructs (e.g., partial indexes with PG-specific functions, `tsvector`/`gin`) that don't translate 1-to-1 to SQLite. The translator must call those out and produce a working approximation; if any PG construct is fundamentally untranslatable, document the gap and decide together.
- `executescript` runs without parameters — any SQL needing parameter binding doesn't fit. Migration files don't use placeholders, so this is fine.
- `LORE_MIGRATIONS_SQLITE_DIR` env var lets tests override the migrations directory (useful for fixture-driven tests in 3C+).
