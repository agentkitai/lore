# Phase 1I — Dashboard Bundle (recent + audit + analytics + topics) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Migrate four small read-only dashboard route files to the Store/Service/Routes layering. After this phase: 14 → 18 migrated route files. Three slices remain: `sharing.py`, `slo.py`, `policies.py` (plus the auth middleware).

Files in scope and their reuse strategy:

| Route file | get_pool calls | Strategy |
|---|---|---|
| `recent.py` | 2 | Reuse existing `MemoryOps.list_memories` (already supports `since` filter via MemoryFilter) |
| `topics.py` | 3 | Delegate to existing Phase-1B `services.graph.entities` (`list_topics`, `get_topic_detail`) |
| `audit.py` | 2 | New `AuditOps` slice (1 method: `query_audit_log`) |
| `analytics.py` | 8 | Extend `AnalyticsOps` (1 new method: `compute_retrieval_analytics`) — all 7 SQL queries collapse into one Store method that returns a populated result dataclass |

Service modules per file (4 total): `services/recent.py`, `services/topics_dashboard.py`, `services/audit.py`, `services/analytics.py`.

**Spec reference:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`. Phase 1H plan as the immediate template.

---

## File structure

### Created in this plan

| Path | Responsibility |
|---|---|
| `src/lore/services/recent.py` | Wraps `MemoryOps.list_memories` with the `since`/format/grouping logic |
| `src/lore/services/topics_dashboard.py` | Delegates to existing `services.graph.entities`; adapts the response shape for the public `/v1/topics` API |
| `src/lore/services/audit.py` | Wraps `AuditOps.query_audit_log` |
| `src/lore/services/analytics.py` | Wraps `AnalyticsOps.compute_retrieval_analytics`; post-processes the result into the response shape |
| `tests/persistence/test_contract_dashboards.py` | Contract tests for the 2 new Store methods |
| `tests/services/test_dashboards.py` | Service tests for all four service modules |
| `tests/server/test_dashboards_routes.py` | Route tests covering all four routes via FakeStore |

### Modified in this plan

| Path | Change |
|---|---|
| `src/lore/persistence/types.py` | Add `StoredAuditEntry` dataclass + `RetrievalAnalyticsResult` dataclass (with nested `ScoreDistributionBucket`, `TopQueryRow`, `DailyStatRow`) |
| `src/lore/persistence/protocol.py` | Add `AuditOps` slice (1 method) + extend `AnalyticsOps` with `compute_retrieval_analytics` |
| `src/lore/persistence/postgres.py` | Implement both new methods on `PostgresStore` |
| `src/lore/persistence/__init__.py` | Re-export new dataclasses |
| `src/lore/server/routes/recent.py` | Handler thin; calls `services.recent` |
| `src/lore/server/routes/audit.py` | Handler thin; calls `services.audit` |
| `src/lore/server/routes/analytics.py` | Handler thin; calls `services.analytics` |
| `src/lore/server/routes/topics.py` | Both handlers thin; call `services.topics_dashboard` |
| `scripts/check_routes_no_sql.py` | Add 4 routes to `MIGRATED_ROUTES` (14 → 18) |
| `tests/persistence/test_types.py`, `tests/persistence/test_protocol.py` | Extend |
| `CHANGELOG.md`, `docs/architecture.md` | Note dashboard slice landed |

### Out of scope

- `routes/sharing.py`, `routes/slo.py`, `routes/policies.py` — future phases.
- `lore/server/auth.py` middleware — its own future phase.
- Refactoring `routes/graph/topics.py` (the UI-facing `/v1/ui/topics` endpoints) — already migrated in Phase 1B; unchanged.

---

## Tasks

### Foundation — types, protocol

**T1 — Dataclasses + protocol extensions**

In `src/lore/persistence/types.py`, add a new `# ── Dashboard slice dataclasses ───` section:

```python
@dataclass(frozen=True, slots=True)
class StoredAuditEntry:
    id: int
    org_id: str
    workspace_id: Optional[str]
    actor_id: str
    actor_type: str
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    metadata: Mapping[str, Any]
    ip_address: Optional[str]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ScoreDistributionBucket:
    bucket: str           # "0.0-0.3" | "0.3-0.5" | "0.5-0.7" | "0.7-0.9" | "0.9-1.0"
    count: int


@dataclass(frozen=True, slots=True)
class TopQueryRow:
    query: str
    count: int
    avg_score: Optional[float]


@dataclass(frozen=True, slots=True)
class DailyStatRow:
    date: str             # 'YYYY-MM-DD'
    queries: int
    avg_score: Optional[float]
    hit_rate: float       # 0..1


@dataclass(frozen=True, slots=True)
class RetrievalAnalyticsResult:
    total_queries: int
    queries_with_results: int
    queries_empty: int
    avg_results_per_query: float
    avg_score: Optional[float]
    avg_max_score: Optional[float]
    avg_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    score_distribution: Sequence[ScoreDistributionBucket]
    top_queries: Sequence[TopQueryRow]
    unique_memories_retrieved: int
    total_memories: int
    daily_stats: Sequence[DailyStatRow]
```

Re-export all new dataclasses from `__init__.py`.

In `src/lore/persistence/protocol.py`:

Add a new `# ── AuditOps ────` section (1 method):

```python
async def query_audit_log(
    self,
    *,
    org_id: str,
    workspace_id: Optional[str] = None,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 50,
) -> Sequence[StoredAuditEntry]: ...
```

Extend the existing `# ── AnalyticsOps ────` section with one method:

```python
async def compute_retrieval_analytics(
    self,
    *,
    org_id: str,
    days: int,
    project: Optional[str] = None,
) -> RetrievalAnalyticsResult: ...
```

Add new types to protocol.py imports.

Update `tests/persistence/test_protocol.py`:
- `REQUIRED_AUDIT_OPS = {"query_audit_log"}` + 2 tests.
- Extend `REQUIRED_ANALYTICS_OPS` with `"compute_retrieval_analytics"`.

Add tests in `tests/persistence/test_types.py` for each new dataclass (defaults, full, frozen, slots).

Commit: `feat(persistence): add dashboard slice types + AuditOps protocol + AnalyticsOps extension`

### PostgresStore — Store impls

**T2 — `query_audit_log` + contract tests**

`PostgresStore.query_audit_log`:

```sql
SELECT id, org_id, workspace_id, actor_id, actor_type, action,
       resource_type, resource_id, metadata, ip_address, created_at
FROM audit_log
WHERE org_id = $1
  [AND workspace_id = $N if filter set]
  [AND action = $N if filter set]
  [AND actor_id = $N if filter set]
  [AND created_at >= $N::timestamptz if since set]
ORDER BY created_at DESC
LIMIT $N
```

`_row_to_audit_entry` helper:
```python
def _row_to_audit_entry(row: "asyncpg.Record") -> StoredAuditEntry:
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata) if metadata else {}
    return StoredAuditEntry(
        id=row["id"],
        org_id=row["org_id"],
        workspace_id=row["workspace_id"],
        actor_id=row["actor_id"],
        actor_type=row["actor_type"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        metadata=dict(metadata or {}),
        ip_address=str(row["ip_address"]) if row["ip_address"] else None,
        created_at=row["created_at"],
    )
```

Stub `compute_retrieval_analytics` with NotImplementedError.

Contract tests in NEW file `tests/persistence/test_contract_dashboards.py`:
- `test_query_audit_log_returns_org_only`.
- `test_query_audit_log_workspace_filter`.
- `test_query_audit_log_action_filter`.
- `test_query_audit_log_since_filter`.
- `test_query_audit_log_orders_by_created_at_desc`.
- `test_query_audit_log_respects_limit`.

Use raw INSERT helper to seed audit_log rows.

Commit: `feat(persistence): AuditOps.query_audit_log`

**T3 — `compute_retrieval_analytics` + contract tests**

`PostgresStore.compute_retrieval_analytics`: combines all 7 queries from the existing `routes/analytics.py` handler in a single method, returning a populated `RetrievalAnalyticsResult`. Each sub-query mirrors the existing SQL exactly:

1. Summary stats (COUNT, FILTER, AVG)
2. P95 latency via `percentile_cont`
3. Score distribution (CASE buckets)
4. Top queries
5. Unique memories retrieved
6. Total memories COUNT (on `memories` table — separate WHERE)
7. Daily stats (date_trunc + GROUP BY)

Construct the result dataclass at the end. Use `_acquire()` for the connection; wrap all 7 in a single `async with conn:` block.

Build a shared WHERE clause for the 6 retrieval_events queries (org_id + days + optional project).

Contract tests:
- `test_compute_analytics_zero_events_returns_zeros` — fresh DB, no events.
- `test_compute_analytics_with_events` — insert via `store.record_retrieval_event` (Phase 1E) several events with varying scores/latency; verify summary stats and score_distribution.
- `test_compute_analytics_filters_by_project`.
- `test_compute_analytics_respects_days_window`.
- `test_compute_analytics_p95_latency`.

Stub the previous one stays; this is the second method.

After T3: zero NotImplementedError stubs in postgres.py.

Commit: `feat(persistence): AnalyticsOps.compute_retrieval_analytics`

### Services

**T4 — `services/recent.py`**

```python
"""Recent-activity service — wraps MemoryOps.list_memories with grouping logic."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

from lore.persistence import MemoryFilter, Store, StoredMemory


async def get_recent_activity(
    store: Store,
    *,
    org_id: str,
    project: Optional[str],
    hours: int,
    max_memories: int,
) -> Sequence[StoredMemory]:
    """Fetch memories created within the last `hours`. Caller does the project grouping."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    filter = MemoryFilter(org_id=org_id, project=project, since=since)
    memories = await store.list_memories(filter)
    return memories[:max_memories]
```

Service tests: 3-4 tests covering the time-window filter, project filter, max_memories cap.

Commit: `feat(services): recent activity service`

**T5 — `services/audit.py`**

```python
"""Audit service — passthrough to AuditOps."""

from typing import Optional, Sequence

from lore.persistence import Store, StoredAuditEntry


async def query_audit_log(
    store: Store,
    *,
    org_id: str,
    workspace_id: Optional[str] = None,
    action: Optional[str] = None,
    actor_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 50,
) -> Sequence[StoredAuditEntry]:
    """Query audit log with filters. Mirrors the AuditOps.query_audit_log signature exactly."""
    return await store.query_audit_log(
        org_id=org_id, workspace_id=workspace_id, action=action,
        actor_id=actor_id, since=since, limit=limit,
    )
```

Service tests: 2-3 tests verifying passthrough semantics.

Commit: `feat(services): audit service`

**T6 — `services/analytics.py`**

```python
"""Analytics service — wraps AnalyticsOps.compute_retrieval_analytics; post-processes derived fields."""

from typing import Optional

from lore.persistence import RetrievalAnalyticsResult, Store


async def get_retrieval_analytics(
    store: Store,
    *,
    org_id: str,
    days: int,
    project: Optional[str] = None,
) -> dict:
    """Compute retrieval analytics and shape into a wire-ready dict.

    Computes derived fields (hit_rate, memory_utilization, score_distribution percentages)
    that the wire response needs but the persistence layer doesn't compute.
    """
    result = await store.compute_retrieval_analytics(org_id=org_id, days=days, project=project)

    total = result.total_queries
    hit_rate = (result.queries_with_results / total) if total else 0.0
    memory_utilization = (result.unique_memories_retrieved / result.total_memories) if result.total_memories else None

    # Compute percentages for score buckets.
    total_scored = sum(b.count for b in result.score_distribution) or 1
    score_dist = [
        {"bucket": b.bucket, "count": b.count, "percentage": round(100 * b.count / total_scored, 1)}
        for b in result.score_distribution
    ]

    return {
        "total_queries": result.total_queries,
        "queries_with_results": result.queries_with_results,
        "queries_empty": result.queries_empty,
        "hit_rate": round(hit_rate, 4),
        "avg_results_per_query": round(result.avg_results_per_query, 2),
        "avg_score": result.avg_score,
        "avg_max_score": result.avg_max_score,
        "avg_latency_ms": result.avg_latency_ms,
        "p95_latency_ms": result.p95_latency_ms,
        "score_distribution": score_dist,
        "top_queries": [{"query": q.query, "count": q.count, "avg_score": q.avg_score} for q in result.top_queries],
        "memory_utilization": memory_utilization,
        "unique_memories_retrieved": result.unique_memories_retrieved,
        "total_memories": result.total_memories,
        "daily_stats": [{"date": d.date, "queries": d.queries, "avg_score": d.avg_score, "hit_rate": d.hit_rate} for d in result.daily_stats],
        "lookback_days": days,
    }
```

Service tests: 3-4 tests via real DB; verify hit_rate computed; verify percentages; verify zero-events case.

Commit: `feat(services): analytics service`

**T7 — `services/topics_dashboard.py`**

Delegates to existing Phase-1B `services.graph.entities`:

```python
"""Topics-dashboard service — adapts the public /v1/topics API onto graph services."""

from typing import Optional

from lore.persistence import Store
from lore.services.graph.entities import list_topics as _graph_list_topics, get_topic_detail as _graph_get_topic_detail


async def list_topics(
    store: Store,
    *,
    entity_type: Optional[str] = None,
    min_mentions: int = 3,
    limit: int = 50,
) -> list[dict]:
    """List topics (entities with mention_count >= threshold). Wraps GraphOps."""
    entities = await _graph_list_topics(store, entity_type=entity_type, min_mentions=min_mentions, limit=limit)
    return [
        {
            "entity_id": e.id,
            "name": e.name,
            "entity_type": e.entity_type,
            "mention_count": e.mention_count,
            "first_seen_at": e.first_seen_at.isoformat() if e.first_seen_at else None,
            "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
            "related_entity_count": 0,
        }
        for e in entities
    ]


async def get_topic_detail(
    store: Store,
    *,
    name: str,
    max_memories: int = 20,
    format: str = "brief",
) -> dict:
    """Get topic detail. Reuses graph services for the entity+relationships+memories triple."""
    detail = await _graph_get_topic_detail(store, name=name, max_memories=max_memories)
    if detail is None:
        return None
    # Adapt the shape for /v1/topics public response (different from /v1/ui/topics)
    # ... shape adaptation
    return detail  # or adapted shape
```

The exact return shape adaptation depends on what `services.graph.entities.get_topic_detail` returns. Implementer reads its current return shape and translates.

Service tests: 3 tests.

Commit: `feat(services): topics dashboard service`

### Route refactor

**T8 — Refactor all 4 dashboard routes**

One commit covering all four small files. Each gets:
- Drop inline SQL + `get_pool()`.
- Add `Depends(get_store)`.
- Replace handler bodies with service calls + thin response building.

After: `grep get_pool|asyncpg` in each → empty.

Existing tests (e.g., `tests/test_recent.py`, `tests/test_audit.py` if any) may need redirecting — handle inline.

Commit: `refactor(routes): recent/audit/analytics/topics use services`

### Tests + cleanup

**T9 — Add route tests with FakeStore**

`tests/server/test_dashboards_routes.py`: 8-12 tests covering all 4 route files' handlers.

Commit: `test(server): add dashboard route tests with FakeStore mocks`

**T10 — Update CI guard**

`scripts/check_routes_no_sql.py` adds the 4 routes (14 → 18). Add docstring allowlist entries if needed.

Commit: `chore(ci): extend routes-no-SQL guard to dashboard slice`

**T11 — Update CHANGELOG + architecture docs (inline, no subagent)**

Update both files. New AuditOps slice; AnalyticsOps extension; 4 service modules; route count 14 → 18; remaining unmigrated narrows to sharing/slo/policies + auth middleware.

Commit: `docs: document dashboard slice migration`

**T12 — Final verification**

Standard checks. No commit.

---

## Self-review

- 2 new Store methods + 1 new slice (AuditOps) + 1 AnalyticsOps extension.
- 5 new dataclasses (StoredAuditEntry + 4 analytics result types).
- 4 new service modules.
- 4 routes refactored.
- CI guard 14 → 18.
- The 7 analytics SQL queries collapse into one Store method — substantial cleanup.

### Known risks

- `topics_dashboard.py` shape adaptation: the `/v1/topics` public response shape differs slightly from `/v1/ui/topics`. The implementer reads `services.graph.entities.get_topic_detail`'s return shape and adapts.
- `analytics.py` post-processing: percentages and hit_rate are derived in the service from the Store's raw counts; this is the proper layer for them.
- The `audit_log` table's `metadata` column is JSONB; decode appropriately. `ip_address` is `INET`; coerce to str.
