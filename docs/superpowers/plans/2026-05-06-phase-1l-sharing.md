# Phase 1L — Sharing Slice Implementation Plan

**Goal:** Migrate `routes/sharing.py` (10 main handlers + 1 mounted `rate_lesson` handler, 13 `get_pool` calls, 406 LOC). New `SharingOps` Store slice on 4 tables + lessons-touching ops (stats / purge / rate).

After Phase 1L: ZERO unmigrated route files. Only `lore/server/auth.py` middleware remains.

**Spec ref:** Phase 1K as the immediate template.

## Files

### New
- `src/lore/services/sharing.py`
- `tests/persistence/test_contract_sharing.py`
- `tests/services/test_sharing.py`
- `tests/server/test_sharing_routes.py`

### Modified
- `src/lore/persistence/types.py` (+8 dataclasses)
- `src/lore/persistence/protocol.py` (SharingOps slice)
- `src/lore/persistence/postgres.py` (~12 new methods)
- `src/lore/persistence/__init__.py`
- `src/lore/server/routes/sharing.py` (10 + 1 handlers refactored)
- `scripts/check_routes_no_sql.py` (20 → 21)
- `tests/persistence/test_types.py`, `test_protocol.py`
- `CHANGELOG.md`, `docs/architecture.md`

## Tasks

**T1 — Foundation: dataclasses + SharingOps protocol slice**

Dataclasses (in `types.py`):

```python
@dataclass(frozen=True, slots=True)
class SharingConfigData:
    enabled: bool
    human_review_enabled: bool
    rate_limit_per_hour: int
    volume_alert_threshold: int
    updated_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class SharingConfigPatch:
    enabled: Optional[bool] = None
    human_review_enabled: Optional[bool] = None
    rate_limit_per_hour: Optional[int] = None
    volume_alert_threshold: Optional[int] = None


@dataclass(frozen=True, slots=True)
class AgentSharingConfigData:
    agent_id: str
    enabled: bool
    categories: Sequence[str]
    updated_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class DenyListRuleData:
    id: str
    pattern: str
    is_regex: bool
    reason: Optional[str]
    created_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class NewDenyListRule:
    org_id: str
    pattern: str
    is_regex: bool = False
    reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class AuditEventData:
    id: str
    event_type: str
    lesson_id: Optional[str]
    query_text: Optional[str]
    initiated_by: str
    created_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class NewAuditEvent:
    org_id: str
    event_type: str
    initiated_by: str
    lesson_id: Optional[str] = None
    query_text: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SharingStatsData:
    count_shared: int
    last_shared: Optional[datetime]
    audit_summary: Mapping[str, int]
```

SharingOps protocol (12 methods):

```python
async def get_or_init_sharing_config(self, org_id: str) -> SharingConfigData: ...
async def update_sharing_config(self, org_id: str, patch: SharingConfigPatch) -> SharingConfigData: ...
async def list_agent_sharing_configs(self, org_id: str) -> Sequence[AgentSharingConfigData]: ...
async def upsert_agent_sharing_config(self, org_id: str, agent_id: str, *, enabled: bool, categories: Sequence[str]) -> AgentSharingConfigData: ...
async def list_deny_rules(self, org_id: str) -> Sequence[DenyListRuleData]: ...
async def create_deny_rule(self, rule: NewDenyListRule) -> DenyListRuleData: ...
async def delete_deny_rule(self, rule_id: str, org_id: str) -> bool: ...
async def list_audit_events(self, org_id: str, *, event_type: Optional[str] = None, from_date: Optional[datetime] = None, to_date: Optional[datetime] = None, limit: int = 50) -> Sequence[AuditEventData]: ...
async def record_audit_event(self, event: NewAuditEvent) -> None: ...
async def get_sharing_stats(self, org_id: str) -> SharingStatsData: ...
async def purge_sharing(self, org_id: str) -> int: ...   # returns deleted_lessons count, runs in tx
async def rate_lesson(self, lesson_id: str, org_id: str, delta: int, initiated_by: str) -> Optional[int]: ...   # atomic update + audit, returns reputation_score or None
```

Update `tests/persistence/test_protocol.py`: REQUIRED_SHARING_OPS + 1 test.

Tests in test_types.py for each dataclass.

Commit: `feat(persistence): add Sharing slice types + SharingOps protocol`

**T2 — PostgresStore: SharingOps config + agent (4 methods)**

`get_or_init_sharing_config`, `update_sharing_config`, `list_agent_sharing_configs`, `upsert_agent_sharing_config`. JSONB encoding for `categories`. Stub remaining 8 with NotImplementedError.

Contract tests in NEW `tests/persistence/test_contract_sharing.py` (~10 tests).

Commit: `feat(persistence): SharingOps config + agent methods`

**T3 — PostgresStore: SharingOps deny + audit (5 methods)**

`list_deny_rules`, `create_deny_rule`, `delete_deny_rule`, `list_audit_events`, `record_audit_event`. Dynamic WHERE for audit filtering.

Contract tests.

Commit: `feat(persistence): SharingOps deny-list + audit`

**T4 — PostgresStore: SharingOps stats + purge + rate (3 methods)**

`get_sharing_stats` reads `lessons` (COUNT + MAX) and `sharing_audit` (event_type → count). `purge_sharing` runs the 5-table cascade in one transaction, returns `deleted_lessons` count. `rate_lesson` is atomic: UPDATE lessons RETURNING reputation_score + INSERT into sharing_audit, all in one transaction.

After T4: zero NotImplementedError stubs.

Contract tests.

Commit: `feat(persistence): SharingOps stats, purge, rate_lesson`

**T5 — services/sharing.py + tests**

11 functions matching the route handlers. The `_record_audit` helper becomes a thin wrapper over `record_audit_event`. Validation (delta ∈ {1,-1}, confirmation == "PURGE") moves to the service — service raises ValueError; route maps to 400.

Commit: `feat(services): sharing service`

**T6 — Refactor routes/sharing.py (10 + 1 handlers)**

Each thin. Drop `_record_audit` (moved to service). Drop both routers' inline SQL.

Commit: `refactor(routes): sharing.py uses sharing service`

**T7 — Route tests with FakeStore**

`tests/server/test_sharing_routes.py` — ~12 tests covering all 11 handlers + 400 (bad delta, bad purge confirmation) + 404 (lesson not found, deny rule not found).

Commit: `test(server): add sharing route tests`

**T8 — CI guard (20 → 21)**

Add sharing.py to MIGRATED_ROUTES. Allowlist: `"a deny rule"` for the Update/Delete docstrings.

Commit: `chore(ci): extend routes-no-SQL guard to sharing slice`

**T9 — Docs (inline)**

CHANGELOG + architecture.md updates. Update unmigrated-list to ONLY the auth.py middleware.

Commit: `docs: document sharing slice migration`

**T10 — Final verification**

Standard checks. No commit.

## Known risks

- The pre-1L `purge` handler returns `deleted_lessons` count BEFORE the actual delete (counted via `SELECT COUNT(*)`). Preserved for now — caller relies on it.
- `rate_lesson` audit insert and lesson update are now wrapped in one transaction (matches existing behavior; `pool.acquire() async with conn.transaction()`).
