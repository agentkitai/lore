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
