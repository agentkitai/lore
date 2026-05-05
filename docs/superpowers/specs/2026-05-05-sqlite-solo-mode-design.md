# SQLite Solo Mode for Lore — Design

**Status:** Approved (brainstorm), pending implementation plan.
**Date:** 2026-05-05
**Author:** Amit Paz, with Claude.

## Goal

Add a second, self-contained backend to Lore so that users can install and run it without Postgres or pgvector. The same install ships three deliverables in one:

1. **Zero-friction local install** — `pip install lore-sdk[solo] && lore serve` works without Docker, Postgres, or any external service.
2. **Embedded library** — a true in-process Python API (`AsyncLore`) for personal coding agents, scripts, notebooks, and other apps that should not depend on a separate server process.
3. **Production solo deployment** — a real, persistent solo install for individuals or small teams that needs the full feature set (graph, SLO, retention policies, plugins, recommendations, etc.) just on a smaller substrate.

Solo mode is a peer of Postgres mode, not a stripped-down sibling. v1 ships with full feature parity — every Postgres-mode capability also works on SQLite.

## Non-goals

- Replacing Postgres mode. Postgres remains the recommended backend for multi-user team and enterprise deployments.
- Multi-process write scaling on SQLite. WAL mode + a single uvicorn worker in solo mode is the explicit design point. Users who outgrow SQLite are expected to migrate to Postgres via the `lore migrate` command introduced in this design.
- Synchronous embedded API. The embedded API is async-only (`AsyncLore`); sync callers wrap with `asyncio.run`.
- Pure-Python brute-force vector fallback. If the `sqlite-vec` C extension fails to load, Lore refuses to start with a clear error rather than silently degrading.

## Design decisions (resolved during brainstorm)

| # | Decision |
|---|---|
| 1 | Goals: zero-friction local install **and** embedded library **and** production solo — all three. |
| 2 | Scope: full feature parity with Postgres mode at v1. No staging. |
| 3 | Implementation strategy: **Store abstraction layer**. Routes/services contain zero SQL; SQL lives only inside `Store` implementations. |
| 4 | Vector backend: **`sqlite-vec`** (C extension; `vec_distance_cosine`, virtual `vec0` tables). No NumPy fallback. |
| 5 | Embedded API: **service-layer refactor** to power both HTTP and a native Python class. Every current route splits into `route` (FastAPI shell) + `service` (logic). Both call the same Store. |
| 6 | Packaging: **single package, config-driven backend**. Backend chosen by `database_url` URL scheme. Solo deps under `[solo]` extra. Default URL when unset: `sqlite:///~/.lore/lore.db`. |
| 7 | Schema strategy: **hand-written parallel migrations** in `migrations_sqlite/`. CI rejects PRs that add `migrations/NNN_*.sql` without a sibling `migrations_sqlite/NNN_*.sql`. Timestamps stored as **TEXT ISO-8601** in SQLite; `TIMESTAMPTZ` stays in Postgres; the Store layer normalizes to Python `datetime`. |
| 8 | Concurrency: **WAL mode + single writer + connection pool** (`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`). `lore serve` defaults to one uvicorn worker in solo mode. |
| 9 | Background workers: **always run, both modes**. Retention, SLO, alerting, and ingest workers start in `lore serve` lifespan and in `AsyncLore.__aenter__`. |
| 9b | Embedded API shape (consequence of 9): **async-only `AsyncLore`**. Workers are asyncio tasks in the user's loop. |
| 10 | Migration tooling: **`lore migrate`** dedicated command, ships in v1. Bidirectional, streaming, ID-preserving, embeds-only-if-needed, resumable. |
| 11 | Multi-user features in solo mode: **schema parity, single-user runtime defaults**. Every table from the existing 15 migrations (`001`–`017`, with `002`/`003` skipped) ports faithfully. First open of an empty SQLite DB auto-bootstraps `org_id="solo"`, `workspace_id="solo"`, an API key written to `~/.lore/key.txt` (mode 600). `lore serve` defaults to `127.0.0.1`; binding `0.0.0.0` requires `--require-auth`. |

## Architecture

Three layers, two backends, two front-ends.

```
┌────────────────────────────┐   ┌───────────────────────────────┐
│ HTTP front-end             │   │   Embedded library            │
│   FastAPI server           │   │   AsyncLore("./lore.db")      │
│   (lore serve)             │   │   async with ...:             │
└─────────────┬──────────────┘   └─────────────┬─────────────────┘
              │ calls                          │ calls
              ▼                                ▼
┌──────────────────────────────────────────────────────────────────┐
│ Service layer (lore.services.*)                                  │
│   recall, remember, ingest, graph_query, slo, retention, …       │
│   Pure async functions: (store, params) → dataclass result       │
│   Owns business logic; never touches HTTP or raw SQL             │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ calls store.method(...)
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│ Store protocol (lore.store.Store)                                │
│   ~40 typed methods: insert_memory, recall_by_embedding,         │
│   add_fact, get_workspace, … (no SQL strings on the interface)   │
└──────┬───────────────────────────────────────────────┬───────────┘
       │                                               │
       ▼                                               ▼
┌─────────────────────┐                ┌──────────────────────────┐
│ PostgresStore       │                │ SqliteStore              │
│ asyncpg, pgvector,  │                │ aiosqlite, sqlite-vec,   │
│ JSONB, HNSW, $1     │                │ TEXT JSON, WAL, ?-binds  │
└─────────────────────┘                └──────────────────────────┘
```

### Invariants

- **Routes contain zero SQL. Services contain zero SQL. SQL lives only in Store implementations.** A contract test suite enforces both stores satisfy the same interface.
- **The Service layer is the only place business logic exists once.** Routes and the embedded API are thin shells over it. Adding a new feature means writing one service function; both surfaces get it for free.
- **Backend chosen by `database_url` scheme.** `postgres://...` → `PostgresStore`; `sqlite:///...` → `SqliteStore`. `LORE_BACKEND` env var is just a shortcut; URL scheme wins on conflict.
- **SQLite uses WAL with one writer.** `lore serve` defaults to a single uvicorn worker in solo mode. Multi-worker on SQLite is technically OK with WAL but offers near-zero benefit since writes still serialize at the file lock; the simplicity wins.
- **Embedded API is async-only.** `AsyncLore` is an async context manager. Sync callers use `asyncio.run`.
- **Workers always run inside the Lore lifecycle.** Both `lore serve` and `AsyncLore.__aenter__` start retention, SLO, alerting, and ingest workers; `__aexit__` and lifespan shutdown stop them cleanly.

## Components

Nine components — four new modules, one refactor of existing route code, one parallel migrations tree, three small additions.

### 1. `lore.store` — Backend abstraction (new)

- `lore/store/protocol.py` — `Store` Protocol (~40 typed async methods, grouped: `MemoryOps`, `GraphOps`, `WorkspaceOps`, `SnapshotOps`, `AnalyticsOps`, `PolicyOps`, `AuthOps`).
- `lore/store/postgres.py` — `PostgresStore`, refactored from today's route SQL.
- `lore/store/sqlite.py` — `SqliteStore`, new.
- `lore/store/factory.py` — `make_store(database_url)` returns the right implementation.
- Depends on: `asyncpg` (Postgres) or `aiosqlite` + `sqlite-vec` (SQLite).
- Used by: services only.

### 2. `lore.services` — Business logic (new, populated by refactoring)

- One module per current route file (~25 modules): `services/recall.py`, `services/memories.py`, `services/graph.py`, `services/slo.py`, ….
- Pure async functions accepting `Store` + typed params, returning dataclasses (no `Request`/`Response` types).
- Owns scoring, ranking, retention logic, embedding orchestration, plugin dispatch, audit emission.
- Depends on: `lore.store`, `lore.embed`, `lore.plugin`, `lore.types`.
- Used by: server routes and `AsyncLore`.

### 3. `lore.server` — FastAPI HTTP shell (refactored)

- `server/app.py` — unchanged shape; lifespan manages worker startup/shutdown.
- `server/routes/*.py` — shrink to: parse request → call service → serialize response. Average ~30 lines/route.
- `server/lifespan.py` (new; replaces today's `server/db.py`) — builds the `Store` from config, runs migrations for the active dialect, hands the store to services via FastAPI dependency injection.

### 4. `lore.AsyncLore` — Embedded API (new)

- `lore/lore_api.py` — single class.
- `async with AsyncLore(database_url, *, workspace="solo", api_key=None) as lore:` opens a Store, runs migrations, starts background workers, returns a handle.
- ~30 high-level methods mirroring the MCP/SDK surface: `remember`, `recall`, `forget`, `add_fact`, `graph_query`, etc. Each is a one-line call into the matching service.
- Workers (SLO, retention, alerting, ingest) start in `__aenter__` and stop in `__aexit__`.

### 5. Migrations — Two parallel trees (new sibling tree)

- `migrations/` — Postgres (existing 15 files, numbered `001`–`017` with `002`/`003` skipped, unchanged).
- `migrations_sqlite/` — SQLite (15 new files mirroring the same numbering; mirror schema, dialect-translated by hand).
- A CI check rejects PRs that add `migrations/NNN_*.sql` without a matching `migrations_sqlite/NNN_*.sql`.

Schema differences (called out for the implementation plan):

| Postgres | SQLite |
|---|---|
| `JSONB` | `TEXT` (queried via `json_extract` / `json_each`) |
| `vector(384)` column | virtual `vec0` table joined by row id |
| `TIMESTAMPTZ` | `TEXT` ISO-8601 |
| `DO $$ … $$` blocks | straight `CREATE TABLE IF NOT EXISTS` (no procedural DDL) |
| HNSW indexes | none (sqlite-vec handles vector indexing internally) |
| `pg_indexes` lookups | not used; SQLite migrations are unconditional |

### 6. Vector layer for SQLite (new, contained in `SqliteStore`)

- A virtual table `memory_vectors USING vec0(embedding float[384])` paired with the `memories` row id.
- Inserts go to both `memories` and `memory_vectors` in one `BEGIN IMMEDIATE … COMMIT` transaction so vector and metadata never go out of sync.
- Recall queries: `SELECT … FROM memories JOIN (SELECT rowid, distance FROM memory_vectors WHERE embedding MATCH ? AND k = ?) v ON memories.rowid = v.rowid`.
- Postgres path unchanged.

### 7. Bootstrap layer (augments existing `lore.bootstrap`)

- On first open of an empty SQLite DB:
  - Insert `org_id="solo"`, `workspace_id="solo"`.
  - Generate API key, write to `~/.lore/key.txt` with mode `0600`.
- `lore serve` defaults: bind `127.0.0.1`, accept the auto-key.
- `--bind 0.0.0.0` requires `--require-auth` flag; otherwise `lore serve` refuses to start with `InsecureBindError` and a clear remediation message.

### 8. `lore migrate` command (new)

- `lore/cli/migrate.py` — `lore migrate --from <url> --to <url>`.
- Streams table-by-table in dependency order; preserves IDs (ULIDs are portable); re-embeds only if the embedding model differs between source and target; row-count validation at the end.
- Resumable via `--continue` flag.
- Internal wire format reuses `lore export`'s JSON shape.

### 9. Config & packaging (small changes)

- `LORE_DATABASE_URL` (or `database_url` in config file) is the single source of truth.
- Default when unset: `sqlite:///~/.lore/lore.db`.
- `pyproject.toml`:
  ```toml
  [project.optional-dependencies]
  solo = ["aiosqlite>=0.19", "sqlite-vec>=0.1.0"]
  ```
- `lore-sdk` core wheel size unchanged; users opt in to solo deps explicitly.

### Estimated scope

| Area | Lines |
|---|---|
| New SQL (`migrations_sqlite/`) | ~700 |
| New code (`store.sqlite`, `AsyncLore`, `lore migrate`, bootstrap, vector layer) | ~1500 |
| Refactored code (routes split into routes+services, SQL pulled into Store methods) | ~6000 mechanical |
| New tests (Store contract suite, embedded API tests, migration round-trips) | ~2000 |

## Data flow

### Path A — HTTP server (`lore serve`)

```
HTTP client (SDK / curl / MCP gateway)
  │ POST /v1/recall {query: "...", k: 10}
  ▼
FastAPI route  (server/routes/retrieve.py)
  │ • parse JSON → RecallRequest
  │ • auth check (api_key, workspace scope)
  │ • inject Store from app.state
  ▼
Service        (services/recall.py: async def recall(store, params))
  │ • embed query → vec[384]
  │ • apply retrieval profile (recency / graph weight / score floor)
  │ • call store.recall_by_embedding(...) → list[StoredMemory]
  │ • optional graph expansion → store.related_memories(...)
  │ • plugin on_recall hooks
  │ • record analytics (store.write_analytics_row)
  ▼
Store          (PostgresStore or SqliteStore)
  │ Postgres: SELECT … (1 - (embedding <=> $1::vector)) AS score …
  │ SQLite:   SELECT … FROM memories JOIN (vec0 KNN) …
  │ returns dataclasses; both stores produce identical shapes
  ▲
  │ rows
Service returns RecallResult dataclass
  ▲
Route          serializes → JSON response
  ▲
HTTP client receives results
```

### Path B — Embedded library

```
Caller code
  async with AsyncLore("sqlite:///./lore.db") as lore:
      results = await lore.recall("...")
  ▼
AsyncLore.recall()  (lore/lore_api.py)
  │ • shape kwargs into the same params object services use
  ▼
Service        (services/recall.py — same function as Path A)
  ▼
Store          (SqliteStore typically; could be Postgres if URL points there)
```

The Service function is identical across paths. Path A wraps it with HTTP/auth/serialization; Path B calls it directly.

### Worker flow (background tasks, both modes)

```
Lifecycle (server lifespan or AsyncLore __aenter__)
  │ start RetentionWorker, SLOWorker, AlertingWorker, IngestWorker
  ▼
RetentionWorker (60s tick)
  │ store.expire_memories(now)            # DELETE WHERE expires_at < now
  │ store.snapshot_if_due(policy)         # writes snapshot file + audit row
  ▼
SLOWorker (60s tick)
  │ store.read_recent_analytics(window)   # rolling p50/p95/p99 + hit rate
  │ if breach → AlertingWorker.dispatch(breach)
  ▼
AlertingWorker (event-driven)
  │ for channel in slo.alert_channels:
  │     send_webhook(...) | send_email(...)
  │ store.record_alert(...)
  ▼
IngestWorker (in-memory queue)
  │ for job in queue:
  │     services.ingest.run(store, job)
```

### Two writes that need transactional pairing in SQLite

1. `remember()` → row in `memories` + row in `memory_vectors` (vec0). Single `BEGIN IMMEDIATE … COMMIT`.
2. `extract_facts()` → atomic facts + entities + relationships across 3 tables. Same transaction.

Postgres has the same invariant via single statements + explicit transactions; SQLite needs slightly more care because the `vec0` virtual table is a separate table object.

### Schema migration flow on startup

```
open store
  │ run migrations dir for active dialect
  │   pg:    apply migrations/*.sql in order, idempotent
  │   sqlite: apply migrations_sqlite/*.sql in order, idempotent
  │ check schema_version row matches expected
  │ if SQLite + empty DB → bootstrap (org/workspace/api_key, write key file)
  ▼
Store ready
```

## Error handling

Three categories of error: storage, configuration, integrity. Each gets a typed exception in the Store/Service layer; the HTTP shell maps to status codes; the embedded API surfaces exceptions as Python exceptions.

### Typed exception hierarchy (new in `lore.exceptions`)

```
LoreError                          # base
├── StoreError                     # anything from the Store layer
│   ├── StoreBusy                  # SQLITE_BUSY, asyncpg LockNotAvailable
│   ├── StoreCorruption            # vec0 corruption, malformed schema
│   ├── StoreSchemaMismatch        # DB version != expected
│   └── StoreNotFound              # row not found (when caller said "must exist")
├── ConfigError                    # bad URL, missing extra, conflicting flags
│   ├── BackendUnavailable         # sqlite-vec extension fails to load
│   └── InsecureBindError          # 0.0.0.0 without --require-auth
└── IntegrityError                 # cross-table invariants violated
    ├── EmbeddingDimMismatch       # row says 384, model produces 768
    └── DanglingVectorError        # memory row exists but vec0 row missing
```

### Storage errors

- **`StoreBusy`** (SQLite `SQLITE_BUSY`, exhausted busy_timeout): retried inside the Store with exponential backoff (50ms → 100ms → 200ms → 400ms, max 4 attempts). If still busy, raise to Service. Service does **not** retry — busy after 750ms means real contention. HTTP returns 503 with `Retry-After: 1`. Embedded API raises directly.
- **`StoreCorruption`**: never auto-recover. Log full error; surface to user with a remediation hint pointing at `lore repair --vector-rebuild` (rebuilds vec0 from `memories.embedding` BLOBs). HTTP 500. Embedded raises.
- **`StoreSchemaMismatch`**: detected at startup. If `schema_version < expected`, run pending migrations automatically (logged). If `schema_version > expected` (DB written by a newer Lore than the running code), refuse to start with: *"DB at <path> requires Lore vN.M, you're running vK.L. Upgrade or use `lore migrate` to copy data into a compatible DB."*
- **`StoreNotFound`**: only raised when the caller specified "must exist" semantics; normal "not found" returns `None` from the Store and the Service maps to HTTP 404.

### Configuration errors

- Bad `database_url` scheme → `ConfigError` at startup, immediate exit with the list of supported schemes.
- `pip install lore-sdk` (no `[solo]`) but `database_url=sqlite:///...` → `BackendUnavailable("sqlite-vec not installed; pip install lore-sdk[solo]")`. Same message shape for `[server]`.
- `sqlite-vec` C extension fails to load → `BackendUnavailable` with the exact `LoadLibrary` error and a link to platform-specific install notes. Lore refuses to start; no silent degradation.
- `--bind 0.0.0.0` without `--require-auth` → `InsecureBindError` at startup. Refuses to start. The error message includes the exact flag combination needed to enable it deliberately.
- `LORE_BACKEND=sqlite` but `database_url=postgres://…` (or vice versa) → `ConfigError` with both values shown; URL scheme wins, the env var is just a shortcut and contradicting it is operator error.

### Integrity errors (mostly SQLite-specific)

- **`EmbeddingDimMismatch`**: a stored row's embedding length doesn't match the active model. Caught when reading. Marks the row as `needs_reembed`; a background sweep re-embeds in the user's idle window. Caller gets the row without scoring; Service logs once per model change.
- **`DanglingVectorError`**: `memories` row without a matching `memory_vectors` row, or vice versa. Should never happen because of the transactional pair, but `lore doctor` checks for it and `lore repair --vector-rebuild` fixes it.

### Worker errors

All four background workers (retention, SLO, alerting, ingest) catch and log per-iteration. A single tick failing never kills the worker. Three consecutive failures escalate to a structured log + emit a metric (`worker_consecutive_failures{worker=...}`); does not crash the server. Embedded mode propagates uncaught worker exceptions out the `__aexit__` boundary so users see them at shutdown rather than silently.

### WAL maintenance

SQLite's WAL file grows over time. Run `PRAGMA wal_checkpoint(TRUNCATE)` automatically every 1000 writes or every 5 minutes (whichever first). Logged at debug level. Failure is non-fatal; just leaves a larger WAL.

### Migration command (`lore migrate`) errors

All-or-nothing per table batch — failures roll back the in-flight batch but already-copied tables stay in the destination. Resumable via `--continue` flag. Mismatched embedding dims between source and target are detected upfront, before any copy starts; user must pass `--re-embed` explicitly to acknowledge the cost.

### What we deliberately don't do

- **No automatic backend fallback.** If `sqlite-vec` doesn't load, Lore refuses to start instead of secretly degrading to brute-force search the user didn't ask for. Surprises in a memory system are worse than crashes.
- **No `try/except: pass` swallows.** Every caught exception either retries deterministically (busy), recovers with a clear plan (corruption → repair command), or is re-raised with context.
- **No silent schema drift.** If migrations have been hand-edited or skipped, startup fails loudly.

## Testing

Five layers; the first is the new one and does most of the heavy lifting.

### Layer 1 — Store contract suite (new, ~1500 LOC)

A single shared test module that runs against every `Store` implementation. Lives at `tests/store_contract/`. Each test method takes a `store: Store` fixture; the same tests run twice via parametrized fixtures — once against `PostgresStore` (Docker Postgres + pgvector), once against `SqliteStore` (temp file).

Coverage of the ~40 Store methods, organized:

- **Memory ops** — insert/read/update/delete round-trips; embedding storage; vector recall ranking on a known fixture set; metadata filtering; tier-based TTL expiry.
- **Graph ops** — entity create/merge; relationship traversal; fact extraction storage; conflict detection.
- **Workspace/auth ops** — multi-key isolation; revocation; bootstrap idempotency.
- **Snapshot/policy ops** — round-trip serialization; restore drill timings.
- **Concurrency** — concurrent writers (`asyncio.gather` of N writes); busy retry exhaustion; WAL recovery (kill mid-write, reopen, verify).
- **Transactional invariants** — `memories` and `memory_vectors` always paired (test by injecting a failure between the two writes and verifying the row is absent in both); fact extraction atomic across 3 tables.
- **Error contracts** — every typed exception is raised by both backends in the same situations.

This is the "if this passes, the abstraction holds" suite. A new feature that lands new SQL must extend this suite; CI rejects PRs that add a new Store method without a contract test.

### Layer 2 — Service tests (refactor of existing tests)

Today's route tests largely exercise business logic via HTTP. They migrate to direct service-function tests that take a fake/test store. Faster, no FastAPI overhead, can test scoring/ranking/retention logic directly.

### Layer 3 — Embedded API tests (new, ~400 LOC)

`tests/embedded/` exercises `AsyncLore` directly:

- Lifecycle: `__aenter__` / `__aexit__` correctness, worker startup/shutdown, no leaked tasks after exit (asserted via `asyncio.all_tasks()`).
- Worker observability: retention actually expires; SLO worker computes from analytics; injected failure → consecutive-failure metric increments → does not crash.
- Sync-script integration: `asyncio.run(main())` pattern works end-to-end.
- Bootstrap: opening an empty SQLite file auto-creates org/workspace/key; opening it again is idempotent.

### Layer 4 — HTTP integration tests (existing, kept)

The current `tests/integration/` suite continues to run against `lore serve` with a real backend. Parametrized to run twice: once with `database_url=postgres://…`, once with `sqlite:///./test.db`. Smoke test that the FastAPI shell, auth, rate limiting, and middleware still work over both backends.

### Layer 5 — Migration round-trip tests (new, ~300 LOC)

`tests/migrate/` tests `lore migrate`:

- Postgres → SQLite → Postgres round-trip: row counts match, embeddings preserved within float tolerance, IDs preserved exactly.
- Resume after partial failure: kill mid-migration, `--continue` finishes correctly.
- Schema-version mismatch refused upfront.
- Re-embed flag actually re-embeds and produces results equivalent to a fresh ingest.

### CI matrix

- **Default PR run:** contract suite (both backends), service tests, embedded tests. Fast (~2–3 min).
- **Full matrix (nightly + on `main`):** the above + Layer 4 HTTP integration on both backends + migration round-trips. Slower (~10–15 min).
- **Migrations parity guard:** a CI step diffs `migrations/` and `migrations_sqlite/` file lists; PR fails if a new Postgres migration lacks its SQLite sibling.

### Test data

- Embedding fixtures: a fixed set of 100 short strings + their precomputed 384-dim embeddings (committed as a JSON file). Avoids embedding model drift in tests.
- Vector ranking tests use cosine similarity hand-checked against the fixture; pass/fail is deterministic.

### Coverage target

The Store contract suite hits 100% of the Store interface (mechanically enforced — the contract suite uses the `Store` Protocol's introspection to assert every method has at least one test). No coverage target on services; rely on contract suite + integration smoke for confidence.

### What we deliberately don't test

- Postgres-specific extensions beyond pgvector (we don't use them).
- Real network alert dispatch (mocked at the channel boundary).
- OIDC issuer round-trips against real IdPs (mocked; covered by their own unit tests).

## Open questions for the implementation plan

These are intentionally not resolved here; they're sized for the implementation plan to call out:

1. **Order of operations.** Does the Store abstraction land first (refactor with only `PostgresStore`, no behavior change), then `SqliteStore` follows on top? Or does the SQLite work happen on a feature branch in parallel? The spec assumes the former — refactor first, add second backend after — because it isolates risk and gives Postgres users the abstraction win even if SQLite slips.
2. **Service layer module shape.** Should services be one module per route (25 modules) or one per domain (memories, graph, workspaces, ops)? The spec says "one per current route file" as the default; the implementation plan can revisit.
3. **`AsyncLore` method surface.** The spec says "~30 high-level methods mirroring MCP/SDK." The plan should produce the full list with signatures before coding starts.
4. **MCP server adaptation.** The current MCP server (`lore.mcp`) hits the HTTP API. Should it switch to `AsyncLore` directly when running on the same machine? Out of scope for this design; flagged for a future ticket.
5. **`lore migrate` UX details.** Progress reporting style, dry-run mode, partial-table resume granularity — implementation choices the plan should pin down.
6. **`lore doctor` and `lore repair`.** The error-handling section mentions both as remediation tools (`lore doctor` checks for `DanglingVectorError`; `lore repair --vector-rebuild` rebuilds vec0 from `memories.embedding`). They are new commands. The implementation plan needs to define their full surface and decide whether `lore doctor` is exclusive to solo mode or also runs Postgres-mode integrity checks.

## Out of scope (deferred to future work)

- Pure-Python NumPy vector fallback when `sqlite-vec` is unavailable.
- Synchronous embedded API (`Lore` class wrapping `AsyncLore` with a thread-hosted event loop).
- Multi-process write scaling on SQLite.
- Pluggable backends beyond Postgres and SQLite (the abstraction supports it; nothing else ships in v1).
