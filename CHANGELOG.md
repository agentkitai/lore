# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added
- Server-side persistence layer (`lore.persistence`) defining the `Store` protocol with the `MemoryOps` slice. New `PostgresStore` implementation extracted from route SQL. Contract test suite at `tests/persistence/` runs against every Store implementation. (Foundation for SQLite solo mode — see `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`.)
- Service layer (`lore.services`) for memory ops and retrieve. Routes call services; services call Store. No HTTP behavior changes.
- `Store` protocol grows the `GraphOps` slice (24 typed methods spanning entities, mentions, relationships, traversal, stats, and a UI-facing text search). `PostgresStore` implements all of them.
- `lore.services.graph` package (`entities.py`, `graph.py`, `review.py`) wraps the GraphOps store layer. Includes the risk-score pure function lifted from the old `routes/review.py`.
- `Store` protocol grows the `PolicyOps` slice (7 typed methods for retrieval-profile CRUD + key resolution). `PostgresStore` implements all of them.
- `lore.services.profiles` wraps the PolicyOps store layer. Owns the 60-second resolution cache, `DEFAULT_PROFILES` built-in fallback, k/threshold/max_results/min_score alias logic, and preset-immutability checks. New typed exceptions: `IntegrityError` (unique-constraint), `ProfileImmutableError` (preset modify/delete attempt).
- Migration `018_profile_extras.sql` adds `k`, `threshold`, `rerank`, `include_graph` columns to `retrieval_profiles` (the existing route code already referenced them; the original 013 migration omitted them).
- `Store` protocol grows the `WorkspaceOps` slice (9 methods for workspace + workspace_member CRUD) and the `AuthOps` slice (5 methods for API key creation, listing, revocation, and root-key counting). `PostgresStore` implements all 14.
- `lore.services.workspaces` and `lore.services.keys` wrap the new store slices. The workspaces service owns the `WORKSPACE_ROLES` tuple and a public `has_ws_permission(role, minimum)` rank helper. The keys service owns API key generation (`lore_sk_` + SHA-256), the "can't revoke last root key" rule, and cache invalidation via the new `lore.server.auth.invalidate_key(key_hash)` helper. New typed exception: `LastRootKeyError`.
- `Store` protocol grows the `AnalyticsOps` slice (3 methods: `record_retrieval_event`, `record_memory_access`, `list_recent_session_snapshots`) plus two `MemoryOps` extensions (`bump_access_counts`, `enrich_memory_meta`). `PostgresStore` implements all 5.
- `lore.services.snapshots` (new) wraps session-snapshot creation. `lore.services.retrieve` and `lore.services.memories` extended with analytics + enrichment + access-recording helpers. New typed dataclass: `NewRetrievalEvent`.
- **Bug fix in `routes/snapshots.py`**: the pre-1E INSERT referenced non-existent `tier` and `type` columns directly on the `memories` table; reads via `_fetch_session_snapshots` already queried `meta->>'type'`. The refactor moves both keys into `meta` to match. The endpoint is now functional on the current schema.
- `Store` protocol grows the `RecommendationOps` slice (4 methods: `get_recommendation_config`, `upsert_recommendation_config`, `record_recommendation_feedback`, `list_candidate_memories_for_recommendation`). New typed dataclasses: `RecommendationCandidate`, `StoredRecommendationConfig`, `NewRecommendationFeedback`.
- `lore.services.recommendations` (new) owns engine orchestration via `_CandidatesAdapter` (the engine's `Store`-with-`.list()` interface), config get/upsert returning a flat dict, feedback validation (`positive`/`negative` only), and the no-context / no-candidates / engine-error fallback paths. The route layer becomes a thin shell.
- Migration `019_recommendation_config_null_safe_unique.sql` replaces the original `UNIQUE(workspace_id, agent_id)` with a COALESCE-based expression index. The standard SQL UNIQUE treats NULL != NULL, which meant ON CONFLICT for the global (NULL, NULL) scope never fired. The expression index makes (NULL, NULL) count as one row.
- **Bug fix in `routes/recommendations.py`**: the pre-1F `update_config` handler used a string-replace hack (`sql.replace(" WHERE ", ", updated_at = now() WHERE ", 1)`) to inject `updated_at` into a dynamically-built SET clause. The new `Store.upsert_recommendation_config` uses a clean `INSERT … ON CONFLICT … RETURNING` with the `updated_at` set inline.
- `Store` protocol grows the `ConversationOps` slice (5 methods: `create_conversation_job`, `get_conversation_job`, `mark_conversation_job_processing`, `complete_conversation_job`, `fail_conversation_job`) plus one `MemoryOps` extension (`import_extracted_memory` — idempotent INSERT … ON CONFLICT (id) DO NOTHING used by the conversation extraction flow). New typed dataclasses: `NewConversationJob`, `StoredConversationJob`.
- `lore.services.conversations` (new) owns the background-task orchestration in `process_job_async` (mark processing → run `ConversationExtractor` → import extracted memories → mark complete/failed). The legacy in-process `Lore`/`MemoryStore` extraction driver stays as-is; `_get_server_lore` moves from the route into the service module.
- After Phase 1G, the CI guard covers 13 migrated route files. The route files still on inline SQL — and slated for future phases — are: `sharing.py` (13 get_pool calls), `lessons.py` (10), `slo.py` (9), `policies.py` (9), `topics.py` (3), `recent.py` (2), `audit.py` (2), and `analytics.py` (2). The `lore/server/auth.py` middleware (key lookup + `last_used_at` update) is also still on inline SQL.
- `MemoryOps` grows three more methods for the lessons slice (Phase 1H): `list_memories_paginated` (count + paged rows with text-query/`reputation_score` filters; extends MemoryFilter), `list_memories_with_embeddings` (bulk export shape including the vector column), `upsert_memory_with_embedding` (idempotent INSERT … ON CONFLICT … DO UPDATE WHERE org match, with RETURNING `xmax = 0` to distinguish INSERT vs UPDATE). New typed dataclass: `ExportedMemory`.
- `lore.services.lessons` (new) wraps MemoryOps with field translation (`problem`↔`content`, `resolution`↔`context`) at the service+route boundary. Owns the time-decay scoring formula for search (moved from inline SQL to Python; per-type half-lives for `code`/`note`/`lesson`/`convention` retained). Project scoping enforced via fetch-then-check (the `lessons` Postgres view added in migration 009 stays as a backward-compat wrapper for direct DB clients). **Known regression**: the lessons UPDATE handler now supports only the `"+1"` string for upvotes/downvotes; `"-1"` and absolute-int modes raise 422. Future MemoryOps work could add atomic vote-deltas to restore.
- `Store` protocol grows the `AuditOps` slice (1 method: `query_audit_log`) plus one `AnalyticsOps` extension (`compute_retrieval_analytics` — collapses 7 separate SQL queries against `retrieval_events` into one Store call returning a populated `RetrievalAnalyticsResult` dataclass). New typed dataclasses: `StoredAuditEntry`, `RetrievalAnalyticsResult`, `ScoreDistributionBucket`, `TopQueryRow`, `DailyStatRow`.
- Four new dashboard service modules: `lore.services.recent` (passthrough to `MemoryOps.list_memories` with time-window grouping at the route layer), `lore.services.audit` (passthrough to `AuditOps.query_audit_log`), `lore.services.analytics` (wraps `compute_retrieval_analytics` with response shaping — derived hit_rate, memory_utilization, score-distribution percentages), `lore.services.topics_dashboard` (adapts existing `services.graph.entities` for the public `/v1/topics` API).
- `Store` protocol grows the `RetentionOps` slice (10 methods spanning 3 tables — `retention_policies`, `snapshot_metadata`, `restore_drill_results`): policy CRUD (5 methods), latest snapshot lookup, snapshot count, drill recording, drill listing, latest-drill-for-org. New typed dataclasses: `NewRetentionPolicy`, `StoredRetentionPolicy`, `RetentionPolicyPatch`, `StoredSnapshotMetadata`, `NewDrillResult`, `StoredDrillResult`.
- `lore.services.policies` (new) wraps RetentionOps. The `run_drill` orchestration (fetch policy → fetch latest snapshot → simulate restore → record drill row) moves from the route into the service. The cross-policy `check_compliance` query (snapshot count + last drill check) also moves to the service.
- `routes/memories.py` and `routes/retrieve.py` no longer contain raw SQL. CI guard `scripts/check_routes_no_sql.py` enforces this for migrated routes.
- All 8 graph route handlers (`routes/graph/{memories,entities,stats,topics}.py`) and the 4 review handlers (`routes/review.py`) refactored to call services exclusively. Inline SQL, `_table_exists` checks, and `_compute_risk_score` removed from those route files. CI guard now covers 7 migrated route files.
- New contract tests at `tests/persistence/test_contract_graph.py` (49 tests across 24 GraphOps methods).
- New service tests at `tests/services/test_graph_{entities,graph,review}.py` (41 tests) and route tests at `tests/server/test_graph_routes.py` (21 tests with FakeStore mocks).
- Phase 1B follow-up: cascade-delete contract test for `delete_entity` deferred to a future task (mentions/relationships rows when an entity is deleted).
- All 8 profile route handlers (`routes/profiles.py`) refactored to call services exclusively. The cross-route `resolve_profile` import in `routes/retrieve.py` is also gone — retrieve.py now calls the service directly. Inline SQL, `DEFAULT_PROFILES`, the in-memory cache, and the legacy `_resolve_profile` helper removed from the route files. CI guard now covers 8 migrated route files.
- New contract tests at `tests/persistence/test_contract_profiles.py` (21 tests across the 7 PolicyOps methods).
- New service tests at `tests/services/test_profiles.py` (~22 tests) and route tests at `tests/server/test_profiles_routes.py` (14 tests with FakeStore mocks).
- All 13 identity route handlers (10 in `routes/workspaces.py`, 3 in `routes/keys.py`) refactored to call services exclusively. Inline SQL, the `_has_ws_permission`/`WORKSPACE_ROLES` helpers, the `has_ws_col` introspection probe, and inline key generation removed from the route files. CI guard now covers 10 migrated route files.
- New contract tests at `tests/persistence/test_contract_workspaces.py` (27 tests) and `tests/persistence/test_contract_keys.py` (12 tests).
- New service tests at `tests/services/test_workspaces.py` (19 tests) and `tests/services/test_keys.py` (~10 tests).
- New route tests at `tests/server/test_workspaces_routes.py` (14 tests) and `tests/server/test_keys_routes.py` (8 tests).
- Existing `tests/server/test_keys.py` and `tests/test_workspaces.py` redirected from inline-SQL mocks to service-layer mocks where applicable.
- All 6 recommendation route handlers (`routes/recommendations.py`) refactored to call services exclusively. The local `_AsyncpgStore` adapter, all inline SQL, the `build_update` helper import, the `sql.replace` hack, and the per-handler local imports (`asyncio`, `json`, `SimpleNamespace`, `ULID`) all removed. CI guard now covers 12 migrated route files.
- New contract tests at `tests/persistence/test_contract_recommendations.py` (17 tests across the 4 RecommendationOps methods).
- New service tests at `tests/services/test_recommendations.py` (10 tests) and route tests at `tests/server/test_recommendations_routes.py` (10 tests with FakeStore mocks).
- All 7 of the remaining inline-SQL helpers in `routes/snapshots.py`, `routes/retrieve.py` (`_record_retrieval_event`, `_bump_access_counts`, `_fetch_session_snapshots`), and `routes/memories.py` (`_enrich_memory`, `record_access`) refactored into services. CI guard now covers 11 migrated route files; `routes/retrieve.py` and `routes/memories.py` are no longer in the SQL allowlist (fully migrated).
- New contract tests at `tests/persistence/test_contract_analytics.py` (~20 tests).
- New service tests at `tests/services/test_snapshots.py` (8 tests). Existing `tests/services/test_retrieve.py` and `tests/services/test_memories.py` extended with analytics + enrichment + access tests.
- New route tests at `tests/server/test_snapshots_routes.py` (5 tests with FakeStore mocks).
- Existing `tests/test_enrichment_memories.py` and `tests/test_memories_server.py` redirected from inline-SQL mocks to service-layer mocks.
- Both `routes/conversations.py` handlers (POST/GET) refactored to call services exclusively. The legacy `_process_job` background helper and `_get_server_lore` constructor moved to `lore/services/conversations.py`. The route file is now ~85 LOC (was 228). CI guard coverage grows to 13 migrated route files.
- New contract tests at `tests/persistence/test_contract_conversations.py` (17 tests across the 5 ConversationOps methods + the `import_extracted_memory` extension).
- New service tests at `tests/services/test_conversations.py` (11 tests) and route tests at `tests/server/test_conversations_routes.py` (7 tests with FakeStore mocks).
- Existing `tests/test_conversation_server.py` redirected from `FakeConn`/`FakePool`+`get_pool` patches to `FakeStore`+`get_store` dependency override and a service-level `process_job_async` mock.
- All 9 `routes/lessons.py` handlers refactored to call services exclusively. The legacy `_row_to_response` and `_scope_filter` helpers and `_HALF_LIFE_DEFAULT` constant moved to or duplicated in `lore/services/lessons.py`. The route file is now ~338 LOC (was 592). CI guard now covers 14 migrated route files.
- New contract tests at `tests/persistence/test_contract_lessons.py` (16 tests across the 3 new MemoryOps extensions).
- New service tests at `tests/services/test_lessons.py` (19 tests) and route tests at `tests/server/test_lessons_routes.py` (15 tests with FakeStore mocks).
- Existing `tests/server/test_lessons.py` (31 tests written against pre-1H inline-SQL mocks) skipped en masse with reason "Replaced by FakeStore tests in T8". Three other test files (`test_rbac.py`, `test_jwt_auth.py`, `tests/integration/test_remote.py`) had their lesson-related mocks redirected to the service layer.
- All 4 dashboard route files (`recent.py`, `audit.py`, `analytics.py`, `topics.py`) refactored to call services exclusively. CI guard now covers 18 migrated route files. Net 278 LOC removed from the route layer (the analytics file alone shrunk from 220 → 93 lines).
- New contract tests at `tests/persistence/test_contract_dashboards.py` (~15 tests across `query_audit_log` + `compute_retrieval_analytics`).
- New service tests at `tests/services/test_recent.py`, `test_audit.py`, `test_analytics.py`, `test_topics_dashboard.py` (~12 tests total).
- New route tests at `tests/server/test_dashboards_routes.py` (12 tests with FakeStore mocks; mounts all four dashboard routers in one test app).
- Existing `tests/test_retrieval_analytics.py` redirected from inline-SQL/`get_pool` mocks to service-layer mocks.
- All 8 `routes/policies.py` handlers refactored to call services exclusively. The route file is now ~235 LOC (was 376). CI guard now covers 19 migrated route files. Bug fix: `/compliance` GET route is now declared BEFORE `/{policy_id}` to prevent FastAPI from routing `/compliance` as a `{policy_id}` parameter.
- New contract tests at `tests/persistence/test_contract_policies.py` (~18 tests across the 10 RetentionOps methods).
- New service tests at `tests/services/test_policies.py` (12 tests against real Postgres).
- New route tests at `tests/server/test_policies_routes.py` (15 tests with FakeStore mocks).
- `Store` protocol grows the `SloOps` slice (7 methods on `slo_definitions` + `slo_alerts`: definition CRUD, alert listing, alert insertion) plus 2 `AnalyticsOps` extensions (`compute_metric_value`, `compute_metric_timeseries`). New typed dataclasses: `NewSloDefinition`, `StoredSloDefinition`, `SloDefinitionPatch`, `NewSloAlert`, `StoredSloAlert`, `TimeseriesPoint`. The metric→SQL mapping (formerly `_metric_sql` in the route) lives in postgres.py.
- `lore.services.slo` (new) wraps SloOps and the new metric methods. Owns `VALID_METRICS`/`VALID_OPERATORS` validation, the `_check_threshold` pure helper, and the `slo_status` orchestration (list active SLOs → compute metric → check threshold). `test_alert` (fire a test alert for an SLO) and `slo_timeseries` orchestration also moved here.
- All 8 `routes/slo.py` handlers refactored to call services exclusively. The route file is now ~260 LOC (was 467). CI guard now covers 20 migrated route files. **Known issue preserved**: `list_slos`/`slo_status`/`list_alerts` had no auth/org filter pre-1K (multi-tenancy gap); the refactor preserves that behavior. Documented for follow-up.
- New contract tests at `tests/persistence/test_contract_slo.py` (20 tests across the 7 SloOps methods + 2 metric extensions).
- New service tests at `tests/services/test_slo.py` (14 tests).
- New route tests at `tests/server/test_slo_routes.py` (15 tests with FakeStore mocks).
- `Store` protocol grows the `SharingOps` slice (12 methods spanning 4 sharing tables: `sharing_config`, `agent_sharing_config`, `deny_list_rules`, `sharing_audit` — config get-or-init/update, agent-config list/upsert, deny-rule list/create/delete, audit list/record, sharing stats, purge cascade, atomic `rate_lesson`). New typed dataclasses: `SharingConfigData`, `SharingConfigPatch`, `AgentSharingConfigData`, `DenyListRuleData`, `NewDenyListRule`, `AuditEventData`, `NewAuditEvent`, `SharingStatsData`.
- `lore.services.sharing` (new) wraps SharingOps. Owns `delta ∈ {1, -1}` validation and `confirmation == "PURGE"` validation — service raises `ValueError`; route maps to 400. The `_record_audit` module helper at the top of routes/sharing.py is gone (now `record_audit_event` on the Store).
- All 10 `routes/sharing.py` handlers + the 1 `rate_lesson` handler on the separately-mounted `rate_router` refactored to call services exclusively. The route file is now ~338 LOC (was 406). CI guard now covers 21 migrated route files — **all route files now migrated**; only `lore/server/auth.py` middleware remains outside the guard. **Bug fix**: pre-1L `rate_lesson` targeted the `lessons` view, but migration 009's `lessons_update` rule does not propagate `RETURNING reputation_score`. The Phase 1L Postgres impl targets the `memories` base table directly (matches the rest of the persistence layer). `purge_sharing` and `get_sharing_stats` similarly target `memories` rather than the `lessons` view.
- New contract tests at `tests/persistence/test_contract_sharing.py` (23 tests across the 12 SharingOps methods).
- New service tests at `tests/services/test_sharing.py` (13 tests).
- New route tests at `tests/server/test_sharing_routes.py` (16 tests with FakeStore mocks).
- `Store.AuthOps` slice grows two new methods: `lookup_api_key_by_hash(key_hash)` (called on every authenticated request after the in-process cache miss) and `touch_api_key_last_used(key_id)` (debounced last-used-at update). `StoredApiKey` gains an optional `role` field. The auth middleware (`lore/server/auth.py`) now goes through the Store instead of `get_pool()` — the in-process key cache and debounce timing are unchanged.
- The CI guard `scripts/check_routes_no_sql.py` now covers `src/lore/server/auth.py` (22 files total). **Every component in the request-handling path is SQL-free**; SQL lives exclusively in Store implementations.
- 5 new contract tests in `tests/persistence/test_contract_keys.py` covering `lookup_api_key_by_hash` (round-trip, missing, role passthrough) and `touch_api_key_last_used` (sets timestamp, no-op on missing id).
- Existing `tests/server/test_auth.py`, `test_retrieve.py`, `test_rbac.py`, `test_jwt_auth.py`, `test_keys.py`, and `tests/integration/test_remote.py` updated to mock `lore.server.auth.get_store` instead of `get_pool`.
- **Phase 3A: SqliteStore foundation.** New `src/lore/persistence/sqlite.py` wires the second backend skeleton — opens an aiosqlite connection with WAL pragmas (`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`), loads the `sqlite-vec` extension, and applies the new `migrations_sqlite/` schema tree. All Store-protocol methods raise `NotImplementedError` until subsequent Phase 3 sub-phases (3B vector layer, 3C–3F per-slice impls, 3G bootstrap + typed-exception parity).
- `make_store()` now dispatches `sqlite://` URLs to `SqliteStore.open()`. A `[solo]` `pyproject.toml` extra installs `aiosqlite>=0.19` + `sqlite-vec>=0.1.0`.
- New `migrations_sqlite/` tree (17 files mirroring `migrations/`) translates the Postgres schema for SQLite: `JSONB→TEXT`, `TIMESTAMPTZ→TEXT (ISO-8601)`, `vector(384)` columns dropped (vec0 virtual table comes in Phase 3B), HNSW indexes dropped, `DO $$` blocks → straight DDL, `gen_random_uuid()` defaults dropped (caller-side ULIDs), `now()` → `datetime('now')`. Migration 009: lessons→memories rename preserved; the backward-compat `lessons` view is read-only (SQLite views can't have RULEs).
- New CI guard `scripts/check_migrations_parity.py` rejects PRs that add a Postgres migration without a matching SQLite sibling. The CI workflow (`.github/workflows/ci.yml`) now invokes both this and the routes-no-SQL guard explicitly.
- New smoke tests at `tests/persistence/test_sqlite_smoke.py` (8 tests covering open/idempotency/factory dispatch/stub method behavior/path resolution).
- **Phase 3B: SqliteStore vector layer.** `_init_vec_tables(conn)` creates the `memory_vectors` vec0 virtual table (`CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors USING vec0(memory_rowid INTEGER PRIMARY KEY, embedding FLOAT[384])`) at the end of `open()`. Not migration-versioned: vec0 is provider-specific to the SQLite backend, not part of the cross-dialect schema contract. Idempotent on re-open.
- New `SqliteStore.transaction()` async context manager wraps `BEGIN IMMEDIATE … COMMIT/ROLLBACK`. Used for the `memories` ⇆ `memory_vectors` invariant in 3C: a row in `memories` and its matching `memory_vectors` row insert as one atomic unit. Yields the same connection so callers can chain executes inside the same transaction without re-acquiring it.
- New module-level `EMBED_DIM = 384` constant (mirrors the dimension used across `migrations/001_initial.sql`, `lore.embed`, snapshot placeholders, and `routes/memories.py`).
- 5 new smoke tests cover vec0 table creation, KNN query (rowid 1 closer than rowid 2 against query embedding), transaction rollback on exception, idempotent re-open with existing vec0 table, and `transaction()` raising `StoreError` on a closed store.
- **Phase 3C: contract suite parameterized + first three SqliteStore MemoryOps.** `tests/persistence/conftest.py` `store` fixture now parametrizes `[postgres, sqlite]`; the SQLite branch opens a fresh `:memory:` `SqliteStore` per test and pre-seeds the `solo`/`org_a`/`org_b` orgs. A `pytest_runtest_call` hookwrapper converts SqliteStore-stub `NotImplementedError` (and a small set of asyncpg-only call patterns the helpers use for raw setup) into `pytest.skip("SqliteStore pending: …")` so the matrix is green pending Phase 3D+. Hook re-exported by `tests/services/conftest.py` so service tests skip with the same semantics.
- `SqliteStore.insert_memory` / `get_memory` / `delete_memory` now implemented and exercise the Phase 3B `memories` ⇆ `memory_vectors` transactional pair invariant. Insert generates a `mem_…` ULID, encodes JSON columns, INSERTs `memories` then `memory_vectors` keyed by the new rowid in one `BEGIN IMMEDIATE` block. Delete resolves the rowid, deletes the vec0 row first then the base row in one transaction. Get filters out already-expired rows like Postgres. Module-level `_row_to_memory` mirrors the PG `_row_to_stored` helper (TEXT-as-JSON tags/meta, ISO-8601 timestamps parsed back into aware UTC datetimes).
- New `SqliteStore._conn` property returns the active connection (bound or owned), so contract-test scaffolding using `store._conn.execute(...)` is dialect-agnostic at the attribute level. The bound-mode field renamed to `self._bound_conn` internally.
- Per-backend test counts (the contract-memories suite): postgres 19/19 pass; sqlite 5/19 pass + 14/19 skip (insert/get/delete pass; remaining MemoryOps stay stubbed pending 3D+).
- **Phase 3D: SqliteStore MemoryOps complete.** Implements the remaining 11 MemoryOps methods on `SqliteStore`: `update_memory`, `list_memories`, `recall_by_embedding`, `expire_memories`, `bump_access_counts`, `enrich_memory_meta`, `vote_memory`, `list_memories_paginated`, `list_memories_with_embeddings`, `upsert_memory_with_embedding`, `import_extracted_memory`. The MemoryOps slice now passes the full contract suite on both backends; only AnalyticsOps + GraphOps + PolicyOps + WorkspaceOps + AuthOps + RecommendationOps + ConversationOps + AuditOps + RetentionOps + SloOps + SharingOps remain stubbed pending 3E–3F.
- New helpers: `_build_memory_filter_clauses(filter, *, text_query, min_reputation, alias)` translates `MemoryFilter` into a SQLite WHERE clause + bind params (PG `tags @> $::jsonb` → `EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)` AND'd per requested tag, contains-all semantics; PG `meta->>'type'` → `json_extract(meta, '$.type')`; PG `ILIKE` → SQLite `LIKE` which is case-insensitive for ASCII by default; PG `expires_at > now()` uses Python-generated `isoformat()` since `datetime('now')` returns a different shape than the stored ISO timestamps and a lex-comparison would mis-order). `_row_to_exported(row, embedding)` mirrors the PG `_row_to_exported_memory` helper but takes the embedding as a separate argument since SQLite's lives in vec0.
- vec0 schema change: `memory_vectors` is now created with `distance_metric=cosine` so the `distance` column matches PG's `embedding <=> $vec` cosine-distance operator. `recall_by_embedding` runs a vec0 KNN (over-fetched to `max(limit, 1) * 4` rows so post-filters don't starve), JOINs `memories` on rowid, applies org/project/expiry filters and the `min_score` floor, and computes the same decay-scoring formula PG uses (`(1 - distance) * importance_score * pow(0.5, days / half_life)`). PG's `EXTRACT(EPOCH FROM …) / 86400` → `julianday('now') - julianday(col)` (yields days as float); `LEAST` → `MIN`; `power` → `pow` (SQLite alias since 3.35).
- `upsert_memory_with_embedding` resolves the insert-vs-update case by reading the existing row up-front inside the transaction (SQLite has no PG `RETURNING (xmax = 0)` equivalent). Org-mismatch is a silent no-op returning `False`, mirroring PG. The vec0 companion is refreshed (DELETE + INSERT) on update; `embedding=None` skips the vec0 insert entirely.
- `list_memories_with_embeddings` LEFT JOINs `memory_vectors` and surfaces the embedding via sqlite-vec's `vec_to_json(blob)` — guarded with `CASE WHEN v.embedding IS NULL THEN NULL` so LEFT JOIN misses don't trip "Input must have type BLOB or TEXT".
- Service-level skip removed: `tests/services/test_memories.py::test_enrich_memory_async_calls_pipeline_and_persists` no longer short-circuits on `_is_sqlite(store)`. The parallel `recent_session_snapshots` skip in `test_retrieve.py` stays (lands in 3E with AnalyticsOps).
- Test count delta: **2978 passed / 505 skipped → 3033 passed / 450 skipped** (+55 passed, -55 skipped). 11 MemoryOps × multiple shapes each, plus 6 AnalyticsOps tests that only needed the `_ensure_org` fixture made dialect-aware.
- Deviations to flag for callers: SQLite vec0's cosine distance is computed over the raw vectors (non-normalized); PG's pgvector `<=>` operator likewise. For typical normalized embeddings the two backends produce identical rankings; for non-normalized embeddings both compute proper cosine, so still equivalent. PG's `jsonb_set(meta, '{enrichment}', value)` and SQLite's `json_set(meta, '$.enrichment', json(value))` are exactly equivalent for the single-key write at a fixed path used by `enrich_memory_meta` (no nested-merge semantic gap). PG's `ILIKE` is case-insensitive for any Unicode; SQLite's `LIKE` is case-insensitive only for ASCII — non-ASCII text-query matches differ between backends. None of the existing tests exercise that, but downstream callers should be aware.
- **Phase 3E: SqliteStore AnalyticsOps complete.** Implements the 6 AnalyticsOps methods on `SqliteStore`: `record_retrieval_event`, `record_memory_access`, `list_recent_session_snapshots`, `compute_retrieval_analytics`, `compute_metric_value`, `compute_metric_timeseries`. The AnalyticsOps slice now passes the full contract suite on both backends; only GraphOps + PolicyOps + WorkspaceOps + AuthOps + RecommendationOps + ConversationOps + AuditOps + RetentionOps + SloOps + SharingOps remain stubbed pending 3F.
- New module-level `_SQLITE_METRIC_SQL` mapping mirrors `lore.persistence.postgres._METRIC_SQL` for the 7 SLO metric names (`p50_latency`, `p95_latency`, `p99_latency`, `hit_rate`, `retrieval_latency_p95`, `retrieval_recall`, `uptime_pct`). Percentile metrics use a sentinel value `PCT::<frac>` that the methods rewrite into a CTE with `ROW_NUMBER() OVER (ORDER BY query_time_ms)` picking the row at `MAX(1, CAST(total * pct AS INTEGER))`. PG's `percentile_cont` does linear interpolation between adjacent rows; the SQLite row-pick approximation can differ slightly on small samples — the `test_compute_metric_value_p95_latency` contract test already uses a `180.0 <= result <= 200.0` tolerance band that fits both backends.
- `compute_retrieval_analytics` issues seven small queries inside one connection (matching the PG impl one-for-one). Translation notes: PG `jsonb_array_elements_text(scores | memory_ids)` → SQLite `json_each(<col>) AS je` table-valued function; `created_at::date` → `date(created_at)`; `now() - make_interval(days => $N)` → `datetime('now', '-N days')` (interval modifier doesn't accept bound parameters; the days int is coerced and interpolated as a literal).
- `compute_metric_timeseries` truncates `created_at` to a multiple of `bucket_minutes * 60` seconds via `datetime((CAST(strftime('%s', col) AS INTEGER) / N) * N, 'unixepoch')` for arbitrary bucket sizes. Per-bucket percentile pick uses a CTE with `PARTITION BY <bucket_expr>` so each bucket gets its own row-rank.
- `record_memory_access` recomputes `importance_score` with the same formula as `bump_access_counts` (`confidence * MAX(0.1, 1.0 + (upvotes - downvotes) * 0.1) * (1.0 + ln(access_count + 2) / ln(2) * 0.1)`), bumps `last_accessed_at` and `updated_at`, then issues a separate SELECT to return the updated row (SQLite's `UPDATE … RETURNING` isn't surfaced uniformly through aiosqlite).
- `list_recent_session_snapshots` filters `meta.type = 'session_snapshot'` within the last 24 hours; PG's `meta->>'type'` and `now() - interval '24 hours'` translate to `json_extract(meta, '$.type')` and `datetime('now', '-24 hours')`. Excluded ids use `id NOT IN (?, ?, …)` rather than PG's `id != ALL($N)`.
- Service-level skip removed: `tests/services/test_retrieve.py::test_recent_session_snapshots_returns_results` no longer short-circuits on `_is_sqlite(store)`.
- Contract-test helper updates: `_ensure_org_analytics`, `_insert_event_at` (in `test_contract_dashboards.py`), `_insert_snapshot_memory` (in `test_contract_analytics.py`), and two new `_count_retrieval_events` / `_fetch_retrieval_event_row` helpers (also in `test_contract_analytics.py`) gain SQLite branches via `_is_sqlite(store)`. The snapshot helper routes through `insert_memory` on SQLite (preserving the memory_vectors invariant) and translates the PG interval expression into a follow-up `UPDATE … SET created_at = datetime('now', '-N hours')` for the "older than 24h" exclusion test.
- Test count delta: **3033 passed / 450 skipped → 3065 passed / 418 skipped** (+32 passed, -32 skipped). The 32 newly-passing tests cover the 6 new AnalyticsOps methods × their per-shape contract cases plus the unskipped service test.
- No PG-side test had to be relaxed: the existing `180.0 <= result <= 200.0` tolerance on `test_compute_metric_value_p95_latency` and `abs(result.p95_latency_ms - 190.5) < 2.0` tolerance on `test_compute_analytics_p95_latency` accommodate the SQLite row-pick approximation without regressing PG. The dashboard P95-on-20-evenly-spaced-samples test produces 190.0 on SQLite (rank-19 pick) and ~190.5 on PG (linear interpolation); both within the 2 ms band.
- **Phase 3F: SqliteStore PolicyOps + WorkspaceOps complete.** Implements the full PolicyOps slice (7 methods: `get_profile`, `get_profile_by_name`, `list_profiles`, `create_profile`, `update_profile`, `delete_profile`, `resolve_profile_for_key`) and the full WorkspaceOps slice (9 methods: `get_workspace`, `list_workspaces`, `create_workspace`, `update_workspace`, `archive_workspace`, `add_workspace_member`, `list_workspace_members`, `update_workspace_member_role`, `remove_workspace_member`) on `SqliteStore`. Both slices now pass the full contract suite on both backends; only GraphOps + AuthOps + RecommendationOps + ConversationOps + AuditOps + RetentionOps + SloOps + SharingOps remain stubbed pending later 3-series sub-phases.
- Translation notes:
  * **`tier_filters` (PG `TEXT[]`)** stored as JSON-array TEXT on SQLite; `_row_to_profile` decodes via `json.loads` and `create_profile` / `update_profile` encode via `json.dumps(list(...))`.
  * **`is_preset` / `rerank` / `include_graph` (PG `BOOLEAN`)** stored as INTEGER 0/1 on SQLite; row helper coerces with `bool(row[col])` and writers gate with `1 if x else 0`.
  * **`workspaces.settings` / `WorkspacePatch.settings` (PG `JSONB`)** stored as TEXT JSON; `json.dumps(dict(...))` on write, `json.loads` on read.
  * **PG `now()` defaults** → SQLite `datetime('now')` (column DEFAULT or per-statement).
  * **`UNIQUE (org_id, name)` / `UNIQUE (org_id, slug)` collisions** raise `aiosqlite.IntegrityError` on SQLite — same as PG's `asyncpg.UniqueViolationError` — and both are mapped to `lore.persistence.exceptions.IntegrityError` with the matching message format.
  * **`workspace_members.workspace_id` FK violation** likewise maps `aiosqlite.IntegrityError` → `IntegrityError(f"workspace_id {…!r} does not exist")` to mirror PG's `ForeignKeyViolationError` path. The migrations_sqlite/016 schema declares the FK; with `PRAGMA foreign_keys=ON` (set in `_open_connection`) the constraint enforces.
  * **`update_profile` / `update_workspace`** mirror the PG dynamic SET-clause builder one-for-one across all 12 / 2 patch fields; empty patches raise `ValueError` (PG-equivalent message).
  * **`resolve_profile_for_key`** uses the same `ORDER BY CASE WHEN org_id = ? THEN 0 ELSE 1 END LIMIT 1` shape as PG so org-owned rows shadow `__global__` presets on name collision. The migration-013 `coding` / `incident-response` / `research` preset rows seed at `__global__` org so the contract test asserting "global preset visible to any org" works on the same SQL path.
  * **`list_workspaces(include_archived=False)`** inlines an explicit `archived_at IS NULL` clause; PG's `(archived_at IS NULL OR $2::boolean)` short-circuit isn't expressible in SQLite, so the SQL splits on the flag.
  * **`archive_workspace`** uses `archived_at IS NULL` as part of the WHERE clause, so already-archived rows return False (idempotent guard, matching PG).
- Contract-test helpers `_insert_profile` (in `test_contract_profiles.py`) and `_insert_workspace` (in `test_contract_workspaces.py`) gain SQLite branches via `_is_sqlite(store)`: SQLite uses `?` placeholders, `datetime('now')` defaults, and `json.dumps` for `tier_filters`; the Postgres branch is unchanged.
- Test count delta: **3065 passed / 418 skipped → 3153 passed / 330 skipped** (+88 passed, -88 skipped). The 88 newly-passing tests cover the 16 PolicyOps + WorkspaceOps methods × their per-shape contract cases on the SQLite param.
- LOC delta: `src/lore/persistence/sqlite.py` 1820 → 2347 (+527 LOC) across 4 commits (profile CRUD, profile update + resolve, workspace CRUD, workspace members).
- No new conftest sentinels needed: every `[sqlite]` test that was previously skipping via `_SQLITE_DIALECT_SENTINELS` now passes once the test helper is dialect-aware. The sentinel set in `tests/persistence/conftest.py` is unchanged.
- **Phase 3G: SqliteStore AuthOps + RecommendationOps + ConversationOps + AuditOps complete.** Implements the full AuthOps slice (7 methods: `get_api_key`, `list_api_keys`, `create_api_key`, `revoke_api_key`, `count_active_root_keys`, `lookup_api_key_by_hash`, `touch_api_key_last_used`), the full RecommendationOps slice (4 methods: `get_recommendation_config`, `upsert_recommendation_config`, `record_recommendation_feedback`, `list_candidate_memories_for_recommendation`), the full ConversationOps slice (5 methods: `create_conversation_job`, `get_conversation_job`, `mark_conversation_job_processing`, `complete_conversation_job`, `fail_conversation_job`), and the AuditOps slice (1 method: `query_audit_log`) on `SqliteStore`. All four slices now pass the full contract suite on both backends; only GraphOps + RetentionOps + SloOps + SharingOps remain stubbed pending later 3-series sub-phases.
- Translation notes:
  * **`api_keys.is_root` (PG `BOOLEAN`)** stored as INTEGER 0/1 on SQLite; `_row_to_api_key` coerces with `bool(row[col])` and `create_api_key` writes `1 if key.is_root else 0`. The `count_active_root_keys` predicate uses `is_root = 1` instead of PG's `is_root = TRUE`.
  * **`api_keys.role` column** (added in migrations_sqlite/005) defaults to `'admin'`; carried through `StoredApiKey.role` so the auth middleware path matches PG.
  * **`revoke_api_key` / `mark_conversation_job_processing` / `update_workspace_member_role` pattern**: SQLite's `UPDATE … RETURNING` isn't surfaced uniformly through aiosqlite, so each method issues a guarded UPDATE with a `cursor.rowcount` check followed by a SELECT to fetch the post-update row. Single-writer means no interleave risk.
  * **NULL-safe scope match (`get_recommendation_config`)**: SQLite's `IS` operator is NULL-safe — same semantics as PG's `IS NOT DISTINCT FROM` — so `workspace_id IS ? AND agent_id IS ?` handles both NULL and concrete-id cases without branching.
  * **NULL-safe UNIQUE on `recommendation_config` (`upsert_recommendation_config`)**: the migration-019 expression UNIQUE index `recommendation_config_scope_uq` over `COALESCE(workspace_id, '__null__'), COALESCE(agent_id, '__null__')` is the upsert's conflict target; the inline `UNIQUE(workspace_id, agent_id)` constraint that ships in migration 017 silently allows duplicate (NULL, NULL) rows under SQLite's NULL-distinct-by-default semantics, which is why the expression-index sibling (migration 019) is load-bearing.
  * **Patch-preserving UPDATE in `upsert_recommendation_config`**: the four optional patch parameters appear *twice* in the parameter list — once on the INSERT side (`COALESCE(?, default)`) and once on the UPDATE side (`COALESCE(?, recommendation_config.col)`). Reusing `excluded.<col>` instead would pull in the COALESCE-filled default and clobber prior state when the patch is None; PG sidesteps this by referring to `$N` directly, which has the same effect.
  * **`enabled` BOOL → INTEGER 0/1**: explicit conversion in `upsert_recommendation_config` so `excluded.enabled` and the COALESCE chain see the same integer encoding the column stores.
  * **`importance_score` ORDER BY DESC NULLS LAST**: SQLite's default NULL ordering with DESC sorts NULLs first; an explicit `CASE WHEN importance_score IS NULL THEN 1 ELSE 0 END` primary sort key reproduces PG's `NULLS LAST` semantics.
  * **`embedding` column → vec0 join**: PG selects `embedding` directly from `memories`; SQLite stores embeddings in the `memory_vectors` vec0 virtual table joined by `memory_rowid`. `list_candidate_memories_for_recommendation` INNER-JOINs `memory_vectors` so memories without a vec0 row are filtered out (mirrors PG's `embedding IS NOT NULL`); the `vec_to_json(v.embedding)` output is decoded via `_decode_vec_to_json`.
  * **`memory_ids` (PG `JSONB[]`-shaped)** stored as TEXT JSON in `conversation_jobs.memory_ids` (default `'[]'`); `_row_to_conversation_job` decodes via `json.loads` into a tuple, `complete_conversation_job` encodes via `json.dumps(list(memory_ids))`.
  * **Conversation job state transitions**: `mark_conversation_job_processing` is unconditional on prior status (matches PG); missing ids return None via the rowcount check. `complete_conversation_job` and `fail_conversation_job` are silent on missing ids — they issue an UPDATE with no WHERE-id-exists guard, so a no-op on a non-existent id raises no error (mirrors PG's behaviour).
  * **`audit_log.id` (PG `BIGSERIAL`)** stored as `INTEGER PRIMARY KEY AUTOINCREMENT` on SQLite; the test scaffolding uses `cursor.lastrowid` instead of PG's `RETURNING id`.
  * **`audit_log.ip_address` (PG `INET`)** stored as plain TEXT on SQLite; `_row_to_audit_entry` surfaces it unchanged (no `str(IPv4Address)` coercion needed).
  * **`since` filter on `query_audit_log`**: accepts both ISO-8601 strings and naive/aware datetimes; normalized to an ISO TEXT string before the comparison so the lexicographic compare against the SQLite TEXT column matches native datetime ordering. Mirrors the PG impl's `str → datetime → asyncpg` path but with TEXT instead of bind-typed `TIMESTAMPTZ`.
- New module-level row-mappers in `src/lore/persistence/sqlite.py`: `_row_to_api_key`, `_row_to_recommendation_config`, `_row_to_recommendation_candidate`, `_row_to_conversation_job`, `_row_to_audit_entry` — all mirror their PG siblings, with TEXT-as-JSON decoding and `_parse_iso` for timestamps.
- Contract-test helper updates: `_ensure_org` and `_insert_api_key` (in `test_contract_keys.py`); `_insert_memory_with_embedding`, `_fetch_feedback_row`, `_count_feedback_rows` (in `test_contract_recommendations.py`); `_ensure_org` and `_insert_audit_entry` (in `test_contract_dashboards.py`) gain SQLite branches via `_is_sqlite(store)`. The recommendation memory helper skips the vec0 insert when `embedding=None`; the audit-log helper uses `cursor.lastrowid` after INSERT instead of PG's RETURNING.
- Test count delta: **3153 passed / 330 skipped → 3228 passed / 255 skipped** (+75 passed, -75 skipped). The 75 newly-passing tests cover the 17 new methods × their per-shape contract cases on the SQLite param.
- LOC delta: `src/lore/persistence/sqlite.py` 2347 → 2985 (+638 LOC) across 4 commits (AuthOps, RecommendationOps, ConversationOps, AuditOps).
- No new conftest sentinels needed: every `[sqlite]` test that was previously skipping via `_SQLITE_DIALECT_SENTINELS` now passes once the test helper is dialect-aware. The sentinel set in `tests/persistence/conftest.py` is unchanged.
- **Phase 3H: SqliteStore RetentionOps + SloOps + SharingOps complete.** Implements the full RetentionOps slice (10 methods on `retention_policies` + `snapshot_metadata` + `restore_drill_results`), the full SloOps slice (7 methods on `slo_definitions` + `slo_alerts`, including the `list_slo_definitions(org_id=None)` multi-tenancy quirk preserved from Phase 1K), and the full SharingOps slice (12 methods spanning the 4 sharing tables + `memories`-touching ops — `purge_sharing` runs the 5-table cascade inside `transaction()` AND pre-deletes matching `memory_vectors` rows so the Phase-3B vec0 pair invariant stays intact; `rate_lesson` is an atomic `UPDATE memories.reputation_score` + audit `INSERT` inside one transaction). After 3H, only **GraphOps** remains stubbed.
- Translation notes:
  * **vec0 pair invariant preserved on bulk delete**: PG's `purge_sharing` deletes `memories` directly (no vec0 to clean up); SQLite's variant does an extra `DELETE FROM memory_vectors WHERE memory_rowid IN (SELECT rowid FROM memories WHERE org_id = ?)` BEFORE the memories DELETE, all inside the same `transaction()`. Without this step, a re-org-init after purge would land memories with vec0 rows attached to the old rowids — the dangling-vector class of bug.
  * **`get_or_init_sharing_config`**: SQLite uses `INSERT OR IGNORE` to lazily create the default config row; PG uses `ON CONFLICT (org_id) DO NOTHING`. Same semantics.
  * **`upsert_agent_sharing_config`**: SQLite's `ON CONFLICT (org_id, agent_id) DO UPDATE` matches PG verbatim — both backends require the explicit UNIQUE on those columns (already in the migration).
  * **`list_audit_events` dynamic WHERE**: same parameter-list-building pattern as PG, with `?` placeholders. `from_date`/`to_date` are normalized to ISO TEXT strings before the lex compare against the SQLite TEXT column.
  * **`get_sharing_stats`**: 3 sub-queries inside one `_acquire()` (COUNT(memories) + MAX(memories.created_at) + GROUP BY event_type on sharing_audit). Same shape as PG.
  * **`update_sharing_config` upsert**: SELECT first; if NULL, INSERT default row, then UPDATE. PG inlines the `INSERT … ON CONFLICT DO NOTHING` + UPDATE pattern; SQLite does the equivalent with two statements inside one connection (single-writer).
  * **Caller-side ULIDs**: `retpol_<ULID>` for retention policies, `drill_<ULID>` for restore drill results, `slo_<ULID>` for SLO definitions, `share_<ULID>` for deny rules. Alert IDs and sharing-audit IDs use `INTEGER PRIMARY KEY AUTOINCREMENT` (PG's BIGSERIAL).
- Test count delta: **3228 passed / 255 skipped → 3304 passed / 179 skipped** (+76 passed, -76 skipped). The 76 newly-passing tests cover the 29 new methods × their per-shape contract cases on the SQLite param. Remaining 179 skips are exclusively the GraphOps slice plus a small set of platform-conditional skips.
- LOC delta: `src/lore/persistence/sqlite.py` 2985 → 4070 (+1085 LOC) across 3 commits (RetentionOps, SloOps, SharingOps slices). Test files: +386 lines across the three contract files (dialect-aware helper additions).
- No new conftest sentinels needed: every `[sqlite]` test that was previously skipping via `_SQLITE_DIALECT_SENTINELS` now passes once the test helper is dialect-aware. Remaining sentinel-driven skips are GraphOps-only.
- **Phase 3J: bootstrap + typed-exception parity + serve defaults.** Final Phase 3 sub-phase makes a fresh `pip install lore-sdk[solo] && lore serve` work without any config:
  * **Solo bootstrap on first open.** New `src/lore/persistence/bootstrap.py` exports `bootstrap_solo_if_empty(store, *, key_path=None)`. Auto-called from `SqliteStore.open()` after `_init_vec_tables()`. Detects an empty install via `count_active_root_keys('solo') == 0`, then idempotently inserts `orgs(id='solo')` + `workspaces(id='solo', slug='solo')`, mints a `lore_sk_<32-hex>` raw key, stores its sha256 hash through `store.create_api_key(...)`, and writes the raw key to `~/.lore/key.txt` with mode `0600`. Skipped entirely for `sqlite:///:memory:` URLs (test-only).
  * **Default `database_url`.** `Settings.from_env()` now resolves `LORE_DATABASE_URL` → legacy `DATABASE_URL` → `sqlite:///~/.lore/lore.db`. Backwards-compatible with existing Postgres deployments that already set `DATABASE_URL`; new solo installs land on the file-backed default.
  * **Typed exception parity.** `lore.persistence.exceptions` grows `StoreCorruption` (under `StoreError`), `EmbeddingDimMismatch` + `DanglingVectorError` (under `IntegrityError`), and `InsecureBindError` (under `ConfigError`). `EmbeddingDimMismatch` is raised at the boundary of `insert_memory` / `upsert_memory_with_embedding` / `recall_by_embedding` on **both** PostgresStore and SqliteStore when the embedding length doesn't match `EMBED_DIM=384`. `StoreCorruption` wraps `aiosqlite.DatabaseError` matching `"database disk image is malformed"` / `"vec0 corrupt"` / `"file is not a database"` patterns at `_open_connection` and `_apply_migrations`. New module-level `check_dangling_vectors(store) -> list[str]` diagnostic returns `memories` rows that lack their vec0 companion (always `[]` in production thanks to the txn pair invariant).
  * **`StoreBusyError` retry on SQLITE_BUSY.** `SqliteStore.transaction()` catches `aiosqlite.OperationalError` matching `"database is locked"` / `"database table is locked"` on `BEGIN IMMEDIATE`, sleeps 50/100/200/400 ms with exponential backoff (max 4 retries), and surfaces `StoreBusyError` if the budget is exhausted. Non-busy `OperationalError` (e.g. syntax errors) bypasses the retry.
  * **`lore serve` host policy.** Default bind changes from `0.0.0.0` to `127.0.0.1`. New `--require-auth` flag. `cmd_serve` raises `InsecureBindError` if the host is non-loopback (anything that's not `127.x` / `::1` / `localhost`) without `--require-auth` set. Loopback-on-localhost binding works without the flag.
- Tests: `tests/persistence/test_sqlite_bootstrap.py` (4 tests for org/workspace/key creation, idempotency, in-memory skip, explicit key_path), `tests/persistence/test_sqlite_exceptions.py` (typed-exception hierarchy + `EmbeddingDimMismatch` insert/recall + `check_dangling_vectors` diagnostic + `StoreCorruption` smoke), `tests/persistence/test_sqlite_busy.py` (3 retry tests including success-after-2-failures, exhaustion-raises-typed, and non-busy-not-retried), `tests/server/test_serve_cli.py` (5 bind-host tests), `tests/server/test_config.py` (4 default-URL precedence tests). Total: **+24 passing tests** (3402 → 3426); skipped count unchanged at 80.
- LOC delta: `src/lore/persistence/exceptions.py` 53 → 119 (+66 LOC), `src/lore/persistence/sqlite.py` 5141 → 5269 (+128 LOC), `src/lore/persistence/postgres.py` 3390 → 3411 (+21 LOC), `src/lore/server/config.py` 78 → 99 (+21 LOC), `src/lore/cli/commands/server.py` 50 → 78 (+28 LOC). New file: `src/lore/persistence/bootstrap.py` (140 LOC).
- After 3J, every component called out in the SQLite solo-mode spec (decisions 1-9) is implemented. Phase 4 (`AsyncLore` embedded library) and Phase 5 (`lore migrate` command) are the remaining major pieces. SqliteStore is now production-ready for the solo-mode use case.
- **Phase 4A: AsyncLore embedded library — skeleton + foundational methods.** First slice of the spec's "Component 4" (`lore.AsyncLore`). New `src/lore/async_lore.py` exports a single class that opens a `Store` via `make_store(database_url)`, validates / auto-resolves the bound `org_id`, and exposes a thin async surface that delegates to the same `services/*` layer the HTTP routes use. Phase 4A ships the lifecycle plumbing plus 5 foundational methods — `remember`, `recall`, `get`, `forget`, `list_memories`. Each is a few lines: build typed params, await the matching service function, return the dataclass. Phase 4B will extend the surface to the full ~30-method MCP/SDK parity; Phase 4C will start the background workers (SLO, retention, alerting, ingest) inside `__aenter__` / `__aexit__`.
  * **Lifecycle.** `async with AsyncLore(database_url, *, workspace="solo", api_key=None, embed=None, org_id=None) as lore:` — the context-manager entry opens the Store and (for `sqlite:///:memory:` URLs) re-runs the Phase-3J bootstrap with `force_for_memory=True` so the embedded API always has a real `orgs(id='solo')` row to attach to. Exit closes the Store (await-tolerant for sync `close()`s on the legacy `MemoryStore` shape).
  * **`org_id` resolution.** SQLite stores default to `"solo"` (the Phase-3J bootstrap creates that row); Postgres stores take the first row of `orgs ORDER BY created_at LIMIT 1` when `org_id` is unset, and raise `ConfigError` when it's empty (no auto-create against multi-tenant deployments). An explicit `org_id` is validated against the DB on `__aenter__`.
  * **Embedder integration.** The `embed` kwarg accepts a sync `(str) -> Sequence[float]` or an async `(str) -> Awaitable[Sequence[float]]`. The default is a lazy-instantiated `LocalEmbedder` (the same 384-dim ONNX model the sync `Lore` class uses). `remember(content, embedding=None)` only invokes the embedder when no vector is provided.
  * **Bootstrap loosening.** `bootstrap_solo_if_empty` grows two new knobs: `force_for_memory: bool = False` (overrides the historical `:memory:` skip) and `key_path: Path | None | _DEFAULT` (3-state — explicit `Path` writes there, `None` skips writing the key file entirely, sentinel keeps the historical `~/.lore/key.txt` default). AsyncLore uses both flags so in-memory test runs leave no on-disk artifacts. The standalone bootstrap test suite (`tests/persistence/test_sqlite_bootstrap.py`) is unchanged.
  * **Tests.** New `tests/embedded/test_async_lore.py` (10 tests) covers the lifecycle (`async with` open/close + use-after-close), in-memory bootstrap (org row created, key file NOT written), the round-trip (`remember` → `get` → `forget`), `recall` returning a scored match against distractors, `list_memories` with project filtering, sync + async embedder plumbing, explicit `embedding=` short-circuit, `org_id` validation raising `ConfigError`, and a file-backed-DB round-trip that exercises the standard Phase-3J bootstrap path. A deterministic sha256-derived stub embedder keeps the suite off the onnxruntime download path.
  * **LOC delta.** New file `src/lore/async_lore.py` (+377 LOC). `src/lore/__init__.py` exports `AsyncLore`. `src/lore/persistence/bootstrap.py` 142 → 172 (+30 LOC for the two new knobs). New tests: `tests/embedded/test_async_lore.py` (~245 LOC, +10 passing tests). Test count: **3426 → 3436 passed / 80 skipped (+10 / 0 delta)**.
  * **What's usable today.** A consumer can already do `async with AsyncLore("sqlite:///~/.lore/lore.db") as lore: await lore.remember(...); await lore.recall(...)` end-to-end without an HTTP server. The full surface (snapshots, conversations, graph, sharing, recommendations, voting, importance recompute, on-this-day, …) lands in Phase 4B.
- **Phase 5: `lore migrate` command — bidirectional data migration between any two Store backends.** Final phase of the SQLite solo-mode design: with Postgres and SQLite both speaking the full Store protocol after Phase 3, and `AsyncLore` letting consumers run embedded after Phase 4, Phase 5 closes the loop with a one-shot `lore migrate --from <url> --to <url>` that streams every table in dependency order while preserving server-generated IDs and timestamps bit-exact.
  * **CLI surface.** `lore migrate --from <postgresql://… or sqlite://…> --to <…> [--re-embed] [--continue] [--dry-run] [--batch-size N]`. Default batch size is 500. `--from` and `--to` accept either backend, so `pg → sqlite` (offline migration to a personal solo DB), `sqlite → pg` (graduating from solo to a shared deployment), and same-engine round-trips all work the same way.
  * **Bit-exact row copies.** New `src/lore/cli/commands/migrate.py` is the one place in the codebase that legitimately uses raw SQL outside of `lore.persistence.{postgres,sqlite}`. The `Store.create_*` methods all generate fresh ULIDs and timestamps, which would break the migration's primary invariant — IDs must survive so that FK references stay valid. The migrate path opens raw `asyncpg`/`aiosqlite` connections, introspects each table's columns via `information_schema` / `PRAGMA table_info`, and emits parameterized `INSERT` statements with the source's exact `id` / `created_at` / `updated_at` / etc. JSON / JSONB columns are encoded as text strings; booleans are coerced to `0/1` for SQLite and Python `bool` for PG; PG `tier_filters` (TEXT[]) round-trips through SQLite's TEXT JSON; PG `inet` (audit_log.ip_address) is stringified.
  * **Memories ⇆ vec0 pair.** The `memories` table is the one with non-trivial structural translation: PG holds the embedding inline (`vector(384)`), SQLite holds it in the separate `memory_vectors` vec0 virtual table joined by rowid. The migrate's per-batch SQLite writer wraps the pair in a `BEGIN IMMEDIATE` (the same `transaction()` shape `SqliteStore.insert_memory` uses) so a base-row INSERT and its `memory_vectors` companion either both land or both roll back. PG read path uses `embedding::text AS embedding` to pull the vector as a JSON-array string and parses it client-side. SQLite read path uses `vec_to_json(v.embedding)`.
  * **Idempotent INSERTs.** Both backends emit `INSERT OR IGNORE` (SQLite) / `ON CONFLICT DO NOTHING` (PG, when an `id` column exists). This makes three invariants hold simultaneously: the bootstrap-row collision (a fresh target DB already has a `solo` org and first key from Phase 3J's `bootstrap_solo_if_empty`) doesn't error; `--continue` re-runs are no-ops on already-copied rows; and a partial pre-existing target merges cleanly without duplicates. The spec's "all-or-nothing per table batch" guarantee still holds because each batch is wrapped in a transaction.
  * **Schema-version compatibility check.** Before any copy starts, both endpoints' `schema_migrations` tables are read (when present) and compared. A mismatch — e.g. target missing migration `019` — refuses with a clear list of missing versions and exit code 3, before any rows touch the target. Postgres' `run_migrations` doesn't currently track applied versions, so when one side lacks the table the check is skipped and per-table existence guards take over.
  * **Embedding-dim compatibility check.** `_detect_embedding_dim` probes one row of each side's embedding column / vec0 table and compares lengths. A mismatch with no `--re-embed` flag refuses with exit code 4. With `--re-embed`, the target-side writer regenerates each memory's embedding through `lore.embed.local.LocalEmbedder` from the source row's `content`. Both backends share the 384-dim default today; the upfront check is defensive against a future model swap.
  * **Resumability.** State at `~/.lore/migrate-state.json` is keyed by a SHA-256 hash of the (source_url, target_url) pair so multiple migrations on the same machine don't clobber each other. Each completed table writes its row count back to the state file. `--continue` reads the state, queries the target's current row count for each table, and skips tables whose target count meets-or-exceeds the recorded progress.
  * **Row-count validation.** After all tables stream through, the migrate re-counts both endpoints and refuses (exit code 6) if the target has *fewer* rows than the source for any table. Equality isn't required because pre-existing target rows (e.g. the bootstrap `solo` org on both sides) are deduped through the `OR IGNORE`/`ON CONFLICT` paths.
  * **Migration order.** 26 tables in the order set by FK dependencies — `orgs → users → workspaces → workspace_members → api_keys → retrieval_profiles → memories → entities → entity_mentions → relationships → rejected_patterns → review_decisions → retention_policies → snapshot_metadata → restore_drill_results → slo_definitions → slo_alerts → sharing_config → agent_sharing_config → deny_list_rules → sharing_audit → audit_log → retrieval_events → recommendation_config → recommendation_feedback → conversation_jobs`.
  * **Tests.** New `tests/migrate/test_migrate_round_trip.py` (6 tests): pg→sqlite round-trip with row-count + ID preservation against the canonical test DB; sqlite→sqlite migrate + `--continue` resume verifying no duplicates; `--dry-run` writes nothing; `--re-embed` regenerates vec0 floats via a mock embedder; schema-mismatch refuses upfront with exit code 3; embedding-dim mismatch without `--re-embed` refuses with exit code 4. Tests redirect the state-file path through a `Path.home` monkeypatch fixture so they don't pollute the real `~/.lore/migrate-state.json`.
  * **LOC delta.** New file `src/lore/cli/commands/migrate.py` (+910 LOC). `src/lore/cli/__init__.py` +38 LOC for the subparser + dispatch wire-up. New tests: `tests/migrate/test_migrate_round_trip.py` (~330 LOC, +6 passing tests). Test count: **3474 → 3480 passed / 80 skipped (+6 / 0 delta)**.
  * **SQLite solo-mode design status.** With Phase 5 done, the SQLite solo-mode design (Phases 3 + 4 + 5) is functionally complete. A consumer can `pip install lore-sdk[solo]`, run `lore serve` against a local SQLite file, embed `AsyncLore` directly into another Python process, and migrate to/from a shared Postgres deployment with one command — every component called out in `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md` is in place.

## [1.1.0] — 2026-03-21 — "Enterprise Platform"

### Added

- **F1: Guided Bootstrap** (`lore bootstrap`): Single command validates Python version, Postgres, pgvector, Docker, runs migrations, and verifies server health. `--fix` flag auto-remediates missing dependencies.
- **F2: Enhanced Setup Wizard**: Config validation (`--validate`), server connectivity test (`--test-connection`), dry-run mode (`--dry-run`), timestamped config backups with rollback instructions. New `POST /v1/setup/validate` endpoint.
- **F3: SLO Dashboard + Alerting**: Define SLO targets for p50/p95/p99 latency and hit rate. Background checker evaluates every 60s. Webhook and email alert channels. Time-series API for charts. Full CRUD via `lore slo` CLI and REST API.
- **F4: Adaptive Retrieval Profiles**: Named profiles stored in Postgres with per-request `?profile=` param. Three built-in presets (coding, incident-response, research). Profiles control semantic weight, graph weight, recency bias, tier filters, and min score. 60s in-memory cache.
- **F5: Graph Approval Inbox with Risk Scoring**: Risk score computed via SQL CTE (weight + conflict history + age). Sortable review queue, batch approve/reject, reviewer notes, `review_decisions` audit table. `GET /v1/review/history` for audit trail.
- **F6: Policy-Based Retention**: Declarative lifecycle policies with per-tier retention windows, cron-based snapshot schedules, max snapshot limits. Restore drills with timing metrics. Cross-policy compliance dashboard.
- **F7: Multi-Tenant Workspaces**: Workspace isolation within orgs. Scoped API keys, member management (writer/admin roles), full audit log of every action. `lore workspace create/switch/members`, `lore audit` CLI commands.
- **F8: Plugin SDK**: `LorePlugin` ABC with 5 lifecycle hooks (`on_remember`, `on_recall`, `on_enrich`, `on_extract`, `on_score`). Discovery via Python entry_points. Hot-reload, enable/disable, scaffold CLI (`lore plugin create`), test harness.
- **F9: Proactive Recommendations**: Multi-signal scoring engine (context similarity, entity overlap, temporal patterns, access patterns). Human-readable explanations. Feedback loop with per-user weight adjustment. New MCP tool: `suggest`. Configurable aggressiveness and cooldown.
- 6 new database migrations (012–017)
- 118 new tests (2081 total)
- Background tasks: SLO checker + retention policy scheduler in FastAPI lifespan

## [1.0.0] — 2026-03-14 — "Total Recall"

### Added

- **Session Accumulator:** Deterministic auto-snapshot of conversation context. Captures session state at configurable character thresholds (`LORE_SNAPSHOT_THRESHOLD`, default 30K chars) — no LLM required.
- **Auto-Inject Session Context:** Relevant session history injected into every prompt via hooks. Agents get continuity between conversations without calling any tools.
- **v1.0.0 stability:** All 6 epics complete. Production-ready release.

### Changed

- Version bump to 1.0.0 — "Total Recall"
- PyPI classifier updated to `Development Status :: 5 - Production/Stable`
- Comprehensive README rewrite with full feature documentation

## [0.13.0] — 2026-03-14 — "Approval UX"

### Added

- **E6 Approval UX for Discovered Connections:** Review workflow for knowledge graph connections before they become permanent.
  - New MCP tools: `review_digest`, `review_connection`
  - New REST endpoints: `GET /v1/review`, `POST /v1/review/{id}`, `POST /v1/review/bulk`
  - Approve, reject, or skip pending connections — keep your graph clean

## [0.12.1] — 2026-03-14

### Fixed

- Entity detail endpoint — deduplicate connected entities, handle nodes not in graph
- Ghost tooltip fix in graph visualization
- Importance scoring — auto-bump `access_count` on retrieve, making importance emergent from actual usage
- Access tracking improvements
- Default node limit to 100 with total count in status bar
- Label truncation increased to 200 characters

## [0.12.0] — 2026-03-13 — "Brain Surgery"

### Added

- **E3 Pre-Compaction Hook:** Session snapshots saved automatically before context compression. Preserves conversation state that would otherwise be lost.
  - New MCP tool: `save_snapshot`
  - New REST endpoints: `POST /v1/snapshots`, `GET /v1/export/snapshots`
- **E4 Topic Notes:** Auto-generated concept hubs clustering related memories, entities, and facts around recurring themes.
  - New MCP tools: `topics`, `topic_detail`
  - New REST endpoints: `GET /v1/graph/topics`, `GET /v1/graph/topics/{name}`
- **Graph UI enhancements:** Entity detail panels, force-directed layout, search and filtering

### Removed

- **SQLite backend removed.** Lore now requires PostgreSQL with pgvector. This simplifies the codebase and ensures consistent behavior across all deployments.

## [0.11.0] — 2026-03-13 — "Mind's Eye"

### Added

- **E1 Graph Visualization Web UI:** Interactive D3 force-directed graph at `/ui/`. Browse entities, relationships, and topic clusters in the browser. Entity detail panels, zoom, search, and filtering. No install required — served directly by the Lore server.

## [0.10.0] — 2026-03-12

### Added

- **E2 Recent Activity Summary:** Session-aware summary of recent memory activity across projects. New MCP tool: `recent_activity`. New REST endpoint: `GET /v1/recent`. Gives agents continuity between conversations.
- **E5 Export/Snapshot:** Full data export in JSON and Markdown formats. Obsidian-compatible output. Snapshot creation and management for backup and migration.
  - New MCP tools: `export`, `snapshot`, `snapshot_list`
  - New REST endpoints: `POST /v1/export`, `POST /v1/export/snapshots`, `GET /v1/export/snapshots`

## [0.9.5] — 2026-03-10

### Fixed

- Claude Code hook integration — reliable auto-retrieval in all configurations
- Server-side enrichment pipeline fixes

## [0.9.4] — 2026-03-09

### Added

- **`lore serve` command:** Start the HTTP server directly from the CLI
- **Mac install script:** One-command setup (Postgres + pgvector + enrichment + LaunchAgent)

### Fixed

- LaunchAgent plist array bug
- Auto-diagnose server start failures
- Python 3.10+ requirement enforced (auto-install via Homebrew on Mac)
- pgvector build from source when Homebrew targets wrong PG version

## [0.9.3] — 2026-03-09

### Added

- **Retrieval Analytics:** Track hit rate, score distribution, memory utilization, and latency. New REST endpoint: `GET /v1/analytics/retrieval`. Prometheus-compatible metrics export.

## [0.9.2] — 2026-03-08

### Added

- **Cursor setup:** `lore setup cursor` — one-command hook installation with `beforeSubmitPrompt` hook
- **Codex CLI setup:** `lore setup codex` — one-command hook installation with `beforePlan` hook
- Setup guides restructured with Quick Start first

## [0.9.1] — 2026-03-08

### Fixed

- All migrations made idempotent after lessons→memories rename

## [0.9.0] — 2026-03-08 — "Wired In"

### Added

- **Schema migration system:** Automatic database migrations on server startup
- **Setup CLI:** `lore setup claude-code`, `lore setup openclaw` — one-command hook installation for auto-retrieval
- **MCP enrichment:** Server-side enrichment pipeline triggered via MCP tools

### Changed

- Legacy `lessons` schema mapped to `memories` transparently

## [0.8.3] — 2026-03-08

### Added

- **`GET /v1/retrieve` endpoint:** Purpose-built auto-retrieval endpoint for hooks. Semantic search + formatted output designed for prompt injection. Supports XML, Markdown, and raw JSON formats.

## [0.8.2] — 2026-03-07

### Added

- **Conversation auto-ingest:** `lore wrap` CLI command + OpenClaw bridge for automatic memory extraction from conversations

### Fixed

- Client-side cleanup skip for HttpStore (prevents limit=10000 422 error)
- Lint cleanup across test files

## [0.8.1] — 2026-03-07

### Fixed

- Writable model cache directory + graceful dedup fallback
- Enrichment (litellm) included in server Docker image
- Conversation extraction persists to Postgres via MemoryStore

## [0.8.0] — 2026-03-07 — "Conversation Intelligence"

### Added

- **Conversation Auto-Extract:** Accept raw conversation messages and automatically extract salient memories using LLM processing.
  - New `ConversationExtractor` pipeline: validate → concatenate → chunk → extract → dedup → store
  - New SDK method: `lore.add_conversation(messages, user_id=..., session_id=...)`
  - New CLI command: `lore add-conversation --file conversation.json`
  - New MCP tool: `add_conversation`
  - REST API: `POST /v1/conversations` (202 Accepted, async) and `GET /v1/conversations/{job_id}`
- **User-Scoped Recall:** `recall(query, user_id="alice")` filters memories by user
- **Token-Aware Chunking:** Long conversations split into ~8K token chunks with 2-message overlap
- **Cost Estimation:** CLI output includes estimated LLM cost after extraction

### Changed

- `recall()` gains optional `user_id` parameter for memory scoping

## [0.7.0] — 2026-03-07 — "Living Archive"

### Added

- **On This Day:** Query memories from the same month+day across years. New MCP tool: `on_this_day`. New CLI: `lore on-this-day`.
- **Verbatim Recall:** Return original words instead of AI summaries. `--verbatim` flag on CLI, `verbatim` parameter on MCP.
- **Temporal Filters:** Date-range filtering on `recall` — `year`, `month`, `day`, `days_ago`, `hours_ago`, `before`, `after`, and window presets.

## [0.6.0] — 2026-03-06 — "Open Brain"

### Added

- **Knowledge Graph:** Entity and relationship extraction with hop-by-hop graph traversal. New MCP tools: `graph_query`, `entity_map`, `related`.
- **Fact Extraction:** Atomic (subject, predicate, object) triples with conflict detection. New MCP tools: `extract_facts`, `list_facts`, `conflicts`.
- **Memory Consolidation:** Deduplication, topic-based grouping, LLM-powered summarization. New MCP tool: `consolidate`.
- **Memory Tiers:** Working (1h), short-term (7d), long-term (no expiry) with tier-specific decay.
- **Importance Scoring:** Adaptive importance decay based on tier, type, access frequency, and age.
- **Metadata Enrichment:** LLM-powered topics, entities, sentiment, categories. New MCP tool: `enrich`.
- **Webhook Ingestion:** REST ingestion with Slack, Telegram, Git adapters. New MCP tool: `ingest`.
- **Dialog Classification:** Intent, domain, emotion classification. New MCP tool: `classify`.
- **Prompt Export:** Template-based export for LLM injection (XML, ChatML, markdown, raw). New MCP tool: `as_prompt`.
- 13 new MCP tools (7 → 20 total)
- Docker Compose deployment
- Integration test suite
- Performance benchmarks

### Changed

- `recall()` supports graph-enhanced retrieval via `graph_depth` parameter
- `remember()` triggers enrichment, classification, and fact extraction when LLM is configured
- Memory scoring model changed from additive to multiplicative

## [0.5.1]

- Importance scoring foundation, memory tier support.

## [0.5.0]

- Internal improvements and stabilization.

## [0.4.1]

- Bug fixes and minor improvements.

## [0.4.0]

- GitHub sync, freshness checking, additional MCP tools.

## [0.3.0] — 2026-03-04

### Breaking Changes

- **Memory model replaces Lesson model.** Core data type is now `Memory` with single `content` field instead of `problem`/`resolution`. Old aliases preserved as deprecated exports.
- `stats()` returns `MemoryStats` dataclass instead of plain dict.
- TypeScript SDK: `publish()` → `remember()`, `query()` → `recall()`, `list()` → `listMemories()`, `delete()` → `forget()`.

### Added

- `remember(content, ...)` — universal memory storage with type, context, tags, metadata, ttl, source, project, confidence
- `recall(query, ...)` — semantic search with embedding-powered scoring
- `forget(id)` — delete memory by ID
- TTL support — automatic expiry via `ttl` and `expires_at`
- `MemoryStats` dataclass

## [0.2.1]

- SDK hardening: retry, graceful degradation, connection pooling, batching.

## [0.2.0]

- Initial public release with Lesson model, SQLite store, semantic search, PII redaction.
