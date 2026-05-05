# Phase 1B — Graph Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task below is dispatched to a fresh implementer subagent with task-specific code spelled out in the dispatch prompt (the controlling Claude has the full graph slice map and synthesizes per-task detail at dispatch time).

**Goal:** Apply the Phase 1A pattern (Store abstraction + Service layer + route refactor) to the graph slice. After this plan: every handler in `routes/graph/*` and `routes/review.py` calls services exclusively; all graph SQL lives in `PostgresStore`'s new `GraphOps` methods.

**Architecture:** No new architecture. Same Store / Services / Routes layering as Phase 1A. ~20 new GraphOps methods on Store; 3 new service modules (`services/graph/{entities,graph,review}.py`); 14 route handlers refactored.

**Tech Stack:** Same as Phase 1A. No new runtime deps. Postgres test DB at `localhost:5432` / `lore_test` reused.

**Spec reference:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`. Section "Components" (1, 2). Phase 1A plan: `docs/superpowers/plans/2026-05-05-phase-1a-foundation-and-memories.md` — read its task structure and mirror the TDD discipline.

---

## File structure

### Created in this plan

| Path | Responsibility |
|---|---|
| `src/lore/services/graph/__init__.py` | Package marker |
| `src/lore/services/graph/entities.py` | `get_entity`, `list_topics`, `get_topic_detail`, `get_entity_with_connections` |
| `src/lore/services/graph/graph.py` | `get_graph_data`, `search_graph_memories`, `get_memory_with_graph`, `get_stats`, `get_clusters`, `get_timeline` |
| `src/lore/services/graph/review.py` | `list_pending_reviews`, `review_relationship`, `bulk_review`, plus pure `_compute_risk_score` helper |
| `tests/persistence/test_contract_graph.py` | Contract tests for the 20 GraphOps methods |
| `tests/services/test_graph_entities.py` | Service tests for entities + topics |
| `tests/services/test_graph_graph.py` | Service tests for graph viz + stats + timeline |
| `tests/services/test_graph_review.py` | Service tests for review workflow + risk score |
| `tests/server/test_graph_routes.py` | New route tests (current code has none for graph routes) |

### Modified in this plan

| Path | Change |
|---|---|
| `src/lore/persistence/types.py` | Add `NewEntity`, `StoredEntity`, `NewMention`, `StoredMention`, `NewRelationship`, `StoredRelationship`, plus stat/timeline result rows |
| `src/lore/persistence/protocol.py` | Add ~20 GraphOps methods to `Store` Protocol |
| `src/lore/persistence/postgres.py` | Implement all new GraphOps methods on `PostgresStore` |
| `src/lore/server/routes/graph/memories.py` | All 3 handlers call services |
| `src/lore/server/routes/graph/entities.py` | Handler calls service |
| `src/lore/server/routes/graph/stats.py` | All 3 handlers call services |
| `src/lore/server/routes/graph/topics.py` | Both handlers call services |
| `src/lore/server/routes/graph/_helpers.py` | Drop `_table_exists` (PostgresStore assumes migrated DB) |
| `src/lore/server/routes/review.py` | All 6 handlers call services |
| `scripts/check_routes_no_sql.py` | Add the migrated graph route files to `MIGRATED_ROUTES`; extend allowlist if needed |
| `CHANGELOG.md`, `docs/architecture.md` | Note GraphOps slice landed |

### Out of scope (deferred)

- `src/lore/graph/` package (sync legacy `Store`-based logic used by `Lore` client class) — stays on `lore.store.base.Store`. Phase 4 (`AsyncLore`) revisits.
- `src/lore/extract/` fact extraction pipeline.
- Migration of `routes/conversations.py` (does not touch graph tables; future phase).
- SQLite-specific concerns (`DISTINCT ON`, `date_trunc` rewrites) — Phase 3.

---

## Tasks (one task = one commit)

Each task follows the Phase 1A discipline: failing test first, run pytest, implement, run pytest, commit. The controlling Claude provides per-task code in the implementer dispatch prompt.

### Foundation — types, protocol

**T1 — Add entity/mention/relationship dataclasses to `lore.persistence.types`**
Add `NewEntity`, `StoredEntity`, `NewMention`, `StoredMention`, `NewRelationship`, `StoredRelationship`, `GraphStats`, `TimelineBucketRow`, `PendingRelationshipRow` as `@dataclass(frozen=True, slots=True)`. Tests in `tests/persistence/test_types.py` for each.
Commit: `feat(persistence): add graph dataclasses to types`

**T2 — Extend `Store` protocol with GraphOps slice**
Add the 20 GraphOps methods to `Store` Protocol with full async signatures + 1-line docstrings. Update `tests/persistence/test_protocol.py` to assert presence + async-ness.
Commit: `feat(persistence): extend Store protocol with GraphOps slice`

### PostgresStore — entity ops

**T3 — `upsert_entity` + `get_entity` + contract tests**
Insert/get round-trip; name normalization happens in service, not store. Stub remaining 18 GraphOps methods with `NotImplementedError` so the protocol smoke test passes.
Commit: `feat(persistence): GraphOps.upsert_entity + get_entity`

**T4 — `get_entity_by_name` + `list_entities` + tests**
Case-sensitive name lookup (services normalize); list with `entity_type` and `min_mentions` filters.
Commit: `feat(persistence): GraphOps entity listing`

**T5 — `update_entity_counts` + `delete_entity` + tests**
Atomic mention_count delta + last_seen_at; cascade delete verifies mentions/relationships removed.
Commit: `feat(persistence): GraphOps entity mutations`

### PostgresStore — mention ops

**T6 — `save_mention` + `get_mentions_for_memory` + `get_mentions_for_entity` + `count_memories_for_entity` + tests**
INSERT ON CONFLICT DO NOTHING by `(entity_id, memory_id)` — verify idempotency. Distinct-memory-count uses `COUNT(DISTINCT memory_id)`.
Commit: `feat(persistence): GraphOps mention operations`

### PostgresStore — relationship ops

**T7 — `save_relationship` + `get_relationship` + `get_active_relationship` + tests**
Active = `valid_until IS NULL`. Test: insert two with same (source,target,type) — first valid_until=NULL, second valid_until=NULL — second insert should hit unique constraint OR upgrade-prior-to-expired logic (decide based on existing graph code; mirror its semantics).
Commit: `feat(persistence): GraphOps.save/get_relationship`

**T8 — `list_relationships_for_entity` + `update_relationship_status` + `update_relationship_weight` + `expire_relationship` + tests**
List with optional status filter; status transitions ('approved'/'rejected'/'pending'); weight is a float; expire sets valid_until=now().
Commit: `feat(persistence): GraphOps.relationship_lifecycle`

**T9 — `list_pending_relationships` + `save_rejected_pattern` + tests**
Pending = `status='pending'` with optional `rel_type` filter; rejected pattern UPSERT by (source_name, target_name, rel_type) with optional `source_memory_id` and `reason`.
Commit: `feat(persistence): GraphOps.review_workflow`

### PostgresStore — traversal + stats

**T10 — `query_relationships` + tests**
The hop query used by GraphTraverser. Direction ∈ {"inbound","outbound","both"}; `active_only=True` adds `valid_until IS NULL`; `at_time` adds `valid_from <= $X AND (valid_until IS NULL OR valid_until > $X)`; `rel_types` is `IN (...)` filter.
Commit: `feat(persistence): GraphOps.query_relationships`

**T11 — `get_graph_stats` + `get_timeline_buckets` + `get_memories_by_entities` + tests**
Stats accumulates counts in one method (single connection, multiple queries internally). Timeline takes a validated `trunc` literal ('hour'|'day'|'week'|'month' — service validates before calling). `get_memories_by_entities` returns memories that mention any of the given entity IDs, optionally excluding a memory ID, ordered by `created_at DESC`.
Commit: `feat(persistence): GraphOps stats + timeline + memories-by-entities`

### Services

**T12 — `services/graph/__init__.py` + `services/graph/entities.py` + tests**
Pure async functions wrapping store ops. Includes name normalization (`name.strip().lower()`) before `get_entity_by_name` calls. Topic detail enriches relationships with direction (outgoing/incoming) — pure logic, no SQL.
Commit: `feat(services): graph entity service + topics`

**T13 — `services/graph/graph.py` + tests**
Visualization (orphan filter in Python after fetching), full-text search (delegates to `store.search_memories`?? no — graph search is `ILIKE` against memories.content; add a thin `store.search_memories_ilike` op OR have the service call existing `recall_by_embedding`?). Decision: add a small new GraphOps method `search_memories_text(query, limit)` (NOT a full-text engine — just `WHERE content ILIKE` for the UI). Add it in T11 retrospectively if missed.

Stats accumulation: service combines result of `store.get_graph_stats` + project filter into typed result. Clusters: service calls `store.list_memories(MemoryFilter(limit=10000))` and groups in Python by `group_by` ∈ {"project","type","tier"}.

Timeline: service validates `bucket` ∈ {"hour","day","week","month"} → `trunc` interval. Calls `store.get_timeline_buckets(trunc=...)`.
Commit: `feat(services): graph viz + stats + timeline + clusters`

**T14 — `services/graph/review.py` + tests + risk-score pure function**
Pull `_compute_risk_score` from `routes/review.py:83-120` into a pure function in this module. Service calls store.list_pending_relationships, scores each, returns sorted. `review_relationship`: action ∈ {"approve","reject"}; on reject, calls `save_rejected_pattern`. `bulk_review`: same in a loop (no DB transaction grouping needed; each op idempotent).
Commit: `feat(services): graph review workflow + risk scoring`

### Route refactors

**T15 — Refactor `routes/graph/memories.py` (3 handlers)**
GET /v1/ui/graph → `get_graph_data`; POST /v1/ui/search → `search_graph_memories`; GET /v1/ui/memory/{id} → `get_memory_with_graph`. Remove inline SQL; remove `_table_exists` reads. Add Depends(get_store) to each handler.
Commit: `refactor(routes): graph/memories.py uses graph services`

**T16 — Refactor `routes/graph/entities.py` (1 handler)**
GET /v1/ui/entity/{id} → `get_entity_with_connections`.
Commit: `refactor(routes): graph/entities.py uses graph services`

**T17 — Refactor `routes/graph/stats.py` (3 handlers)**
GET /v1/ui/stats → `get_stats`; GET /v1/ui/graph/clusters → `get_clusters`; GET /v1/ui/timeline → `get_timeline`.
Commit: `refactor(routes): graph/stats.py uses graph services`

**T18 — Refactor `routes/graph/topics.py` (2 handlers)**
GET /v1/ui/topics → `list_topics`; GET /v1/ui/topics/{name} → `get_topic_detail`.
Commit: `refactor(routes): graph/topics.py uses graph services`

**T19 — Drop `_table_exists` from `routes/graph/_helpers.py`**
After T15-T18, no remaining caller. Delete the helper. If the function is referenced by `routes/graph/__init__.py` or anywhere else, clean those up. Run `grep -r "_table_exists" src/` to confirm.
Commit: `refactor(routes): drop graph _table_exists helper`

**T20 — Refactor `routes/review.py` handlers (batched, ~6 handlers)**
List pending; approve/reject single; bulk approve/reject; list rejected patterns. Each calls a service function from `services/graph/review.py`. Risk-score logic gone from this file. Verify no SQL left.
Commit: `refactor(routes): review.py uses graph review service`

### Tests + cleanup

**T21 — Add new graph route tests**
`tests/server/test_graph_routes.py`: 14 tests covering each handler with `FakeStore` mocks. The pattern is established in `tests/test_memories_server.py`.
Commit: `test(server): add graph route tests with FakeStore mocks`

**T22 — Update CI guard**
`scripts/check_routes_no_sql.py` MIGRATED_ROUTES adds `routes/graph/{memories,entities,stats,topics,_helpers}.py` and `routes/review.py`. Allowlist any genuine remaining `pool = await get_pool()` references (likely none — graph helpers are fully migrated).
Commit: `chore(ci): extend routes-no-SQL guard to graph slice`

**T23 — Update docs**
CHANGELOG.md Unreleased section: GraphOps slice + graph services. `docs/architecture.md`: persistence-layer section grows to mention GraphOps.
Commit: `docs: document graph slice migration`

**T24 — Final verification**
Run `pytest tests/ 2>&1 | tail -3` — must show 2127 + new tests passing, 0 failures. Run `python scripts/check_routes_no_sql.py` — must exit 0. No commit.

---

## Self-review

- All 14 graph route handlers (8 graph/ + 6 review) refactored to call services.
- All 20 GraphOps methods implemented + contract-tested.
- Three service modules + three test files match.
- New route tests cover handlers (graph routes had no tests before).
- CI guard extended.

### Known risks (don't block this plan)

- **No graph route tests today.** T21 adds them; if any handler has subtle behavior the tests miss, it surfaces in production. Mitigation: integration tests + manual smoke test.
- **`DISTINCT ON` and `date_trunc` are Postgres-specific.** PostgresStore uses them natively; SQLite port (Phase 3) needs subquery rewrite + `strftime` substitute. Document in Phase 3 risk register.
- **`entity_mentions.memory_id` FK references `lessons` (pre-009).** FK resolves correctly post-migration 009; contract tests confirm.
- **`save_relationship` semantics around active edges.** Existing graph code uses an upsert-then-expire pattern; the contract test for T7 must mirror this exactly. If the legacy code's behavior is unclear, escalate.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — Fresh implementer per task; controlling Claude provides per-task code at dispatch time using the graph slice map as reference.

**2. Inline Execution** — Apply tasks in this session via executing-plans.

Which approach?
