# Phase 6G — claude-mem Parity Implementation Plan

**Spec:** `docs/superpowers/specs/2026-05-07-lore-claude-mem-parity-design.md`

**Status:** Implemented. PR: https://github.com/agentkitai/lore/pull/44

**Goal:** Add a `memories.scope` column with project/global semantics, auto-derive `project` from git context at capture time, ship a `timeline` MCP tool for cheap causality drill-down, and wire UserPromptSubmit + SessionEnd hooks so user intent informs extraction and sessions seal with a high-signal summary observation.

**Architecture:** New SQL migration adds the `scope` column + two indices. `resolve_project()` helper runs in `capture-extract` and is passed to the subagent prompt. Subagent prompt gains three additive directives (read prompt entries, emit `kind:'intent'` observation per batch when prompts are present, return `scope` per observation). New `GET /v1/timeline` endpoint and matching MCP tool. Recall/search apply `(scope='global') OR (scope='project' AND project=:current)` filter. New `lore-capture-prompt.sh` and `lore-capture-end.sh` hooks installed by `lore setup claude-code`. New `lore session-finalize` CLI emits one `kind:'summary'` observation per session.

## Task list (each = one commit on `phase-6g-claude-mem-parity`)

| # | Task | Commit |
|---|------|--------|
| T1 | Migration 024 + scope field on `NewMemory`/`StoredMemory`/`NewObservation` + sqlite + postgres reads/writes | `ab94612` |
| T2 | `resolve_project()` helper in `src/lore/cli/commands/_project.py` | `b152a1b` |
| T3 | `strip_private()` helper (same module) | `9c41b0f` |
| T4-T5 | `scope` param on `/v1/observations` + `/v1/memories` + MCP write tools, with type-based defaults | `6a25c3a` |
| T6 | Scope filter on `/v1/retrieve`, `/v1/search`, recall (with `scope='all'` opt-out) | `668cf91` |
| T7-T8 | `GET /v1/timeline` endpoint + `timeline` MCP tool | `37fe9ad` |
| T9 (cap8) | Subagent prompt: read `kind:"prompt"` entries, emit `kind:"intent"` obs, return scope; project plumbing | `9f89610` |
| T10 (h1) | UserPromptSubmit hook (`hooks/lore-capture-prompt.sh`) with `<private>` stripping | `d5dad85` |
| T11 (h2) | SessionEnd hook (`hooks/lore-capture-end.sh`) + `lore session-finalize` CLI | `71ed31f` |
| T12 (s) | `lore setup claude-code` installs both new hooks + registers them in Claude Code settings | `d5a37d2` |
| T13 (d) | CHANGELOG entry | `c60b66b` |

## File summary

### New files
- `migrations_sqlite/024_scope_and_kind.sql`, `migrations/024_scope_and_kind.sql` — column + indices + backfill (sqlite + postgres)
- `src/lore/cli/commands/_project.py` — `resolve_project`, `strip_private`, `_normalize_remote_url`
- `src/lore/cli/commands/session_finalize.py` — `lore session-finalize` CLI subcommand
- `src/lore/server/routes/timeline.py` — `GET /v1/timeline`
- `hooks/lore-capture-prompt.sh`, `hooks/lore-capture-end.sh` — Claude Code hooks
- `tests/cli/test_resolve_project.py`, `test_private_strip.py`, `test_capture_extract_prompt.py`, `test_session_finalize.py`, `test_setup_claude_code_phase6g.py`
- `tests/persistence/test_scope_field.py`, `test_phase6g_timeline.py`
- `tests/services/test_phase6g_scope.py`, `test_observation_kind_mapping.py`
- `tests/server/test_timeline_route.py`
- `tests/integration/test_phase6g_e2e.py`
- `tests/test_capture_prompt_hook.py`

### Modified
- `src/lore/persistence/types.py` — `scope: str = "project"` on `NewMemory`, `NewObservation`, `StoredMemory`; matching default on `ScoredMemory.score`
- `src/lore/persistence/sqlite.py` — `_MEMORY_COLS` includes `scope`; all INSERTs and SELECTs propagate it; `recall_by_*` apply scope predicate; `list_timeline_around` added
- `src/lore/persistence/postgres.py` — same shape
- `src/lore/persistence/protocol.py` — `Store.list_timeline_around`, `scope_mode` on retrieval methods
- `src/lore/services/memories.py` — `default_scope_for_type()` helper, `scope` kwarg on `create_memory`, `scope_mode` on `search_memories`
- `src/lore/services/observations.py` — passes `scope` through; `_classify_kind()` sets `meta.kind` from tags
- `src/lore/services/lessons.py`, `retrieve.py` — same shape
- `src/lore/server/models.py` — `scope` on create/search request models, `scope` on response models, `TimelineEntry`/`TimelineResponse`
- `src/lore/server/routes/observations.py`, `memories.py`, `lessons.py`, `retrieve.py`, `search.py` — plumb `scope` and `scope_mode`
- `src/lore/server/app.py` — register timeline router
- `src/lore/mcp/server.py` — `scope` param on `remember`/`remember_observation`; `scope` opt-out on `recall`/`search`; new `timeline` tool
- `src/lore/cli/commands/capture.py` — `_build_extraction_prompt()` extracted; `--cwd` and `--foreground` flags; project resolution
- `src/lore/cli/__init__.py` — `session-finalize` subcommand registered
- `src/lore/setup.py` — embedded hook scripts + path helpers + Claude Code event registration
- `src/lore/lore.py`, `async_lore.py` — `scope` / `scope_mode` on `remember`/`recall`
- `src/lore/store/http.py` — `_memory_to_lesson` includes scope
- `tests/embedded/test_async_lore.py`, `test_setup.py` — updated to match new defaults
- `CHANGELOG.md` — Phase 6G entry

## Test results

- New Phase 6G tests: **100/100 passing** in unit/service/route/integration files (8 skip without Postgres).
- Full local sweep (excluding pre-existing-broken `tests/test_http_store_integration.py`): **2756 passed, 0 failed**, 1172 skipped (pg-pool / contract markers).
- Up from ~2607 passing pre-Phase-6G.

## Behavior change

Unscoped recall queries with no current project now restrict to `scope='global'` rows only. SDK callers who relied on cross-project bleed-through must either set a `project` or pass `scope_mode='all'`. Two `tests/embedded/test_async_lore.py` recall tests that depended on the old cross-bleed were updated to use the documented `scope_mode='all'` opt-in.
