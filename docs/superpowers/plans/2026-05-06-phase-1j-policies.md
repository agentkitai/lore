# Phase 1J — Retention Policies (RetentionOps) Implementation Plan

**Goal:** Migrate `routes/policies.py` (8 handlers, 376 LOC). New `RetentionOps` Store slice (~10 methods across 3 tables).

**Tables in scope (per migration 015):** `retention_policies`, `snapshot_metadata`, `restore_drill_results`.

**Spec ref:** Phase 1I plan as the immediate template.

---

## File structure

### New
- `src/lore/services/policies.py`
- `tests/persistence/test_contract_policies.py`
- `tests/services/test_policies.py`
- `tests/server/test_policies_routes.py`

### Modified
- `src/lore/persistence/types.py` (+6 dataclasses)
- `src/lore/persistence/protocol.py` (RetentionOps slice)
- `src/lore/persistence/postgres.py` (10 new methods)
- `src/lore/persistence/__init__.py` (re-exports)
- `src/lore/server/routes/policies.py` (refactor 8 handlers)
- `scripts/check_routes_no_sql.py` (+ policies.py → 19)
- `tests/persistence/test_types.py`, `test_protocol.py` (extend)
- `CHANGELOG.md`, `docs/architecture.md`

---

## Tasks

### T1 — Foundation: dataclasses + protocol

Add 6 dataclasses under `# ── Retention slice ───`:

```python
@dataclass(frozen=True, slots=True)
class NewRetentionPolicy:
    org_id: str
    name: str
    retention_window: Mapping[str, Any] = field(default_factory=lambda: {"working": 3600, "short": 604800, "long": None})
    snapshot_schedule: Optional[str] = None
    encryption_required: bool = False
    max_snapshots: int = 50
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class StoredRetentionPolicy:
    id: str
    org_id: str
    name: str
    retention_window: Mapping[str, Any]
    snapshot_schedule: Optional[str]
    encryption_required: bool
    max_snapshots: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RetentionPolicyPatch:
    name: Optional[str] = None
    retention_window: Optional[Mapping[str, Any]] = None
    snapshot_schedule: Optional[str] = None
    encryption_required: Optional[bool] = None
    max_snapshots: Optional[int] = None
    is_active: Optional[bool] = None


@dataclass(frozen=True, slots=True)
class StoredSnapshotMetadata:
    id: str
    org_id: str
    policy_id: Optional[str]
    name: str
    path: str
    size_bytes: Optional[int]
    memory_count: Optional[int]
    encrypted: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewDrillResult:
    org_id: str
    snapshot_id: Optional[str]
    snapshot_name: str
    started_at: datetime
    completed_at: Optional[datetime]
    recovery_time_ms: Optional[int]
    memories_restored: Optional[int]
    status: str  # 'running'|'success'|'failed'
    error: Optional[str]


@dataclass(frozen=True, slots=True)
class StoredDrillResult:
    id: str
    org_id: str
    snapshot_id: Optional[str]
    snapshot_name: str
    started_at: datetime
    completed_at: Optional[datetime]
    recovery_time_ms: Optional[int]
    memories_restored: Optional[int]
    status: str
    error: Optional[str]
    created_at: datetime
```

Re-export from `__init__.py`.

Add `# ── RetentionOps ────` section to protocol.py (10 methods):

```python
async def list_retention_policies(self, org_id: str) -> Sequence[StoredRetentionPolicy]: ...
async def get_retention_policy(self, policy_id: str, org_id: str) -> Optional[StoredRetentionPolicy]: ...
async def create_retention_policy(self, policy: NewRetentionPolicy) -> StoredRetentionPolicy: ...
async def update_retention_policy(self, policy_id: str, org_id: str, patch: RetentionPolicyPatch) -> Optional[StoredRetentionPolicy]: ...
async def delete_retention_policy(self, policy_id: str, org_id: str) -> bool: ...

async def get_latest_snapshot_for_policy(self, policy_id: str, org_id: str) -> Optional[StoredSnapshotMetadata]: ...
async def count_snapshots_for_policy(self, policy_id: str) -> int: ...

async def record_drill_result(self, drill: NewDrillResult) -> StoredDrillResult: ...
async def list_drill_results_for_policy(self, policy_id: str, org_id: str, *, limit: int = 20) -> Sequence[StoredDrillResult]: ...
async def get_latest_drill_result(self, org_id: str) -> Optional[StoredDrillResult]: ...
```

Update test_protocol.py: REQUIRED_RETENTION_OPS + 2 tests. Tests in test_types.py for each dataclass.

Commit: `feat(persistence): add retention slice types + RetentionOps protocol`

### T2 — PostgresStore: retention policy CRUD (5 methods)

`list_retention_policies`, `get_retention_policy`, `create_retention_policy`, `update_retention_policy`, `delete_retention_policy`.

- ID: `f"retpol_{ULID()}"` for create.
- `retention_window` is JSONB; encode via `json.dumps(dict(...))`.
- create raises `IntegrityError` on `(org_id, name)` unique violation.
- `update_retention_policy` builds dynamic SET, bumps `updated_at = now()`, raises ValueError on empty patch.
- `delete_retention_policy` returns `True` if deleted.

Add `_row_to_retention_policy` helper.

Stub the 5 snapshot/drill methods with NotImplementedError.

Contract tests in NEW `tests/persistence/test_contract_policies.py`:
- list (org isolation, ordering by name).
- get round-trip + None.
- create round-trip + uniqueness.
- update changes field + missing returns None + empty patch ValueError.
- delete True/False.

Commit: `feat(persistence): RetentionOps policy CRUD (list/get/create/update/delete)`

### T3 — PostgresStore: snapshot + drill methods (5 methods)

- `get_latest_snapshot_for_policy(policy_id, org_id)`: `SELECT * FROM snapshot_metadata WHERE policy_id=$1 AND org_id=$2 ORDER BY created_at DESC LIMIT 1`. Add `_row_to_snapshot_metadata` helper.
- `count_snapshots_for_policy(policy_id)`: `SELECT COUNT(*)::int FROM snapshot_metadata WHERE policy_id=$1`.
- `record_drill_result(drill)`: ID `f"drill_{ULID()}"`. INSERT. RETURNING. Add `_row_to_drill_result`.
- `list_drill_results_for_policy(policy_id, org_id, limit)`: `SELECT r.* FROM restore_drill_results r JOIN snapshot_metadata s ON s.id = r.snapshot_id WHERE s.policy_id=$1 AND r.org_id=$2 ORDER BY r.created_at DESC LIMIT $3`.
- `get_latest_drill_result(org_id)`: `SELECT * FROM restore_drill_results WHERE org_id=$1 ORDER BY created_at DESC LIMIT 1`.

After T3: zero NotImplementedError stubs.

Contract tests cover each method with raw `_insert_snapshot`/`_insert_drill` helpers.

Commit: `feat(persistence): RetentionOps snapshot + drill methods`

### T4 — services/policies.py + tests

8 functions matching the 8 route handlers:
- `list_policies(store, *, org_id) -> Sequence[StoredRetentionPolicy]` — passthrough.
- `get_policy(store, *, policy_id, org_id) -> StoredRetentionPolicy` — raises StoreNotFoundError.
- `create_policy(store, *, org_id, body)` — builds NewRetentionPolicy.
- `update_policy(store, *, policy_id, org_id, patch)` — pre-fetch for 404; empty-patch ValueError.
- `delete_policy(store, *, policy_id, org_id)` — pre-fetch for 404; calls store.delete.
- `run_drill(store, *, policy_id, org_id) -> StoredDrillResult` — fetches policy (404 if missing), latest snapshot, simulates restore, records drill via `record_drill_result`. Imports `time` and `datetime` deferred.
- `list_drills(store, *, policy_id, org_id, limit)` — passthrough; verify policy exists first for 404.
- `check_compliance(store, *, org_id) -> list[dict]` — fetches active policies, for each computes snapshot count + checks for last drill, returns list of `{policy_id, policy_name, compliant, issues}`.

Service tests: ~12 tests covering happy paths + 404 + IntegrityError + empty patch.

Commit: `feat(services): policies service`

### T5 — Refactor routes/policies.py (8 handlers)

Each thin: parse → call service → translate to PolicyResponse. Drop _ts helper (datetimes go via `.isoformat()` inline). Drop all inline SQL.

Imports: `from lore.persistence import Store, StoredRetentionPolicy, ...`, `from lore.server.db import get_store`, `from lore.services import policies as policies_service`.

`Depends(get_store)` on each handler. Map exceptions: StoreNotFoundError→404, IntegrityError→409, ValueError→400.

After: file ~150 LOC (was 376).

Commit: `refactor(routes): policies.py uses policies service`

### T6 — Route tests with FakeStore

`tests/server/test_policies_routes.py`: 8-12 tests covering all 8 handlers + error paths.

Commit: `test(server): add policies route tests`

### T7 — CI guard (18 → 19)

Add policies.py to MIGRATED_ROUTES. Allowlist `"a retention policy"` if Update/Delete docstrings trip the regex.

Commit: `chore(ci): extend routes-no-SQL guard to policies slice`

### T8 — Docs (inline)

CHANGELOG + architecture.md updates.

Commit: `docs: document policies slice migration`

### T9 — Final verification

Standard checks. No commit.

---

## Known risks

- `compliance` endpoint runs N+2 queries (list policies + count snapshots per policy + last drill). Service does this in Python over Store calls — acceptable for the small N.
- The `run_drill` handler simulates restore; the new service preserves this behavior. Production restore would use a real worker.
- `retention_window` JSONB roundtrip: asyncpg returns it as a string; decode in `_row_to_retention_policy`.
- Existing tests: check `tests/server/test_policies.py` and redirect mocks if needed.
