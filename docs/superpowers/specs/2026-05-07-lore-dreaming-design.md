# Lore Dreaming (Phase 6E) — Design

**Status:** Approved (autonomous trust mandate), pending implementation.
**Date:** 2026-05-07

## Goal

Periodically run an LLM-driven consolidation pass over Lore's memory database to merge near-duplicates, prune low-value entries, resolve contradictions, and synthesize higher-order "lessons learned" from raw observations. Mirrors Anthropic's "Dreaming" feature for Claude Managed Agents (announced 2026-05-06 at Code w/ Claude).

After 6E: Lore's memory base self-curates. Captured observations don't pile up indefinitely; the high-signal ones get promoted to polished memories, the rest are archived or pruned. Contradictions surface in `fact_conflicts` and get resolved (or flagged for human review).

## Non-goals

- Replacing the auto-capture pipeline. 6A still does raw capture; 6E reads what 6A wrote.
- Real-time consolidation. Dreaming is batch; latency-insensitive.
- Mandatory automation. The trigger is opt-in; users can run `lore dream` manually any time.
- Cross-org consolidation. Each org's memories are processed independently.

## Design decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Trigger condition | Same as Anthropic's auto-dream: **24h elapsed AND ≥5 sessions since last consolidation**. Tracked in a new `dream_runs` table. Manual `lore dream --force` bypasses both. |
| 2 | What scheduler triggers it? | A new **Stop hook** check (`lore-dream-trigger.sh`): on session end, check if conditions are met; if yes, fire `lore dream` as a detached subprocess. Defers heavy work to background. |
| 3 | Who runs the consolidation LLM? | Same as 6A: `claude -p` subagent in user's session. Inherits Claude Code's auth. |
| 4 | What pipeline? | Anthropic's 4 phases: **Orient → Gather Signal → Consolidate → Prune & Index**. |
| 5 | Tool surface for the dream subagent | All existing MCP tools (`recall`, `search`, `get_memories`, `consolidate`, `forget`, `remember`, `upvote_memory`, `downvote_memory`, `entity_map`, etc.) — no new MCP tools required. |
| 6 | Human-review mode? | Optional. `LORE_DREAM_REVIEW=true` writes a proposed-changes diff to `~/.lore/dreams/<run_id>.md` and skips applying changes. User runs `lore dream apply <run_id>` to commit. Default OFF. |
| 7 | Concurrency safety | Lock file at `~/.lore/dreams/lock`. Refuse to start a second concurrent dream. |
| 8 | Failure policy | Fail-open. A failed dream logs to `~/.lore/dreams/<run_id>/errors.log` and leaves the DB unchanged. Next trigger retries. |

## Architecture

```
End of Claude Code session
        │
        ▼
Stop hook (lore-capture-stop.sh)  →  also calls lore-dream-trigger.sh
                                             │
                                             ▼
                            check `dream_runs.last_run_at`
                            count session_id distinct since then
                                             │
                                             ▼  (24h + ≥5 sessions met?)
                                  spawn `lore dream` (detached)
                                             │
                                             ▼
                            ┌────────────────┴────────────────┐
                            │  Phase 1: Orient                 │
                            │  - count by type, by project     │
                            │  - top entities, recent activity │
                            └────────────────┬────────────────┘
                                             ▼
                            ┌────────────────┴────────────────┐
                            │  Phase 2: Gather Signal          │
                            │  - grep transcripts for          │
                            │    corrections, "actually X"     │
                            │  - find recurring patterns       │
                            │  - identify save_snapshot calls  │
                            └────────────────┬────────────────┘
                                             ▼
                            ┌────────────────┴────────────────┐
                            │  Phase 3: Consolidate            │
                            │  - cluster near-duplicates       │
                            │  - merge via consolidate()       │
                            │  - resolve fact_conflicts        │
                            │  - promote observations →        │
                            │    lessons/facts when confident  │
                            └────────────────┬────────────────┘
                                             ▼
                            ┌────────────────┴────────────────┐
                            │  Phase 4: Prune & Index          │
                            │  - forget low-importance unused  │
                            │  - normalize "yesterday" dates   │
                            │  - update topic summaries        │
                            └────────────────┬────────────────┘
                                             ▼
                                  record run in dream_runs
                                             ▼
                                  log summary to dreams/<run_id>/summary.md
```

### Invariants

- **Single dreamer.** `flock` prevents concurrent runs.
- **Idempotent within a run.** Each phase uses the existing MCP tools (consolidate, forget, etc.) which Lore already makes idempotent.
- **No silent destruction.** Every `forget` call is logged with id + content snippet to `dreams/<run_id>/audit.log`. Recoverable from snapshots if needed.
- **Trigger is observable.** Users can run `lore dream --status` to see when the next dream would fire.

## Components

### Schema additions

| Path | What it adds |
|------|--------------|
| `migrations/022_dream_runs.sql` | `CREATE TABLE dream_runs (id TEXT PRIMARY KEY, org_id TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ, status TEXT NOT NULL, summary JSONB, error TEXT)` |
| `migrations_sqlite/022_dream_runs.sql` | Equivalent SQLite schema (`status TEXT`, `summary TEXT` (JSON), `started_at TEXT`). |

### CLI

```
lore dream                    Run consolidation now (forces if conditions not met).
lore dream --force            Skip trigger checks.
lore dream --status           Print last run + next-eligible-time.
lore dream --review           Write proposed changes to ~/.lore/dreams/<id>.md;
                              don't apply. Combine with `apply`.
lore dream apply <run_id>     Apply a previously-deferred review run.
lore dream --org-id solo      Override default org.
```

### Subagent prompt template

The `lore dream` command builds a prompt for `claude -p` with embedded instructions for the 4 phases. The subagent has full Lore MCP access. Pseudo-code:

```
You are Lore's Dream worker. Consolidate the user's memory base.

Current state (Phase 1: Orient):
{stats_json}                  ← total memories, by type, by project
{recent_activity}             ← last 7 days
{top_entities}                ← top 20 entities by mention count

Recent session signal (Phase 2):
{transcript_grep_results}     ← corrections, "actually...", "I prefer", explicit
                                "remember this" calls, save_snapshot calls

Your job (Phase 3 + 4):
1. CONSOLIDATE near-duplicates: when multiple memories say the same thing,
   call consolidate(canonical_id, duplicate_ids).
2. RESOLVE contradictions: query fact_conflicts; pick the one supported by
   newer corrections; downvote/forget the loser.
3. PROMOTE observations: when an observation has been retrieved >3× and
   has importance > 0.7, call remember(content=narrative, type=lesson).
4. PRUNE: when an observation is older than 30 days, importance < 0.3,
   and access_count = 0, call forget(memory_id).
5. NORMALIZE dates: scan recent memories' content; if you see "yesterday",
   "last week", etc., call update_memory to replace with absolute dates.

Be surgical. Reorder/merge/prune; don't invent new content.

When done, return:
  PHASE_1_ORIENT_COMPLETE
  PHASE_2_SIGNAL_COMPLETE
  PHASE_3_CONSOLIDATE_COMPLETE: <count_merged> <count_promoted>
  PHASE_4_PRUNE_COMPLETE: <count_pruned>
  RUN_ID: <run_id>
```

### Trigger hook

`~/.claude/hooks/lore-dream-trigger.sh` (bash, ~30 LOC):
- Read `LORE_DREAM_AUTO` env (default `true`); exit 0 if false.
- Query `lore dream --status --json`; parse `next_eligible_at`.
- If `now >= next_eligible_at`: spawn `nohup lore dream &`; exit 0.
- Else exit 0.

Wired into `Stop` hook chain (Phase 6A also uses Stop). The two hooks coexist — Phase 6A captures, Phase 6E may trigger consolidation.

### Service layer

```python
# src/lore/services/dreams.py

@dataclass(frozen=True, slots=True)
class DreamRun:
    id: str
    org_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    status: str  # "running" | "completed" | "failed"
    summary: Mapping[str, Any]
    error: Optional[str]

async def is_dream_eligible(store, org_id: str) -> bool:
    """Return True iff conditions met (24h + ≥5 sessions)."""

async def start_dream(store, org_id: str) -> str:
    """Insert a 'running' row in dream_runs; return run_id."""

async def complete_dream(store, run_id: str, summary: dict) -> None:
    """Mark complete with the parsed summary."""

async def fail_dream(store, run_id: str, error: str) -> None:
    """Mark failed."""

async def get_status(store, org_id: str) -> dict:
    """Return last_run_at, next_eligible_at, sessions_since_last."""
```

### CLI command

`src/lore/cli/commands/dream.py` (~250 LOC):
- Parse args (`--force`, `--review`, `--status`, `--org-id`, `apply <run_id>`).
- Acquire `flock` on `~/.lore/dreams/lock`.
- Call `start_dream` → get run_id.
- Build prompt (calls helper to gather Phase 1 + 2 inputs).
- Invoke `claude -p <prompt>` (subprocess, captures stdout to `dreams/<run_id>/extract.log`).
- Parse phase markers from output.
- Update `dream_runs` row with summary.
- Print human-readable summary.

### Tests

| Layer | Coverage |
|-------|----------|
| Migrations | 022 applies cleanly on both backends. |
| Service | `is_dream_eligible` math (24h + ≥5 sessions). |
| Service | `start_dream` / `complete_dream` / `fail_dream` round-trip on both backends. |
| CLI | `lore dream --status` (no args) — fresh DB → "never run, eligible after first session". |
| CLI | `lore dream --force` with mocked `claude -p` → run completes, dream_runs row updated. |
| CLI | Concurrency: simulate two dreams via flock; second exits cleanly. |
| Hook | `lore-dream-trigger.sh` with `LORE_DREAM_AUTO=false` → exit 0, no spawn. |
| Integration | End-to-end mocked dream: synthetic memories + transcript, mocked subagent that calls real MCP tools, assert dream_runs.summary correct. |

## Scope

| Component | LOC |
|-----------|-----|
| Migrations 022 (PG + SQLite) | ~40 |
| `services/dreams.py` | ~150 |
| `cli/commands/dream.py` | ~250 |
| `cli/__init__.py` (subparser) | ~20 |
| `setup.py` (trigger hook + install) | ~80 |
| Tests | ~350 |
| Docs | ~40 |
| **Total** | **~930** |

## Out of scope

- Multi-org consolidation. Each org runs independently.
- Adaptive trigger (e.g. "dream after 50 new memories"). Fixed 24h+5 sessions for v1.
- LLM-driven entity-level consolidation (merging "k8s" and "kubernetes" entities). Phase 6F.
- UI for browsing dream history. CLI-only.
- Custom prompts per org. Single global template.
