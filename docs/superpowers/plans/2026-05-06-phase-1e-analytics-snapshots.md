# Phase 1E — Analytics + Snapshots Slice (AnalyticsOps) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task below is dispatched to a fresh implementer subagent with task-specific code spelled out in the dispatch prompt (the controlling Claude has the full slice map and synthesizes per-task detail at dispatch time).

**Goal:** Apply the Phase 1A–1D pattern (Store abstraction + Service layer + route refactor) to the analytics + snapshots cleanup. After this plan: every handler in `routes/snapshots.py` calls services exclusively; the three remaining allowlisted helpers in `routes/retrieve.py` (`_record_retrieval_event`, `_bump_access_counts`, `_fetch_session_snapshots`) and the two in `routes/memories.py` (`_enrich_memory`, `record_access`) all flow through services + Store. Adds an `AnalyticsOps` slice (4 methods) and a single `MemoryOps` extension (`enrich_memory_meta`).

**Architecture:** No new architecture. Same Store / Services / Routes layering as 1A–1D. ~5 new methods on Store (4 new in `AnalyticsOps`, 1 new in `MemoryOps`); one new service module (`services/snapshots.py`); existing `services/retrieve.py` and `services/memories.py` extended; 3 route files refactored.

**Tech Stack:** Same as 1A–1D. No new runtime deps. Postgres test DB at `localhost:5432` / `lore_test` reused.

**Spec reference:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`. Section "Components" (1, 2). Phase 1D plan: `docs/superpowers/plans/2026-05-06-phase-1d-identity.md` — the immediate template.

**Bug fix bundled in this phase:** `routes/snapshots.py` currently INSERTs into non-existent `tier` and `type` columns on `memories` (the columns don't exist in any migration; verified via `\d memories`). The reader path `_fetch_session_snapshots` queries `meta->>'type' = 'session_snapshot'`, so reads expect `type` in `meta`. The refactor moves `tier` and `type` into the `meta` dict at insert time, matching the read pattern. This unblocks any caller of `POST /v1/snapshots` on a current-schema DB.

---

## File structure

### Created in this plan

| Path | Responsibility |
|---|---|
| `src/lore/services/snapshots.py` | Session-snapshot service module — owns the snapshot tag list, `meta` construction (incl. `type`/`tier` keys), and `session_id` defaulting (uses `uuid4()[:12]` like the current route) |
| `tests/persistence/test_contract_analytics.py` | Contract tests for the 4 `AnalyticsOps` methods + the `MemoryOps.enrich_memory_meta` extension |
| `tests/services/test_snapshots.py` | Service tests for snapshot construction (tags, meta) + delegation to `MemoryOps.insert_memory` |
| `tests/server/test_snapshots_routes.py` | Route tests for the snapshots handler using `FakeStore` mocks |

### Modified in this plan

| Path | Change |
|---|---|
| `src/lore/persistence/types.py` | Add `NewRetrievalEvent` dataclass |
| `src/lore/persistence/protocol.py` | Add 4 `AnalyticsOps` methods + 1 `MemoryOps` extension (`enrich_memory_meta`) to `Store` Protocol |
| `src/lore/persistence/postgres.py` | Implement all 5 new methods on `PostgresStore` |
| `src/lore/persistence/__init__.py` | Re-export `NewRetrievalEvent` |
| `src/lore/services/retrieve.py` | Add `record_retrieval_event`, `bump_access_counts`, `recent_session_snapshots` service functions (with logged-and-swallowed error semantics for fire-and-forget callers) |
| `src/lore/services/memories.py` | Add `enrich_memory_async` and `record_memory_access` service functions |
| `src/lore/server/routes/snapshots.py` | Single handler calls services; drops inline SQL; bug fix: `type`/`tier` go into `meta` |
| `src/lore/server/routes/retrieve.py` | Drops the 3 helpers (now in `services/retrieve.py`); fire-and-forget `asyncio.create_task` calls now invoke service functions; the `_FORMATTERS`-related code stays |
| `src/lore/server/routes/memories.py` | Drops `_enrich_memory` (moved to `services/memories.py`); `record_access` handler delegates to service |
| `scripts/check_routes_no_sql.py` | Add `routes/snapshots.py` to `MIGRATED_ROUTES` (10 → 11); REMOVE the entire allowlist entries for `routes/memories.py` and `routes/retrieve.py` (both files are now fully migrated) |
| `tests/persistence/test_types.py`, `tests/persistence/test_protocol.py` | Extend to cover the new dataclass + protocol methods |
| `tests/server/test_retrieve.py`, `tests/test_memories_server.py`, `tests/test_enrichment_memories.py` | Redirect mocks if any test depends on the inline-helper paths |
| `CHANGELOG.md`, `docs/architecture.md` | Note `AnalyticsOps` slice landed |

### Out of scope (deferred)

- **`lore/server/auth.py` middleware migration** — the hot-path key lookup at `auth.py:198` and the `last_used_at` update at `auth.py:255` still call `get_pool()`. Reserved for the auth-middleware phase.
- **`routes/conversations.py` and `routes/recommendations.py`** — Phase 1F or 1G.
- **A new `session_snapshots` table** — there isn't one and we aren't adding one. Snapshots stay as `memories` rows tagged via `meta.type`.
- **Migration to add `tier`/`type` columns to memories** — we deliberately move both into `meta` instead, matching the read path; no schema change needed.

---

## Tasks (one task = one commit)

Each task follows the Phase 1A/1B/1C/1D discipline: failing test first, run pytest, implement, run pytest, commit.

### Foundation — types, protocol

**T1 — Add `NewRetrievalEvent` dataclass to `lore.persistence.types`**

Add `NewRetrievalEvent` as `@dataclass(frozen=True, slots=True)`:
```python
@dataclass(frozen=True, slots=True)
class NewRetrievalEvent:
    org_id: str
    query: str
    results_count: int
    scores: Sequence[float]
    memory_ids: Sequence[str]
    avg_score: Optional[float]
    max_score: Optional[float]
    min_score_threshold: Optional[float]
    query_time_ms: Optional[float]
    project: Optional[str] = None
    format: Optional[str] = None
```

(No `id` — `retrieval_events.id` is BIGSERIAL.) Re-export from `lore/persistence/__init__.py`. Round-trip + immutability tests in `tests/persistence/test_types.py`.

Commit: `feat(persistence): add NewRetrievalEvent dataclass`

**T2 — Extend `Store` Protocol with `AnalyticsOps` slice + `MemoryOps.enrich_memory_meta`**

Under a new `# ── AnalyticsOps ────` section after `# ── AuthOps ────`:
```python
async def record_retrieval_event(self, event: NewRetrievalEvent) -> None: ...
async def bump_access_counts(self, org_id: str, memory_ids: Sequence[str]) -> None: ...
async def record_memory_access(self, org_id: str, memory_id: str) -> Optional[StoredMemory]: ...
async def list_recent_session_snapshots(
    self, org_id: str, *, project: Optional[str] = None,
    exclude_ids: Sequence[str] = (), limit: int = 3,
) -> Sequence[StoredMemory]: ...
```

Inside the existing `# ── MemoryOps ────` section, add ONE new method:
```python
async def enrich_memory_meta(self, memory_id: str, enrichment_data: Mapping[str, Any]) -> None: ...
```

Update `tests/persistence/test_protocol.py` with `REQUIRED_ANALYTICS_OPS` set + 2 new tests, and extend the existing `REQUIRED_MEMORY_OPS` set to include `enrich_memory_meta`.

Commit: `feat(persistence): extend Store protocol with AnalyticsOps slice + MemoryOps.enrich_memory_meta`

### PostgresStore — analytics ops

**T3 — `record_retrieval_event` + `bump_access_counts` + contract tests**

- `record_retrieval_event(event)`: SQL INSERT into `retrieval_events`. JSONB columns (`scores`, `memory_ids`) via `json.dumps(list(...))` + `::jsonb`. No RETURN value (fire-and-forget; auto-incrementing id is server-side).
  ```sql
  INSERT INTO retrieval_events
      (org_id, query, results_count, scores, memory_ids,
       avg_score, max_score, min_score_threshold, query_time_ms, project, format)
  VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10, $11)
  ```

- `bump_access_counts(org_id, memory_ids)`: copy the SQL from the existing `_bump_access_counts` helper at `routes/retrieve.py:298-316` exactly. Multi-row UPDATE + importance recompute.
  ```sql
  UPDATE memories
  SET access_count = COALESCE(access_count, 0) + 1,
      last_accessed_at = now(),
      importance_score = COALESCE(confidence, 1.0)
          * GREATEST(0.1, 1.0 + (COALESCE(upvotes, 0) - COALESCE(downvotes, 0)) * 0.1)
          * (1.0 + ln(COALESCE(access_count, 0) + 2) / ln(2) * 0.1)
  WHERE id = ANY($1) AND org_id = $2
  ```
  No-op when `memory_ids` is empty (early return).

- Stub the remaining 3 methods (`record_memory_access`, `list_recent_session_snapshots`, `enrich_memory_meta`) with `NotImplementedError` so the protocol smoke test passes.

Contract tests in NEW file `tests/persistence/test_contract_analytics.py`:
- `test_record_retrieval_event_inserts_row` — insert event; verify with raw `SELECT COUNT(*) FROM retrieval_events WHERE org_id=$1`.
- `test_record_retrieval_event_with_empty_results` — `results_count=0`, `scores=[]`, `memory_ids=[]` works.
- `test_bump_access_counts_increments` — insert two memories, bump both, verify `access_count` went up by 1.
- `test_bump_access_counts_empty_list_is_noop` — empty `memory_ids` doesn't error.
- `test_bump_access_counts_org_isolation` — bump under wrong org doesn't change rows.

Commit: `feat(persistence): AnalyticsOps.record_retrieval_event + bump_access_counts`

**T4 — `record_memory_access` + `list_recent_session_snapshots` + contract tests**

- `record_memory_access(org_id, memory_id)`: SQL identical to existing `routes/memories.py:240-250`:
  ```sql
  UPDATE memories
  SET access_count = COALESCE(access_count, 0) + 1,
      last_accessed_at = now(),
      importance_score = (
          confidence
          * GREATEST(0.1, 1.0 + (upvotes - downvotes) * 0.1)
          * (1.0 + ln(COALESCE(access_count, 0) + 2) / ln(2) * 0.1)
      ),
      updated_at = now()
  WHERE id = $1 AND org_id = $2
  RETURNING id, org_id, content, context, tags, confidence, source, project,
            created_at, updated_at, expires_at, upvotes, downvotes,
            meta, importance_score, access_count, last_accessed_at
  ```
  Returns `Optional[StoredMemory]` via `_row_to_stored`. None when row missing (caller handles 404).

- `list_recent_session_snapshots(org_id, *, project=None, exclude_ids=(), limit=3)`: SQL similar to existing `_fetch_session_snapshots` at `routes/retrieve.py:330-360`:
  ```sql
  SELECT id, org_id, content, context, tags, confidence, source, project,
         created_at, updated_at, expires_at, upvotes, downvotes,
         meta, importance_score, access_count, last_accessed_at
  FROM memories
  WHERE org_id = $1
    AND (expires_at IS NULL OR expires_at > now())
    AND meta->>'type' = 'session_snapshot'
    AND created_at > now() - interval '24 hours'
    [AND project = $N if project is not None]
    [AND id != ALL($N) if exclude_ids non-empty]
  ORDER BY created_at DESC
  LIMIT $N
  ```
  Build params dynamically. Returns `Sequence[StoredMemory]` via `_row_to_stored`.

Contract tests:
- `test_record_memory_access_increments_and_returns_row` — round-trip; verify access_count+=1, last_accessed_at populated, importance_score recomputed.
- `test_record_memory_access_returns_none_when_missing`.
- `test_record_memory_access_org_isolation` — wrong org returns None.
- `test_list_recent_session_snapshots_returns_recent` — insert a memory with `meta.type='session_snapshot'`, list returns it.
- `test_list_recent_session_snapshots_excludes_old` — insert with `created_at = now() - interval '25 hours'` (raw SQL), list excludes it.
- `test_list_recent_session_snapshots_filters_project` — list with project filter.
- `test_list_recent_session_snapshots_excludes_ids` — pass `exclude_ids=[mem_id]`, that mem is filtered out.
- `test_list_recent_session_snapshots_limit_respected`.

Commit: `feat(persistence): AnalyticsOps.record_memory_access + list_recent_session_snapshots`

**T5 — `MemoryOps.enrich_memory_meta` + contract tests**

- `enrich_memory_meta(memory_id, enrichment_data)`: SQL identical to existing `_enrich_memory` at `routes/memories.py:113-117`:
  ```sql
  UPDATE memories SET
      meta = jsonb_set(COALESCE(meta, '{}'::jsonb), '{enrichment}', $2::jsonb),
      updated_at = now()
  WHERE id = $1
  ```
  No RETURN value. JSONB encode the dict via `json.dumps(dict(enrichment_data))`.
- No-op semantics when `memory_id` doesn't exist (UPDATE matches 0 rows; no error).

Contract tests:
- `test_enrich_memory_meta_sets_enrichment_key` — create memory, enrich, verify `meta.enrichment` populated.
- `test_enrich_memory_meta_overwrites_existing_enrichment` — enrich twice with different data; second wins.
- `test_enrich_memory_meta_preserves_other_meta_keys` — set `meta = {"foo": "bar"}` on insert; after enrichment, `meta = {"foo": "bar", "enrichment": {...}}`.
- `test_enrich_memory_meta_silent_on_missing_id` — call with non-existent id; no exception.

Commit: `feat(persistence): MemoryOps.enrich_memory_meta`

### Services

**T6 — `services/snapshots.py` + service tests**

Module structure:
```python
"""Snapshots service — session-snapshot creation as tagged memories."""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from lore.persistence import NewMemory, Store, StoredMemory


def _make_session_id() -> str:
    return uuid.uuid4().hex[:12]


async def create_snapshot(
    store: Store,
    *,
    org_id: str,
    content: str,
    title: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    project: Optional[str] = None,
) -> StoredMemory:
    """Create a session snapshot stored as a tagged memory.

    Snapshots aren't a separate table — they're memories with
    meta.type='session_snapshot' and tags=['session_snapshot', session_id, *user_tags].
    """
    sid = session_id or _make_session_id()
    snap_title = title or content[:80].strip()
    all_tags = ("session_snapshot", sid, *(tags or ()))
    meta = {
        "session_id": sid,
        "title": snap_title,
        "extraction_method": "raw",
        "type": "session_snapshot",   # bug fix: was a non-existent column
        "tier": "long",                # bug fix: was a non-existent column
    }
    nm = NewMemory(
        org_id=org_id,
        content=content,
        embedding=[0.0] * 384,    # snapshots aren't recall targets; placeholder
        tags=all_tags,
        confidence=1.0,
        project=project,
        meta=meta,
    )
    return await store.insert_memory(nm)
```

(Decision: snapshots have a placeholder zero-vector embedding because the existing route NEVER computed an embedding — its INSERT skipped the `embedding` column entirely. Since `memories.embedding` is NOT NULL with 384 dims, the existing INSERT also bypassed that constraint OR the column allows NULL. Verify schema before finalizing this. If schema requires non-null embedding, we can pass a 384-zero vector OR call the embedder for snapshots — but that's a behavior change. **Default: pass zero-vector to preserve "no semantic embedding for snapshots" intent**.)

Service tests in `tests/services/test_snapshots.py`:
- `test_create_snapshot_inserts_with_session_snapshot_tags` — verify tags include `"session_snapshot"` and the session_id.
- `test_create_snapshot_generates_session_id_when_missing` — no session_id passed; verify one is generated.
- `test_create_snapshot_uses_provided_session_id`.
- `test_create_snapshot_meta_contains_type_and_tier` — verify `meta.type == 'session_snapshot'` and `meta.tier == 'long'`.
- `test_create_snapshot_default_title_is_truncated_content` — title omitted → first 80 chars.
- `test_create_snapshot_passes_through_project`.

Use the `store` fixture from `tests/services/conftest.py` (real Postgres).

Commit: `feat(services): snapshots service + meta-keyed type/tier`

**T7 — Extend `services/retrieve.py` with analytics functions**

Add three new functions:

1. `async def record_retrieval_event(store, *, auth, query_text, memories, min_score, elapsed_ms, fmt, effective_project) -> None`:
   - Build `NewRetrievalEvent` from the inputs (extract `scores`, `memory_ids`, compute `avg_score`/`max_score`).
   - Call `store.record_retrieval_event(event)`.
   - Emit Prometheus metrics (lifted verbatim from current `_record_retrieval_event` body at `routes/retrieve.py:251-296`).
   - Wrap in `try/except Exception: logger.warning(...)` — fire-and-forget.

2. `async def bump_access_counts(store, org_id, memory_ids) -> None`:
   - Passthrough to `store.bump_access_counts`.
   - Wrap in `try/except Exception: logger.warning(...)`.

3. `async def recent_session_snapshots(store, *, org_id, project, exclude_ids, limit=3) -> Sequence[StoredMemory]`:
   - Passthrough to `store.list_recent_session_snapshots`.
   - On error: log and return `[]` (the route currently swallows errors and returns no snapshots).

Service tests in `tests/services/test_retrieve.py` (extend existing file):
- `test_record_retrieval_event_calls_store_and_metrics` — uses real store; verify `retrieval_events` row appears.
- `test_record_retrieval_event_swallows_store_error` — monkey-patch store.record_retrieval_event to raise; service returns None; logger.warning called.
- `test_bump_access_counts_calls_store`.
- `test_bump_access_counts_swallows_error`.
- `test_recent_session_snapshots_returns_results`.
- `test_recent_session_snapshots_returns_empty_on_error`.

Commit: `feat(services): extend retrieve service with analytics helpers`

**T8 — Extend `services/memories.py` with enrichment + access functions**

Add:

1. `async def enrich_memory_async(store, *, memory_id, content, context) -> None`:
   - Run the LLM enrichment pipeline (lifted from current `_enrich_memory` at `routes/memories.py:94-122`).
   - On result, call `store.enrich_memory_meta(memory_id, enrichment_data)`.
   - Wrap in `try/except Exception: logger.warning(...)`.

2. `async def record_memory_access(store, org_id, memory_id) -> StoredMemory`:
   - Call `store.record_memory_access(org_id, memory_id)`.
   - If None, raise `StoreNotFoundError("memories", memory_id)`.

Service tests in `tests/services/test_memories.py` (extend):
- `test_enrich_memory_async_calls_pipeline_and_persists` — monkeypatch `EnrichmentPipeline.enrich` to return fake data; verify `store.enrich_memory_meta` called.
- `test_enrich_memory_async_skips_persist_when_pipeline_returns_none`.
- `test_enrich_memory_async_swallows_pipeline_error`.
- `test_record_memory_access_returns_updated_row`.
- `test_record_memory_access_raises_not_found`.

Commit: `feat(services): extend memories service with enrichment + access functions`

### Route refactors

**T9 — Refactor `routes/snapshots.py`**

Single handler delegates to `services.snapshots.create_snapshot`. Drops:
- `import uuid`, `from ulid import ULID` (now in service)
- `pool = await get_pool()` + the inline INSERT
- `from lore.server.db import get_pool` (no longer used)

Add `Depends(get_store)`. Keep the Pydantic models (`SnapshotCreateRequest`, `SnapshotCreateResponse`).

Build `SnapshotCreateResponse` from the returned `StoredMemory`: `id` from `stored.id`; `session_id`, `title`, `extraction_method` from `stored.meta`; `created_at` from `stored.created_at.isoformat()`.

Commit: `refactor(routes): snapshots.py uses snapshots service`

**T10 — Refactor `routes/retrieve.py` to drop the 3 inline helpers**

In the `/v1/retrieve` handler, replace:
- `await _fetch_session_snapshots(...)` → `await services.retrieve.recent_session_snapshots(store, ...)`
- `asyncio.create_task(_record_retrieval_event(...))` → `asyncio.create_task(services.retrieve.record_retrieval_event(store, ...))`
- `asyncio.create_task(_bump_access_counts(...))` → `asyncio.create_task(services.retrieve.bump_access_counts(store, ...))`

DELETE the three local helper definitions (`_record_retrieval_event`, `_bump_access_counts`, `_fetch_session_snapshots`) entirely.

DELETE the unused `from lore.server.db import get_pool` import.

After this commit: `grep "get_pool\|asyncpg" src/lore/server/routes/retrieve.py` is empty.

Commit: `refactor(routes): retrieve.py uses services for analytics + snapshots`

**T11 — Refactor `routes/memories.py` to drop the 2 inline helpers**

Replace `asyncio.create_task(_enrich_memory(stored.id, stored.content, stored.context))` with `asyncio.create_task(services.memories.enrich_memory_async(store, memory_id=stored.id, content=stored.content, context=stored.context))`.

DELETE `_enrich_memory` function definition entirely.

In the `record_access` handler (`POST /v1/memories/{memory_id}/access`), replace the inline UPDATE + RETURNING SQL with a service call: `updated = await services.memories.record_memory_access(store, auth.org_id, memory_id)`. Drop the `_scope_filter` usage if it's no longer needed inside this handler — but check whether `_scope_filter` is used elsewhere in the file before deleting it.

After this commit: `grep "get_pool\|asyncpg" src/lore/server/routes/memories.py` is empty.

Commit: `refactor(routes): memories.py uses services for enrichment + access`

### Tests + cleanup

**T12 — Add snapshots route tests with FakeStore mocks**

`tests/server/test_snapshots_routes.py`: 4-6 tests covering the single handler with FakeStore mocks (using `monkeypatch.setattr(snapshots_service, "create_snapshot", ...)`).
- `test_create_returns_201` — happy path.
- `test_create_with_explicit_session_id`.
- `test_create_response_includes_meta_extraction_method`.
- `test_create_403_on_non_writer_role` — covers the `Depends(require_role("writer", "admin"))` guard via monkeypatching `require_role`.

Pattern follows `tests/server/test_workspaces_routes.py` (Phase 1D).

Commit: `test(server): add snapshots route tests with FakeStore mocks`

**T13 — Update CI guard**

`scripts/check_routes_no_sql.py`:
- Add `"src/lore/server/routes/snapshots.py"` to `MIGRATED_ROUTES` (alphabetized).
- REMOVE the entire `routes/memories.py` allowlist entry (file is now fully migrated).
- REMOVE the entire `routes/retrieve.py` allowlist entry (file is now fully migrated).

Confirm `python3 scripts/check_routes_no_sql.py` reports `Routes-no-SQL guard: 11 files OK` and exits 0.

Commit: `chore(ci): extend routes-no-SQL guard to analytics+snapshots; drop fully-migrated route allowlists`

**T14 — Update CHANGELOG + architecture docs**

- `CHANGELOG.md` Unreleased section: AnalyticsOps slice (4 methods), MemoryOps.enrich_memory_meta extension, snapshots service, retrieve+memories service extensions, snapshots.py bug fix (tier/type → meta).
- `docs/architecture.md` persistence-layer section: add AnalyticsOps to the slice list, update slice progression, bump migrated-route count from 10 → 11 with breakdown.

Commit: `docs: document analytics + snapshots slice migration`

**T15 — Final verification**

- `pytest tests/` — all pass.
- `ruff check src/ tests/` — clean.
- `python3 scripts/check_routes_no_sql.py` — exit 0, 11 files OK.
- `grep -nE "get_pool|asyncpg" src/lore/server/routes/snapshots.py src/lore/server/routes/retrieve.py src/lore/server/routes/memories.py` — empty.
- `grep -E "tier|type" src/lore/server/routes/snapshots.py | grep -v "tags=\|extraction_method\|content_type"` — sanity check that `tier`/`type` aren't referenced as columns anymore.

No commit.

---

## Self-review

- All 4 `AnalyticsOps` methods + 1 `MemoryOps` extension implemented + contract-tested.
- `routes/snapshots.py`, `routes/retrieve.py`, `routes/memories.py` all SQL-free after the phase.
- Bug fix: `tier`/`type` columns moved into `meta` — matches reader path.
- CI guard grows from 10 → 11 routes; the retrieve.py + memories.py allowlist entries are gone (no longer needed).
- One new service module + two extended ones.
- Fire-and-forget error semantics preserved on the analytics + enrichment paths.

### Known risks (don't block this plan)

- **Existing `tests/test_enrichment_memories.py`** mocks `_enrich_memory` from `routes/memories`. After T11 the function is gone; the test must redirect to `services.memories.enrich_memory_async`. Size this as part of T11.
- **Existing `tests/server/test_retrieve.py`** mocks may reference `_record_retrieval_event` / `_bump_access_counts` / `_fetch_session_snapshots`. Redirect those mocks to the new service paths in T10.
- **Snapshot embedding**: the existing snapshot INSERT bypasses the `embedding` column. The new path uses `MemoryOps.insert_memory`, which expects a 384-dim embedding. Decision: pass a zero-vector placeholder (snapshots are not recall targets; they're surfaced via `meta.type` filter). If the schema requires non-null embedding, this works; if there are NOT NULL constraints with a different default, the contract test will catch it.
- **Service-tests for fire-and-forget paths**: race-y if not isolated. Use `await` directly on the service function in tests (not `asyncio.create_task`) — the route layer wraps with `create_task` but the service is just an async function.
- **Prometheus metrics emit at the service layer**: previously emitted from the route's helper. Moving to the service is fine (services already import other modules); the metrics module is import-safe. If the metrics import path raises (e.g., metrics disabled), the existing `try/except` keeps swallowing.
- **`record_access` and `bump_access_counts` SQL nearly-duplicate**: kept as separate methods because the result shapes differ (RETURNING vs. void) and the WHERE clause differs (`id = $1` vs. `id = ANY($1)`). Could be unified later but not in this phase.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — Fresh implementer per task; controlling Claude provides per-task code at dispatch time using this plan as reference. Mirrors Phase 1B/1C/1D execution.

**2. Inline Execution** — Apply tasks in this session via executing-plans.

Which approach?
