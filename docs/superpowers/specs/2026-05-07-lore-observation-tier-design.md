# Lore Observation Tier (Phase 6B) — Design

**Status:** Approved (autonomous trust mandate), pending implementation.
**Date:** 2026-05-07
**Author:** Amit Paz, with Claude.

## Goal

Distinguish raw, high-volume **observations** captured automatically by the Phase 6A pipeline from polished **memories** that the user explicitly saves or that survive a future consolidation pass (Phase 6E). Today every memory looks the same regardless of whether it came from a careful `remember("user prefers X", type="preference")` call or from an automated subagent extraction.

After this phase, Lore stores the same memory shape it already does (no DB migration), but observations carry a structured `{title, facts, narrative}` block in their `meta` field and a dedicated `type="observation"` discriminator. New MCP tool `remember_observation` makes it cheap for the auto-capture subagent to emit structured observations without learning a new pipeline.

## Non-goals

- Schema migrations. Observations live inside the existing `memories.meta` JSON column.
- Auto-promotion of observations to lessons/facts. That's Phase 6E (Dreaming).
- Retrieval changes. The hybrid scoring of Phase 6C will treat observations and memories with different defaults; for now, both retrieve uniformly.
- New storage tables. Tempting (`observations` as a separate table with TTL), but YAGNI for v1 — the existing `memories` retention machinery already supports TTL via `expires_at`.

## Context

This is sub-project 2 of the 6-phase memory overhaul.

| # | Name | Status |
|---|------|--------|
| 6A | Auto-capture pipeline | ✅ Shipped (PR #36) |
| **6B** | **Observation tier + structured schema** *(this spec)* | In progress |
| 6C | Hybrid retrieval | Planned |
| 6D | Progressive disclosure | Planned |
| 6E | Dreaming | Planned |
| 6F | Temporal graph reasoning | Planned |

## Design decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Separate `observations` table or extend `memories`? | Extend. Structured fields go in `memories.meta` JSON. Zero migration. |
| 2 | New `type` value? | Yes — `observation`. Joins existing six (`lesson, fact, preference, pattern, convention, note`). |
| 3 | New MCP tool? | Yes — `mcp__lore__remember_observation(title, facts, narrative, tags?, project?)`. Coexists with `remember()`. |
| 4 | Should the auto-capture subagent emit structured observations? | Yes. Update the 6A prompt to prefer `remember_observation` for non-polished extractions and reserve `remember(type=lesson|fact|preference|...)` for cases where the subagent is confident. |
| 5 | TTL / retention defaults? | None in 6B. The existing `expires_at` mechanism applies. Phase 6E will set defaults like "observations expire after 30 days unless promoted." |
| 6 | Service-layer surface? | New `services.observations.create_observation(...)` thin wrapper that calls `services.memories.create_memory` with the right meta + type. |
| 7 | Inspection CLI? | New `lore observations list/show` for debugging. Read-only. Uses existing `list_memories(filter type=observation)`. |

## Architecture

```
auto-capture subagent
       │
       │  emits structured observation:
       │    title    = "Phase 6A bootstrap quirk"
       │    facts    = ["bootstrap skips :memory: by default", "force_for_memory=True opt-in"]
       │    narrative= "Investigated why AsyncLore tests failed; found bootstrap_solo_if_empty
       │                 had a :memory: skip-clause. Added force_for_memory flag to opt in."
       ▼
mcp__lore__remember_observation
       │
       ▼
services.observations.create_observation()
       │
       ▼  (sets type='observation' + meta={title, facts, narrative})
services.memories.create_memory()
       │
       ▼
Existing storage path (vector + JSON meta)
```

### Invariants

- **Backward compatible.** No schema migration; existing memories unchanged.
- **No new RPC endpoint.** `POST /v1/memories` already accepts arbitrary `meta` and `type`. The MCP tool calls it with the right shape.
- **Type discriminator is authoritative.** Anything with `type='observation'` is an observation, regardless of meta presence. Anything with structured `meta.title/facts/narrative` but a different type is a regular memory that happens to carry richer metadata (e.g. an explicitly-saved lesson with extra facts).

## Components

### New / modified files

| Path | Change |
|------|--------|
| `src/lore/services/observations.py` | **New.** `create_observation(store, NewObservation) -> StoredMemory`. ~50 LOC. |
| `src/lore/persistence/types.py` | **Add** `NewObservation` frozen dataclass: `org_id, title, facts (Sequence[str]), narrative, tags, project, source`. |
| `src/lore/server/routes/observations.py` | **New.** `POST /v1/observations` thin wrapper around the service. ~40 LOC. |
| `src/lore/server/app.py` | Register `observations_router`. |
| `src/lore/mcp/server.py` | **Add** `remember_observation(title, facts, narrative, tags?, project?)` MCP tool. ~30 LOC. |
| `src/lore/cli/commands/observations.py` | **New.** `lore observations list / show <id>`. ~80 LOC. |
| `src/lore/cli/__init__.py` | Register `observations` subparser. |
| `src/lore/setup.py` | Update `LORE_CAPTURE_TOOL_HOOK_SCRIPT` and `LORE_CAPTURE_STOP_HOOK_SCRIPT` prompts to prefer `remember_observation` over `remember` for non-polished extractions. |
| `tests/test_observations.py` | **New.** Round-trip + MCP tool + service-layer tests. |
| `CHANGELOG.md`, this spec | Phase 6B entry. |

### Type discriminator + meta shape

```python
# Existing types stay the same; add one.
ALLOWED_TYPES = {"lesson", "fact", "preference", "pattern", "convention", "note", "observation"}

# meta payload for an observation:
{
  "title": "Phase 6A bootstrap quirk",                           # short label, ~80 chars
  "facts": ["bootstrap skips :memory: by default", "..."],       # bullet-style atoms
  "narrative": "Investigated why AsyncLore tests failed ...",    # the prose context
  "captured_by": "auto",                                         # "auto" | "manual"
  "session_id": "abc123",                                        # source session, optional
}
```

The `content` column gets the `narrative` (so existing vector recall finds it on the same axis as polished memories). `title` and `facts` are searchable later via Phase 6C's FTS layer.

## Service-layer surface

```python
# src/lore/services/observations.py

@dataclass(frozen=True, slots=True)
class NewObservation:
    org_id: str
    title: str
    facts: Sequence[str]
    narrative: str
    tags: Sequence[str] = ()
    project: Optional[str] = None
    source: Optional[str] = None  # e.g. "claude-code-capture"
    captured_by: str = "auto"     # "auto" | "manual"
    session_id: Optional[str] = None

async def create_observation(store, obs: NewObservation, embed) -> StoredMemory:
    # narrative goes into content; title+facts+narrative duplicated in meta for retrieval.
    embedding = await embed(f"{obs.title}\n{obs.narrative}")
    new_mem = NewMemory(
        org_id=obs.org_id,
        content=obs.narrative,
        context=obs.title,                          # the existing context column gets the title
        tags=tuple(obs.tags),
        confidence=0.5,
        source=obs.source or "observation",
        project=obs.project,
        embedding=embedding,
        meta={
            "title": obs.title,
            "facts": list(obs.facts),
            "narrative": obs.narrative,
            "captured_by": obs.captured_by,
            "session_id": obs.session_id,
            "memory_type": "observation",            # explicit discriminator inside meta too
        },
    )
    stored = await store.insert_memory(new_mem)
    # Set type='observation' via existing update path (Lore stores type in meta today).
    return stored
```

Note: Lore's existing `MemoryType` field is stored in `memories.meta.memory_type` already (look at `services/memories.py`), so the service uses the same path.

## MCP tool

```python
@mcp.tool()
def remember_observation(
    title: str,
    facts: List[str],
    narrative: str,
    tags: Optional[List[str]] = None,
    project: Optional[str] = None,
) -> str:
    """Record a structured observation extracted from a session.

    Use this when capturing a multi-faceted event (a debugging session, a
    decision with trade-offs, a workflow pattern). Use the simpler
    remember(content, type=...) for single-fact memories.

    Returns the memory ID.
    """
    lore = _get_lore()
    obs = NewObservation(
        org_id=lore.org_id,
        title=title,
        facts=facts,
        narrative=narrative,
        tags=tags or [],
        project=project,
    )
    stored = asyncio.run(create_observation(lore.store, obs, lore.embed))
    return f"Saved observation {stored.id}"
```

## Auto-capture prompt updates (Phase 6A integration)

Update the prompt template in `LORE_CAPTURE_TOOL_HOOK_SCRIPT` and `LORE_CAPTURE_STOP_HOOK_SCRIPT` to:

1. Prefer `remember_observation(title, facts, narrative, tags?, project?)` for the typical extraction case (raw observations the subagent isn't 100% sure should be polished memories).
2. Fall back to `remember(content, type=lesson|fact|preference|...)` only when the subagent is confident the item is a clean, single-fact polished memory.
3. The "Be selective" guidance now reads "0–3 observations OR 0–1 polished memories per batch."

This keeps the volume manageable while letting the subagent push high-value items straight into the polished tier.

## CLI inspection

```
lore observations list [--limit 20] [--project PROJECT] [--since DURATION]
lore observations show <memory_id>
```

`list` returns title + first fact + age. `show` prints the full structured payload.

## Tests

| Layer | Coverage |
|-------|----------|
| Unit | `NewObservation` dataclass invariants, content-vs-narrative round-trip, type-discriminator location. |
| Integration | Service + Store round-trip on both backends; assert meta payload preserved. |
| Integration | MCP tool calls (mocked Lore client) hit the right HTTP route with the right body. |
| Integration | `POST /v1/observations` 200 with valid body, 400 with missing required fields, auth-failure paths. |
| Integration | Auto-capture prompt: render the updated `LORE_CAPTURE_TOOL_HOOK_SCRIPT` and assert it contains `remember_observation`. |
| CLI | `lore observations list` returns the saved observation (round-trip via in-memory SQLite). |

## Scope estimate

| Component | LOC |
|-----------|-----|
| services/observations.py | ~50 |
| persistence/types.py (NewObservation) | ~25 |
| server/routes/observations.py + app wiring | ~50 |
| mcp/server.py (remember_observation) | ~40 |
| cli/commands/observations.py | ~80 |
| setup.py (prompt update) | ~30 |
| Tests | ~200 |
| Docs | ~30 |
| **Total** | **~505 LOC** |

## Out of scope

- Auto-promotion (observation → lesson/fact). Phase 6E.
- Observation-specific TTL defaults. Phase 6E.
- Different vector model for observations. Same default.
- Observation-only retrieval profile. Phase 6C may add this.
