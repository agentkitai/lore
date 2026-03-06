# F4 Memory Tiers - User Stories

**Feature:** F4 — Multi-Level Memory Tiers
**Version:** v0.6.0 ("Open Brain")
**Sprint Planning:** SM breakdown into INVEST-compliant stories

---

## Story Map

```
S1 Schema Migration ──┐
                       ├── S3 SQLite Store ──┐
S2 Dataclass/Constants─┤                     │
                       ├── S4 MemoryStore ───┤
                       ├── S5 HttpStore ─────┼── S6 Facade (remember/recall/list/stats) ──┬── S7 CLI
                       │                     │                                             ├── S8 MCP
                       └─────────────────────┘                                             └── S9 Tests
```

---

## S1: Schema Migration — Add Tier Column to SQLite

**Size:** S
**Dependencies:** None
**Priority:** P0 (foundation)

### Description

Add a `tier TEXT DEFAULT 'long'` column to the SQLite `memories` table via an idempotent auto-migration method, following the existing `_maybe_add_context_column()` pattern. Add standalone and composite indexes.

### Acceptance Criteria

**AC1 — New database gets tier column**
- **Given** a fresh SQLite database with no existing `memories` table
- **When** `SqliteStore.__init__()` runs
- **Then** the `memories` table includes a `tier TEXT DEFAULT 'long'` column in the CREATE TABLE DDL

**AC2 — Existing database auto-migrates**
- **Given** an existing SQLite database created before F4 (no `tier` column)
- **When** `SqliteStore.__init__()` runs
- **Then** `_maybe_add_tier_column()` adds the `tier` column with `DEFAULT 'long'`
- **And** all existing rows have `tier = 'long'`

**AC3 — Migration is idempotent**
- **Given** a database that already has the `tier` column
- **When** `_maybe_add_tier_column()` runs again
- **Then** no error occurs and no duplicate columns are created

**AC4 — Indexes created**
- **Given** the migration has run
- **Then** index `idx_memories_tier` exists on `memories(tier)`
- **And** index `idx_memories_project_tier` exists on `memories(project, tier)`

### Implementation Notes

- Add `_maybe_add_tier_column()` method to `store/sqlite.py`
- Call it in `__init__()` after `_maybe_add_context_column()`
- Update `_SCHEMA` constant to include `tier TEXT DEFAULT 'long'` and both indexes

---

## S2: Memory Dataclass and Tier Constants

**Size:** S
**Dependencies:** None
**Priority:** P0 (foundation)

### Description

Add the `tier` field to the `Memory` dataclass with default `"long"`, and add tier-related constants (`VALID_TIERS`, `TIER_DEFAULT_TTL`, `TIER_RECALL_WEIGHT`) to `types.py`. Add `by_tier` to `MemoryStats`.

### Acceptance Criteria

**AC1 — Memory dataclass has tier field**
- **Given** a new `Memory` instance created with no `tier` argument
- **When** the instance is inspected
- **Then** `memory.tier == "long"`

**AC2 — Memory accepts explicit tier**
- **Given** `Memory(id="x", content="y", tier="working")`
- **When** the instance is inspected
- **Then** `memory.tier == "working"`

**AC3 — VALID_TIERS constant**
- **Given** the `VALID_TIERS` constant is imported from `types.py`
- **Then** it equals `("working", "short", "long")`

**AC4 — TIER_DEFAULT_TTL constant**
- **Given** the `TIER_DEFAULT_TTL` constant
- **Then** `TIER_DEFAULT_TTL["working"] == 3600`
- **And** `TIER_DEFAULT_TTL["short"] == 604800`
- **And** `TIER_DEFAULT_TTL["long"] is None`

**AC5 — TIER_RECALL_WEIGHT constant**
- **Given** the `TIER_RECALL_WEIGHT` constant
- **Then** `TIER_RECALL_WEIGHT["working"] == 1.0`
- **And** `TIER_RECALL_WEIGHT["short"] == 1.1`
- **And** `TIER_RECALL_WEIGHT["long"] == 1.2`

**AC6 — MemoryStats has by_tier**
- **Given** a new `MemoryStats` instance
- **Then** `stats.by_tier` is an empty dict by default

### Implementation Notes

- Add `tier: str = "long"` after `type` in Memory dataclass
- Add constants as module-level dicts/tuples
- Add `by_tier: Dict[str, int] = field(default_factory=dict)` to MemoryStats

---

## S3: SQLite Store — Tier Persistence and Filtering

**Size:** M
**Dependencies:** S1, S2
**Priority:** P1

### Description

Update `SqliteStore` methods (`save`, `update`, `list`, `count`, `_row_to_memory`) to persist and query the `tier` field. Add tier filter support to `list()` and `count()`.

### Acceptance Criteria

**AC1 — Save persists tier**
- **Given** a Memory with `tier="working"`
- **When** `store.save(memory)` is called
- **Then** the `tier` column in SQLite contains `"working"`
- **And** `store.get(memory.id).tier == "working"`

**AC2 — Update persists tier**
- **Given** a saved Memory with `tier="working"`
- **When** `memory.tier = "long"` and `store.update(memory)` is called
- **Then** `store.get(memory.id).tier == "long"`

**AC3 — List filters by tier**
- **Given** 3 memories: one working, one short, one long
- **When** `store.list(tier="working")` is called
- **Then** only the working-tier memory is returned

**AC4 — List without tier returns all**
- **Given** 3 memories of different tiers
- **When** `store.list()` is called (no tier filter)
- **Then** all 3 memories are returned

**AC5 — Count filters by tier**
- **Given** 2 working and 1 long memory
- **When** `store.count(tier="working")` is called
- **Then** the result is `2`

**AC6 — _row_to_memory handles missing tier column**
- **Given** a row from a pre-migration database without `tier` key
- **When** `_row_to_memory()` processes the row
- **Then** the resulting Memory has `tier = "long"` (defensive default)

### Implementation Notes

- Update INSERT/UPDATE SQL to include `tier`
- Add `AND tier = ?` clause to list/count when tier is not None
- Update base.py `Store` ABC signatures for `list()` and `count()` to include `tier: Optional[str] = None`

---

## S4: MemoryStore — Tier Filtering

**Size:** S
**Dependencies:** S2
**Priority:** P1

### Description

Update the in-memory `MemoryStore` to support tier filtering in `list()` and `count()`.

### Acceptance Criteria

**AC1 — List filters by tier**
- **Given** 3 memories of different tiers saved in MemoryStore
- **When** `store.list(tier="short")` is called
- **Then** only the short-tier memory is returned

**AC2 — Count filters by tier**
- **Given** 2 working and 1 long memory
- **When** `store.count(tier="working")` is called
- **Then** result is `2`

**AC3 — No tier filter returns all**
- **Given** 3 memories of different tiers
- **When** `store.list()` is called
- **Then** all 3 are returned

### Implementation Notes

- Add tier filter to list comprehension in `list()` and `count()`
- No changes needed to `save()` or `update()` — they store the full Memory object

---

## S5: HttpStore — Tier Support

**Size:** S
**Dependencies:** S2
**Priority:** P1

### Description

Update `HttpStore` to include `tier` in memory serialization/deserialization and add client-side tier filtering to `list()`. Pass tier to `search()` payload.

### Acceptance Criteria

**AC1 — Tier persisted in payload**
- **Given** a Memory with `tier="working"`
- **When** `_memory_to_lesson()` serializes it
- **Then** the payload's `meta` dict contains `"tier": "working"`

**AC2 — Tier read from response**
- **Given** an API response with `meta.tier = "short"`
- **When** `_lesson_to_memory()` deserializes it
- **Then** the resulting Memory has `tier = "short"`

**AC3 — Missing tier defaults to long**
- **Given** an API response with no `tier` in meta (old server)
- **When** `_lesson_to_memory()` deserializes it
- **Then** the resulting Memory has `tier = "long"`

**AC4 — List filters by tier client-side**
- **Given** memories of mixed tiers from the server
- **When** `store.list(tier="long")` is called
- **Then** only long-tier memories are returned (filtered client-side)

**AC5 — Search passes tier to server**
- **Given** a search request with `tier="working"`
- **When** `store.search()` is called
- **Then** the POST payload includes `"tier": "working"`

### Implementation Notes

- Store tier in `meta` dict (alongside `type`)
- Client-side post-filter for `list()` (tier not a server query param)
- Pass tier in search payload for server-side filtering

---

## S6: Lore Facade — Tier in Remember, Recall, List, Stats

**Size:** L
**Dependencies:** S2, S3 (or S4 for testing)
**Priority:** P2

### Description

Update the `Lore` facade to accept `tier` parameter in `remember()`, `recall()`, `list_memories()`, and `stats()`. Implement tier validation, default TTL resolution, tier-based recall weight, and tier counting in stats.

### Acceptance Criteria

**AC1 — remember() accepts and validates tier**
- **Given** `lore.remember("x", tier="invalid")`
- **When** the call executes
- **Then** a `ValueError` is raised with a message including the valid tiers

**AC2 — remember() applies tier default TTL**
- **Given** `lore.remember("x", tier="working")` with no explicit `ttl`
- **When** the memory is saved
- **Then** `memory.ttl == 3600` and `memory.expires_at` is ~1 hour from now

**AC3 — remember() explicit TTL overrides tier default**
- **Given** `lore.remember("x", tier="working", ttl=7200)`
- **When** the memory is saved
- **Then** `memory.ttl == 7200` (not 3600)

**AC4 — remember() backward compat (no tier)**
- **Given** `lore.remember("x")` with no tier or ttl
- **When** the memory is saved
- **Then** `memory.tier == "long"` and `memory.ttl is None` and `memory.expires_at is None`

**AC5 — recall() applies tier weight in scoring**
- **Given** two memories with identical content and cosine similarity
- **And** one is `tier="long"` (weight 1.2) and one is `tier="working"` (weight 1.0)
- **When** `lore.recall("query")` is called
- **Then** the long-tier memory has a higher score than the working-tier memory

**AC6 — recall() filters by tier**
- **Given** memories of different tiers
- **When** `lore.recall("query", tier="working")` is called
- **Then** only working-tier memories appear in results

**AC7 — list_memories() filters by tier**
- **Given** memories of different tiers
- **When** `lore.list_memories(tier="short")` is called
- **Then** only short-tier memories are returned

**AC8 — stats() includes by_tier**
- **Given** 2 long and 1 working memory
- **When** `lore.stats()` is called
- **Then** `stats.by_tier == {"long": 2, "working": 1}`

**AC9 — Configurable tier weights**
- **Given** `Lore(tier_recall_weights={"working": 1.0, "short": 1.0, "long": 1.5})`
- **When** recall scoring runs
- **Then** the custom weights are used instead of defaults

### Implementation Notes

- Validate tier in `remember()` before any other logic
- `effective_ttl = ttl if ttl is not None else TIER_DEFAULT_TTL[tier]`
- Tier weight: `tier_weight = self._tier_weights.get(memory.tier, 1.0)` applied to similarity component only
- Pass tier to `_recall_local()` which passes it to `store.list()`
- Add `tier_recall_weights` param to `Lore.__init__()`

---

## S7: CLI Updates — --tier Flag

**Size:** S
**Dependencies:** S6
**Priority:** P3

### Description

Add `--tier` argument to the `remember`, `memories` (list), and `recall` CLI subcommands. Update table/result output to display tier.

### Acceptance Criteria

**AC1 — remember --tier flag**
- **Given** the command `lore remember "Current PR is #42" --tier working`
- **When** executed
- **Then** the memory is saved with `tier="working"` and TTL=3600

**AC2 — remember default tier**
- **Given** the command `lore remember "Always use backoff"` (no --tier)
- **When** executed
- **Then** the memory is saved with `tier="long"`

**AC3 — memories --tier filter**
- **Given** the command `lore memories --tier working`
- **When** executed
- **Then** only working-tier memories are displayed

**AC4 — memories output shows tier column**
- **Given** the command `lore memories`
- **When** output is displayed
- **Then** a "Tier" column is present in the table output

**AC5 — recall --tier filter**
- **Given** the command `lore recall "rate limiting" --tier long`
- **When** executed
- **Then** only long-tier memories appear in results

**AC6 — recall output shows tier**
- **Given** recall results
- **When** output is displayed
- **Then** each result line includes the tier (e.g., `(lesson, long)`)

### Implementation Notes

- `--tier` choices: `["working", "short", "long"]`
- Default for `remember`: `"long"`; default for `memories`/`recall`: `None` (no filter)
- Pass `tier=args.tier` to respective facade methods

---

## S8: MCP Tool Updates — Tier Parameter

**Size:** S
**Dependencies:** S6
**Priority:** P3

### Description

Add `tier` parameter to the MCP `remember`, `recall`, `list_memories` tools. Update `stats` output to include tier breakdown. Update recall output format to show tier.

### Acceptance Criteria

**AC1 — MCP remember accepts tier**
- **Given** an MCP call to `remember(content="x", tier="working")`
- **When** executed
- **Then** the memory is saved with `tier="working"`
- **And** the response includes `tier: working`

**AC2 — MCP remember default tier**
- **Given** an MCP call to `remember(content="x")` (no tier)
- **When** executed
- **Then** `tier="long"` is used

**AC3 — MCP recall filters by tier**
- **Given** an MCP call to `recall(query="x", tier="working")`
- **When** executed
- **Then** only working-tier memories are returned

**AC4 — MCP recall output shows tier**
- **Given** MCP recall results
- **Then** each result includes tier in the display format: `(score: X, id: Y, type: Z, tier: T)`

**AC5 — MCP list_memories filters by tier**
- **Given** an MCP call to `list_memories(tier="short")`
- **When** executed
- **Then** only short-tier memories are returned

**AC6 — MCP stats shows tier breakdown**
- **Given** an MCP call to `stats()`
- **When** executed
- **Then** the output includes a `By tier:` line with counts

### Implementation Notes

- Add `tier: str = "long"` to remember tool, `tier: Optional[str] = None` to recall/list
- Update tool descriptions to explain tier semantics
- Pass tier through to facade methods

---

## S9: Comprehensive Test Coverage

**Size:** L
**Dependencies:** S1-S8 (can be developed incrementally alongside each story)
**Priority:** P4 (but test-first development encouraged per story)

### Description

Add comprehensive tests covering schema migration, tier filtering, recall weighting, TTL interaction, backward compatibility, and CLI/MCP integration.

### Acceptance Criteria

**AC1 — Schema migration tests**
- **Given** a pre-F4 SQLite database
- **When** SqliteStore initializes
- **Then** tier column exists with DEFAULT 'long'
- **And** both indexes are present

**AC2 — Tier filtering tests (per store)**
- **Given** memories of mixed tiers
- **When** `list(tier=X)` and `count(tier=X)` are called
- **Then** only matching memories are returned/counted
- **And** `list()` with no tier returns all

**AC3 — Recall weighting tests**
- **Given** two memories with identical cosine similarity but different tiers
- **When** `recall()` is called
- **Then** long-tier memory scores higher than working-tier

**AC4 — TTL interaction tests**
- **Given** `remember("x", tier="working")` — no explicit TTL
- **Then** `memory.ttl == 3600`
- **Given** `remember("x", tier="working", ttl=7200)`
- **Then** `memory.ttl == 7200` (explicit wins)
- **Given** `remember("x")` — no tier, no TTL
- **Then** `memory.tier == "long"` and `memory.ttl is None`

**AC5 — Backward compatibility tests**
- **Given** existing tests from v0.5.1
- **When** all tests run
- **Then** all pass without modification
- **And** `remember("x")` produces identical behavior to pre-F4

**AC6 — Validation tests**
- **Given** `remember("x", tier="invalid")`
- **Then** `ValueError` is raised

**AC7 — MCP/CLI integration tests**
- **Given** MCP `remember` called with `tier="working"`
- **Then** memory is saved with correct tier and TTL
- **Given** CLI `--tier working` on remember
- **Then** memory is saved with correct tier and TTL

### Implementation Notes

- Target: >= 30 new tests as specified in PRD success metrics
- Test categories: types (5), store layer (7), facade (9), integration (4), MCP/CLI (6)
- Use `freezegun` or manual clock control for TTL expiry tests

---

## Priority & Sprint Order

| Priority | Stories | Rationale |
|----------|---------|-----------|
| P0 | S1, S2 | Foundation — schema + data model must land first |
| P1 | S3, S4, S5 | Store implementations — can be parallelized |
| P2 | S6 | Facade — depends on stores being tier-aware |
| P3 | S7, S8 | Interface layers — depend on facade |
| P4 | S9 | Tests formalized last, but developed alongside each story |

### Dependency Graph

```
S1 (Schema) ──────────→ S3 (SQLite Store) ──→ S6 (Facade) ──→ S7 (CLI)
                                               ↑               → S8 (MCP)
S2 (Dataclass) ──┬────→ S3 (SQLite Store) ────┘               → S9 (Tests)
                  ├────→ S4 (MemoryStore) ─────→ S6
                  └────→ S5 (HttpStore) ───────→ S6
```

### Size Summary

| Size | Stories | Count |
|------|---------|-------|
| S | S1, S2, S4, S5, S7, S8 | 6 |
| M | S3 | 1 |
| L | S6, S9 | 2 |
| **Total** | | **9 stories** |
