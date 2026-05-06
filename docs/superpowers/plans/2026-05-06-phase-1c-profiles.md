# Phase 1C — Profiles Slice (PolicyOps) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task below is dispatched to a fresh implementer subagent with task-specific code spelled out in the dispatch prompt (the controlling Claude has the full slice map and synthesizes per-task detail at dispatch time).

**Goal:** Apply the Phase 1A/1B pattern (Store abstraction + Service layer + route refactor) to the profiles slice. After this plan: every handler in `routes/profiles.py` calls services exclusively, the `_resolve_profile` helper in `routes/retrieve.py` is replaced with a service call, and all profile SQL lives in `PostgresStore`'s new `PolicyOps` methods. Adds migration `018` to make the schema match what the route code already assumes.

**Architecture:** No new architecture. Same Store / Services / Routes layering as Phase 1A/1B. ~7 new `PolicyOps` methods on `Store`; one new service module (`services/profiles.py`); 8 route handlers refactored; one helper in `retrieve.py` rewired.

**Tech Stack:** Same as Phase 1A/1B. No new runtime deps. Postgres test DB at `localhost:5432` / `lore_test` reused.

**Spec reference:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`. Section "Components" (1, 2). Phase 1A plan: `docs/superpowers/plans/2026-05-05-phase-1a-foundation-and-memories.md`. Phase 1B plan: `docs/superpowers/plans/2026-05-05-phase-1b-graph-slice.md` — read its task structure and mirror the TDD discipline.

---

## File structure

### Created in this plan

| Path | Responsibility |
|---|---|
| `migrations/018_profile_extras.sql` | Add `k`, `threshold`, `rerank`, `include_graph` columns to `retrieval_profiles` (the route code already assumes them) |
| `src/lore/services/profiles.py` | Profiles service module — owns `DEFAULT_PROFILES`, the 60s in-memory cache, preset-immutability checks, `k`/`threshold`/`max_results`/`min_score` alias logic |
| `tests/persistence/test_contract_profiles.py` | Contract tests for the 7 `PolicyOps` methods |
| `tests/services/test_profiles.py` | Service tests for cache TTL, alias logic, preset immutability, default fallback |
| `tests/server/test_profiles_routes.py` | Route tests for the 8 profiles handlers using `FakeStore` mocks |

### Modified in this plan

| Path | Change |
|---|---|
| `src/lore/persistence/types.py` | Add `NewProfile`, `StoredProfile`, `ProfilePatch`, `ResolvedProfile` dataclasses |
| `src/lore/persistence/protocol.py` | Add 7 `PolicyOps` methods to `Store` Protocol |
| `src/lore/persistence/postgres.py` | Implement all `PolicyOps` methods on `PostgresStore` |
| `src/lore/persistence/exceptions.py` | Add `ProfileImmutableError` (preset can't be modified) |
| `src/lore/server/routes/profiles.py` | All 8 handlers call services; drop inline SQL, `DEFAULT_PROFILES`, `_profile_cache`, `_row_to_response`, and `resolve_profile` (now service-owned) |
| `src/lore/server/routes/retrieve.py` | Replace inline `_resolve_profile` import + `get_pool()` block with `services.profiles.resolve_profile(store, ...)` call |
| `scripts/check_routes_no_sql.py` | Add `routes/profiles.py` to `MIGRATED_ROUTES`; drop the retrieve.py profile-resolution allowlist entry |
| `tests/persistence/test_types.py` | Add round-trip tests for new dataclasses |
| `tests/persistence/test_protocol.py` | Assert new `PolicyOps` methods exist + are async |
| `CHANGELOG.md`, `docs/architecture.md` | Note `PolicyOps` slice landed |

### Out of scope (deferred)

- `migrations_sqlite/018_*.sql` sibling — Phase 3 (SQLite store implementation). Solo-mode design's parity-CI check does not exist yet; first migration to add it is whatever phase introduces the parity guard.
- Multi-process cache invalidation — current per-process cache preserved (solo mode = single uvicorn worker).
- The `recommendation_config` table (also "policy"-flavored) — Phase 1F (`RecommendationOps`).
- The other three `retrieve.py` allowlisted helpers (`_record_retrieval_event`, `_bump_access_counts`, `_fetch_session_snapshots`) — `AnalyticsOps`/`SnapshotOps` slice; future phase.
- `routes/profiles.py` Pydantic request/response models stay where they are (route-layer concern). Only the SQL and the cache move.

---

## Tasks (one task = one commit)

Each task follows the Phase 1A/1B discipline: failing test first, run pytest, implement, run pytest, commit. The controlling Claude provides per-task code in the implementer dispatch prompt.

### Foundation — schema, types, protocol

**T1 — Migration 018: profile extras columns**
Add `migrations/018_profile_extras.sql`:
```sql
-- Profile extras: columns the route code already references but the original 013 migration omitted.
ALTER TABLE retrieval_profiles
  ADD COLUMN IF NOT EXISTS k             INT,
  ADD COLUMN IF NOT EXISTS threshold     REAL,
  ADD COLUMN IF NOT EXISTS rerank        BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS include_graph BOOLEAN DEFAULT TRUE;
```
Apply to test DB:
```
docker exec -i lore-test-pg psql -U lore -d lore_test < migrations/018_profile_extras.sql
```
Verify columns landed: `docker exec lore-test-pg psql -U lore -d lore_test -c "\d retrieval_profiles"`.
No code changes; commit just the migration file.
Commit: `feat(migrations): 018 — add k/threshold/rerank/include_graph columns to retrieval_profiles`

**T2 — Profile dataclasses in `lore.persistence.types`**
Add `NewProfile`, `StoredProfile`, `ProfilePatch`, `ResolvedProfile` as `@dataclass(frozen=True, slots=True)`:
- `NewProfile`: `org_id, name, semantic_weight=1.0, graph_weight=1.0, recency_bias=30.0, tier_filters=None, min_score=0.3, max_results=10, is_preset=False, k=None, threshold=None, rerank=False, include_graph=True`. No `id` (store generates).
- `StoredProfile`: same fields + `id, created_at, updated_at` (datetimes).
- `ProfilePatch`: every field `Optional[...]=None`. Empty patch is rejected at the service layer.
- `ResolvedProfile`: thin wrapper around either a `StoredProfile` row or a `DEFAULT_PROFILES` mapping — exposes `k`, `threshold`, `max_results`, `min_score`, `rerank`, `include_graph` accessors so `retrieve.py` can introspect without caring about source. Discriminator: `source: Literal["stored","default"]`.

Round-trip + immutability tests in `tests/persistence/test_types.py`.
Commit: `feat(persistence): add profile dataclasses to types`

**T3 — Add `ProfileImmutableError` to exceptions; extend `Store` Protocol with `PolicyOps` slice**
Add to `lore/persistence/exceptions.py`:
```python
class ProfileImmutableError(LoreError):
    """Raised when caller attempts to modify or delete a preset profile."""
```
Extend `Store` Protocol with the 7 methods (full async signatures + 1-line docstrings):
```python
async def get_profile(self, profile_id: str) -> Optional[StoredProfile]: ...
async def get_profile_by_name(self, org_id: str, name: str) -> Optional[StoredProfile]: ...
async def list_profiles(self, org_id: str) -> Sequence[StoredProfile]: ...
async def create_profile(self, profile: NewProfile) -> StoredProfile: ...
async def update_profile(self, profile_id: str, patch: ProfilePatch) -> Optional[StoredProfile]: ...
async def delete_profile(self, profile_id: str, org_id: str) -> bool: ...
async def resolve_profile_for_key(self, org_id: str, name: str) -> Optional[StoredProfile]: ...
```
Update `tests/persistence/test_protocol.py` to assert presence + async-ness.
Commit: `feat(persistence): extend Store protocol with PolicyOps slice`

### PostgresStore — profile ops

**T4 — `get_profile` + `get_profile_by_name` + contract tests**
- `get_profile`: `SELECT * FROM retrieval_profiles WHERE id = $1` — returns `StoredProfile` or `None`.
- `get_profile_by_name`: `SELECT * FROM retrieval_profiles WHERE name = $1 AND org_id = $2` — returns `StoredProfile` or `None`. Case-sensitive match (matches current `=` semantics in the route code; the legacy `LOWER(name)=LOWER($1)` form in topics doesn't apply here).
- Stub remaining 5 `PolicyOps` methods with `NotImplementedError` so the protocol smoke test passes.
- Contract tests insert a row directly via raw SQL fixture, then exercise both reads.

Commit: `feat(persistence): PolicyOps.get_profile + get_profile_by_name`

**T5 — `list_profiles` + `create_profile` + contract tests**
- `list_profiles`: `SELECT * FROM retrieval_profiles WHERE org_id = $1 OR org_id = '__global__' ORDER BY name`. Returns org-owned + global presets (matches current behavior at `routes/profiles.py:138-144`).
- `create_profile`: store generates ULID for `id`; `INSERT INTO retrieval_profiles (id, org_id, name, semantic_weight, graph_weight, recency_bias, tier_filters, min_score, max_results, is_preset, k, threshold, rerank, include_graph) VALUES (...) RETURNING *`. Maps unique-constraint violation `(org_id, name)` to `IntegrityError` from `lore.persistence.exceptions`.
- Contract tests: round-trip create→list; uniqueness collision raises typed error; presets ordered alongside org-owned.

Commit: `feat(persistence): PolicyOps.list_profiles + create_profile`

**T6 — `update_profile` + `delete_profile` + contract tests**
- `update_profile`: dynamic `SET` clause from `ProfilePatch` (only set fields), bumps `updated_at = now()`, `RETURNING *`. Returns `Optional[StoredProfile]` — `None` if row not found. Caller (service) is responsible for ensuring patch is non-empty.
- `delete_profile`: `DELETE FROM retrieval_profiles WHERE id = $1 AND org_id = $2`. Returns `True` if a row was deleted (status string starts with `"DELETE 1"`), `False` otherwise.
- Contract tests: patch single field, patch multiple fields, patch with not-found id returns `None`; delete existing returns `True`, delete non-existent returns `False`.
- **Note for implementer:** the `is_preset` immutability check is enforced by the **service**, not the store. The store will happily UPDATE a preset row if asked. Service-layer test in T8 covers the rejection path.

Commit: `feat(persistence): PolicyOps.update_profile + delete_profile`

**T7 — `resolve_profile_for_key` + contract tests**
SQL identical to current `routes/profiles.py:383-389`:
```sql
SELECT * FROM retrieval_profiles
WHERE name = $1 AND (org_id = $2 OR org_id = '__global__')
ORDER BY CASE WHEN org_id = $2 THEN 0 ELSE 1 END
LIMIT 1
```
Returns `Optional[StoredProfile]`. Contract test: insert global `__global__` preset and an org-owned profile with the same name; assert `resolve_profile_for_key` returns the org-owned row (it shadows the global).
Commit: `feat(persistence): PolicyOps.resolve_profile_for_key`

### Services

**T8 — `services/profiles.py` + service tests**

Module structure (`src/lore/services/profiles.py`):
- `DEFAULT_PROFILES` constant (lifted verbatim from `routes/profiles.py:71-82`).
- `_profile_cache: Dict[str, tuple[ResolvedProfile, float]]` + `_PROFILE_CACHE_TTL = 60.0` + `_get_cached`/`_set_cached` helpers (lifted from `routes/profiles.py:115-128`). Process-local; same as today.
- 9 functions:
  - `list_profiles(store, org_id) -> Sequence[StoredProfile]`
  - `get_profile(store, profile_id) -> StoredProfile` — raises `StoreNotFound` if missing
  - `create_profile(store, org_id, body: ProfileCreateInput) -> StoredProfile` — generates `NewProfile` from input, applies alias logic (see below)
  - `update_profile_by_id(store, profile_id, org_id, patch: ProfilePatch) -> StoredProfile` — fetch row first; raise `StoreNotFound` (404) or `ProfileImmutableError` (403) if preset; apply alias sync; reject empty patches; call `store.update_profile`
  - `update_profile_by_name(store, org_id, name, patch) -> StoredProfile` — resolves name → id, then delegates to `update_profile_by_id`
  - `delete_profile_by_id(store, profile_id, org_id) -> None` — fetch row first; raise `StoreNotFound` or `ProfileImmutableError`; call `store.delete_profile`
  - `delete_profile_by_name(store, org_id, name) -> None` — resolves name → id, then delegates
  - `get_default_profiles() -> Mapping[str, dict]` — pure; returns `DEFAULT_PROFILES`
  - `resolve_profile(store, org_id, requested_name, key_default) -> Optional[ResolvedProfile]`:
    1. `name = requested_name or key_default`; if `None`, return `None`.
    2. Cache lookup by `f"{org_id}:{name}"`; if hit, return cached.
    3. `store.resolve_profile_for_key(org_id, name)` — if found, wrap in `ResolvedProfile(source="stored", row=...)`, cache, return.
    4. Fallback: if `name in DEFAULT_PROFILES`, build `ResolvedProfile(source="default", data=DEFAULT_PROFILES[name])`, cache, return.
    5. Otherwise return `None`.

**Alias logic** (mirrors current `routes/profiles.py:256-275` exactly — preserve the sync-on-create-and-update behavior):
- On create: if `k` set and `max_results` not set → `max_results = k`. If `threshold` set and `min_score` not set → `min_score = threshold`.
- On update (in patch): same rule — if `k` set and `max_results` not set → also patch `max_results = k`; if `threshold` set and `min_score` not set → also patch `min_score = threshold`. Both fields can diverge if both explicitly set.

Tests (`tests/services/test_profiles.py`) — use a `FakeStore` (mirror Phase 1A test pattern):
- `create_profile` with `k=5` and no `max_results` → store row has `max_results=5`.
- `create_profile` with both `k=5` and `max_results=10` → both kept as-is (divergence allowed).
- `update_profile_by_id` on preset row raises `ProfileImmutableError`.
- `delete_profile_by_id` on preset row raises `ProfileImmutableError`.
- `update_profile_by_id` on non-existent id raises `StoreNotFound`.
- Empty `ProfilePatch` raises `ValueError("No fields to update")` — matches today's HTTP 400.
- `resolve_profile`: store hit returns `ResolvedProfile(source="stored")`; store miss + `name in DEFAULT_PROFILES` returns `ResolvedProfile(source="default")`; store miss + name not in defaults returns `None`.
- Cache TTL: monkeypatch `time.monotonic`; second call within 60s returns cached object (no store call); after 60s+ cache is bypassed.

Commit: `feat(services): profiles service + cache + alias logic + default fallback`

### Route refactors

**T9 — Refactor `routes/retrieve.py` profile-resolution call**
Replace this block (currently at the top of the recall handler, ~line 95):
```python
if profile:
    from lore.server.routes.profiles import resolve_profile as _resolve_profile
    pool = await get_pool()
    async with pool.acquire() as conn:
        resolved = await _resolve_profile(conn, auth.org_id, profile, None)
```
with:
```python
if profile:
    resolved = await services.profiles.resolve_profile(store, auth.org_id, profile, key_default=None)
```
The `resolved` consumer (line ~103+) keeps using `resolved.k`, `resolved.threshold`, etc. — `ResolvedProfile`'s accessor surface matches the dict-key access pattern via attribute access. (Implementer: if the existing consumer code uses `resolved.get("k")`-style dict access, swap to attribute access; the diff is mechanical.)

The other three retrieve.py allowlisted helpers (`_record_retrieval_event`, `_bump_access_counts`, `_fetch_session_snapshots`) stay — out of scope for this phase.
Commit: `refactor(routes): retrieve.py uses profiles service for resolution`

**T10 — Refactor `routes/profiles.py` (8 handlers)**
Each handler: parse request → call service → serialize response.

Delete from `routes/profiles.py`:
- `DEFAULT_PROFILES` constant (now in service)
- `_profile_cache` dict + `_get_cached_profile`/`_set_cached_profile` (now in service)
- `_row_to_response` (replaced with `StoredProfile → ProfileResponse` Pydantic conversion at handler boundary; one small helper kept inline if useful)
- `resolve_profile` async function (now `services.profiles.resolve_profile`)
- All inline SQL + `pool = await get_pool()` calls

Add `Depends(get_store)` to each handler. Map service-raised exceptions:
- `StoreNotFound` → `HTTPException(404, ...)`
- `ProfileImmutableError` → `HTTPException(403, "Cannot modify preset profiles")` (or "Cannot delete...")
- `ValueError("No fields to update")` → `HTTPException(400, ...)`
- `IntegrityError` → `HTTPException(409, "Profile name already exists")` (matches today's catch at line 209)

Eight handlers to refactor (current → new):
1. `GET /v1/profiles` → `services.profiles.list_profiles`
2. `GET /v1/profiles/{profile_id}` → `services.profiles.get_profile`
3. `POST /v1/profiles` → `services.profiles.create_profile`
4. `PUT /v1/profiles/{profile_id}` → `services.profiles.update_profile_by_id`
5. `DELETE /v1/profiles/{profile_id}` → `services.profiles.delete_profile_by_id`
6. `GET /v1/profiles/defaults` → `services.profiles.get_default_profiles` (pure return; no store call)
7. `PUT /v1/profiles/name/{profile_name}` → `services.profiles.update_profile_by_name`
8. `DELETE /v1/profiles/name/{profile_name}` → `services.profiles.delete_profile_by_name`

Commit: `refactor(routes): profiles.py uses profiles service`

### Tests + cleanup

**T11 — Add profiles route tests**
`tests/server/test_profiles_routes.py`: 8 tests covering each handler with `FakeStore` mocks. Pattern follows `tests/test_memories_server.py` (Phase 1A) and `tests/server/test_graph_routes.py` (Phase 1B).
Cover happy paths + 404 + 403 (preset) + 400 (empty update) + 409 (uniqueness).
Commit: `test(server): add profiles route tests with FakeStore mocks`

**T12 — Update CI guard**
`scripts/check_routes_no_sql.py`:
- `MIGRATED_ROUTES += "src/lore/server/routes/profiles.py"`.
- Drop the retrieve.py allowlist entry that reads:
  ```python
  "pool = await get_pool()",   # profile resolution + analytics + bump_access + session snapshots
  ```
  Replace with:
  ```python
  "pool = await get_pool()",   # analytics + bump_access + session snapshots (profile resolution migrated in 1C)
  ```
  The remaining three allowlist entries (`retrieval_events`, `UPDATE memories\n`, `len(params)`) stay — they cover the three other helpers still in scope for future phases.
Commit: `chore(ci): extend routes-no-SQL guard to profiles slice`

**T13 — Update docs**
- `CHANGELOG.md` Unreleased section: PolicyOps slice + profiles services + migration 018.
- `docs/architecture.md` persistence-layer section: mention `PolicyOps` as the third Store group landed (after `MemoryOps` and `GraphOps`).
Commit: `docs: document profiles slice migration`

**T14 — Final verification**
- Run `pytest tests/ 2>&1 | tail -3` — must show all existing + new tests passing, 0 failures.
- Run `python scripts/check_routes_no_sql.py` — must exit 0 with `routes/profiles.py` listed in the OK count.
- Run `docker exec lore-test-pg psql -U lore -d lore_test -c "\d retrieval_profiles"` — confirms the four new columns are present.
- Run `grep -rn "get_pool\|asyncpg\|from lore.server.routes.profiles" src/lore/server/routes/profiles.py src/lore/server/routes/retrieve.py` — should show only the three remaining allowlisted retrieve.py helpers, no profiles.py references, no cross-route imports of `resolve_profile`.
No commit.

---

## Self-review

- All 8 profiles handlers refactored to call services.
- The `_resolve_profile` cross-route import in `retrieve.py` is gone.
- All 7 `PolicyOps` methods implemented + contract-tested.
- One service module + three new test files match the plan.
- Migration 018 lands the four schema columns the existing route code already references.
- CI guard extended; allowlist trimmed by one entry.
- `DEFAULT_PROFILES` and the 60s cache moved into the service exactly as today (no behavior change).

### Known risks (don't block this plan)

- **Existing production DBs.** If a real Lore-Cloud DB has these columns added by a manual `ALTER` (not in git), `ADD COLUMN IF NOT EXISTS` is a no-op — safe. If it doesn't, the migration adds them — also safe. No data backfill needed (`k` and `threshold` default to `NULL`; the alias logic in the service handles nulls correctly via the `max_results`/`min_score` fallback chain).
- **Cache is per-process.** Same as today. Multi-worker uvicorn with shared cache is out of scope; solo mode runs one worker so this is fine. Postgres-mode multi-worker deployments rebuild the cache on each worker independently — same as today.
- **`DEFAULT_PROFILES` wire shape.** Currently returned as `Dict[str, Any]` from `/v1/profiles/defaults`. The service returns the same `Mapping[str, dict]` so the wire shape is preserved exactly.
- **Preset/global vs. org-owned uniqueness.** `(org_id, name)` is unique; presets live in `__global__`. Creating an org-owned profile with the same name as a preset is allowed (different `org_id`); the resolver test in T7 confirms org-owned shadows global at lookup time.
- **Order matters: T9 before T10.** T10 deletes `resolve_profile` from `routes/profiles.py`. T9 must rewire `retrieve.py` to use the service first, otherwise the intermediate commit has a dangling import. Plan order is correct.
- **Alias-logic preservation.** The current route code's `k`/`max_results` and `threshold`/`min_score` sync-on-create-and-update behavior is mildly weird (allows divergence when both fields explicitly set, but auto-syncs when only one is). Tests in T8 pin this exactly; if anyone wants to clean it up, that's a follow-up, not Phase 1C.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — Fresh implementer per task; controlling Claude provides per-task code at dispatch time using this plan as reference. Mirrors Phase 1B's execution model.

**2. Inline Execution** — Apply tasks in this session via executing-plans.

Which approach?
