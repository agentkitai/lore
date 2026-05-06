# Phase 1D — Identity Slice (WorkspaceOps + AuthOps) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task below is dispatched to a fresh implementer subagent with task-specific code spelled out in the dispatch prompt (the controlling Claude has the full slice map and synthesizes per-task detail at dispatch time).

**Goal:** Apply the Phase 1A/1B/1C pattern (Store abstraction + Service layer + route refactor) to the identity slice. After this plan: every handler in `routes/workspaces.py` and `routes/keys.py` calls services exclusively; all workspace/member/api-key SQL lives in `PostgresStore`'s new `WorkspaceOps` (9 methods) and `AuthOps` (5 methods) slices.

**Architecture:** No new architecture. Same Store / Services / Routes layering as 1A–1C. ~14 new methods on Store across two slices; two new service modules (`services/workspaces.py`, `services/keys.py`); 13 route handlers refactored.

**Tech Stack:** Same as 1A–1C. No new runtime deps. Postgres test DB at `localhost:5432` / `lore_test` reused.

**Spec reference:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`. Section "Components" (1, 2). Phase 1C plan: `docs/superpowers/plans/2026-05-06-phase-1c-profiles.md` — the immediate template; Phase 1B plan: `docs/superpowers/plans/2026-05-05-phase-1b-graph-slice.md` — original task structure.

---

## File structure

### Created in this plan

| Path | Responsibility |
|---|---|
| `src/lore/services/workspaces.py` | Workspace + member service module — owns `WORKSPACE_ROLES` constant, the role-rank helper `has_ws_permission`, archive logic, slug-uniqueness exception mapping |
| `src/lore/services/keys.py` | API key service module — owns key generation (`lore_sk_` prefix + SHA-256 hash), the "can't revoke last root key" rule, and the cache-invalidation handoff to `auth.invalidate_key` |
| `tests/persistence/test_contract_workspaces.py` | Contract tests for the 9 `WorkspaceOps` methods |
| `tests/persistence/test_contract_keys.py` | Contract tests for the 5 `AuthOps` methods |
| `tests/services/test_workspaces.py` | Service tests for workspace + member operations |
| `tests/services/test_keys.py` | Service tests for key generation, revocation, "last root key" rule |
| `tests/server/test_workspaces_routes.py` | Route tests for the 10 workspace handlers using `FakeStore` mocks |
| `tests/server/test_keys_routes.py` | Route tests for the 3 keys handlers using `FakeStore` mocks |

### Modified in this plan

| Path | Change |
|---|---|
| `src/lore/persistence/types.py` | Add `NewWorkspace`, `StoredWorkspace`, `WorkspacePatch`, `NewMember`, `StoredMember`, `NewApiKey`, `StoredApiKey` dataclasses |
| `src/lore/persistence/protocol.py` | Add 9 `WorkspaceOps` + 5 `AuthOps` methods to `Store` Protocol |
| `src/lore/persistence/postgres.py` | Implement all `WorkspaceOps` + `AuthOps` methods on `PostgresStore` |
| `src/lore/persistence/__init__.py` | Re-export new dataclasses |
| `src/lore/server/routes/workspaces.py` | All 10 handlers call services; drop `_has_ws_permission` (now in service); drop inline SQL |
| `src/lore/server/routes/keys.py` | All 3 handlers call services; drop `has_ws_col` introspection probe (schema is guaranteed post-016); drop inline SQL |
| `src/lore/server/auth.py` | Add public `invalidate_key(key_hash)` helper that pops `_key_cache` |
| `scripts/check_routes_no_sql.py` | Add `routes/workspaces.py` and `routes/keys.py` to `MIGRATED_ROUTES` (8 → 10) |
| `tests/server/test_keys.py` | Redirect mocks from inline-SQL fixtures to service-layer mocks; OR keep as integration test that exercises the full app over the new service stack — pick whichever produces a clean diff |
| `tests/test_workspaces.py` | Same redirect concern as `test_keys.py` |
| `tests/persistence/test_types.py`, `tests/persistence/test_protocol.py` | Extend to cover the new dataclasses + protocol methods |
| `CHANGELOG.md`, `docs/architecture.md` | Note WorkspaceOps + AuthOps slices landed |

### Out of scope (deferred)

- **`lore/server/auth.py` middleware migration** — the hot-path key lookup at `auth.py:198` and the `last_used_at` update at `auth.py:255` still call `get_pool()`. That's a separate slice (call it 1E or fold into Phase 4 `AsyncLore`); it sits at the per-request hot path and merits its own plan with caching/concurrency tests.
- **`routes/audit.py` and `src/lore/server/audit.py`** — audit log queries + helper. Different domain (event sourcing, not identity); future phase.
- **Workspace-scoped data isolation** (memories filtered by `workspace_id`, key-scope enforcement) — feature work, not refactor.
- **`get_api_key_by_hash` Store method** — needed by `auth.py` middleware but not by `routes/keys.py`. Adding it now without a consumer would be premature; the middleware-migration phase introduces it.
- **The remaining `retrieve.py`/`memories.py` allowlisted helpers from 1A** — orthogonal slice, future phase.

---

## Tasks (one task = one commit)

Each task follows the Phase 1A/1B/1C discipline: failing test first, run pytest, implement, run pytest, commit.

### Foundation — types, protocol

**T1 — Add identity dataclasses to `lore.persistence.types`**
Add `NewWorkspace`, `StoredWorkspace`, `WorkspacePatch`, `NewMember`, `StoredMember`, `NewApiKey`, `StoredApiKey` as `@dataclass(frozen=True, slots=True)`. Round-trip + immutability tests in `tests/persistence/test_types.py`.
Commit: `feat(persistence): add identity dataclasses to types`

**T2 — Extend `Store` protocol with WorkspaceOps slice**
Add 9 method signatures with full async types + 1-line docstrings. Update `tests/persistence/test_protocol.py` to assert presence + async-ness.
Commit: `feat(persistence): extend Store protocol with WorkspaceOps slice`

**T3 — Extend `Store` protocol with AuthOps slice**
Add 5 method signatures (`get_api_key`, `list_api_keys`, `create_api_key`, `revoke_api_key`, `count_active_root_keys`). Re-export new types from `lore.persistence.__init__`. Update protocol smoke tests.
Commit: `feat(persistence): extend Store protocol with AuthOps slice`

### PostgresStore — workspace ops

**T4 — `get_workspace` + `list_workspaces` + contract tests**
- `get_workspace`: `SELECT * FROM workspaces WHERE id = $1 AND org_id = $2` (org-scoped lookup).
- `list_workspaces`: `SELECT * FROM workspaces WHERE org_id = $1 AND (archived_at IS NULL OR $2) ORDER BY name`. The `include_archived` parameter toggles the second clause.
- Stub remaining 7 WorkspaceOps + 5 AuthOps with `NotImplementedError` so the protocol smoke test passes.
- Contract tests: round-trip both reads; org isolation; archived filtering.
Commit: `feat(persistence): WorkspaceOps.get_workspace + list_workspaces`

**T5 — `create_workspace` + `update_workspace` + `archive_workspace` + contract tests**
- `create_workspace`: store generates ULID `f"ws_{ULID()}"`. INSERT + RETURNING. Maps `asyncpg.UniqueViolationError` on `(org_id, slug)` to `IntegrityError`.
- `update_workspace`: dynamic SET clause for `name` and `settings` from `WorkspacePatch`. Returns `Optional[StoredWorkspace]`. Empty patch → `ValueError("No fields to update")`.
- `archive_workspace`: `UPDATE workspaces SET archived_at = now() WHERE id=$1 AND org_id=$2 AND archived_at IS NULL` — returns `bool` based on `result.endswith(" 1")`. Idempotent — already-archived workspace returns False.
- Contract tests for each + slug-conflict + archive idempotency.
Commit: `feat(persistence): WorkspaceOps.create + update + archive_workspace`

**T6 — Workspace member CRUD (4 methods) + contract tests**
- `add_workspace_member`: store generates ULID `f"wsm_{ULID()}"`. INSERT into `workspace_members` + RETURNING. Maps unique-collision (workspace_id, user_id) to `IntegrityError` if such a constraint exists; otherwise just allows duplicates (matches current schema — `workspace_members` has no UNIQUE on (workspace_id, user_id) per migration 016).
- `list_workspace_members`: `SELECT * FROM workspace_members WHERE workspace_id = $1` ordered by invited_at.
- `update_workspace_member_role`: `UPDATE ... SET role = $1 WHERE workspace_id = $2 AND user_id = $3 RETURNING *`. Returns `Optional[StoredMember]`.
- `remove_workspace_member`: `DELETE WHERE workspace_id = $1 AND user_id = $2`. Returns `bool`.
- Contract tests for each. Test member listing returns only members of the targeted workspace.
Commit: `feat(persistence): WorkspaceOps member operations (add/list/update/remove)`

### PostgresStore — auth ops

**T7 — `get_api_key` + `list_api_keys` + contract tests**
- `get_api_key`: `SELECT * FROM api_keys WHERE id = $1`. Returns `Optional[StoredApiKey]`.
- `list_api_keys`: `SELECT * FROM api_keys WHERE org_id = $1 ORDER BY created_at`. Returns `Sequence[StoredApiKey]`.
- Contract tests: insert raw rows (since `create_api_key` is still stubbed at this point), exercise both reads, verify ordering.
Commit: `feat(persistence): AuthOps.get_api_key + list_api_keys`

**T8 — `create_api_key` + `revoke_api_key` + `count_active_root_keys` + contract tests**
- `create_api_key`: store generates ULID `f"key_{ULID()}"`. INSERT all fields including `workspace_id`. RETURNING *. The schema HAS the `workspace_id` column (added in migration 016) — assume it exists; do NOT do an `information_schema` probe.
- `revoke_api_key`: `UPDATE api_keys SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL RETURNING *`. Returns the updated `StoredApiKey` if it was active, `None` otherwise (already-revoked or non-existent).
- `count_active_root_keys`: `SELECT COUNT(*) FROM api_keys WHERE org_id = $1 AND is_root = TRUE AND revoked_at IS NULL`. Returns `int`.
- Contract tests: round-trip create→get; revoke active vs already-revoked vs missing; count_active_root_keys reflects revocations.
Commit: `feat(persistence): AuthOps.create + revoke + count_active_root_keys`

### Services

**T9 — `services/workspaces.py` + service tests**
Module structure:
- `WORKSPACE_ROLES = ("viewer", "member", "admin", "owner")` constant.
- `_ROLE_RANK = {r: i for i, r in enumerate(WORKSPACE_ROLES)}`.
- `has_ws_permission(role: str, minimum: str) -> bool` — pure function, public (no underscore).
- 10 functions covering the 10 workspace+member operations:
  - `list_workspaces`, `get_workspace`, `create_workspace`, `update_workspace`, `replace_workspace` (alias for full-update PUT semantics — same store call as update, just maps differently from the PUT request body), `archive_workspace`, `add_member`, `list_members`, `update_member_role`, `remove_member`.
  - Each fetches the workspace first to verify org ownership; raises `StoreNotFoundError` on org mismatch or missing.
  - Member operations verify the workspace exists in-org first.

Tests cover happy paths, org-isolation 404, slug-conflict (`IntegrityError`), empty-patch (`ValueError`), and the role-rank helper (`has_ws_permission`).
Commit: `feat(services): workspaces service + role-rank helper`

**T10 — `services/keys.py` + auth.invalidate_key helper + service tests**

In `src/lore/server/auth.py` add (small public helper):
```python
def invalidate_key(key_hash: str) -> None:
    """Remove a key_hash from the in-process cache (called on revoke)."""
    _key_cache.pop(key_hash, None)
```

In `src/lore/services/keys.py`:
- `RAW_KEY_PREFIX = "lore_sk_"`.
- `_generate_key() -> tuple[str, str, str]` — returns `(raw_key, key_hash, key_prefix)` using `secrets.token_hex(16)` and `hashlib.sha256`.
- 4 functions:
  - `create_api_key(store, org_id, body) -> tuple[StoredApiKey, str]` — generates key, calls `store.create_api_key`, returns the row plus the raw key (caller exposes raw key once, never again).
  - `list_api_keys(store, org_id) -> Sequence[StoredApiKey]`.
  - `revoke_api_key(store, key_id, org_id) -> None`. Service flow:
    1. Fetch via `store.get_api_key(key_id)`.
    2. If None or `row.org_id != org_id`, raise `StoreNotFoundError("api_keys", key_id)`.
    3. If `row.is_root` and `row.revoked_at is None`: check `store.count_active_root_keys(row.org_id)`; if it equals 1, raise `LastRootKeyError("Cannot revoke the last root key")`.
    4. Call `store.revoke_api_key(key_id)` — discard the bool, we already verified.
    5. Call `auth.invalidate_key(row.key_hash)`.
  - (no `get_api_key` service function — routes/keys.py doesn't have a `GET /v1/keys/{id}` endpoint; the 3 handlers are POST, GET (list), DELETE).

Add new exception `LastRootKeyError(LoreError)` to `lore/persistence/exceptions.py` with docstring "Cannot revoke the last active root API key for an org."

Service tests use the `store` fixture from `tests/services/conftest.py`. Cover:
- Key generation produces `lore_sk_` prefix and 64-char hex hash.
- Round-trip create→list.
- Revoke last root → `LastRootKeyError`.
- Revoke non-root with one root present → succeeds.
- Revoke non-existent or other-org key → `StoreNotFoundError`.
- `auth.invalidate_key` is called on successful revoke (use monkeypatch + counter).

Commit: `feat(services): keys service + auth.invalidate_key helper`

### Route refactors

**T11 — Refactor `routes/workspaces.py` (10 handlers)**
Each handler: parse → check role permission → call service → serialize. Map exceptions:
- `StoreNotFoundError` → 404
- `IntegrityError` (slug collision) → 409
- `ValueError` (empty patch) → 400
- Workspace-role permission failures → 403 at the **route layer** via `services.workspaces.has_ws_permission(auth.role, "admin")` BEFORE the service call. The service layer doesn't know about request-level `AuthContext`; routes own that concern. Mirrors current routes/workspaces.py behavior at lines 156-159.

Drop from `routes/workspaces.py`:
- `WORKSPACE_ROLES` constant (now in service)
- `_ROLE_RANK` dict (now in service)
- `_has_ws_permission` helper (now `services.workspaces.has_ws_permission`)
- All inline SQL + `pool = await get_pool()`
- The `_ts` helper (StoredWorkspace.created_at is datetime; routes do `.isoformat()` inline at the boundary)

Add `Depends(get_store)` on each handler. Existing tests in `tests/test_workspaces.py` may need their mocks redirected from inline-SQL to service-layer or store-layer; if the existing tests are full-app integration tests using the real store, they should keep working unchanged.

Commit: `refactor(routes): workspaces.py uses workspaces service`

**T12 — Refactor `routes/keys.py` (3 handlers)**
Drop:
- `has_ws_col` introspection probe (schema is guaranteed post-016)
- All inline SQL + `pool = await get_pool()`
- `_require_root` can stay at the route layer (it's an HTTP-403 mapping concern)

Add `Depends(get_store)`. Map exceptions:
- `StoreNotFoundError` → 404
- `LastRootKeyError` → 400 (matches current `HTTPException(400, "Cannot revoke last root key")`)

`tests/server/test_keys.py` mocks need redirection from `_valid_key_row` (raw asyncpg fixture) to service/store mocks. Either keep the existing tests as-is (if they exercise the full HTTP path with real DB via app fixtures) or replace with FakeStore-style mocks — pick whichever yields a clean diff.

Commit: `refactor(routes): keys.py uses keys service`

### Tests + cleanup

**T13 — Add workspaces route tests**
`tests/server/test_workspaces_routes.py`: 10 tests covering each handler with `FakeStore` mocks. Mirror the pattern from Phase 1C's `tests/server/test_profiles_routes.py`. Cover happy paths + 404 + 403 + 409 + 400.
Commit: `test(server): add workspaces route tests with FakeStore mocks`

**T14 — Add keys route tests**
`tests/server/test_keys_routes.py`: 3 tests + error-case tests (404 missing, 400 last-root-key, root-required 403). FakeStore pattern.
Commit: `test(server): add keys route tests with FakeStore mocks`

**T15 — Update CI guard**
`scripts/check_routes_no_sql.py`:
- `MIGRATED_ROUTES += {"src/lore/server/routes/workspaces.py", "src/lore/server/routes/keys.py"}` (alphabetized).
- Allowlist any docstrings that match the regex (likely "Update a workspace", "Delete a key" — narrow strings).
- Confirm 10 files OK after the change.
Commit: `chore(ci): extend routes-no-SQL guard to identity slice`

**T16 — Update docs**
- `CHANGELOG.md` Unreleased section: WorkspaceOps + AuthOps slices, services, the `auth.invalidate_key` helper, and the `LastRootKeyError` exception.
- `docs/architecture.md` persistence-layer section: add WorkspaceOps + AuthOps to the slice list; update slice progression sentence; bump migrated-route count from 8 → 10 with breakdown.
Commit: `docs: document identity slice migration`

**T17 — Final verification**
- `pytest tests/` — all pass.
- `python3 scripts/check_routes_no_sql.py` — exit 0, 10 files OK.
- `grep -nE "get_pool|asyncpg" src/lore/server/routes/workspaces.py src/lore/server/routes/keys.py` — empty.
- `grep "has_ws_col" src/lore/server/routes/keys.py` — empty (probe dropped).
No commit.

---

## Self-review

- All 13 identity route handlers (10 workspace + 3 keys) refactored to call services.
- All 14 store methods (9 WorkspaceOps + 5 AuthOps) implemented + contract-tested.
- Two service modules + four test files match.
- New `auth.invalidate_key` helper exposes a clean cache-invalidation hook.
- New `LastRootKeyError` typed exception replaces string-matching.
- `has_ws_col` introspection probe dropped — schema is guaranteed post-016.
- CI guard extended to 10 routes; no widening of the allowlist beyond docstring suppressions.

### Known risks (don't block this plan)

- **Existing `tests/test_workspaces.py` and `tests/server/test_keys.py`** are written against the pre-refactor SQL paths. T11 and T12 must redirect their mocks or accept that they're now integration tests. If they break in unexpected ways, the implementer may need to rewrite them; size this as part of T11/T12.
- **`workspace_members` lacks a UNIQUE constraint** on `(workspace_id, user_id)`. The current route at `routes/workspaces.py:227-234` does no pre-check before INSERT, so duplicates are technically allowed. The plan preserves this — add a follow-up note.
- **Route ordering for `/workspaces/{workspace_id}/members`** — make sure no static-string path is shadowed by a `{workspace_id}` parameter (mirrors the `/defaults` issue from Phase 1C). Current ordering looks safe; T11 should re-verify.
- **`PUT /workspaces/{id}` (replace) vs. `PATCH /workspaces/{id}` (update)** — both go through the same store method with different patch shapes. Service layer has separate `replace_workspace` vs `update_workspace` functions, both calling `store.update_workspace` — the difference is only how the patch is constructed at the boundary. Document this in T9 to avoid implementer confusion.
- **`auth.py` middleware still uses `get_pool()`** — this is intentional for 1D. Note in CHANGELOG that the auth slice migration is a follow-up phase.
- **`tests/persistence/test_contract_workspaces.py` foreign-key**: `workspace_members.workspace_id` references `workspaces(id)`. Member-op tests must create a workspace first via `create_workspace` (or raw INSERT). The transaction-rollback fixture isolates this safely.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — Fresh implementer per task; controlling Claude provides per-task code at dispatch time using this plan as reference. Mirrors Phase 1B/1C execution.

**2. Inline Execution** — Apply tasks in this session via executing-plans.

Which approach?
