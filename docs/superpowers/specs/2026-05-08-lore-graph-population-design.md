# Lore Graph Population — Design

**Status:** Draft v2 — switched extraction provider from OpenAI to Claude (`claude -p`) per review feedback. Pending review.
**Date:** 2026-05-08

## Problem

The graph UI shows isolated nodes with no edges. Diagnosis confirmed four stacked gaps:

1. `routes/observations.py:create_observation` skips the `enrich_memory_async` fire-and-forget that `routes/memories.py:create_memory` already fires. The dream subagent saves through `remember_observation`, so all observations land unenriched.
2. Even when `enrich_memory_async` runs, it only writes `meta.enrichment` JSON via `store.enrich_memory_meta`. It does **not** populate the `entities` / `entity_mentions` / `relationships` tables — the structured graph the UI reads from.
3. The structured-graph code path (`Lore._update_graph` → `EntityManager`) is gated on `_knowledge_graph_enabled` + `_entity_manager`, both initialized only in local-SqliteStore mode in `Lore.__init__`. In HTTP-store mode (production), they're false / None, and `Lore.graph_backfill` silently `return 0`s.
4. Nothing auto-schedules backfill after dream/capture finishes — no Phase 5, no idle hook, no SessionEnd kick.

After this work: every memory and observation creation triggers structured entity / mention / relationship extraction on the server side, the tables fill in real time, and the UI graph shows actual edges. A backfill endpoint replays extraction over historical rows that pre-date this change.

## Non-goals

- Cross-org entity resolution. Each org's graph is independent.
- Embedding-similarity entity dedup. Case-insensitive name match + aliases is enough until we have evidence of false splits at scale.
- Real-time relationship inference from raw conversations. We extract from a single memory's `content + context` per call; cross-memory inference is the dream subagent's job, not the entity extractor's.
- Migrating off the existing `entities` / `entity_mentions` / `relationships` schema. We populate it; we don't redesign it.
- Removing the SDK-side `EntityManager` in this round. It stays, gated to local-store mode where it already works; HTTP mode gets the new server-side path.

## Design decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Sync vs async vs queued vs scheduled batch? | **Async fire-and-forget on create**, plus a manual `POST /v1/graph/backfill` for replay. Sync would put a 1–2s LLM call on every memory write; queued needs infra we don't have; scheduled batch leaves the graph stale between runs. Mirrors the existing `enrich_memory_async` pattern, so the reasoning model used by callers stays consistent. |
| 2 | Where does extraction live? | **New `services/graph_extraction.py`.** Server-side, called from `routes/memories.py` and `routes/observations.py` create handlers. The SDK-side `EntityManager` keeps doing what it does in local-store mode; HTTP-mode callers go through the service. |
| 3 | What does the LLM extract per call? | **Entities (`name`, `type`, `description`, `aliases`) + relationships (`subject_name`, `predicate`, `object_name`, `confidence`).** Mentions are derived: every extracted entity is a mention of `memory_id` with `mention_type='extracted'` and the LLM's per-entity confidence. Single LLM call per memory, JSON output. |
| 4 | Entity dedup strategy | **Case-insensitive name match scoped by `(org_id, type)` first; alias match second.** Lookup: lowercase the extracted name, search `entities` for a row where `lower(name) = ?` OR `? = ANY(lower(aliases))`. If found, attach to that `entity_id`. If not, insert. Embedding-similarity dedup is deferred — we can add it later as a `merge_entities` migration without changing the extraction path. |
| 5 | What about the 50+ already-unenriched memories? | **`POST /v1/graph/backfill`** endpoint that walks `memories WHERE id NOT IN (SELECT memory_id FROM entity_mentions)`, runs extraction on each, inserts results. Idempotent. Returns `{processed, skipped, failed}` counts. The existing `lore graph-backfill` CLI gets retargeted to call this endpoint instead of the SDK-side dead path. |
| 6 | Concurrency control on async fan-out | **A module-level `asyncio.Semaphore(N)` capped at `LORE_GRAPH_EXTRACTION_CONCURRENCY` (default 2)** so a 50-observation burst from dream finalize doesn't spawn 50 `claude -p` subprocesses in parallel. Cap is lower than the OpenAI-equivalent would be (4–8) because each `claude -p` is a full subprocess spawn, not a thin HTTP call. The task itself is `asyncio.create_task(extract_and_persist(...))`; the semaphore is acquired inside before the subprocess spawn. |
| 7 | Failure handling | **Log and swallow**, same as `enrich_memory_async`. Subprocess timeout (default 30s), JSON parse failure, claude not on PATH, or non-zero exit → log + skip. The memory has no graph edges until the next backfill run. No retry queue in v1; the backfill endpoint is the recovery mechanism. |
| 8 | Feature flag | **`LORE_GRAPH_EXTRACTION_ENABLED`** env (default: auto-on iff `shutil.which("claude")` returns a path — same probe dream/capture already use to decide whether to spawn). Explicitly settable to `false` to disable. No OpenAI dependency: extraction is offline once the user has Claude Code installed. |
| 9 | New MCP tool? | **No, not in v1.** HTTP route is enough — the dream subagent doesn't need fine-grained control. Could add `mcp__lore__extract_graph(memory_id)` later for explicit re-extraction during debugging; not blocking the rollout. |
| 10 | Idempotency / re-extraction | **Per-memory extraction is idempotent**: before insert, `DELETE FROM entity_mentions WHERE memory_id = ?` and `DELETE FROM relationships WHERE source_memory_id = ?`. Entities themselves are kept (other memories may reference them); only this memory's edges are rewritten. |
| 11 | Cost shape | One `claude -p` call per memory. Anthropic API cost depends on prompt+completion tokens, but each call is small (memory content is typically a few hundred tokens, output is JSON ≤ 1KB). Roughly $0.001–0.01 per memory on Sonnet-class; cheaper on Haiku. The user already pays for Claude Code, so no new vendor relationship. |
| 12 | Subprocess spawn vs HTTP-to-Anthropic? | **Subprocess `claude -p`**, not direct Anthropic SDK. Reasoning: (a) reuses dream/capture's existing infrastructure, (b) authentication piggybacks on the user's Claude Code login, (c) no new dependency on `anthropic` Python SDK in lore-sdk's core deps. Trade-off: subprocess spawn is slower than an HTTP call (~500ms-1s overhead) and harder to test (we mock `Popen`, not an SDK client). |
| 13 | Why not let the dream subagent extract entities inline? | **Considered and rejected.** The dream subagent's prompt is already complex (Phase 1–4); folding entity extraction into it would tangle two orthogonal concerns and make the prompt brittle. Also wouldn't help memories created outside the dream loop (manual `remember()`, direct HTTP POST, capture-extract). Keeping graph extraction as a separate server-side concern means any create path benefits, including the ones we haven't built yet. |

## Architecture

```
[create endpoint]                              [backfill endpoint]
  routes/memories.py                            routes/graph.py
  routes/observations.py                          POST /v1/graph/backfill
       │                                              │
       │ asyncio.create_task(...)                     │ for each memory_id without
       │                                              │ entity_mentions: …
       ▼                                              ▼
┌────────────────────────────────────────────────────────────────┐
│  services/graph_extraction.py                                  │
│    extract_and_persist(store, memory_id, content, context)     │
│      ├─ acquire semaphore (LORE_GRAPH_EXTRACTION_CONCURRENCY)  │
│      ├─ subprocess.Popen([                                     │
│      │     "claude", "-p", _build_extraction_prompt(           │
│      │                       content=content, context=context),│
│      │     "--output-format", "stream-json", "--verbose",      │
│      │     "--permission-mode", "default",                     │
│      │ ], stdout=PIPE, stderr=STDOUT, timeout=30s)             │
│      ├─ stream-parse stdout → final assistant message → JSON   │
│      │    → {entities: [...], relationships: [...]}            │
│      ├─ for each entity:                                       │
│      │    upsert by (org_id, type, lower(name) | aliases)      │
│      │    insert entity_mentions(memory_id, entity_id,         │
│      │           mention_type='extracted', confidence)         │
│      ├─ for each relationship:                                 │
│      │    resolve subject_entity_id + object_entity_id         │
│      │    insert relationships(source_memory_id=memory_id, …)  │
│      └─ release semaphore                                      │
└────────────────────────────────────────────────────────────────┘
       │
       ▼
[entities] ←─ entity_mentions ─→ [memories]
       └────── relationships ─→ [memories] (source_memory_id)
                              ─→ [entities]  (subject + object)
                              [UI reads these tables]
```

**Subprocess flags:** mirror the dream/capture spawn pattern (PRs #48, #49) — `--output-format stream-json --verbose` so the output is parseable. `--permission-mode default` (not `bypassPermissions`) because the extractor only emits text JSON and never calls MCP tools — restricting it keeps the trust posture minimal.

### Invariants

- **Append-only graph state.** Backfill never deletes entities (they may be referenced by other memories). Only this memory's mentions + outgoing relationships are rewritten on re-extraction.
- **Org-scoped entities.** All inserts include `org_id`; lookups filter by `org_id`. No cross-org entity sharing.
- **Idempotent backfill.** Calling `POST /v1/graph/backfill` twice yields the same graph (the second call skips memories that already have mentions, but if `force=true` it deletes-then-re-extracts).
- **Bounded concurrency.** No more than `LORE_GRAPH_EXTRACTION_CONCURRENCY` (default 4) extractions in flight per process.

## Components

### Schema

No new tables. Existing schema (`entities`, `entity_mentions`, `relationships`) is sufficient. Migration risk: zero.

### LLM extraction prompt

A single deterministic prompt builder in `src/lore/services/graph_extraction.py`:

```
You are an entity-extraction worker. Read the memory below and return
a single JSON object. Do not call any tools. Do not include any text
outside the JSON.

Memory content: {content}
{?context: Memory context: {context}}

Schema:

  {
    "entities": [
      {"name": "<canonical name>",
       "type": "<one of: person, project, technology, concept, organization, location, other>",
       "description": "<one line>",
       "aliases": ["<other ways this is referenced>"],
       "confidence": 0.0-1.0}
    ],
    "relationships": [
      {"subject": "<name from entities[]>",
       "predicate": "<verb-phrase, kebab-case>",
       "object": "<name from entities[]>",
       "confidence": 0.0-1.0}
    ]
  }

Only extract entities and relationships explicitly stated. Do not infer.
Do not extract pronouns or indefinite references.
Empty arrays are fine. Return JSON, nothing else.
```

The prompt's deterministic shape is the testing seam: tests inject a fake `Popen` whose stdout is canned stream-json output containing the JSON in the assistant's final message. No live `claude` invocation in CI.

### Service: `services/graph_extraction.py`

```python
_SEM = asyncio.Semaphore(int(os.environ.get("LORE_GRAPH_EXTRACTION_CONCURRENCY", "2")))
_TIMEOUT_S = float(os.environ.get("LORE_GRAPH_EXTRACTION_TIMEOUT", "30"))

async def extract_and_persist(
    store: Store, *, org_id: str, memory_id: str,
    content: str, context: Optional[str],
    spawn_fn: Optional[Callable[..., subprocess.Popen]] = None,
) -> ExtractionResult:
    """Spawn `claude -p` with a deterministic extraction prompt, parse
    the JSON from the final assistant message, persist entities +
    mentions + relationships. Idempotent: deletes this memory's
    existing edges first. Logs and swallows on failure
    (returns ExtractionResult with .error set)."""

def _spawn_claude(prompt: str) -> subprocess.Popen:
    """Default spawn fn — kept separate so tests can monkeypatch the
    whole subprocess seam without monkeypatching subprocess.Popen
    globally. Mirrors the flag set used in cli/commands/dream.py
    (post PR #48, #49) but with --permission-mode=default since the
    extractor doesn't need MCP tools."""

@dataclass
class ExtractionResult:
    memory_id: str
    entities_inserted: int
    entities_reused: int
    mentions_inserted: int
    relationships_inserted: int
    error: Optional[str] = None
```

The `spawn_fn` parameter is the testing seam — pass a fake `Popen` factory in tests; production code uses `_spawn_claude`. No `anthropic` SDK dependency added to lore-sdk.

### Store protocol additions (server-side ops)

```python
# Extends EntityOps / GraphOps slice on PostgresStore + SqliteStore

async def upsert_entity(
    self, *, org_id: str, name: str, entity_type: str,
    description: Optional[str], aliases: Sequence[str],
) -> Tuple[str, bool]:  # (entity_id, inserted_new)
    """Lookup-then-insert by (org_id, lower(name)) OR alias match.
    Returns (entity_id, True) if inserted; (entity_id, False) if reused."""

async def replace_memory_mentions(
    self, memory_id: str, mentions: Sequence[NewMention],
) -> int:
    """DELETE existing mentions for this memory; INSERT the new set.
    Returns count inserted."""

async def replace_memory_relationships(
    self, memory_id: str, relationships: Sequence[NewRelationship],
) -> int:
    """DELETE existing relationships for this memory; INSERT the new set."""

async def list_memories_without_mentions(
    self, org_id: str, *, limit: int = 1000,
) -> Sequence[StoredMemory]:
    """Memories that have no row in entity_mentions. Used by backfill."""
```

### Routes

| Path | What it does |
|------|--------------|
| `routes/memories.py:create_memory` | After `enrich_memory_async` task is fired, fire a second `extract_and_persist` task gated on `_graph_extraction_enabled()`. |
| `routes/observations.py:create_observation` | Add the same enrich + extract task pair (currently fires neither). |
| `routes/graph.py` (new) | `POST /v1/graph/backfill` body `{limit?, force?: bool}`. Walks rows, runs `extract_and_persist`, returns count summary. |

### CLI

`src/lore/cli/commands/graph.py:cmd_graph_backfill` — currently calls the dead SDK path. Retarget to `POST /v1/graph/backfill` via `HttpStore._request`. Output unchanged (just prints the count).

### MCP server

No changes in v1. The dream subagent doesn't need to call extraction directly; the create-time fire-and-forget covers its writes.

## Phasing (PR plan)

1. **PR A — foundation (`services/graph_extraction.py` + store ops + tests).** Pure compute and persistence; no route wiring yet. Stub `LLMClient` injected in tests; live service auto-builds the real one. Adds `upsert_entity`, `replace_memory_mentions`, `replace_memory_relationships`, `list_memories_without_mentions` to both Postgres and SQLite stores. Tests: round-trip through the parametrized `store` fixture.

2. **PR B — wiring + backfill endpoint.** `routes/memories.py` and `routes/observations.py` fire `asyncio.create_task(extract_and_persist(...))`. New `routes/graph.py` exposes `POST /v1/graph/backfill`. Retarget `lore graph-backfill` CLI. Tests: route fires the task; backfill processes only rows without mentions; `force=true` rewrites them; concurrency cap respected under burst.

3. **PR C — UI polish (optional).** If the UI graph still feels sparse after extraction is live, address: (a) edge-confidence threshold for display, (b) entity-type color coding, (c) "show isolated nodes" toggle. Out of scope for getting edges to appear; in scope for making the populated graph readable.

Each PR ships green CI independently. PR B is the one users feel — after merge + reinstall, the next memory created (or the next dream finalize) populates the graph.

## Test plan

### Unit / service-layer

- **`extract_and_persist` happy path** with `spawn_fn` returning a fake `Popen` whose stdout has stream-json containing 2 entities + 1 relationship in the final assistant message → assert 2 entities upserted, 2 mentions inserted, 1 relationship inserted.
- **Entity dedup**: pre-seed an entity with name `"Pinecone"`, run extraction returning `"pinecone"` → assert no new entity row, mention attached to existing entity.
- **Alias dedup**: pre-seed entity with alias `"PC"`, run extraction returning `"PC"` → resolves to the existing entity.
- **Idempotency**: run `extract_and_persist` twice on the same memory with the same stub output → assert second run replaces, doesn't double.
- **Failure swallow on subprocess timeout**: spawn_fn returns a `Popen` whose `wait` raises `TimeoutExpired` → result has `error` set, no rows inserted, no exception bubbles.
- **Failure swallow on JSON parse**: stub stdout has malformed JSON in the assistant message → result has `error` set, no rows inserted.
- **Failure swallow on missing claude**: monkeypatch `shutil.which("claude")` to return None → `extract_and_persist` no-ops with a "claude not on PATH" error result.
- **Concurrency cap**: fire 10 `extract_and_persist` tasks with `LORE_GRAPH_EXTRACTION_CONCURRENCY=2` and a slow stub → assert max 2 spawn_fn invocations in flight at any moment.
- **Spawn args sanity**: assert spawn_fn was called with `--output-format stream-json --verbose --permission-mode default` (regression guard for the dream/capture flag-saga: PRs #48 and #49 burned us once already).

### HTTP

- `POST /v1/memories` with `enrich=true` and graph extraction enabled → background task fires; after polling, `entities` and `entity_mentions` rows exist.
- `POST /v1/observations` → same.
- `POST /v1/graph/backfill` on a fresh DB with 5 memories without mentions → returns `processed=5`, mentions table has 5+ rows.
- `POST /v1/graph/backfill` second time → returns `processed=0, skipped=5` (no rows already covered).
- `POST /v1/graph/backfill {force: true}` → re-runs, replaces, idempotent result.
- Auth: backfill requires `writer` or `admin` role; reader gets 403.

### Integration

- Manual: `lore session-finalize <session-id>` on a buffered session → memories saved → polling shows `entities` and `entity_mentions` populating within a few seconds.
- Manual: open the UI graph → edges visible.

## Out-of-scope follow-ups (named for tracking)

- **Embedding-similarity entity dedup.** Add when false-split rate matters.
- **Cross-memory relationship inference from the dream subagent.** A separate phase could let the dream worker propose relationships across multiple memories it just consolidated.
- **Graph-aware retrieval signals.** Phase 6C already weights graph hits; we could weight by entity centrality once the graph is populated.
- **`mcp__lore__extract_graph` MCP tool.** Add when there's a debugging use case.

## Open questions

None blocking. Three worth flagging in review:

1. **Should extraction default-on when `claude` is on PATH, or default-off?** Default-on gives the user the "graph populates itself" experience they expected. Default-off is safer if Claude API costs are a concern (each extraction is one API call). Lean default-on; user can set `LORE_GRAPH_EXTRACTION_ENABLED=false` explicitly to disable.

2. **Should the dream subagent's prompt mention the new behavior?** Probably not — the subagent doesn't need to know about graph extraction; the route layer handles it transparently. Adds prompt complexity for no behavioral gain.

3. **Subprocess overhead acceptable, or should we batch?** A `claude -p` spawn is ~500ms-1s overhead before the actual LLM work. For per-memory extraction this is acceptable — it's async and bounded by the concurrency semaphore. If the user reports the dream-finalize backfill feels slow on large session buffers, we can revisit by adding a "process N memories per spawn" batched mode (spec'd as a deferred follow-up, not v1).
