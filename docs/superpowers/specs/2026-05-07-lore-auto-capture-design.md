# Lore Auto-Capture Pipeline (Phase 6A) — Design

**Status:** Approved (brainstorm), pending implementation plan.
**Date:** 2026-05-07
**Author:** Amit Paz, with Claude.

## Goal

Make Lore's memory database grow on its own as the user works in Claude Code, without the agent having to remember to call `remember()` and without firing a paid OpenAI extraction call on every action.

After this phase: every Claude Code session contributes 0–N polished memories to Lore automatically. The user never types "save this" — relevant lessons, decisions, preferences, and gotchas accumulate in the background.

## Non-goals

- Building a richer "observation tier" with structured `{type, title, facts, narrative}` schema. That's Phase 6B; 6A produces ordinary Lore memories using the existing six types (`lesson, fact, preference, pattern, convention, note`).
- Improving retrieval quality. That's Phase 6C/6D.
- Memory consolidation, reflection, or pruning. That's Phase 6E ("Dreaming").
- Temporal reasoning over the knowledge graph. That's Phase 6F.
- Capturing observations from runtimes other than Claude Code. Phase 6A scope is Claude Code only; OpenClaw/Cursor/Codex equivalents land in their own follow-ups.

## Context: where 6A sits in the larger overhaul

This spec is sub-project 1 of a 6-phase memory-system overhaul. Each phase ships independently; subsequent phases get their own brainstorming → spec → plan cycles.

| # | Name | One-line summary |
|---|------|------------------|
| **6A** | **Auto-capture pipeline** *(this spec)* | Save-side hooks + Claude Code subagent extracts memories automatically |
| 6B | Observation tier + structured schema | claude-mem-style `{type, title, facts, narrative}` for raw observations distinct from polished memories |
| 6C | Hybrid retrieval | Vector + FTS + graph + recency + importance, profile-driven weights |
| 6D | Progressive disclosure | search → drill-in 3-layer retrieval; ~10× token savings |
| 6E | Dreaming | Anthropic's 4-phase consolidation (Orient → Gather Signal → Consolidate → Prune/Index); 24h + ≥5 sessions trigger |
| 6F | Temporal graph reasoning | Bitemporal facts; activate `fact_conflicts` table for contradiction detection |

Dependencies: 6A → 6B → (6C, 6D in parallel) → (6E, 6F).

## Design decisions (resolved during brainstorm)

| # | Question | Decision |
|---|----------|----------|
| 1 | Who runs the extraction LLM? | Claude Code subagent in current session (via `claude -p` subprocess). Inherits the user's existing Claude Code auth. **No separate API key needed.** |
| 2 | When does the subagent fire? | claude-mem-style: every N tool calls during the session, plus a final pass on Stop. |
| 3 | What does the subagent produce? | Ordinary Lore memories via `mcp__lore__remember(content, type=...)`. The richer schema lands in 6B. |
| 4 | What's the buffer? | Append-only `~/.lore/sessions/<session_id>/buffer.jsonl`. PostToolUse appends one line per non-skipped tool call. |
| 5 | Skip list defaults | `Read, Glob, Grep, LS, BashOutput, ToolSearch, ListMcpResources, TodoWrite, mcp__lore__*`. Override via `LORE_CAPTURE_SKIP`. |
| 6 | Failure policy | Fail-open. Hook exits 0 on any error; Claude Code session never breaks because of capture failures. Errors logged to `~/.lore/sessions/<session_id>/errors.log`. |
| 7 | Concurrency | `flock` on `~/.lore/sessions/<session_id>/lock`. A second concurrent subagent invocation noops. |

## Architecture

```
Claude Code session
     │
     ├─ PostToolUse hook (every tool call)
     │     │  filter via skip-list → append to buffer
     │     ▼
     │  ~/.lore/sessions/<session_id>/buffer.jsonl
     │     │
     │     ▼  (when buffer ≥ N unprocessed events)
     │  spawn `claude -p` subagent (fire-and-forget, detached)
     │                              │
     ├─ Stop hook ──────────────────┤  (always fires regardless of count)
     │                              ▼
     │              Subagent reads buffer.jsonl + transcript_path tail.
     │              For each memory worth saving:
     │                  mcp__lore__remember(content, type)
     │              Marks consumed events at buffer.jsonl.cursor.
     ▼
   Lore DB (existing /v1/memories endpoint via MCP)
```

### Invariants

- **Hook fail-open.** No hook ever causes Claude Code to error. All failures log and exit 0.
- **One subagent per session at a time.** `flock` ensures no concurrent extraction passes step on each other's buffer cursor.
- **No recursion.** The skip list excludes `mcp__lore__*` so the subagent's own MCP calls don't trigger fresh capture batches.
- **Idempotent.** Subagent re-runs are safe: `buffer.jsonl.cursor` records the highest processed `seq`; re-running on the same buffer produces no duplicate memories (existing Lore vector-similarity dedup is the second line of defense).

## Components

### New files

| Path | Purpose |
|------|---------|
| `~/.claude/hooks/lore-capture-tool.sh` | **PostToolUse hook.** Reads stdin JSON, filters via skip-list, appends to `buffer.jsonl`, increments unprocessed counter, fires subagent if `≥ LORE_CAPTURE_N`. |
| `~/.claude/hooks/lore-capture-stop.sh` | **Stop hook.** Always invokes the subagent on the unprocessed tail. |
| `src/lore/cli/commands/capture.py` | **`lore capture-extract` subcommand.** Internal — the subagent's own entry point. Builds the prompt, invokes `claude -p`, advances the cursor on success. Centralizes logic so both hooks call the same code. |

### Modified files

| Path | Change |
|------|--------|
| `src/lore/cli/__init__.py` | Register `capture-extract` subcommand. |
| `src/lore/setup.py` | Extend `setup_claude_code()` to install both hooks and register them in `~/.claude/settings.json` under `PostToolUse` and `Stop`. |

## Data flow

### Buffer schema (`~/.lore/sessions/<session_id>/buffer.jsonl`)

One JSON object per line, append-only:

```json
{"seq": 42, "ts": "2026-05-07T12:00:00Z", "tool": "Edit", "input_summary": "file=src/lore/setup.py", "output_summary": "Updated 1 line"}
```

- `seq` — monotonic per-session counter. Used by the cursor file to track processed prefix.
- `ts` — ISO-8601 UTC timestamp.
- `tool` — Claude Code tool name (e.g. `Edit`, `Bash`, `Write`, `Task`, `mcp__github__create_pr`).
- `input_summary` / `output_summary` — pre-truncated to ~200 chars each. The subagent gets the gist without consuming context on giant payloads.

Cursor file (`~/.lore/sessions/<session_id>/buffer.jsonl.cursor`) holds a single integer: the highest `seq` already processed by a subagent. The subagent updates this atomically (write-and-rename) on success.

### Skip list

Default skipped tools (rationale in parens):

- `Read, Glob, Grep, LS` — info gathering; the *use* of the info shows up in subsequent Edit/Bash/Write events.
- `BashOutput` — passive read of an already-running task.
- `ToolSearch, ListMcpResources` — meta tooling.
- `TodoWrite` — agent's own internal scratchpad.
- `mcp__lore__*` — recursion guard.

Override: `LORE_CAPTURE_SKIP="Read,Glob,Grep,..."` (CSV) replaces the default list. Empty value disables the skip list entirely.

### Trigger condition

After every successful PostToolUse append:

```
unprocessed_count = total_seq_in_buffer - cursor_seq
if unprocessed_count >= LORE_CAPTURE_N (default 10):
    spawn subagent in background
```

Stop hook ignores the count and always fires on the unprocessed tail.

## Subagent invocation

### Mechanism: `claude -p` subprocess

The subagent runs as a detached subprocess invoked from `lore capture-extract`:

```python
subprocess.Popen(
    ["claude", "-p", prompt, "--output-format", "stream-json"],
    stdin=subprocess.DEVNULL,
    stdout=open(error_log, "a"),
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
```

Why `claude -p` over the Claude Agent SDK:
- **Zero extra Python deps** — `claude` CLI is already on PATH if the user is running Claude Code.
- **Inherits Claude Code's auth** — no `ANTHROPIC_API_KEY` configuration in Lore.
- **Already familiar to users** — same model and same billing as the foreground session.
- **Hook stays fast** — `Popen` returns immediately; the hook is non-blocking.

Trade-off: text I/O instead of structured. Acceptable given the subagent's job is to call MCP tools, not return data to the hook.

### Prompt template

The hook builds a prompt of roughly this shape (~600 tokens):

```
You are Lore's memory extraction worker for an active Claude Code
session. Your job: read the session log and recent transcript, decide
what (if anything) is worth remembering, and call Lore's MCP remember
tool for each kept item.

Inputs:
  Buffer (tool calls since last extraction):
    {buffer_jsonl_contents}

  Transcript tail (last {LORE_CAPTURE_TRANSCRIPT_TURNS} turns):
    {transcript_tail}

  Memories already saved this session (do NOT re-save):
    {recent_memory_titles}

Goal: identify decisions, lessons, user preferences, gotchas, and key
facts about the codebase or environment.

For each kept item, call:
  mcp__lore__remember(content="<short, self-contained>", type="<one of:
  lesson, fact, preference, pattern, convention, note>")

Rules:
  - Be selective. Quality > quantity. 0 memories is fine.
  - Typical batch: 0–3 memories.
  - Skip trivial info-gathering, WIP noise, work the agent didn't finish.
  - Skip anything similar to a memory already in `recent_memory_titles`.
  - Use complete sentences. The memory should make sense out of context.

After processing, return: PROCESSED_THROUGH_SEQ=<highest seq from buffer>
```

The subagent has Lore's MCP tools available because Claude Code's MCP config is global; subagents inherit it.

### Cursor advancement

On the subagent's stdout, `lore capture-extract` parses `PROCESSED_THROUGH_SEQ=<n>` and atomically updates the cursor file. If the subagent crashes or the line is missing, the cursor stays put — next batch will retry the same events. The retry is safe because Lore's vector-similarity dedup catches re-saves.

## Tunables

| Env var | Default | Purpose |
|---------|---------|---------|
| `LORE_AUTO_SAVE` | `true` | Master kill-switch. Set to `false` to disable both hooks without uninstalling. |
| `LORE_CAPTURE_N` | `10` | Tool-calls per batch trigger. Lower = more granular but more subagent calls. |
| `LORE_CAPTURE_SKIP` | (default skip list above) | CSV override of skipped tools. Empty = capture everything. |
| `LORE_CAPTURE_TRANSCRIPT_TURNS` | `50` | How much transcript context goes to the subagent. |
| `LORE_CAPTURE_RECENT_MEMORIES` | `20` | How many recent-memory titles to dedupe against. |
| `LORE_CAPTURE_DEBUG` | `false` | If true, hooks log every step to `errors.log`. |
| `LORE_API_URL` / `LORE_API_KEY` | per existing setup | Inherited from the existing retrieval hook. |

## Failure modes (all fail-open)

| Failure | Behavior |
|---------|----------|
| Subagent crash / OOM | Error logged to `errors.log`. Cursor not advanced. Next batch retries. |
| `claude` CLI not on PATH | Hook logs warning once per session, exits 0. Subsequent fires also no-op. |
| Buffer write failure (disk full, permission) | Log + skip, hook exits 0. Claude Code unaffected. |
| Concurrent subagent invocations | `flock` on `~/.lore/sessions/<session_id>/lock`; second invocation noops. |
| MCP server (Lore) unreachable | Subagent retries 3× with backoff; logs and exits if still unreachable. Cursor not advanced. |
| Subagent returns no `PROCESSED_THROUGH_SEQ` line | Cursor not advanced. Re-process on next fire (idempotent via Lore dedup). |
| Hook receives malformed input JSON | Log + skip, exit 0. |
| `LORE_AUTO_SAVE=false` | Hook exits 0 immediately on entry — no buffer writes, no subagent. |

## Testing

| Layer | Coverage |
|-------|----------|
| Unit | Skip-list filter, buffer append (with truncation), batch-counter math, cursor read/write atomicity. |
| Integration | `lore capture-extract` against a fixture buffer + transcript file: invoke a *mocked* `claude -p` that emits memories via a captured-MCP harness; assert correct calls to `mcp__lore__remember`. |
| Integration | Concurrency: two simultaneous `lore capture-extract` invocations; assert one no-ops via `flock`. |
| Integration | Failure paths: missing `claude` binary, missing buffer file, MCP unreachable, malformed transcript — all exit 0 with errors logged. |
| End-to-end (manual, not CI) | Real Claude Code session against a running `lore serve`. Verify memories accumulate, dedup works, no Claude Code disruption. |

CI cannot exercise the real `claude -p` subagent (requires Claude account + token billing). The integration tests mock at the subprocess boundary.

## Scope estimate

| Component | LOC |
|-----------|-----|
| `lore-capture-tool.sh` | ~80 |
| `lore-capture-stop.sh` | ~50 |
| `src/lore/cli/commands/capture.py` | ~200 |
| `src/lore/cli/__init__.py` (subparser registration) | ~10 |
| `src/lore/setup.py` (hook install) | ~40 |
| Unit + integration tests | ~150 |
| Docs (CHANGELOG, this spec, README snippet) | ~50 |
| **Total** | **~580 LOC** |

Estimated effort: 1 working day (~6 hours) for an attentive implementer following an explicit plan.

## Open questions for the implementation plan

1. **Subagent prompt — exact wording.** The shape above is sketched; the implementation plan should pin the final string and check it through a few realistic transcripts to validate the "be selective" instruction actually produces 0–3 memories rather than 10.
2. **Truncation policy for `input_summary` / `output_summary`.** ~200 chars sketched; the plan should decide what to keep vs drop for very long Bash outputs / Edit diffs. Likely: head + tail, joined with `…`.
3. **Should the cursor file be SQLite-row-on-`session_id` instead of a side file?** Side file is simpler today; SQLite would integrate with the migration from the existing `conversation_jobs` table if 6B/6E need to query session state.
4. **`Stop` hook vs `SubagentStop`.** Claude Code has both. Stop fires on the main agent's stop; SubagentStop fires after a Task tool subagent finishes. We want Stop only — verify in the implementation plan.
5. **Buffer rotation/cleanup.** Sessions accumulate buffer files in `~/.lore/sessions/`. Implementation plan should decide retention (e.g. delete after 7 days, or after the matching memories have been consolidated by Phase 6E).

## Out of scope (deferred to future phases)

- Equivalent hooks for OpenClaw / Cursor / Codex — different hook protocols, different subagent invocation. Each gets its own follow-up.
- Structured `{type, title, facts, narrative}` observation tier — Phase 6B.
- Cross-session deduplication beyond Lore's existing vector-similarity dedup — Phase 6E (Dreaming) handles longitudinal consolidation.
- Surfacing the buffer / inspecting captured observations via UI — could be useful for debugging but not required for the capture loop to work. Future tooling.
- Prompt caching for the subagent — `claude -p` doesn't expose cache controls; if 6A's costs become a problem, fold caching into the implementation plan or use the Claude Agent SDK in a follow-up.
