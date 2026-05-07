# Lore claude-mem Parity (Phase 6G) — Design

**Status:** Approved (brainstorm), pending implementation.
**Date:** 2026-05-07
**Author:** Amit Paz, with Claude.

## Goal

Close three architectural gaps surfaced by the [claude-mem architecture
talk](https://youtu.be/KM2qHN3cMnU), plus the two foundational changes those
gaps depend on. The talk's framing of "biomimetic memory" and "10× token
savings" is mostly already in lore (Phases 6A–6F), but three specific pieces
are missing or partial:

1. **UserPromptSubmit capture** — record verbatim user prompts so the
   extraction subagent has intent signal, not just inferred-from-tools intent
2. **`timeline` MCP tool** — middle drill-down layer between `search` and
   `get_memories` that returns chronologically adjacent events for causality
3. **SessionEnd hook** — final buffer flush + emit one consolidated
   `session_summary` observation for next-session continuity

Two foundational changes are load-bearing for the above and so live in the
same phase:

4. **Auto-derive `project`** from git context (remote URL → common-dir
   basename fallback). Without this, project-scoped retrieval is meaningless
   because `project` is `NULL` everywhere today.
5. **`memories.scope` column** (`'project' | 'global'`). Without this, you
   either start a new project with a clean slate (project filtering with no
   global pool) or every repo's auto-captured noise bleeds into every other
   repo (no filtering at all). With it, universal lessons surface
   everywhere; repo-specific stuff stays scoped.

After 6G: the agent can establish causality cheaply via `timeline` before
paying for full content; user intent is part of the extraction context, not
inferred after the fact; sessions seal cleanly with a high-signal summary
observation; and project scoping is real — cross-worktree memory works,
cross-repo bleed-through stops.

## Non-goals

- Changing the consolidation/dreaming pipeline (Phase 6E stays as-is).
- Changing retrieval ranking (Phase 6C stays as-is).
- Changing the `search` / `get_memories` shapes from Phase 6D.
- Reproject migration of orphaned `project=NULL` memories — separate admin
  command, out of scope for this phase.
- Multi-tenant `<private>` policy beyond simple tag stripping.

## Context

This is sub-project 7 of the memory overhaul.

| #  | Name | Status |
|----|------|--------|
| 6A | Auto-capture pipeline | ✅ Shipped (PR #36) |
| 6B | Observation tier + structured schema | ✅ Shipped (PR #37) |
| 6C | Hybrid retrieval | ✅ Shipped (PR #38) |
| 6D | Progressive disclosure | ✅ Shipped (PR #39) |
| 6E | Dreaming | ✅ Shipped (PR #40) |
| 6F | Temporal reasoning | ✅ Shipped (PR #41) |
| **6G** | **claude-mem parity** *(this spec)* | In progress |

## Design decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Timeline scope unit | **Same `project`, ±N entries by `created_at`, with `same_session` flag.** Not pure same-session — the user works in monorepo + git worktrees, so cross-session within project is the actual use case. |
| 2 | Project derivation | **Hybrid: git remote URL → common-dir basename fallback.** Remote URL is stable cross-machine; common-dir handles repos without remotes. Both correctly group worktrees. |
| 3 | Scope classification | **LLM on auto-capture path; type-based default on manual `remember()` path.** Auto-capture already runs an LLM; one extra field is free. Manual calls are deterministic and the user already chose a type. |
| 4 | Cross-project knowledge transfer | **Two-bucket model: `scope='global'` for portable lessons/preferences/patterns/conventions, `scope='project'` for repo-specific stuff. Recall = (current project) ∪ (global).** Starting a new project = empty project shelf + same global shelf. |
| 5 | Timeline window definition | **Count with time max** — default ±10 entries, hard cutoff ±2h. Predictable token cost; can't drag in stuff from a different work block hours later. |
| 6 | Timeline per-entry shape | **`{id, created_at, type, title, narrative_1l, same_session}`** ~60 tokens/entry. Title alone isn't enough for causality; full content collapses the 3-layer story back to 2. |
| 7 | Timeline default direction | **Symmetric `both`** — split is `before = ceil(limit/2)`, `after = floor(limit/2)` (when `limit` is odd, the extra entry goes *before* the anchor — leading-up context is usually richer). Agent can override via `direction='before'\|'after'`. |
| 8 | UserPromptSubmit destination | **Buffer entry + per-batch `intent` observation.** Buffer entry sharpens extraction; one consolidated intent observation per batch keeps it searchable without flooding the DB with one row per prompt. |
| 9 | SessionEnd behavior | **Final flush + emit `session_summary` observation.** No consolidation/dreaming on session close (too eager — that stays on its own schedule). The summary becomes the highest-signal entry for the next session's `recent_activity`. |
| 10 | `<private>` handling | **Strip `<private>...</private>` blocks (non-greedy, DOTALL) at hook level.** Unbalanced opening tag → strip to end-of-prompt (fail-closed). |

## Architecture

```
                ┌─────────────────────────────────────────────────────────┐
                │                  Claude Code session                    │
                │                                                         │
   prompt   ───►│  UserPromptSubmit hook  ──┐                             │
                │                            │                            │
   tool I/O ───►│  PostToolUse hook   ──────┼──►  buffer.jsonl            │
                │                            │     (kind: prompt | tool)  │
   stop     ───►│  Stop hook    ────────────┘            │                │
                │                                        │                │
   exit     ───►│  SessionEnd hook  ───────────────► final flush + seal   │
                └────────────────────────────────────────┼────────────────┘
                                                         │
                                                         ▼
                                ┌──────────────────────────────────────────┐
                                │  capture-extract  (claude -p subagent)   │
                                │                                          │
                                │  Inputs: buffer slice + recent memories  │
                                │          + git project + cwd             │
                                │                                          │
                                │  Output (JSON per observation):          │
                                │    {title, facts, narrative, scope,      │
                                │     kind: 'tool'|'intent'|'summary'}     │
                                └──────────────────┬───────────────────────┘
                                                   │
                                                   ▼
                                       ┌─────────────────────┐
                                       │ POST /v1/observations│
                                       │   project=auto       │
                                       │   scope=from-LLM     │
                                       └──────────┬───────────┘
                                                  │
                                                  ▼
                                          memories table
                                          (+ scope column,
                                           + meta.kind)

  agent ───► search(query)              ───► [{id, title, score, signals}]
        ───► timeline(anchor_id)         ───► [{id, ts, type, title, narrative_1l, same_session}]
        ───► get_memories(ids)           ───► [full StoredMemory]
```

### Invariants

- **Project resolution is deterministic per-cwd.** Same cwd → same project,
  no LLM involved. Cached for the duration of a `capture-extract` invocation.
- **`scope` is never `NULL`.** Column has `NOT NULL DEFAULT 'project'` and
  `CHECK (scope IN ('project','global'))`.
- **Recall always applies the scope filter.** `(scope='global') OR
  (scope='project' AND project = :current_project)`. The `scope='all'`
  override is opt-in and never default.
- **`session_summary` is one-per-session.** Idempotent via the `sealed`
  marker file.
- **`<private>` stripping happens at the hook level, before any data
  touches lore.** No path exists where a private block reaches the DB.
- **Existing 6D shapes (`/v1/search`, `/v1/memories/details`) are
  unchanged.** `timeline` is purely additive.

## Components

### Schema & migration (`migrations_sqlite/023_scope_and_kind.sql`)

```sql
ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'project'
    CHECK (scope IN ('project', 'global'));

CREATE INDEX idx_memories_scope_project
    ON memories(scope, project, created_at);

CREATE INDEX idx_memories_project_session
    ON memories(project, json_extract(meta, '$.session_id'), created_at);

-- Backfill: type-based defaults for existing rows.
UPDATE memories
SET scope = 'global'
WHERE type IN ('lesson', 'preference', 'pattern', 'convention');
```

A matching migration ships for the Postgres tree. `meta.kind` is JSON-only;
no column.

### Project resolution (`src/lore/cli/commands/capture.py`)

```python
def resolve_project(cwd: Path) -> Optional[str]:
    """Resolve project from git context. Cached per-cwd."""
    url = _git_config_or_none(cwd, "remote.origin.url")
    if url:
        return _normalize_remote_url(url)  # → "github.com/user/repo"
    common = _git_rev_parse_or_none(cwd, "--git-common-dir")
    if common:
        return Path(common).resolve().parent.name
    return None
```

Normalization rules for remote URLs:
- `https://github.com/user/repo.git` → `github.com/user/repo`
- `git@github.com:user/repo.git` → `github.com/user/repo`
- Strip trailing `.git`, lowercase host, collapse path separators.

Called once per `capture-extract` invocation. Result passed into the
subagent prompt and applied to every `POST /v1/observations` call from
that batch.

### Hooks (Claude Code shell scripts in `hooks/`)

Three new scripts, installed by `lore setup claude-code`:

| Script | Event | Action |
|---|---|---|
| `lore-capture-prompt.sh` | UserPromptSubmit | Strip `<private>...</private>`, append `{seq, ts, kind:"prompt", text}` to `buffer.jsonl`. No batching — prompts are infrequent. Cap at `LORE_PROMPT_MAX_BYTES` (default 8KB). |
| `lore-capture-stop.sh` | Stop | (existing, unchanged) Drain buffer through `capture-extract`. |
| `lore-capture-end.sh` | SessionEnd | Final `capture-extract` flush (foreground, NOT detached — we need to know when storage completes), then `lore session-finalize --session-id <sid>`, then write `~/.lore/sessions/<sid>/sealed`. The foreground flush is the one place the existing async pattern is intentionally inverted, because `session-finalize` reads the observations the flush just wrote. |

The existing `lore-capture-tool.sh` (PostToolUse) is unchanged.

### Subagent prompt updates (`src/lore/cli/commands/capture.py`)

Three additive changes to the extraction prompt:

1. **Reads `kind:"prompt"` buffer entries** and folds them into context as
   `user said: "<text>"`. Observations now reflect intent, not just
   tool effect.
2. **Emits one `kind:"intent"` observation per batch** — concise summary
   of what the user was trying to do during this batch. Title ≤80 chars,
   1-sentence narrative, 2–4 atomic facts. **Skip emission if the batch
   contains zero `kind:"prompt"` entries** — without prompts there's no
   intent signal to summarize, and inferring it from tool I/O would
   re-introduce the noise this design is meant to remove.
3. **Returns `scope` per observation.** Prompt directive:
   > Set `scope:'project'` for anything mentioning specific files,
   > functions, decisions, or behavior in *this* repo. Set
   > `scope:'global'` only for universal lessons that would apply in any
   > codebase (language gotchas, framework patterns, tool quirks).
   > When in doubt, pick `'project'`.

Output validation: invalid `scope` → fallback to `'project'` with a
warning to `errors.log`. Missing `kind` → default `'tool'`. Malformed
JSON line → skip with log; advance cursor.

### `lore session-finalize` (new CLI subcommand)

Called by `lore-capture-end.sh`. Spawns one final small subagent with a
distinct prompt:

> Given the observations from session `<sid>`, emit a single observation
> with `kind:'summary'`: title (≤80 chars), facts (3–5 atoms — what was
> accomplished, decisions made, open threads), narrative (2–3
> sentences). Do NOT re-extract from raw buffer events; the per-batch
> extractions are already done. Read only the observations.

Idempotent — first checks for `~/.lore/sessions/<sid>/sealed` and
no-ops if present.

### New MCP tool: `timeline` (`src/lore/mcp/server.py`)

```python
@mcp.tool(description=(
    "Phase 6G middle drill-down: return chronologically adjacent events "
    "(±N entries, hard cap ±max_hours) around an anchor memory ID, "
    "scoped to the same project. Each entry: id, created_at, type, "
    "title, narrative_1l, same_session. USE THIS AFTER search() "
    "identifies a promising hit, BEFORE get_memories(), to establish "
    "causality without paying for full content."
))
def timeline(
    anchor_id: str,
    limit: int = 10,           # total entries (split by direction)
    direction: str = "both",   # 'before' | 'after' | 'both'
    max_hours: float = 2.0,    # hard time cap
) -> str: ...
```

Server endpoint: `GET /v1/timeline?anchor_id=...&limit=...&direction=...&max_hours=...`.

Implementation:
1. Fetch anchor row to get its `project`, `created_at`, and `session_id`
   (from `meta`).
2. Single SQL query against `idx_memories_project_session`:
   ```sql
   SELECT id, created_at, type, content, meta,
          json_extract(meta, '$.session_id') AS session_id
   FROM memories
   WHERE project = :anchor_project
     AND created_at BETWEEN
         datetime(:anchor_ts, '-' || :max_hours || ' hours')
         AND
         datetime(:anchor_ts, '+' || :max_hours || ' hours')
     AND id != :anchor_id
   ORDER BY ABS(julianday(created_at) - julianday(:anchor_ts))
   LIMIT :limit_total
   ```
   For `direction='before'`/`'after'`, add a `created_at < :anchor_ts`
   or `created_at > :anchor_ts` predicate; `'both'` splits the limit
   into before/after halves with two queries unioned.
3. Decorate each row: `same_session = (session_id == anchor.session_id)`,
   `narrative_1l = first_sentence(meta.narrative) or content[:80]`. Where
   `first_sentence(s)` = everything up to the first `.`, `!`, or `?`
   followed by whitespace or EOL, capped at 200 characters.
4. Strip heavy fields (`facts`, full `narrative`, `tags`) from the
   response.

### Recall/search filtering by scope

`recall`, `search`, `/v1/retrieve`, `/v1/search` all gain an implicit
predicate:

```sql
WHERE (scope = 'global')
   OR (scope = 'project' AND project = :current_project)
```

`current_project` resolution at the request boundary:
1. If `auth.project` is set (project-scoped key) → use it.
2. Else if request body/query carries explicit `project=` → use it.
3. Else → `None`. With `current_project=None`, only `scope='global'`
   memories are visible (we explicitly do NOT match `project IS NULL`,
   to prevent orphaned cross-bleed).

Explicit override: `recall(query, scope='all')` and `search(query,
scope='all')` skip the predicate. Documented as opt-in for the rare
cross-project search.

### `remember()` and `remember_observation()` MCP tools

Both gain an optional `scope: Optional[str] = None` parameter.

**`remember()`** (manual path):
- If `scope` is passed → use it.
- If omitted → default by type:
  - `lesson`, `preference`, `pattern`, `convention` → `'global'`
  - `note`, `fact`, anything else → `'project'`
- No LLM call.

**`remember_observation()`** (auto-capture path):
- Subagent always returns `scope` in its JSON output.
- The MCP tool passes it through.
- Server validates and stores.

## Edge cases

- **No git context (cwd not a repo):** `resolve_project()` returns `None`.
  Observations land with `project=NULL, scope='project'`. Invisible to
  project-scoped recall in any real repo (correct), reachable via
  `scope='all'`.
- **Subagent returns invalid `scope`:** validate against allowed values;
  default to `'project'` with a warning. Don't fail the observation.
- **`<private>` unbalanced tag:** non-greedy `re.DOTALL` strip; if no
  closing tag, strip from `<private>` to end-of-prompt (fail-closed).
- **SessionEnd after Stop already drained:** final `capture-extract`
  no-ops on empty unprocessed window. `session-finalize` runs over
  stored observations to emit summary. `sealed` flag prevents double-run.
- **SessionEnd doesn't fire (SIGKILL, OS crash):** no summary
  observation, but per-batch observations are still in the DB. Accept
  this; future `lore reaper` cron can finalize stale unsealed sessions.
- **UserPromptSubmit flood:** cap each prompt at `LORE_PROMPT_MAX_BYTES`
  (default 8KB), truncate with marker. No batching needed.
- **Timeline anchor in different project than caller:** resolve project
  from the anchor row, not the caller. If caller has a project-scoped
  key for a different project → 403. Otherwise return events from the
  anchor's project.
- **Migration on populated DB:** type-based backfill in the migration
  itself. Existing rows without `project` set get `scope='project',
  project=NULL` — invisible by default. Reproject migration is a
  separate admin command (out of scope for this phase).
- **Buffer corruption / partial JSON line:** existing 6A behavior — skip,
  advance cursor, log to `errors.log`.

## Migration

Single migration (`023_scope_and_kind.sql`, mirrored for Postgres):

1. `ALTER TABLE memories ADD COLUMN scope ...` with default and check.
2. `CREATE INDEX` for `(scope, project, created_at)`.
3. `CREATE INDEX` for `(project, session_id_from_meta, created_at)`.
4. Type-based backfill `UPDATE`.

No data is rewritten beyond the backfill `UPDATE`. No memory shape
changes. No deprecations.

## Tunables

| Env var | Default | Effect |
|---|---|---|
| `LORE_PROMPT_MAX_BYTES` | `8192` | Max bytes per buffered user prompt. |
| `LORE_TIMELINE_DEFAULT_LIMIT` | `10` | Default `limit` for `timeline`. |
| `LORE_TIMELINE_DEFAULT_HOURS` | `2.0` | Default `max_hours` for `timeline`. |
| `LORE_PROJECT_OVERRIDE` | (unset) | If set, skip git resolution, use this verbatim. Useful for CI / non-git work. |
| `LORE_AUTO_SAVE` | `true` | (existing) master kill switch. |

## Tests

### Unit

- `resolve_project()`: git-remote URL normalization (https/ssh/.git);
  common-dir fallback; no-git returns None.
- `<private>` strip: balanced tags, unbalanced (strip-to-end), nested,
  empty, multi-line.
- Subagent output validation: invalid `scope` → fallback; missing
  `kind` → default `'tool'`; malformed JSON → skip.
- Timeline window math: direction split for `both`; time-cap intersection
  with count cap; anchor-at-edge (no entries before / after).

### Integration

- Auto-capture in two worktrees of same repo, recall in either →
  memories from both surface; `same_session=False` on cross-worktree
  entries.
- Manual `remember(content, type='lesson')` → `scope='global'`,
  recall in unrelated project surfaces it.
- Manual `remember(content, type='note')` → `scope='project'`, invisible
  in unrelated project.
- `recall(query, scope='all')` from project A → returns memories from B.
- Migration on populated DB: `type='lesson'` rows → `scope='global'`;
  observations → `scope='project'`; row counts unchanged; no NULL scopes.
- `timeline(anchor_id)` with limit=10, direction=both → ≤10 entries,
  ordered by `created_at`, `same_session` flag correct, respects 2h cap.
- `timeline` with anchor from different project (unscoped key) →
  events from anchor's project.
- `timeline` with project-scoped key against another project's anchor →
  403.
- End-to-end SessionEnd: final flush runs; `session_summary` observation
  created; `sealed` marker present; subsequent `capture-extract` no-ops.
- Idempotent SessionEnd: calling `session-finalize` twice doesn't
  double-summarize.
- `<private>` end-to-end: prompt with `<private>SECRET</private>` →
  buffer entry contains no SECRET; observation never references it.

### Token-cost regression

`benchmarks/test_progressive_disclosure_cost.py`: measure tokens to
retrieve "the 5 most causally relevant" via three flows on a 100-obs
session — full recall (a), 6D search+drill (b), 6G search+timeline+drill
(c). Assert c < b < a, c roughly 1/3 of a.

### Out of scope for testing

- Subagent's *judgment* on scope classification (LLM behavior; we test
  the contract — output shape, validation — not the calls themselves).
- Specific session-summary content quality (same reason).
- `recent_activity` ranking changes (it picks up `kind='summary'`
  observations naturally; no special-casing needed).
