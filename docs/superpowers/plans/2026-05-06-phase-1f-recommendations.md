# Phase 1F — Recommendations Slice (RecommendationOps) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task below is dispatched to a fresh implementer subagent with task-specific code spelled out in the dispatch prompt.

**Goal:** Apply the Phase 1A–1E pattern (Store abstraction + Service layer + route refactor) to the recommendations slice. After this plan: every handler in `routes/recommendations.py` calls services exclusively; all recommendation SQL lives in `PostgresStore`'s new `RecommendationOps` slice (4 methods).

**Architecture:** No new architecture. Same Store / Services / Routes layering as 1A–1E. 4 new methods on Store; one new service module (`services/recommendations.py`); 6 route handlers refactored.

**Tech Stack:** Same as 1A–1E. No new runtime deps. Postgres test DB at `localhost:5432` / `lore_test` reused.

**Spec reference:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`. Section "Components" (1, 2). Phase 1E plan: `docs/superpowers/plans/2026-05-06-phase-1e-analytics-snapshots.md` — the immediate template.

**Bug fix bundled in this phase:** the current `update_config` handler uses a string-replace hack (`sql.replace(" WHERE ", ", updated_at = now() WHERE ")`) to inject `updated_at = now()` into the dynamically-built SET clause. The new `Store.upsert_recommendation_config` handles `updated_at` cleanly within its parameterized SQL.

---

## File structure

### Created in this plan

| Path | Responsibility |
|---|---|
| `src/lore/services/recommendations.py` | Recommendation service module — config get/update, feedback submit, and the recommend orchestration (loads config → fetches candidates → runs the `RecommendationEngine` via `asyncio.to_thread`) |
| `tests/persistence/test_contract_recommendations.py` | Contract tests for the 4 `RecommendationOps` methods |
| `tests/services/test_recommendations.py` | Service tests for config/feedback/recommend flows |
| `tests/server/test_recommendations_routes.py` | Route tests for the 6 recommendations handlers using `FakeStore` mocks |

### Modified in this plan

| Path | Change |
|---|---|
| `src/lore/persistence/types.py` | Add `RecommendationCandidate`, `StoredRecommendationConfig`, `NewRecommendationFeedback` dataclasses |
| `src/lore/persistence/protocol.py` | Add 4 `RecommendationOps` methods to `Store` Protocol |
| `src/lore/persistence/postgres.py` | Implement all 4 `RecommendationOps` methods on `PostgresStore` |
| `src/lore/persistence/__init__.py` | Re-export the 3 new dataclasses |
| `src/lore/server/routes/recommendations.py` | All 6 handlers call services; drop inline SQL + the `build_update`/`sql.replace` hack |
| `scripts/check_routes_no_sql.py` | Add `routes/recommendations.py` to `MIGRATED_ROUTES` (11 → 12) |
| `tests/persistence/test_types.py`, `tests/persistence/test_protocol.py` | Extend to cover new dataclasses + protocol methods |
| `CHANGELOG.md`, `docs/architecture.md` | Note `RecommendationOps` slice landed |

### Out of scope (deferred)

- **`lore/recommend/engine.py` refactor** — the engine's `Store`-with-`.list()` interface stays as-is; the service uses a thin `_CandidatesAdapter(candidates)` wrapper to satisfy it.
- **Per-workspace / per-agent config scopes** — the schema supports them via `recommendation_config.workspace_id` and `agent_id` columns, but current routes only use the global (both NULL) scope. The Store method takes optional `workspace_id` / `agent_id` kwargs for future use; the service hardcodes None.
- **`lore/server/auth.py` middleware migration** — still its own future slice.
- **`routes/conversations.py`** — Phase 1G.
- **FK constraint on `recommendation_feedback.memory_id`** — schema doesn't enforce it; this phase doesn't add one.

---

## Tasks (one task = one commit)

### Foundation — types, protocol

**T1 — Add recommendation dataclasses to `lore.persistence.types`**

Add three `@dataclass(frozen=True, slots=True)` classes:

```python
@dataclass(frozen=True, slots=True)
class RecommendationCandidate:
    """Memory shape the recommendation engine expects: includes embedding."""
    id: str
    content: str
    embedding: Sequence[float]
    metadata: Mapping[str, Any]
    created_at: datetime
    access_count: int
    last_accessed_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class StoredRecommendationConfig:
    id: str
    workspace_id: Optional[str]
    agent_id: Optional[str]
    aggressiveness: float
    enabled: bool
    max_suggestions: int
    cooldown_minutes: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewRecommendationFeedback:
    org_id: str
    memory_id: str
    actor_id: str
    feedback: str               # validated by service: "positive" or "negative"
    workspace_id: Optional[str] = None
    signal: str = "manual"
    context_hash: Optional[str] = None
```

Section comment: `# ── Recommendations slice dataclasses ───`. Place after the existing analytics dataclasses.

Re-export from `__init__.py` (alphabetical position in import block + `__all__`).

Add tests in `tests/persistence/test_types.py` for each dataclass (defaults, full-population, frozen, slots).

Commit: `feat(persistence): add recommendation dataclasses`

**T2 — Extend `Store` protocol with `RecommendationOps` slice**

Under a new `# ── RecommendationOps ────` section (after `# ── AnalyticsOps ────`):

```python
async def get_recommendation_config(
    self, *, workspace_id: Optional[str] = None, agent_id: Optional[str] = None,
) -> Optional[StoredRecommendationConfig]: ...

async def upsert_recommendation_config(
    self,
    *,
    workspace_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    aggressiveness: Optional[float] = None,
    enabled: Optional[bool] = None,
    max_suggestions: Optional[int] = None,
    cooldown_minutes: Optional[int] = None,
) -> StoredRecommendationConfig: ...

async def record_recommendation_feedback(
    self, feedback: NewRecommendationFeedback,
) -> None: ...

async def list_candidate_memories_for_recommendation(
    self, org_id: str, *, limit: int = 500,
) -> Sequence[RecommendationCandidate]: ...
```

Add the 3 new types to `protocol.py` imports.

Update `tests/persistence/test_protocol.py`: `REQUIRED_RECOMMENDATION_OPS` set + 2 new tests.

Commit: `feat(persistence): extend Store protocol with RecommendationOps slice`

### PostgresStore — RecommendationOps

**T3 — `get_recommendation_config` + `upsert_recommendation_config` + contract tests**

- `get_recommendation_config`: `SELECT * FROM recommendation_config WHERE workspace_id IS NOT DISTINCT FROM $1 AND agent_id IS NOT DISTINCT FROM $2 LIMIT 1`. (`IS NOT DISTINCT FROM` handles NULL-equals-NULL correctly.) Returns `Optional[StoredRecommendationConfig]`.

- `upsert_recommendation_config`: use Postgres `INSERT … ON CONFLICT (workspace_id, agent_id) DO UPDATE SET …` to atomically upsert. The unique constraint exists in migration 017 as `UNIQUE(workspace_id, agent_id)`. The dynamic SET clause should only update fields that are not None in the input — for fields that ARE None, preserve the existing value. Approach:
  ```sql
  INSERT INTO recommendation_config
      (id, workspace_id, agent_id, aggressiveness, enabled,
       max_suggestions, cooldown_minutes, updated_at)
  VALUES ($1, $2, $3, COALESCE($4, 0.5), COALESCE($5, TRUE),
          COALESCE($6, 3), COALESCE($7, 15), now())
  ON CONFLICT (workspace_id, agent_id) DO UPDATE
  SET aggressiveness   = COALESCE(EXCLUDED.aggressiveness,   recommendation_config.aggressiveness),
      enabled          = COALESCE(EXCLUDED.enabled,          recommendation_config.enabled),
      max_suggestions  = COALESCE(EXCLUDED.max_suggestions,  recommendation_config.max_suggestions),
      cooldown_minutes = COALESCE(EXCLUDED.cooldown_minutes, recommendation_config.cooldown_minutes),
      updated_at       = now()
  RETURNING *
  ```

  **Subtlety:** `COALESCE(EXCLUDED.X, recommendation_config.X)` only works when EXCLUDED.X is NULL. For an UPSERT with optional fields, this is correct: if the caller passes None for a field, we want to preserve the existing row's value. For first-INSERT (no existing row), the COALESCE in the VALUES clause kicks in with the default.

  ID generation: `f"reccfg_{ULID()}"` for first INSERT; ON CONFLICT updates the existing row's columns (id stays unchanged).

- Stub the other 2 RecommendationOps methods with `NotImplementedError`.

Add `_row_to_recommendation_config` helper.

Contract tests:
- `test_get_config_returns_none_when_missing`.
- `test_upsert_config_inserts_when_missing` — call upsert, verify row appears with the supplied fields and defaults.
- `test_upsert_config_updates_existing` — insert via upsert, then upsert with one different field; verify only that field changed and updated_at moved.
- `test_upsert_config_preserves_none_fields` — insert with all fields, then upsert with only `aggressiveness=0.9` (others None); verify enabled/max_suggestions/cooldown_minutes UNCHANGED.
- `test_upsert_config_global_scope_uses_null_keys` — both workspace_id and agent_id NULL; round-trip via `get_recommendation_config()`.
- `test_get_config_uses_is_not_distinct_for_null_match`.

Commit: `feat(persistence): RecommendationOps.get_recommendation_config + upsert_recommendation_config`

**T4 — `record_recommendation_feedback` + contract tests**

- `record_recommendation_feedback`: store generates ID `f"recfb_{ULID()}"`. SQL:
  ```sql
  INSERT INTO recommendation_feedback
      (id, org_id, workspace_id, memory_id, actor_id, signal, feedback, context_hash)
  VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
  ```
  No RETURN value. (Schema's `created_at` column has DB default `now()`.)

Contract tests:
- `test_record_feedback_inserts_row` — insert; verify with raw `SELECT COUNT(*) FROM recommendation_feedback WHERE memory_id = $1`.
- `test_record_feedback_with_workspace_id` — passes through correctly.
- `test_record_feedback_with_optional_signal_and_context_hash`.

Commit: `feat(persistence): RecommendationOps.record_recommendation_feedback`

**T5 — `list_candidate_memories_for_recommendation` + contract tests**

- `list_candidate_memories_for_recommendation(org_id, *, limit=500)`:
  SQL identical to current `routes/recommendations.py:102-111`:
  ```sql
  SELECT id, content, embedding, meta, created_at, access_count, last_accessed_at
  FROM memories
  WHERE org_id = $1 AND embedding IS NOT NULL
  ORDER BY importance_score DESC NULLS LAST
  LIMIT $2
  ```
  Returns `Sequence[RecommendationCandidate]`.

  **Embedding decoding:** asyncpg returns `vector` columns as a string of the form `'[0.1,0.2,...]'` (pgvector's text format) when no codec is registered. Check how existing code handles it. If pgvector codec is registered (verify via `_register_pgvector` or similar at conftest/init time), embedding comes back as a Python list directly.

  In the existing route (line 124), `r["embedding"]` is passed unmodified to `SimpleNamespace(embedding=r["embedding"], ...)`. The engine then handles whatever shape that is. Mirror that — pass through as-is to `RecommendationCandidate.embedding`. Type the field as `Sequence[float]` or `Any` — recommend `Sequence[float]` and trust the codec / convert at the boundary if needed.

  **Meta decoding:** `meta` is JSONB. asyncpg returns JSONB as a Python dict natively if the JSON codec is registered, or a string otherwise. Check how `_row_to_stored` handles it; mirror that pattern (`json.loads` if string).

Add `_row_to_recommendation_candidate` helper.

Contract tests:
- `test_list_candidates_returns_memories_with_embeddings` — insert 2 memories via `_insert_memory_with_embedding` helper (raw SQL setting `embedding`), call list, verify both returned.
- `test_list_candidates_excludes_null_embedding` — insert one with embedding, one without; list returns only the embedded one.
- `test_list_candidates_org_isolation`.
- `test_list_candidates_ordered_by_importance_score_desc` — three memories with different `importance_score`; verify ordering.
- `test_list_candidates_respects_limit`.

Use a small helper to insert raw memory rows with embedding. asyncpg's vector codec handling may need explicit casting:
```python
await store._conn.execute(
    "INSERT INTO memories (id, org_id, content, embedding, importance_score) "
    "VALUES ($1, $2, $3, $4::vector, $5)",
    mid, org_id, content, json.dumps(embedding), importance,
)
```

Commit: `feat(persistence): RecommendationOps.list_candidate_memories_for_recommendation`

### Service

**T6 — `services/recommendations.py` + service tests**

Module structure (`src/lore/services/recommendations.py`):

```python
"""Recommendation service — config CRUD, feedback, and engine orchestration."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Sequence

from lore.persistence import (
    NewRecommendationFeedback,
    RecommendationCandidate,
    Store,
    StoredRecommendationConfig,
)


logger = logging.getLogger(__name__)


# Defaults when no row exists. Match the pre-1F route's fallback values.
DEFAULT_AGGRESSIVENESS = 0.5
DEFAULT_ENABLED = True
DEFAULT_MAX_SUGGESTIONS = 3
DEFAULT_COOLDOWN_MINUTES = 15


class _CandidatesAdapter:
    """Wrap a list of candidates to satisfy the engine's `.list()` interface."""

    def __init__(self, candidates: Sequence[RecommendationCandidate]) -> None:
        self._c = list(candidates)

    def list(self, limit: int = 500):
        return self._c[:limit]
```

### Functions (4 total)

1. **`async def get_config(store, *, workspace_id=None, agent_id=None) -> dict`**
   - Calls `store.get_recommendation_config(workspace_id=workspace_id, agent_id=agent_id)`.
   - If None, returns a dict with the four default values.
   - If found, returns a dict with the row's values.
   - Returns `dict` (not `StoredRecommendationConfig`) so the route can convert to `ConfigResponse` without a defaults check.

2. **`async def update_config(store, *, workspace_id=None, agent_id=None, aggressiveness=None, enabled=None, max_suggestions=None, cooldown_minutes=None) -> dict`**
   - Calls `store.upsert_recommendation_config(...)` passing all kwargs.
   - Returns a dict matching the shape of `get_config` (so the route can use a single conversion path).

3. **`async def submit_feedback(store, *, org_id, memory_id, actor_id, feedback, workspace_id=None) -> None`**
   - Validates `feedback in {"positive", "negative"}`; raises `ValueError("Feedback must be 'positive' or 'negative'")` otherwise.
   - Builds `NewRecommendationFeedback`; calls `store.record_recommendation_feedback`.

4. **`async def recommend(store, *, org_id, context, session_entities=None, max_results=3) -> list`**
   - If `not context`, return `[]` (matches current behavior).
   - `cfg = await store.get_recommendation_config()` → defaults to `(0.5, 3)` if None.
   - Extract `aggressiveness` and `max_suggestions` (with defaults).
   - `candidates = await store.list_candidate_memories_for_recommendation(org_id)`.
   - If empty, return `[]`.
   - `try`:
     - `from lore.embed import LocalEmbedder; from lore.recommend.engine import RecommendationEngine`
     - Build engine: `RecommendationEngine(store=_CandidatesAdapter(candidates), embedder=LocalEmbedder(), aggressiveness=aggressiveness, max_suggestions=max_suggestions)`.
     - `results = await asyncio.to_thread(engine.suggest, context=context, session_entities=session_entities or None, limit=max_results)`.
   - `except Exception: logger.exception(...); return []`.
   - Return engine results (list of `Recommendation` objects from `lore.recommend.engine`).

### Service tests (`tests/services/test_recommendations.py`)

Use the `store` fixture from `tests/services/conftest.py` (real Postgres). Tests:

- `test_get_config_returns_defaults_when_missing` — verify defaults dict.
- `test_get_config_returns_stored_values` — pre-insert a row via raw SQL, call get_config, verify values match.
- `test_update_config_inserts_then_returns_dict` — call update with explicit aggressiveness, verify dict shape, verify second `get_config` returns the same.
- `test_update_config_preserves_none_fields` — update aggressiveness only; subsequent get verifies enabled/max_suggestions/cooldown_minutes unchanged.
- `test_submit_feedback_validates_value` — `feedback="bogus"` → `ValueError`.
- `test_submit_feedback_records_row` — happy path; verify with raw SQL count.
- `test_recommend_returns_empty_when_context_blank`.
- `test_recommend_returns_empty_when_no_candidates` — fresh org with no embedded memories.
- `test_recommend_returns_engine_results` — insert 1 candidate memory with embedding; monkeypatch `RecommendationEngine.suggest` to return a fixed result; verify the service returns it. (The actual engine's behavior is tested separately in `tests/test_recommend.py` if it exists — service test focuses on orchestration.)
- `test_recommend_swallows_engine_error` — monkeypatch `RecommendationEngine.suggest` to raise; service returns `[]`.

For monkey-patching `RecommendationEngine`: `monkeypatch.setattr("lore.recommend.engine.RecommendationEngine", FakeEngineClass)`. `FakeEngineClass.__init__(*args, **kwargs)` accepts and ignores; `suggest(context, session_entities, limit)` returns the predetermined list.

Commit: `feat(services): recommendations service + engine orchestration`

### Route refactor

**T7 — Refactor `routes/recommendations.py` (6 handlers)**

Each handler thin: parse → call service → serialize.

### Things to DELETE

- The local `_AsyncpgStore` adapter class (now in service as `_CandidatesAdapter`).
- All inline SQL.
- All `pool = await get_pool()` calls.
- The `from lore.server.routes._helpers import build_update` import (no longer needed; service+Store handle the upsert).
- The `sql.replace(" WHERE ", ", updated_at = now() WHERE ", 1)` hack — gone.
- Local imports inside handlers (`import asyncio`, `import json as _json`, `from types import SimpleNamespace`, `from ulid import ULID`) — moved to the service.

### Things to KEEP

- All Pydantic models: `RecommendationResponse`, `RecommendationRequest`, `FeedbackRequest`, `ConfigResponse`, `ConfigUpdateRequest`.
- The 6 handlers (rewritten thin).
- `from lore.server.auth import AuthContext, get_auth_context`.
- `logger = logging.getLogger(__name__)`.

### Imports to ADD

```python
from lore.persistence import Store
from lore.server.db import get_store
from lore.services import recommendations as recommendations_service
```

### Handler-by-handler mapping

1. `GET /v1/recommendations` — placeholder, returns `[]`. No service call needed.
2. `POST /v1/recommendations` → `await recommendations_service.recommend(store, org_id=auth.org_id, context=body.context, session_entities=body.session_entities or None, max_results=body.max_results)`. Convert each engine `Recommendation` to `RecommendationResponse` via the existing pattern.
3. `GET /v1/recommendations/proactive` → unchanged delegation pattern, but calls `recommend` directly instead of `post_recommendations` (cleaner; same result).
4. `POST /v1/recommendations/{memory_id}/feedback` → `await recommendations_service.submit_feedback(store, org_id=auth.org_id, memory_id=memory_id, actor_id=auth.key_id, feedback=body.feedback)`. Catch `ValueError` → 400.
5. `GET /v1/recommendations/config` → `cfg = await recommendations_service.get_config(store)`. Build `ConfigResponse(**cfg)`.
6. `PATCH /v1/recommendations/config` → `cfg = await recommendations_service.update_config(store, **body.dict(exclude_unset=True))`. Build `ConfigResponse(**cfg)`.

Add `Depends(get_store)` to handlers 2-6 (handler 1 doesn't need it).

After refactor: file should be ~120-150 LOC (was 285).

Commit: `refactor(routes): recommendations.py uses recommendations service`

### Tests + cleanup

**T8 — Add recommendations route tests with FakeStore**

`tests/server/test_recommendations_routes.py`: 8-10 tests covering all 6 handlers.

Pattern matches `tests/server/test_snapshots_routes.py` (Phase 1E). Tests:
1. `test_get_returns_empty` — placeholder GET returns `[]`.
2. `test_post_returns_engine_results` — service mock returns 2 fake `Recommendation`s; verify response.
3. `test_post_returns_empty_when_context_missing`.
4. `test_proactive_delegates_with_parsed_entities` — query string `entities="a,b,c"` parses to `["a","b","c"]`; verify the service was called with the right list.
5. `test_feedback_records_and_returns_status` — happy path; verify status 200 + `{"status":"recorded",...}`.
6. `test_feedback_400_on_invalid_value`.
7. `test_get_config_returns_defaults`.
8. `test_get_config_returns_stored_values`.
9. `test_patch_config_updates_and_returns_new_values`.
10. `test_patch_config_partial_update_preserves_unchanged`.

Commit: `test(server): add recommendations route tests with FakeStore mocks`

**T9 — Update CI guard**

`scripts/check_routes_no_sql.py`:
- Add `"src/lore/server/routes/recommendations.py"` to `MIGRATED_ROUTES` (alphabetized).

After: `python3 scripts/check_routes_no_sql.py` reports `Routes-no-SQL guard: 12 files OK` and exits 0.

Commit: `chore(ci): extend routes-no-SQL guard to recommendations slice`

**T10 — Update CHANGELOG + architecture docs**

- `CHANGELOG.md` Unreleased section: `RecommendationOps` slice (4 methods), `services/recommendations`, the `update_config` bug fix (drops the string-replace hack), 3 new dataclasses.
- `docs/architecture.md` persistence-layer section: add `RecommendationOps` to the slice list (between AnalyticsOps and the implementations heading); update slice progression sentence; bump migrated-routes count from 11 → 12 with breakdown.

**Note for the implementer:** Phase 1E's PR backfilled the lost Phase 1D docs. Make sure docs/architecture.md still has all six prior slices plus the new RecommendationOps. Double-check the slice count.

**WORKING-DIRECTORY NOTE:** The implementer subagent for this task MUST verify it's running in the worktree (not in `/home/amit/projects/lore` main repo) before committing. Run `pwd && git rev-parse HEAD` first; the path must be `/home/amit/projects/lore/.claude/worktrees/solo-mode+phase-1f` and the branch must be `solo-mode/phase-1f`. (Phase 1C T13 and Phase 1E T14 hit a bug where docs subagents committed to local main; cherry-pick was needed to recover. Avoid that this time.)

Commit: `docs: document recommendations slice migration`

**T11 — Final verification**

- `pytest tests/` — all pass.
- `ruff check src/ tests/` — clean.
- `python3 scripts/check_routes_no_sql.py` — exit 0, 12 files OK.
- `grep -nE "get_pool|asyncpg" src/lore/server/routes/recommendations.py` — empty.
- `grep "build_update\|sql.replace" src/lore/server/routes/recommendations.py` — empty.

No commit.

---

## Self-review

- All 4 `RecommendationOps` methods implemented + contract-tested.
- All 6 recommendation route handlers refactored to call services.
- One new service module + three test files match the plan.
- The `update_config` string-replace hack is gone — replaced by clean `INSERT … ON CONFLICT … RETURNING *`.
- CI guard grows from 11 → 12 routes.

### Known risks (don't block this plan)

- **pgvector codec for embedding column**: `list_candidate_memories_for_recommendation` returns `embedding` as whatever asyncpg gives us — probably a string in pgvector text format. The engine in `lore/recommend/engine.py` may need a list[float] or a string; the existing route passed it through unchanged (line 124 of pre-1F recommendations.py). Mirror that exactly. If a test catches a type mismatch, decode via `pgvector.asyncpg.register_vector` at the connection-init level (out of scope for this plan).
- **Engine import is conditional**: `from lore.embed import LocalEmbedder; from lore.recommend.engine import RecommendationEngine` happens INSIDE the service's `recommend` function (matches the pre-1F route). This is intentional — the imports trigger ML-model loading and shouldn't happen at module import time.
- **`RecommendationEngine` interface**: the engine has a `Store` parameter expecting a `.list(limit)` method. The `_CandidatesAdapter` provides it. If the engine's interface ever changes, the adapter needs to track. Documented in the engine's docstring as out of scope for this phase.
- **Default scope only**: get_config and update_config always pass `workspace_id=None, agent_id=None` from the service. The Store methods accept them as kwargs for future use. If routes ever start passing non-default scopes, the wiring is ready.
- **Existing `tests/server/test_recommendations.py` (if any)** may mock the inline SQL; check and redirect mocks during T7. If it exists and uses the global `app` with `dependency_overrides`, the same pattern from Phase 1E applies.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — Fresh implementer per task; controlling Claude provides per-task code at dispatch time using this plan as reference. Mirrors Phase 1B/1C/1D/1E execution.

**2. Inline Execution** — Apply tasks in this session via executing-plans.

Which approach?
