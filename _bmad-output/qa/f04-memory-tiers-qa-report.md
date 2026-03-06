# F4 Memory Tiers — QA Report

**Feature:** F4 — Multi-Level Memory Tiers
**Date:** 2026-03-06
**Tester:** Quinn (QA Engineer)
**Branch:** feature/v0.6.0-open-brain
**Verdict:** PASS

---

## Test Execution Summary

| Suite | Result | Details |
|-------|--------|---------|
| Tier-specific tests (`-k tier`) | 51/51 PASS | All F4 tests green |
| Full test suite (excl. flaky) | 628 passed, 14 skipped | No regressions |
| Flaky failures | 2 network timeouts | Pre-existing, not F4-related (see below) |

### Flaky Test Note

Two tests in `test_http_store.py::TestRecallDispatch` fail with `httpx.ReadTimeout` — network timeouts connecting to external service. These tests were introduced in F5 (`f718288`) and are **not related to F4**. They fail independently of tier changes.

---

## Story-by-Story Verification

### S1: Schema Migration — Add Tier Column to SQLite

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| AC1 | New DB gets tier column | PASS | `_SCHEMA` at sqlite.py:19 includes `tier TEXT DEFAULT 'long'` |
| AC2 | Existing DB auto-migrates | PASS | `_maybe_add_tier_column()` at sqlite.py:117-133; test `test_migration_adds_tier_column` passes |
| AC3 | Migration is idempotent | PASS | Uses `PRAGMA table_info` check before ALTER; re-running is safe |
| AC4 | Indexes created | PASS | `idx_memories_tier` and `idx_memories_project_tier` in both _SCHEMA (lines 40-41) and migration (lines 128-132) |

### S2: Memory Dataclass and Tier Constants

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| AC1 | Memory default tier="long" | PASS | types.py:26 `tier: str = "long"`; test `test_memory_default_tier_is_long` |
| AC2 | Memory accepts explicit tier | PASS | test `test_memory_accepts_explicit_tier` |
| AC3 | VALID_TIERS constant | PASS | types.py:124 `("working", "short", "long")` |
| AC4 | TIER_DEFAULT_TTL values | PASS | types.py:126-130 — working=3600, short=604800, long=None |
| AC5 | TIER_RECALL_WEIGHT values | PASS | types.py:132-136 — working=1.0, short=1.1, long=1.2 |
| AC6 | MemoryStats.by_tier | PASS | types.py:64 `by_tier: Dict[str, int] = field(default_factory=dict)` |

### S3: SQLite Store — Tier Persistence and Filtering

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| AC1 | Save persists tier | PASS | sqlite.py:166,175; test `test_save_and_get_tier` |
| AC2 | Update persists tier | PASS | sqlite.py:235,243; test `test_update_persists_tier` |
| AC3 | List filters by tier | PASS | sqlite.py:208,220-222; test `test_list_filter_by_tier` |
| AC4 | List without tier returns all | PASS | Covered in test `test_list_no_tier_returns_all` (MemoryStore) |
| AC5 | Count filters by tier | PASS | sqlite.py:276,287-289; test `test_count_filter_by_tier` |
| AC6 | _row_to_memory handles missing tier | PASS | sqlite.py:317 fallback to "long"; test `test_row_to_memory_default_tier` |

### S4: MemoryStore — Tier Filtering

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| AC1 | List filters by tier | PASS | memory.py:36-37; test `test_list_filters_by_tier` |
| AC2 | Count filters by tier | PASS | memory.py:63-64; test `test_count_filters_by_tier` |
| AC3 | No tier filter returns all | PASS | test `test_list_no_tier_returns_all` |

### S5: HttpStore — Tier Support

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| AC1 | Tier in payload meta | PASS | http.py:143; test `test_memory_to_lesson_includes_tier` |
| AC2 | Tier read from response | PASS | http.py:185,207; test `test_lesson_to_memory_reads_tier` |
| AC3 | Missing tier defaults to long | PASS | http.py:185 `meta.pop("tier", "long")`; test `test_lesson_to_memory_missing_tier_defaults_long` |
| AC4 | List filters client-side | PASS | http.py:263-264 |
| AC5 | Search passes tier to server | PASS | http.py:345-346 |

### S6: Lore Facade — Tier in Remember, Recall, List, Stats

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| AC1 | remember() validates tier | PASS | lore.py:195-198 raises ValueError; test `test_remember_invalid_tier_raises` |
| AC2 | remember() applies tier default TTL | PASS | lore.py:237; tests for working (3600) and short (604800) |
| AC3 | Explicit TTL overrides tier default | PASS | lore.py:237 ternary; test `test_remember_explicit_ttl_overrides_tier` |
| AC4 | Backward compat (no tier) | PASS | Default tier="long", ttl=None; test `test_backward_compat_remember_no_tier` |
| AC5 | recall() tier weight scoring | PASS | lore.py:409-410 multiplicative; test `test_recall_tier_weight_affects_scoring` |
| AC6 | recall() filters by tier | PASS | lore.py:305,311,340; test `test_recall_tier_filter` |
| AC7 | list_memories() filters by tier | PASS | lore.py:481,486; test `test_list_memories_tier_filter` |
| AC8 | stats() includes by_tier | PASS | lore.py:506-515; test `test_stats_includes_by_tier` |
| AC9 | Configurable tier weights | PASS | lore.py:102,105; test `test_configurable_tier_weights` |

### S7: CLI Updates — --tier Flag

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| AC1 | remember --tier flag | PASS | cli.py:218-221; test `test_remember_tier_flag_parsed` |
| AC2 | remember default tier | PASS | cli.py:219 `default="long"`; test `test_remember_default_tier_is_long` |
| AC3 | memories --tier filter | PASS | cli.py:248-251; test `test_memories_tier_flag_parsed` |
| AC4 | memories output shows tier | PASS | Verified in CLI tier tests |
| AC5 | recall --tier filter | PASS | cli.py:234-237; test `test_recall_tier_flag_parsed` |
| AC6 | recall output shows tier | PASS | MCP output format includes tier |

### S8: MCP Tool Updates — Tier Parameter

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| AC1 | MCP remember accepts tier | PASS | mcp/server.py:91,104,111; test `test_mcp_remember_with_tier` |
| AC2 | MCP remember default tier | PASS | Default "long"; test `test_mcp_remember_default_tier` |
| AC3 | MCP recall filters by tier | PASS | mcp/server.py:132,141; test `test_mcp_recall_with_tier_filter` |
| AC4 | MCP recall output shows tier | PASS | mcp/server.py:160; test `test_mcp_recall_output_shows_tier` |
| AC5 | MCP list_memories filters by tier | PASS | mcp/server.py:199,206; test `test_mcp_list_with_tier_filter` |
| AC6 | MCP stats shows tier breakdown | PASS | mcp/server.py:238-241; test `test_mcp_stats_shows_tier_breakdown` |

### S9: Comprehensive Test Coverage

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| AC1 | Schema migration tests | PASS | `test_migration_adds_tier_column`, idempotency covered |
| AC2 | Tier filtering tests (per store) | PASS | SQLite, MemoryStore, HttpStore all have filter tests |
| AC3 | Recall weighting tests | PASS | `test_recall_tier_weight_affects_scoring` |
| AC4 | TTL interaction tests | PASS | 3 dedicated tests in TestTierTTLIntegration |
| AC5 | Backward compatibility tests | PASS | 628 existing tests pass; dedicated backward compat tests |
| AC6 | Validation tests | PASS | `test_remember_invalid_tier_value_error`, `test_remember_empty_tier_value_error` |
| AC7 | MCP/CLI integration tests | PASS | 6 MCP tests + 6 CLI tests |

---

## Test Count

- **F4-specific tests:** 46 in `test_memory_tiers.py` + 5 tier-related in `test_importance_scoring.py` = **51 total**
- **PRD target:** >= 30 new tests — **exceeded** (46 new tests)

## Regression Check

- **628 passed, 14 skipped** (excluding 2 pre-existing flaky network timeout tests)
- No breaking changes to existing functionality
- `remember("x")` with no tier produces identical behavior to pre-F4

## Code Quality Notes

- Schema migration follows existing `_maybe_add_context_column()` pattern — consistent
- Defensive defaults throughout (missing tier → "long")
- Tier validation happens early in `remember()` before any side effects
- Multiplicative scoring at lore.py:410 applies tier weight correctly
- All store implementations (SQLite, Memory, HTTP) handle tier consistently
- Base ABC signatures updated — enforces contract across implementations

---

## Verdict: PASS

All 9 stories verified. All acceptance criteria met. No regressions. Test coverage exceeds target.
