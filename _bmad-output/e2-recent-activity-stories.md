# E2: Recent Activity Summary — Sprint Stories

**Epic:** E2 — Session Context
**Version:** v0.10.0
**Author:** Bob (Scrum Master)
**Date:** 2026-03-14
**Status:** Draft

---

## Sprint Overview

**Total Stories:** 11
**Estimated Effort:** ~36-52 hours (M average)
**Parallelization:** 3 batches with concurrent work opportunities

### Dependency Graph

```
S1 (Types) ──┬──► S2 (Store ABC + SQLite)
              │         │
              │         ├──► S3 (HttpStore)
              │         │
              │         └──► S5 (SDK Method) ◄── S4 (Formatting)
              │                   │
              │                   ├──► S6 (MCP Tool)
              │                   ├──► S7 (CLI Command)
              │                   └──► S8 (REST Endpoint)
              │                              │
              │                              └──► S9 (OpenClaw Hook)
              │
              └──► S4 (Formatting)
                         │
                         └──► S10 (LLM Summary)

S6 ──► S11 (Setup Commands)
```

### Batch Plan

| Batch | Stories | Can Parallelize | Notes |
|-------|---------|-----------------|-------|
| **Batch 1** | S1, S4 | Yes (independent) | Foundation: types + formatting module |
| **Batch 2** | S2, S3, S5 | S2→S3 sequential; S5 after S2+S4 | Store layer + SDK |
| **Batch 3** | S6, S7, S8 | Yes (all depend on S5 only) | All surface layers |
| **Batch 4** | S9, S10, S11 | Yes (independent after their deps) | Integration + enhancements |

---

## Stories

---

### S1: Data Types — ProjectGroup and RecentActivityResult

**Size:** S (1-2h)
**Dependencies:** None
**Batch:** 1

**Description:**
Add `ProjectGroup` and `RecentActivityResult` dataclasses to `src/lore/types.py`. These are the core data structures that all layers depend on.

**Acceptance Criteria:**
- [ ] `ProjectGroup` dataclass with fields: `project: str`, `memories: List[Memory]`, `count: int`, `summary: Optional[str] = None`
- [ ] `RecentActivityResult` dataclass with fields: `groups: List[ProjectGroup]`, `total_count: int`, `hours: int`, `has_llm_summary: bool = False`, `query_time_ms: float = 0.0`, `generated_at: str = ""`
- [ ] Both dataclasses importable from `lore.types`
- [ ] No changes to existing dataclasses

**Test Scenarios:**
1. `test_project_group_creation` — Create a ProjectGroup with memories, verify all fields
2. `test_project_group_defaults` — summary defaults to None
3. `test_recent_activity_result_creation` — Create with groups, verify total_count and hours
4. `test_recent_activity_result_defaults` — has_llm_summary=False, query_time_ms=0.0, generated_at=""

**Files:**
- Modify: `src/lore/types.py`
- Modify: `tests/test_types.py` (or add new tests)

---

### S2: Store Layer — `since` Parameter on Store ABC + SQLite

**Size:** M (2-4h)
**Dependencies:** S1
**Batch:** 2

**Description:**
Add `since: Optional[str] = None` parameter to the `Store.list()` ABC method and implement it in `SqliteStore`. The `since` parameter accepts an ISO 8601 datetime string and filters to memories created at or after that timestamp.

**Acceptance Criteria:**
- [ ] `Store.list()` ABC signature includes `since: Optional[str] = None`
- [ ] Default `None` preserves backward compatibility — all existing callers unaffected
- [ ] `SqliteStore.list()` adds `WHERE created_at >= ?` when `since` is provided
- [ ] `MemoryStore` (in-memory test store) also supports `since` filtering
- [ ] Combined filters work: `since` + `project`, `since` + `limit`, `since` + `project` + `limit`
- [ ] Existing `list()` tests still pass (no regressions)

**Test Scenarios:**
1. `test_sqlite_list_since_filters_old` — Insert memories at different times, `since` excludes older ones
2. `test_sqlite_list_since_none_returns_all` — `since=None` returns all memories (backward compat)
3. `test_sqlite_list_since_with_project` — Combined `since` + `project` filter
4. `test_sqlite_list_since_with_limit` — Combined `since` + `limit` respects both
5. `test_sqlite_list_since_inclusive` — Memories exactly at `since` timestamp are included
6. `test_memory_store_list_since` — In-memory store filters by `since` correctly

**Files:**
- Modify: `src/lore/store/base.py`
- Modify: `src/lore/store/sqlite.py`
- Modify: `src/lore/store/memory.py`
- Add/modify: `tests/test_store_since.py`

---

### S3: Store Layer — HttpStore `since` Support

**Size:** S (1-2h)
**Dependencies:** S2
**Batch:** 2

**Description:**
Update `HttpStore.list()` to pass the `since` parameter as a query parameter to the remote server. Also update the server-side `/v1/lessons` endpoint to accept and apply the `since` filter.

**Acceptance Criteria:**
- [ ] `HttpStore.list()` passes `since` as query parameter when provided
- [ ] Server's `/v1/lessons` endpoint accepts `since` query param and adds `WHERE created_at >= $N`
- [ ] `since=None` sends no query param (backward compat)
- [ ] Works with combined filters (`since` + `project`)

**Test Scenarios:**
1. `test_http_store_passes_since_param` — Mock HTTP client, verify `since` sent as query param
2. `test_http_store_since_none_omits_param` — No `since` param sent when None
3. `test_server_lessons_since_filter` — Integration test: server filters by `since` correctly

**Files:**
- Modify: `src/lore/store/http.py`
- Modify: `src/lore/server/routes/lessons.py` (add `since` query param)
- Add/modify: tests

---

### S4: Formatting Module — `src/lore/recent.py`

**Size:** M (2-4h)
**Dependencies:** S1
**Batch:** 1 (parallel with S2)

**Description:**
Create `src/lore/recent.py` containing all grouping and formatting logic. This is a pure-logic module with no external dependencies beyond the data types, making it independently testable.

**Acceptance Criteria:**
- [ ] `group_memories_by_project(memories)` → groups memories by `project` field, `None` project → "default"
- [ ] Groups sorted by most recent memory in each group (newest first)
- [ ] Memories within groups sorted by `created_at` DESC
- [ ] `format_brief(result)` → one line per memory: `- [HH:MM] type: content[:100]...`, grouped by project
- [ ] `format_brief` with empty result returns `"No recent activity in the last {hours}h."`
- [ ] `format_brief` shows first 3 memories per group + `(N more)` for overflow (token budget)
- [ ] `format_brief` renders LLM `summary` when available instead of raw listings
- [ ] `format_detailed(result)` → full content with metadata (tier, importance, tags)
- [ ] `format_structured(result)` → returns dict with all fields for JSON serialization
- [ ] `format_cli(result)` → plain text, no markdown (no `##`, no `**`)
- [ ] `_format_time(iso_str)` → extracts `HH:MM`, returns `??:??` for invalid input

**Test Scenarios:**
1. `test_group_empty_list` — Empty memories → empty groups
2. `test_group_single_project` — All same project → one group
3. `test_group_multiple_projects` — Correct grouping across projects
4. `test_group_null_project` — `project=None` → "default" group
5. `test_group_sorted_by_newest` — Groups ordered by most recent memory
6. `test_format_brief_no_memories` — Returns "No recent activity" message
7. `test_format_brief_basic` — Correct format with truncation at 100 chars
8. `test_format_brief_with_summary` — LLM summary replaces raw listing
9. `test_format_brief_overflow` — >3 memories per group shows "(N more)"
10. `test_format_detailed_metadata` — Includes tier, importance, tags
11. `test_format_structured_json` — Valid dict with all expected keys
12. `test_format_cli_no_markdown` — No markdown syntax in output
13. `test_format_time_valid` — Extracts HH:MM from valid ISO timestamp
14. `test_format_time_invalid` — Returns "??:??" for malformed/short timestamps

**Files:**
- Create: `src/lore/recent.py`
- Create: `tests/test_recent.py`

---

### S5: SDK Method — `Lore.recent_activity()`

**Size:** L (4-8h)
**Dependencies:** S2, S4
**Batch:** 2

**Description:**
Add `recent_activity()` method to the `Lore` class in `lore.py`. This is the central method that all surface layers (MCP, REST, CLI) will call. Handles parameter clamping, time computation, store querying, expiry filtering, and grouping.

**Acceptance Criteria:**
- [ ] Method signature: `recent_activity(*, hours=24, project=None, format="brief", max_memories=50) -> RecentActivityResult`
- [ ] `hours` clamped to [1, 168] (not rejected)
- [ ] `max_memories` clamped to [1, 200]
- [ ] Computes `since` cutoff as `(now_utc - timedelta(hours=hours)).isoformat()`
- [ ] Calls `self._store.list(project=project, since=since, limit=max_memories)`
- [ ] Filters out expired memories (`expires_at < now`)
- [ ] Includes all tiers (working, short, long)
- [ ] Uses `group_memories_by_project()` from formatting module
- [ ] Falls back to `LORE_PROJECT` env var when `project=None`
- [ ] Returns `RecentActivityResult` with correct `total_count`, `hours`, `generated_at`, `query_time_ms`
- [ ] Returns empty result (not error) when no memories found
- [ ] Fail-open: store errors return empty result

**Test Scenarios:**
1. `test_recent_activity_default_params` — 24h, all projects, returns RecentActivityResult
2. `test_recent_activity_custom_hours` — Respects custom lookback window
3. `test_recent_activity_hours_clamped_low` — hours=0 → clamped to 1
4. `test_recent_activity_hours_clamped_high` — hours=500 → clamped to 168
5. `test_recent_activity_max_memories_clamped` — max_memories=0 → 1, max_memories=999 → 200
6. `test_recent_activity_project_filter` — Only returns memories for specified project
7. `test_recent_activity_project_env_fallback` — Uses LORE_PROJECT when project=None
8. `test_recent_activity_excludes_expired` — Expired memories not in result
9. `test_recent_activity_includes_all_tiers` — working, short, long all present
10. `test_recent_activity_empty_store` — No memories → empty groups, total_count=0
11. `test_recent_activity_store_error` — Store raises exception → empty result, no crash
12. `test_recent_activity_query_time_recorded` — query_time_ms > 0
13. `test_recent_activity_generated_at_set` — generated_at is valid ISO timestamp

**Files:**
- Modify: `src/lore/lore.py`
- Create: `tests/test_lore_recent.py`

---

### S6: MCP Tool — `recent_activity`

**Size:** M (2-4h)
**Dependencies:** S5
**Batch:** 3 (parallel with S7, S8)

**Description:**
Register `recent_activity` as an MCP tool in `mcp/server.py`. Update the FastMCP `instructions` to guide agents to call it at session start.

**Acceptance Criteria:**
- [ ] `recent_activity` tool registered with FastMCP and discoverable
- [ ] Tool description includes "CALL THIS AT THE START OF EVERY SESSION"
- [ ] Parameters: `hours` (int, default 24), `project` (str, optional), `format` (str, default "brief"), `max_memories` (int, default 50)
- [ ] Returns formatted string for `brief`/`detailed` formats
- [ ] Returns JSON string for `structured` format
- [ ] Catches all exceptions and returns error message string (never crashes)
- [ ] FastMCP `instructions` field updated to mention `recent_activity`
- [ ] Works identically across all MCP-compatible platforms

**Test Scenarios:**
1. `test_mcp_recent_activity_tool_registered` — Tool exists and is discoverable
2. `test_mcp_recent_activity_brief_returns_string` — Returns formatted string
3. `test_mcp_recent_activity_structured_returns_json` — Returns valid JSON string
4. `test_mcp_recent_activity_error_handling` — Exception → error message string, not crash
5. `test_mcp_instructions_mention_recent_activity` — instructions field contains "recent_activity"

**Files:**
- Modify: `src/lore/mcp/server.py`
- Add/modify: `tests/test_mcp_recent.py` or `tests/test_recent_integration.py`

---

### S7: CLI Command — `lore recent`

**Size:** M (2-4h)
**Dependencies:** S5
**Batch:** 3 (parallel with S6, S8)

**Description:**
Add `recent` subcommand to the CLI in `cli.py`. Uses `Lore.recent_activity()` and formats output with `format_cli()`.

**Acceptance Criteria:**
- [ ] `lore recent` runs without error, shows last 24h activity
- [ ] `--hours` option controls lookback window (default: 24)
- [ ] `--project` option filters to specific project
- [ ] `--format` option supports `brief` and `detailed` (default: brief)
- [ ] `--db` option specifies database path (default: `~/.lore/memories.db`)
- [ ] Output is clean terminal text (no markdown headers, no bold syntax)
- [ ] Empty result shows "No recent activity" message, exit code 0

**Test Scenarios:**
1. `test_cli_recent_runs` — Command exits 0
2. `test_cli_recent_default_output` — Shows "Recent Activity (last 24h)" header
3. `test_cli_recent_custom_hours` — `--hours 72` shows correct header
4. `test_cli_recent_project_filter` — `--project foo` filters correctly
5. `test_cli_recent_empty` — No memories → "No recent activity" message
6. `test_cli_recent_format_detailed` — `--format detailed` shows metadata

**Files:**
- Modify: `src/lore/cli.py`
- Add/modify: `tests/test_cli_recent.py` or integration tests

---

### S8: REST Endpoint — `GET /v1/recent`

**Size:** L (4-8h)
**Dependencies:** S1 (uses Pydantic models, direct DB query — does NOT go through SDK)
**Batch:** 3 (parallel with S6, S7)

**Description:**
Add `GET /v1/recent` endpoint to the FastAPI server. This is a server-side implementation that queries Postgres directly (not via SDK). Includes Pydantic response models, auth, query param validation, and analytics recording.

**Acceptance Criteria:**
- [ ] `GET /v1/recent` returns 200 with valid JSON response
- [ ] Query params: `hours` (1-168, default 24), `project` (optional), `format` (brief|detailed|structured), `max_memories` (1-200, default 50)
- [ ] `hours` and `max_memories` clamped to valid range (not rejected)
- [ ] Auth required — missing/invalid API key → 401
- [ ] Invalid `format` value → 422 with descriptive error
- [ ] `format=structured` → response with `groups` field (list of project groups with memories)
- [ ] `format=brief|detailed` → response with `formatted` field (text string)
- [ ] Response includes `total_count`, `hours`, `generated_at`, `has_llm_summary`, `query_time_ms`
- [ ] Memories grouped by project, sorted by `created_at` DESC within groups
- [ ] Groups sorted by most recent memory (newest first)
- [ ] Excludes expired memories (`expires_at IS NULL OR expires_at > now()`)
- [ ] SQL query excludes `embedding` column (explicit SELECT, not SELECT *)
- [ ] Analytics event recorded (same pattern as `/v1/retrieve`)
- [ ] Router included in main app

**Test Scenarios:**
1. `test_rest_recent_200_brief` — Returns 200 with `formatted` field
2. `test_rest_recent_200_structured` — Returns 200 with `groups` field
3. `test_rest_recent_200_detailed` — Returns 200 with detailed text
4. `test_rest_recent_auth_required` — No auth header → 401
5. `test_rest_recent_invalid_format` — `format=invalid` → 422
6. `test_rest_recent_default_params` — No params → 24h, brief, max 50
7. `test_rest_recent_project_filter` — `?project=foo` returns only that project
8. `test_rest_recent_excludes_expired` — Expired memories not in response
9. `test_rest_recent_grouping` — Multiple projects → correct grouping
10. `test_rest_recent_empty` — No memories → `total_count: 0`, empty groups
11. `test_rest_recent_query_time` — `query_time_ms` present and > 0

**Files:**
- Create: `src/lore/server/routes/recent.py`
- Modify: `src/lore/server/app.py` (include router)
- Create: `tests/test_rest_recent.py`

---

### S9: OpenClaw Hook Enhancement

**Size:** M (2-4h)
**Dependencies:** S8
**Batch:** 4

**Description:**
Modify the existing `lore-retrieve` OpenClaw hook handler to fetch recent activity alongside semantic retrieval. Uses two parallel HTTP calls (`/v1/retrieve` + `/v1/recent`). Recent activity block is injected before semantic results.

**Acceptance Criteria:**
- [ ] Hook makes two parallel HTTP calls: `/v1/retrieve` (existing) + `/v1/recent` (new)
- [ ] Recent activity block injected BEFORE semantic results
- [ ] Recent activity block uses `📋 Recent Activity (last 24h):` header (distinct from `🧠`)
- [ ] Hardcoded `max_memories=10` for hook (keeps context tight, ~300 tokens)
- [ ] Disabled via `LORE_RECENT_ACTIVITY=false` env var
- [ ] `LORE_RECENT_HOURS` env var overrides default 24h window
- [ ] Fail-open: if recent call fails, semantic results still work
- [ ] If `total_count=0`, no recent activity block injected (no empty section)
- [ ] Existing semantic retrieval behavior unchanged (no regressions)

**Test Scenarios:**
1. `test_hook_injects_recent_activity` — Recent block appears before semantic results
2. `test_hook_recent_disabled_by_env` — `LORE_RECENT_ACTIVITY=false` → no recent block
3. `test_hook_recent_fail_open` — Recent call fails → semantic still works
4. `test_hook_recent_empty_skipped` — No recent memories → no empty block injected
5. `test_hook_recent_custom_hours` — `LORE_RECENT_HOURS=48` respected
6. `test_hook_semantic_unchanged` — Existing semantic behavior unaffected

**Files:**
- Modify: OpenClaw hook handler (`handler.ts` or equivalent)
- Add/modify: hook tests

---

### S10: LLM Summary Enhancement (Optional)

**Size:** M (2-4h)
**Dependencies:** S5, S4
**Batch:** 4 (parallel with S9, S11)

**Description:**
When LLM enrichment is enabled (`LORE_ENRICHMENT_ENABLED=true`), summarize each project group into 2-3 bullet points. Integrated into `Lore.recent_activity()`. Falls back to structured listing on any LLM failure.

**Acceptance Criteria:**
- [ ] When enrichment enabled and `format != "structured"`: each ProjectGroup gets LLM summary
- [ ] LLM prompt: "Summarize these recent activities into 2-3 bullet points focusing on key decisions, changes, and open items"
- [ ] Memory content truncated to 2000 chars total before sending to LLM
- [ ] `result.has_llm_summary = True` when any group was summarized
- [ ] `group.summary` set to LLM response text
- [ ] LLM failure → `group.summary` stays `None`, no error raised (graceful fallback)
- [ ] LLM timeout → same graceful fallback
- [ ] `format=structured` bypasses LLM entirely
- [ ] Without `LORE_ENRICHMENT_ENABLED=true`, no LLM calls made

**Test Scenarios:**
1. `test_llm_summary_enabled` — Enrichment on → summary populated
2. `test_llm_summary_disabled` — Enrichment off → no LLM call, summary is None
3. `test_llm_summary_structured_skipped` — format=structured → no LLM call
4. `test_llm_summary_failure_fallback` — LLM raises → summary None, no crash
5. `test_llm_summary_timeout_fallback` — LLM times out → summary None
6. `test_llm_summary_content_truncated` — Content capped at 2000 chars
7. `test_has_llm_summary_flag` — Flag set True when any group summarized

**Files:**
- Modify: `src/lore/lore.py` (add LLM call in `recent_activity()`)
- Modify: `src/lore/recent.py` (formatting respects `summary` field — may already work from S4)
- Add/modify: tests

---

### S11: Setup Commands — Claude Code + Cursor

**Size:** S (1-2h)
**Dependencies:** S6
**Batch:** 4 (parallel with S9, S10)

**Description:**
Update `lore setup claude-code` and `lore setup cursor` CLI commands to include instructions for `recent_activity` in CLAUDE.md and .cursorrules respectively.

**Acceptance Criteria:**
- [ ] `lore setup claude-code` appends Memory section to CLAUDE.md mentioning `recent_activity`
- [ ] `lore setup cursor` appends Memory section to .cursorrules mentioning `recent_activity`
- [ ] Appended text matches the templates from the PRD (§6.2, §6.4)
- [ ] Idempotent: running setup twice doesn't duplicate the section
- [ ] Existing CLAUDE.md / .cursorrules content preserved

**Test Scenarios:**
1. `test_setup_claude_code_adds_memory_section` — CLAUDE.md has recent_activity instruction
2. `test_setup_cursor_adds_memory_section` — .cursorrules has recent_activity instruction
3. `test_setup_idempotent` — Running twice doesn't duplicate
4. `test_setup_preserves_existing` — Existing content not overwritten

**Files:**
- Modify: `src/lore/cli.py` (setup subcommand)
- Add/modify: tests

---

## Performance Story (Cross-Cutting)

### S-PERF: Performance Validation

**Size:** S (1-2h)
**Dependencies:** S5 (runs after SDK method exists)
**Batch:** 3 or 4

**Description:**
Create a performance test that validates the <200ms target for 500 memories in the local SQLite store.

**Acceptance Criteria:**
- [ ] Insert 500 memories with varying timestamps and projects
- [ ] Call `recent_activity()` with default params
- [ ] Assert total time < 200ms
- [ ] Test runs in CI without flakiness (use generous margin: assert < 500ms in CI, < 200ms locally)

**Test Scenarios:**
1. `test_recent_500_memories_under_200ms` — Insert 500, query, assert latency

**Files:**
- Create: `tests/test_recent_performance.py`

---

## Summary Table

| Story | Title | Size | Dependencies | Batch |
|-------|-------|------|--------------|-------|
| S1 | Data Types | S (1-2h) | None | 1 |
| S2 | Store ABC + SQLite `since` | M (2-4h) | S1 | 2 |
| S3 | HttpStore `since` | S (1-2h) | S2 | 2 |
| S4 | Formatting Module | M (2-4h) | S1 | 1 |
| S5 | SDK Method | L (4-8h) | S2, S4 | 2 |
| S6 | MCP Tool | M (2-4h) | S5 | 3 |
| S7 | CLI Command | M (2-4h) | S5 | 3 |
| S8 | REST Endpoint | L (4-8h) | S1 | 3 |
| S9 | OpenClaw Hook | M (2-4h) | S8 | 4 |
| S10 | LLM Summary | M (2-4h) | S5, S4 | 4 |
| S11 | Setup Commands | S (1-2h) | S6 | 4 |
| S-PERF | Performance Test | S (1-2h) | S5 | 3/4 |

**Critical Path:** S1 → S2 → S5 → S6/S7/S8 → S9/S11
