# Lore Temporal Reasoning (Phase 6F) — Design

**Status:** Approved (autonomous trust mandate), pending implementation.
**Date:** 2026-05-07

## Goal

Make Lore aware of *time-of-truth*: memories can be superseded by newer ones without being deleted. Queries can ask "what was true about X at time T?" and get the answer that was canonical *then*. When the auto-capture pipeline sees a correction ("actually, X is now Y"), it marks the old memory as superseded rather than forgetting it.

After 6F: Lore preserves history. Stale facts don't pollute current retrieval (because superseded memories drop in score) but stay queryable for audit / "what changed?" questions.

## Non-goals

- Bitemporal storage on every memory column. The existing `relationships.valid_from/valid_until` covers graph edges; we add **supersession** (memory-level versioning) without rebuilding storage.
- Automated contradiction detection between every pair of memories. Conflict detection is hint-driven (the dreaming pass + auto-capture subagent flag candidates; a human or the dream worker confirms).
- Time travel for analytics / SLO. Phase 4C / 6E already cover periodic state.

## Design decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | New table or extend `memories`? | **New `memory_supersessions` table.** Append-only audit log: `(memory_id, superseded_by, reason, ts, agent)`. Doesn't modify `memories`; reads are LEFT JOIN. Zero migration risk to existing data. |
| 2 | What does "superseded" mean for retrieval? | Score multiplier: superseded memories get `score *= 0.1` in hybrid retrieval (Phase 6C). Still findable for explicit `at_time` queries; suppressed in normal recall. |
| 3 | What about the existing `relationships.valid_from/valid_until`? | Already correct. Activate it: `list_relationships_for_entity` filters by valid-now unless caller passes `at` param. |
| 4 | `at_time` query shape | `GET /v1/memories/at_time?at=ISO_DATE&entity=X` returns memories about entity X that were valid (non-superseded) at the given timestamp. |
| 5 | New MCP tool? | Yes — `mcp__lore__supersede(memory_id, superseded_by, reason)`. Explicit; the subagent calls this when it detects a correction. |
| 6 | Auto-capture prompt integration | Prompt addition: "When you see a correction ('actually X is Y now', 'we changed X'), prefer `supersede(old_memory_id, new_memory_id, reason)` over `forget(old)`. Use `recall(query=old_topic)` to find candidates for old_memory_id." |
| 7 | Conflict detection | Hint-driven, not exhaustive. The Phase 6E dream subagent's prompt now includes: "When you see two recent memories that contradict each other, call supersede() on the older one." |
| 8 | Idempotency | Supersession is monotonic — once a memory is superseded, it stays superseded. Re-supersession (changing `superseded_by`) appends a new row to the audit log. |

## Architecture

```
auto-capture subagent / dream subagent / explicit caller
       │
       ▼
mcp__lore__supersede(old_id, by_id, reason)
       │
       ▼  POST /v1/memories/<old_id>/supersede {by, reason}
       │
       ▼
services/temporal.py:supersede_memory(store, old_id, by_id, reason)
       │
       ▼
INSERT INTO memory_supersessions (memory_id, superseded_by, reason, ts, agent)

Retrieval path (Phase 6C hybrid_retrieve):
       │
       ▼  for each candidate, LEFT JOIN memory_supersessions
       │
       ▼  if superseded: score *= 0.1
       │
       ▼  filter post-RRF as usual
```

### Invariants

- **Append-only.** Supersession events never delete; they only add rows.
- **Latest wins.** A memory is "superseded" iff its latest row in `memory_supersessions` has `superseded_by IS NOT NULL`. The audit log shows the chain.
- **Score suppression, not filtering.** Hybrid retrieval drops the score (×0.1), doesn't omit. `min_score` then naturally filters out weak survivors. Explicit `at_time` queries override this.

## Components

### Schema

| Path | What it adds |
|------|--------------|
| `migrations/023_memory_supersessions.sql` | `CREATE TABLE memory_supersessions (id BIGSERIAL PRIMARY KEY, memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE, superseded_by TEXT REFERENCES memories(id) ON DELETE SET NULL, reason TEXT, ts TIMESTAMPTZ NOT NULL DEFAULT now(), agent TEXT NOT NULL DEFAULT 'auto')` + `CREATE INDEX ON memory_supersessions (memory_id, ts DESC)`. |
| `migrations_sqlite/023_memory_supersessions.sql` | Equivalent SQLite schema. |

### Store protocol additions

```python
# Extends MemoryOps slice

async def record_supersession(
    self,
    memory_id: str,
    *,
    superseded_by: Optional[str],
    reason: Optional[str],
    agent: str = "auto",
) -> None:
    """Record an event in the supersession audit log."""

async def is_superseded(
    self,
    memory_id: str,
    *,
    at: Optional[datetime] = None,
) -> bool:
    """True iff the memory's latest supersession event before `at` (default now)
    has superseded_by != NULL."""

async def get_supersession_chain(
    self,
    memory_id: str,
) -> Sequence[StoredSupersession]:
    """Full audit trail (oldest first)."""

async def list_memories_at_time(
    self,
    org_id: str,
    *,
    at: datetime,
    entity_name: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
) -> Sequence[StoredMemory]:
    """Memories that existed and were not superseded at the given timestamp.
    If `entity_name` is given, filter via the mentions table."""
```

### Service layer

```python
# src/lore/services/temporal.py

async def supersede_memory(
    store, memory_id: str, *, superseded_by: Optional[str],
    reason: Optional[str], agent: str = "auto",
) -> None: ...

async def memories_at_time(
    store, org_id: str, *, at: datetime,
    entity_name: Optional[str] = None,
    type_filter: Optional[str] = None,
    limit: int = 20,
) -> Sequence[StoredMemory]: ...
```

### Hybrid retrieval integration (Phase 6C delta)

In `services/retrieve._hybrid_recall`, add a step after RRF fusion:

```python
# Annotate with supersession state; multiply score by 0.1 if superseded.
superseded_set = await store.are_superseded({m.id for m, *_ in fused})
for i, (memory, base_score) in enumerate(fused):
    if memory.id in superseded_set:
        fused[i] = (memory, base_score * 0.1)
```

`are_superseded` is a batch helper added to MemoryOps for efficiency.

### HTTP routes

| Path | Method | Behavior |
|------|--------|----------|
| `POST /v1/memories/{id}/supersede` | body `{by: id_or_null, reason: text}` | Record supersession event. |
| `GET /v1/memories/at_time?at=ISO&entity=X&type=fact` | | Return memories valid at given time. |
| `GET /v1/memories/{id}/supersession-chain` | | Audit trail. |

### MCP tools

```python
@mcp.tool()
def supersede(memory_id: str, superseded_by: Optional[str] = None,
              reason: Optional[str] = None) -> str: ...

@mcp.tool()
def list_at_time(at: str, entity: Optional[str] = None,
                  type: Optional[str] = None, limit: int = 20) -> str: ...

@mcp.tool()
def supersession_chain(memory_id: str) -> str: ...
```

### Auto-capture + dreaming prompt updates

`src/lore/cli/commands/capture.py` — `_build_prompt` adds:

```
If you see a correction in the conversation (e.g. "actually X is Y now",
"we changed our approach", "I no longer prefer X"), prefer
mcp__lore__supersede(old_memory_id, new_memory_id, reason) over
mcp__lore__forget(old_memory_id). Use mcp__lore__recall(query=topic) to
find the old memory's id first.
```

`src/lore/cli/commands/dream.py` — Phase 3 (Consolidate) prompt adds the same instruction PLUS: "If two non-superseded memories contradict, call supersede() on the older one with reason='contradicted by mem_NEW'."

## Tests

| Layer | Coverage |
|-------|----------|
| Migrations | 023 applies cleanly on both backends. |
| Contract | `record_supersession`, `is_superseded`, `get_supersession_chain`, `list_memories_at_time`, `are_superseded` round-trip on both backends. |
| Service | `supersede_memory` happy path + idempotent re-supersession. |
| Service | `memories_at_time` with entity filter. |
| Retrieval | Hybrid recall: a superseded memory's final score is `≤0.1× base`. Confirms suppression without filtering. |
| HTTP | All three routes (post supersede, at_time, chain). |
| MCP | All three tools (supersede, list_at_time, supersession_chain). |
| Prompts | `_build_prompt` (capture) and dream prompt template both contain the supersede guidance string. |

## Scope

| Component | LOC |
|-----------|-----|
| Migrations 023 (PG + SQLite) | ~50 |
| Persistence types + protocol + impls | ~280 |
| `services/temporal.py` | ~100 |
| Hybrid retrieval delta | ~40 |
| Routes | ~120 |
| MCP tools | ~80 |
| Prompt updates | ~20 |
| Tests | ~400 |
| Docs | ~30 |
| **Total** | **~1120** |

## Out of scope

- Automatic contradiction detection. Hint-driven by 6A capture + 6E dream.
- Time-travel API for SLO/analytics tables. Phase 4C scope; out of band.
- Multi-version graph traversal ("show me the entity-X subgraph as of yesterday"). Possible follow-up; relationships already have valid_from/valid_until — wire it later.
- UI for visualizing supersession chains. CLI-only.
