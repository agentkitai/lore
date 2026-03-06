# Architecture: F4 — Multi-Level Memory Tiers

**Feature:** F4 — Multi-Level Memory Tiers
**Version:** v0.6.0 ("Open Brain")
**Status:** Draft
**PRD:** `_bmad-output/planning-artifacts/f04-memory-tiers-prd.md`

---

## 1. Overview

This document specifies the architecture for adding working/short/long memory tiers to Lore. Tiers provide default TTLs and recall weight boosts, following the cognitive-science model of working → short-term → long-term memory.

**Design Principles:**
- Tier is additive — it enriches the existing TTL and scoring systems, never replaces them.
- Backward compatible — all existing memories default to `long`; all APIs work without the tier parameter.
- Minimal surface — tier is a single VARCHAR field, not a separate table or storage backend.

---

## 2. Schema Changes

### 2.1 SQLite Migration

Follow the existing `_maybe_add_context_column()` pattern in `store/sqlite.py:89-97`.

```python
# store/sqlite.py — new method
def _maybe_add_tier_column(self) -> None:
    """Add tier column to existing memories table if missing."""
    cols = {
        row[1]
        for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
    }
    if "tier" not in cols:
        self._conn.execute(
            "ALTER TABLE memories ADD COLUMN tier TEXT DEFAULT 'long'"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier)"
        )
        self._conn.commit()
```

Call `self._maybe_add_tier_column()` in `__init__()` after the existing `_maybe_add_context_column()` call.

**Schema DDL update** — add to the `CREATE TABLE` statement (`_SCHEMA` constant):

```sql
tier TEXT DEFAULT 'long',
```

Add to the index block:

```sql
CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
```

### 2.2 Composite Index Strategy

For queries filtering by `(project, tier)` — which is the common recall/list pattern — add a composite index:

```sql
CREATE INDEX IF NOT EXISTS idx_memories_project_tier
    ON memories(project, tier);
```

This covers:
- `WHERE project = ? AND tier = ?` (full match)
- `WHERE project = ?` (prefix match, existing queries benefit)

The standalone `idx_memories_tier` index covers `WHERE tier = ?` (no project filter).

### 2.3 Postgres Migration (Server)

```sql
-- server/migrations/003_add_tier.sql
ALTER TABLE memories ADD COLUMN tier VARCHAR(10) DEFAULT 'long';
CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_memories_project_tier ON memories(project, tier);
```

All existing rows receive `tier = 'long'` from the DEFAULT clause. No backfill query needed.

---

## 3. Memory Dataclass Updates

### 3.1 New Field

In `types.py`, add `tier` field to the `Memory` dataclass after `type`:

```python
@dataclass
class Memory:
    id: str
    content: str
    type: str = "general"
    tier: str = "long"              # NEW — "working", "short", or "long"
    context: Optional[str] = None
    # ... rest unchanged ...
```

### 3.2 Tier Constants

Add to `types.py`:

```python
VALID_TIERS: Tuple[str, ...] = ("working", "short", "long")

TIER_DEFAULT_TTL: Dict[str, Optional[int]] = {
    "working": 3600,       # 1 hour
    "short":   604800,     # 7 days
    "long":    None,       # no expiry
}

TIER_RECALL_WEIGHT: Dict[str, float] = {
    "working": 1.0,        # baseline
    "short":   1.1,
    "long":    1.2,
}
```

### 3.3 MemoryStats Update

Add `by_tier` field to `MemoryStats`:

```python
@dataclass
class MemoryStats:
    total: int
    by_type: Dict[str, int] = field(default_factory=dict)
    by_tier: Dict[str, int] = field(default_factory=dict)   # NEW
    oldest: Optional[str] = None
    newest: Optional[str] = None
    expired_cleaned: int = 0
```

---

## 4. Store ABC Enhancements

### 4.1 Updated Signatures

In `store/base.py`, add `tier` parameter to `list()` and `count()`:

```python
class Store(ABC):
    @abstractmethod
    def list(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,       # NEW
        limit: Optional[int] = None,
    ) -> List[Memory]: ...

    @abstractmethod
    def count(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,       # NEW
    ) -> int: ...
```

`save()` and `update()` require no signature changes — they accept the full `Memory` object which already has the `tier` field. Implementations just need to persist it.

### 4.2 SqliteStore Changes

**`save()`** — Add `tier` to the INSERT column list and values:

```python
# In the INSERT OR REPLACE statement, add tier column
self._conn.execute(
    """INSERT OR REPLACE INTO memories
       (id, content, type, tier, context, tags, metadata, source, project,
        embedding, created_at, updated_at, ttl, expires_at,
        confidence, upvotes, downvotes)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    (memory.id, memory.content, memory.type, memory.tier, ...),
)
```

**`update()`** — Add `tier` to the UPDATE SET clause:

```python
self._conn.execute(
    """UPDATE memories SET content=?, type=?, tier=?, context=?, ...
       WHERE id=?""",
    (memory.content, memory.type, memory.tier, ...),
)
```

**`list()`** — Add tier filter to WHERE clause:

```python
def list(self, project=None, type=None, tier=None, limit=None):
    sql = "SELECT * FROM memories WHERE 1=1"
    params: List[Any] = []
    if project is not None:
        sql += " AND project = ?"
        params.append(project)
    if type is not None:
        sql += " AND type = ?"
        params.append(type)
    if tier is not None:                    # NEW
        sql += " AND tier = ?"
        params.append(tier)
    sql += " ORDER BY created_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = self._conn.execute(sql, params).fetchall()
    return [self._row_to_memory(r) for r in rows]
```

**`count()`** — Same pattern as list, add tier filter.

**`_row_to_memory()`** — Read `tier` from the row, defaulting to `"long"` for robustness:

```python
def _row_to_memory(self, row) -> Memory:
    return Memory(
        id=row["id"],
        content=row["content"],
        type=row["type"] or "general",
        tier=row["tier"] if "tier" in row.keys() else "long",  # NEW
        context=row["context"],
        # ... rest unchanged ...
    )
```

### 4.3 MemoryStore Changes

Add tier filtering to `list()` and `count()`:

```python
def list(self, project=None, type=None, tier=None, limit=None):
    memories = list(self._memories.values())
    if project is not None:
        memories = [m for m in memories if m.project == project]
    if type is not None:
        memories = [m for m in memories if m.type == type]
    if tier is not None:                    # NEW
        memories = [m for m in memories if m.tier == tier]
    memories.sort(key=lambda m: m.created_at, reverse=True)
    if limit is not None:
        memories = memories[:limit]
    return memories
```

`save()` and `update()` need no changes — they store the full Memory object.

---

## 5. Tier Weighting in Recall

### 5.1 Scoring Formula

The tier weight is a **multiplicative factor on the similarity component** of the score. It does NOT affect the freshness component — freshness is time-based and should remain independent.

**Current formula** (`lore.py:_recall_local`, ~line 370):
```python
similarity = cosine_score * memory.confidence * vote_factor
final_score = similarity_weight * similarity + freshness_weight * freshness
```

**New formula:**
```python
tier_weight = TIER_RECALL_WEIGHT.get(memory.tier, 1.0)
similarity = cosine_score * memory.confidence * vote_factor * tier_weight
final_score = similarity_weight * similarity + freshness_weight * freshness
```

**Rationale:** Applying tier_weight to the similarity component (not to the full score) means:
- Long-term memories get a 20% boost on their relevance signal.
- Freshness decay still operates independently — a stale long-term memory won't be artificially kept at the top.
- The boost is small enough that a highly-relevant working-tier memory still beats a marginally-relevant long-tier memory.

### 5.2 Tier Filtering in Recall

When `tier` is passed to `recall()`, it restricts candidates:

```python
def recall(self, query, *, tier=None, ...):
    # ...
    if hasattr(self._store, 'search'):
        # Remote: pass tier to server
        results = self._store.search(embedding=query_vec, tier=tier, ...)
    else:
        results = self._recall_local(query_vec, tier=tier, ...)
```

In `_recall_local()`, pass `tier` through to `self._store.list()`:

```python
all_memories = self._store.list(project=self.project, type=type, tier=tier)
```

This is a **pre-filter** — the store returns only matching-tier memories, reducing the scoring workload.

---

## 6. TTL Interaction

### 6.1 Rules

Tiers provide DEFAULT TTL only when explicit `ttl` is `None`. Explicit TTL always wins.

| Scenario | Effective TTL | Tier Recorded |
|----------|--------------|---------------|
| `remember("x")` | None (no expiry) | `long` |
| `remember("x", tier="working")` | 3600s | `working` |
| `remember("x", tier="short")` | 604800s | `short` |
| `remember("x", tier="working", ttl=7200)` | 7200s (explicit wins) | `working` |
| `remember("x", ttl=300)` | 300s (explicit wins) | `long` |

### 6.2 Implementation in `lore.py:remember()`

```python
def remember(self, content, *, tier="long", ttl=None, ...):
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid tier {tier!r}, must be one of: {VALID_TIERS}")

    # Tier provides default TTL only when no explicit TTL
    effective_ttl = ttl if ttl is not None else TIER_DEFAULT_TTL[tier]

    # Compute expires_at from effective TTL
    expires_at = None
    if effective_ttl is not None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=effective_ttl)
        ).isoformat()

    memory = Memory(
        # ...
        tier=tier,
        ttl=effective_ttl,
        expires_at=expires_at,
        # ...
    )
    self._store.save(memory)
```

**Key:** The `ttl` field stored in the Memory records the **effective** TTL, not the user's raw input. This means `memory.ttl` always reflects the actual TTL in effect, whether from explicit input or tier default.

---

## 7. Database Schema — Complete View

### 7.1 Full CREATE TABLE (Post-F4)

```sql
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    type        TEXT DEFAULT 'general',
    tier        TEXT DEFAULT 'long',
    context     TEXT,
    tags        TEXT,
    metadata    TEXT,
    source      TEXT,
    project     TEXT,
    embedding   BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    ttl         INTEGER,
    expires_at  TEXT,
    confidence  REAL DEFAULT 1.0,
    upvotes     INTEGER DEFAULT 0,
    downvotes   INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_memories_project    ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_type       ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_tier        ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_memories_project_tier ON memories(project, tier);
```

### 7.2 Migration Steps (SQLite Auto-Migration)

```
1. On SqliteStore.__init__():
   a. _maybe_migrate()            — existing: lessons → memories table
   b. _maybe_add_context_column() — existing: add context column
   c. _maybe_add_tier_column()    — NEW: add tier column + indexes
```

Each migration is idempotent (checks column/table existence before acting).

---

## 8. Configuration

### 8.1 Tier Weights (Configurable)

Allow overriding tier recall weights via `Lore.__init__()`:

```python
def __init__(
    self,
    # ... existing params ...
    tier_recall_weights: Optional[Dict[str, float]] = None,   # NEW
):
    self._tier_weights = tier_recall_weights or dict(TIER_RECALL_WEIGHT)
```

Usage in scoring:
```python
tier_weight = self._tier_weights.get(memory.tier, 1.0)
```

### 8.2 Tier Default TTLs (Not Configurable — Constants)

Tier default TTLs are defined as module-level constants in `types.py`. They are not per-instance configurable because:
- They define the semantic meaning of each tier.
- Explicit `ttl` override covers all customization needs.
- Keeping them constant simplifies reasoning about memory lifecycle.

If per-project TTL overrides become necessary (future), they would be added to a project configuration system, not the Lore constructor.

---

## 9. HTTP Store Updates

### 9.1 Field Mapping

In `store/http.py`, the `_memory_to_lesson()` and `_lesson_to_memory()` methods handle field mapping between Memory objects and the HTTP API payload.

**`_memory_to_lesson()`** — Add `tier` to the payload:

```python
def _memory_to_lesson(self, memory: Memory) -> Dict[str, Any]:
    payload = {
        # ... existing fields ...
    }
    meta = dict(memory.metadata) if memory.metadata else {}
    meta["type"] = memory.type
    meta["tier"] = memory.tier            # NEW
    # ... rest unchanged ...
    payload["meta"] = meta
    return payload
```

**`_lesson_to_memory()`** — Extract `tier` from response:

```python
def _lesson_to_memory(self, data: Dict[str, Any]) -> Memory:
    meta = data.get("meta", {})
    return Memory(
        # ... existing fields ...
        tier=meta.get("tier", "long"),    # NEW — default "long" for old data
        # ...
    )
```

### 9.2 List with Tier Filter

```python
def list(self, project=None, type=None, tier=None, limit=None):
    params: Dict[str, Any] = {}
    if project is not None:
        params["project"] = project
    if limit is not None:
        params["limit"] = limit

    resp = self._request("GET", "/v1/lessons", params=params)
    data = resp.json()
    memories = [self._lesson_to_memory(l) for l in data.get("lessons", [])]

    # Client-side post-filter (type stored in meta, tier stored in meta)
    if type is not None:
        memories = [m for m in memories if m.type == type]
    if tier is not None:                   # NEW
        memories = [m for m in memories if m.tier == tier]

    return memories
```

**Note:** Tier filtering is client-side in HttpStore because the remote API stores tier in `meta`. If the server API (Section 9.3) adds native `tier` support, it can be passed as a query param instead.

### 9.3 Search with Tier

```python
def search(self, embedding, *, tier=None, ...):
    payload = {
        "embedding": embedding,
        "limit": limit,
        # ...
    }
    if tier is not None:                   # NEW
        payload["tier"] = tier

    resp = self._request("POST", "/v1/lessons/search", json=payload)
    # ... parse results ...
```

### 9.4 Server API Endpoints

**POST /api/v1/memories** — Accept `tier` in request body. Validate against `VALID_TIERS`.

**GET /api/v1/memories** — Accept `tier` query parameter. Filter in SQL WHERE clause.

**POST /api/v1/memories/search** — Accept `tier` in search payload:
- If provided, add `WHERE tier = ?` to candidate query.
- Apply `TIER_RECALL_WEIGHT` in scoring (server-side scoring mirrors client-side formula).

**GET /api/v1/stats** — Add `by_tier` to response body.

---

## 10. CLI Updates

### 10.1 `remember` Subcommand

```python
p.add_argument(
    "--tier",
    choices=["working", "short", "long"],
    default="long",
    help="Memory tier: working (1h), short (7d), long (permanent)",
)
```

Handler passes `tier=args.tier` to `lore.remember()`.

### 10.2 `memories` (List) Subcommand

```python
p.add_argument(
    "--tier",
    choices=["working", "short", "long"],
    default=None,
    help="Filter by memory tier",
)
```

Handler passes `tier=args.tier` to `lore.list_memories()`.

**Table Output** — Add Tier column:

```
ID                          Tier      Type        Created              Content
--------------------------  --------  ----------  -------------------  --------------------------------------------------
01HXYZ...                   long      lesson      2024-01-15T10:30:00  Always use exponential backoff for retries...
01HXYZ...                   working   general     2024-01-15T11:00:00  Current PR is #42...
```

### 10.3 `recall` Subcommand

```python
p.add_argument(
    "--tier",
    choices=["working", "short", "long"],
    default=None,
    help="Filter by memory tier",
)
```

Handler passes `tier=args.tier` to `lore.recall()`.

**Output** — Add tier to the result line:

```
[0.85] 01HXYZ... (lesson, long)
  Always use exponential backoff for retries
  Tags: api, resilience
```

---

## 11. MCP Tool Updates

### 11.1 `remember` Tool

Add `tier` parameter:

```python
@mcp.tool(
    description=(
        "Save a memory — any knowledge worth preserving. "
        # ... existing description ...
        "Optionally set tier: 'working' (auto-expires in 1h, for scratch context), "
        "'short' (auto-expires in 7d, for session learnings), "
        "or 'long' (default, no expiry, for lasting knowledge)."
    ),
)
def remember(
    content: str,
    type: str = "general",
    tier: str = "long",               # NEW
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    project: Optional[str] = None,
    ttl: Optional[int] = None,
) -> str:
    lore = _get_lore()
    memory_id = lore.remember(
        content=content,
        type=type,
        tier=tier,                    # NEW
        tags=tags,
        metadata=metadata,
        source=source,
        project=project,
        ttl=ttl,
    )
    return f"Memory saved (ID: {memory_id}, tier: {tier})"
```

### 11.2 `recall` Tool

Add `tier` parameter:

```python
def recall(
    query: str,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,       # NEW
    limit: int = 5,
    repo_path: Optional[str] = None,
) -> str:
    lore = _get_lore()
    results = lore.recall(
        query=query,
        tags=tags,
        type=type,
        tier=tier,                    # NEW
        limit=limit,
        # ...
    )
```

**Output format** — Add tier to each result:

```
Memory 1  (score: 0.85, id: abc123, type: lesson, tier: long)
```

### 11.3 `list_memories` Tool

Add `tier` parameter:

```python
def list_memories(
    type: Optional[str] = None,
    tier: Optional[str] = None,       # NEW
    project: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    lore = _get_lore()
    memories = lore.list_memories(
        type=type,
        tier=tier,                    # NEW
        project=project,
        limit=limit,
    )
```

### 11.4 `stats` Tool

Add tier breakdown to output:

```python
def stats(project=None):
    lore = _get_lore()
    s = lore.stats(project=project)
    lines = [
        f"Total memories: {s.total}",
        f"By type: {s.by_type}",
        f"By tier: {s.by_tier}",       # NEW
        # ...
    ]
```

---

## 12. Lore Facade Changes Summary

### 12.1 `remember()` — Lines ~156-231

- Add `tier: str = "long"` parameter.
- Validate: `if tier not in VALID_TIERS: raise ValueError(...)`.
- Compute effective TTL: `effective_ttl = ttl if ttl is not None else TIER_DEFAULT_TTL[tier]`.
- Set `memory.tier = tier`.
- Use `effective_ttl` for `expires_at` calculation and `memory.ttl`.

### 12.2 `recall()` — Lines ~233-288

- Add `tier: Optional[str] = None` parameter.
- Pass `tier` to `_recall_local()` and remote `search()`.

### 12.3 `_recall_local()` — Lines ~290-383

- Add `tier: Optional[str] = None` parameter.
- Pass `tier` to `self._store.list(project=self.project, type=type, tier=tier)`.
- Apply tier weight in scoring: `tier_weight = self._tier_weights.get(memory.tier, 1.0)`.

### 12.4 `list_memories()` — Lines ~393-410

- Add `tier: Optional[str] = None` parameter.
- Pass `tier` to `self._store.list(project=project, type=type, tier=tier)`.

### 12.5 `stats()` — Lines ~412-432

- Compute `by_tier` counts alongside `by_type`:

```python
by_tier: Dict[str, int] = {}
for m in all_memories:
    by_type[m.type] = by_type.get(m.type, 0) + 1
    by_tier[m.tier] = by_tier.get(m.tier, 0) + 1
```

---

## 13. Backward Compatibility

### 13.1 Guarantees

| Aspect | Guarantee |
|--------|-----------|
| Existing memories | All get `tier = 'long'` via DEFAULT clause — no behavior change |
| `remember("x")` (no tier) | Defaults to `tier="long"`, `ttl=None` — identical to v0.5.1 |
| `remember("x", ttl=300)` | Defaults to `tier="long"`, `ttl=300` — identical to v0.5.1 |
| Store ABC `list(project, type, limit)` | Still works — `tier=None` means no filter |
| Store ABC `count(project, type)` | Still works — `tier=None` means no filter |
| MCP tools without `tier` param | All default to existing behavior |
| CLI without `--tier` flag | All default to existing behavior |
| Recall scoring (no tier) | `TIER_RECALL_WEIGHT["long"] = 1.2` — all existing memories get same boost, so relative ordering unchanged |
| HttpStore with old server | `tier` stored in meta dict — old servers ignore unknown meta keys |

### 13.2 Why 1.2x Weight for Long-Tier Doesn't Break Existing Recall

All existing memories are `long` tier. Since tier_weight is a uniform multiplier across all candidates, it scales all scores equally — the **relative ordering** is preserved. Tier weighting only differentiates when memories of different tiers are compared.

---

## 14. Implementation Sequence

Ordered by dependency:

```
1. types.py          — VALID_TIERS, TIER_DEFAULT_TTL, TIER_RECALL_WEIGHT constants
                       Memory.tier field, MemoryStats.by_tier field

2. store/base.py     — Add tier param to list() and count() signatures

3. store/sqlite.py   — _maybe_add_tier_column(), update _SCHEMA,
                       update save/update/list/count/_row_to_memory

4. store/memory.py   — Add tier filter to list() and count()

5. store/http.py     — Tier in _memory_to_lesson/_lesson_to_memory,
                       tier filter in list(), tier in search()

6. lore.py           — remember() tier param + default TTL logic,
                       recall() tier filter + weight,
                       _recall_local() tier weight in scoring,
                       list_memories() tier filter,
                       stats() by_tier counts

7. mcp/server.py     — tier param on remember, recall, list_memories;
                       tier in stats output; tier in recall display

8. cli.py            — --tier flag on remember, memories, recall;
                       tier in table/result output

9. Tests             — Unit tests for all above (see Section 15)
```

Steps 1-2 are pure interface changes. Steps 3-5 can be parallelized. Step 6 depends on 1-5. Steps 7-8 depend on 6. Step 9 can be developed alongside each step.

---

## 15. Test Plan

### 15.1 Unit Tests — types.py

- `test_memory_default_tier_is_long` — Memory() has tier="long"
- `test_valid_tiers_contains_all` — VALID_TIERS has all three
- `test_tier_default_ttl_values` — verify dict values
- `test_tier_recall_weight_values` — verify dict values
- `test_memory_stats_has_by_tier` — MemoryStats() has empty by_tier dict

### 15.2 Unit Tests — Store layer

- `test_sqlite_migration_adds_tier_column` — open old DB, verify column exists
- `test_sqlite_save_and_get_tier` — save with tier, get back correct tier
- `test_sqlite_list_filter_by_tier` — save mixed tiers, list with filter
- `test_sqlite_count_filter_by_tier` — count with tier filter
- `test_sqlite_row_to_memory_missing_tier` — defaults to "long"
- `test_memory_store_list_filter_by_tier` — in-memory tier filtering
- `test_memory_store_count_filter_by_tier` — in-memory tier counting

### 15.3 Unit Tests — Lore facade

- `test_remember_default_tier` — no tier → long, no TTL
- `test_remember_working_tier_default_ttl` — tier=working → ttl=3600
- `test_remember_short_tier_default_ttl` — tier=short → ttl=604800
- `test_remember_explicit_ttl_overrides_tier` — tier=working, ttl=7200 → ttl=7200
- `test_remember_invalid_tier_raises` — tier="invalid" → ValueError
- `test_recall_tier_weight_affects_scoring` — long-tier memory scores higher than working-tier at same cosine similarity
- `test_recall_tier_filter` — only returns memories of specified tier
- `test_list_memories_tier_filter` — filters by tier
- `test_stats_includes_by_tier` — by_tier dict populated

### 15.4 Integration Tests

- `test_working_memory_expires` — remember with working tier, advance clock past 1h, verify expired
- `test_long_memory_no_expiry` — remember with long tier, no expires_at set
- `test_backward_compat_remember_no_tier` — identical behavior to v0.5.1
- `test_backward_compat_existing_db` — open pre-F4 database, verify all memories have tier=long

### 15.5 MCP/CLI Tests

- `test_mcp_remember_with_tier` — MCP tool passes tier through
- `test_mcp_recall_with_tier_filter` — MCP tool filters by tier
- `test_mcp_list_with_tier_filter` — MCP tool filters by tier
- `test_cli_remember_tier_flag` — --tier flag parsed correctly
- `test_cli_memories_tier_filter` — --tier filter works
- `test_cli_recall_tier_filter` — --tier filter works

---

## 16. Risk Mitigations

| Risk | Mitigation |
|------|------------|
| Store ABC signature change breaks custom stores | `tier` param defaults to `None` — existing implementations work without it |
| Tier column missing on old SQLite DBs | `_maybe_add_tier_column()` auto-migrates idempotently |
| HttpStore with old server | Tier stored in meta dict; old servers ignore unknown meta keys |
| Tier weight distorts recall for irrelevant long-term memories | Weight boost is small (1.0-1.2x); cosine similarity still dominates |
| `_row_to_memory` fails on DBs without tier column | Defensive `row["tier"] if "tier" in row.keys() else "long"` |

---

## 17. Diagrams

### 17.1 Tier-TTL Resolution Flow

```
remember(content, tier=T, ttl=U)
         │
         ├─ tier not in VALID_TIERS? → raise ValueError
         │
         ├─ U is not None?
         │    yes → effective_ttl = U        (explicit wins)
         │    no  → effective_ttl = TIER_DEFAULT_TTL[T]
         │
         ├─ effective_ttl is not None?
         │    yes → expires_at = now + effective_ttl
         │    no  → expires_at = None        (long-tier, no expiry)
         │
         └─ save Memory(tier=T, ttl=effective_ttl, expires_at=...)
```

### 17.2 Recall Scoring with Tier Weight

```
For each candidate memory:
    cosine_score = cosine_similarity(query_vec, memory_vec)
    freshness    = 0.5 ^ (age_days / half_life)
    vote_factor  = 1.0 + (upvotes - downvotes) * 0.1
    tier_weight  = TIER_RECALL_WEIGHT[memory.tier]     ← NEW

    similarity   = cosine_score * confidence * vote_factor * tier_weight
    final_score  = sim_weight * similarity + fresh_weight * freshness
```

### 17.3 Data Flow Through Layers

```
CLI/MCP  →  Lore Facade  →  Store ABC  →  SQLite/HTTP/Memory
  │              │                │
  │  tier param  │  validate      │  persist tier column
  │  --tier flag │  default TTL   │  filter WHERE tier=?
  │              │  tier weight   │  read tier from row
  │              │  in scoring    │
```
