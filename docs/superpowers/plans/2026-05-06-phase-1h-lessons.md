# Phase 1H — Lessons Slice (MemoryOps extensions) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task below is dispatched to a fresh implementer subagent with task-specific code spelled out in the dispatch prompt.

**Goal:** Apply the Phase 1A–1G pattern to the lessons slice. After this plan: every handler in `routes/lessons.py` calls services exclusively. The lessons routes wire-shape (`/v1/lessons` URL, `problem`/`resolution` Pydantic fields) is preserved; the `lessons` Postgres view (added in migration 009 as a backward-compat wrapper around `memories`) is no longer touched directly — the service uses `MemoryOps` against the `memories` table with field translation at the route+service boundary.

**Architecture:** No new architecture. Same Store / Services / Routes layering as 1A–1G. 3 new methods on `MemoryOps` (extending the existing slice — no new Store group); one new service module (`services/lessons.py`); 9 route handlers refactored.

**Tech Stack:** Same as 1A–1G. No new runtime deps. Postgres test DB at `localhost:5432` / `lore_test` reused.

**Spec reference:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`. Phase 1G plan: `docs/superpowers/plans/2026-05-06-phase-1g-conversations.md` — the immediate template.

**Why no new Store group:** lessons are memories. Migration 009 made `lessons` a view backed by `memories` with column aliases (`problem`→`content`, `resolution`→`context`). All operations on `lessons` already route to `memories` via INSTEAD OF rules. Treating lessons as a separate Store domain would duplicate the abstraction needlessly. Instead we extend `MemoryOps` with 3 new methods that lessons.py needs (paginated list with text+count, export with embedding, upsert with embedding) and translate field names at the route+service boundary.

---

## File structure

### Created in this plan

| Path | Responsibility |
|---|---|
| `src/lore/services/lessons.py` | Lessons service module — owns 9 functions wrapping MemoryOps + field translation (`problem`↔`content`, `resolution`↔`context`), the time-decay scoring formula for search, and project-scoping pre-checks |
| `tests/persistence/test_contract_lessons.py` | Contract tests for the 3 new MemoryOps methods + the MemoryFilter extension |
| `tests/services/test_lessons.py` | Service tests for create/get/update/delete/search/list/export/import + project scoping |
| `tests/server/test_lessons_routes.py` | Route tests for the 9 lessons handlers using FakeStore mocks |

### Modified in this plan

| Path | Change |
|---|---|
| `src/lore/persistence/types.py` | Add `ExportedMemory` dataclass; extend `MemoryFilter` with `text_query: Optional[str]` and `min_reputation: Optional[int]` fields |
| `src/lore/persistence/protocol.py` | Add 3 new MemoryOps methods (`list_memories_paginated`, `list_memories_with_embeddings`, `upsert_memory_with_embedding`) |
| `src/lore/persistence/postgres.py` | Implement all 3 new methods on `PostgresStore` |
| `src/lore/persistence/__init__.py` | Re-export `ExportedMemory` |
| `src/lore/server/routes/lessons.py` | All 9 handlers call services; drop `_row_to_response`, `_scope_filter`, all inline SQL |
| `scripts/check_routes_no_sql.py` | Add `routes/lessons.py` to `MIGRATED_ROUTES` (13 → 14) |
| `tests/persistence/test_types.py`, `tests/persistence/test_protocol.py` | Extend to cover new dataclass + protocol methods + filter fields |
| `tests/server/test_lessons.py` (existing, if it exists) | Redirect mocks if any test depends on inline-SQL paths |
| `CHANGELOG.md`, `docs/architecture.md` | Note lessons slice landed; route count → 14 |

### Out of scope (deferred)

- **Refactoring existing `MemoryOps.update_memory` to support atomic vote increments** — kept separate. The lessons service makes two calls (update_memory for non-vote fields + vote_memory for vote changes); not atomic across the two calls. Document as a known concurrency relaxation.
- **The `lessons` Postgres view + INSTEAD OF rules** — stay as backward-compat for any direct DB clients. Don't touch.
- **The other 7 unmigrated route files** (`sharing.py`, `slo.py`, `policies.py`, `topics.py`, `recent.py`, `audit.py`, `analytics.py`). Future phases.
- **`lore/server/auth.py` middleware** — its own future slice.
- **The `body.context` field on `LessonCreateRequest`** is preserved on the wire but never stored anywhere meaningful (the `context` view column is always NULL per migration 009; the underlying `memories.context` column gets `body.resolution`, not `body.context`). The current route also has this no-op behavior — preserved for wire stability.

---

## Tasks (one task = one commit)

### Foundation — types, protocol

**T1 — Add `ExportedMemory` dataclass + extend `MemoryFilter`**

Add to `src/lore/persistence/types.py`:

```python
@dataclass(frozen=True, slots=True)
class ExportedMemory:
    """Memory shape for bulk export — includes embedding + all wire-relevant fields."""
    id: str
    org_id: str
    content: str
    context: Optional[str]
    tags: Sequence[str]
    confidence: float
    source: Optional[str]
    project: Optional[str]
    embedding: Optional[Sequence[float]]
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]
    upvotes: int
    downvotes: int
    meta: Mapping[str, Any]
```

Place under a new `# ── Lessons slice dataclasses ───` section comment.

Extend `MemoryFilter` with two new optional fields (the existing dataclass already has `org_id`, `project`, `type`, `tier`, `tags`, `since`):

```python
text_query: Optional[str] = None       # ILIKE search across content + context
min_reputation: Optional[int] = None   # reputation_score >= N
```

Re-export `ExportedMemory` from `__init__.py`.

Tests in `tests/persistence/test_types.py`:
- 4 tests for `ExportedMemory` (defaults, full, frozen, slots).
- 2 tests for the `MemoryFilter` additions (default None for both new fields; full population with text_query + min_reputation).

Commit: `feat(persistence): add ExportedMemory + extend MemoryFilter for lessons`

**T2 — Extend `Store` Protocol with 3 new MemoryOps methods**

Add inside the existing `# ── MemoryOps ────` section of `src/lore/persistence/protocol.py`:

```python
async def list_memories_paginated(
    self, filter: MemoryFilter, *, limit: int = 50, offset: int = 0,
) -> tuple[int, Sequence[StoredMemory]]: ...

async def list_memories_with_embeddings(
    self, filter: MemoryFilter,
) -> Sequence[ExportedMemory]: ...

async def upsert_memory_with_embedding(
    self,
    *,
    memory_id: str,
    org_id: str,
    content: str,
    context: Optional[str],
    tags: Sequence[str],
    confidence: float,
    source: Optional[str],
    project: Optional[str],
    embedding: Optional[Sequence[float]],
    expires_at: Optional[datetime],
    upvotes: int,
    downvotes: int,
    meta: Mapping[str, Any],
) -> bool: ...
```

Add `ExportedMemory` to protocol.py imports.

Update `tests/persistence/test_protocol.py`:
- Extend `REQUIRED_MEMORY_OPS` to include the 3 new method names.
- Existing presence + async-ness tests will cover them automatically.

Commit: `feat(persistence): extend MemoryOps protocol with paginated list + export + upsert methods`

### PostgresStore — MemoryOps extensions

**T3 — `list_memories_paginated` + contract tests**

- `list_memories_paginated(filter: MemoryFilter, *, limit=50, offset=0) -> tuple[int, Sequence[StoredMemory]]`:

  Build dynamic WHERE from `MemoryFilter` (mirror existing `list_memories` SQL but extend with the new `text_query` and `min_reputation` clauses):

  ```sql
  SELECT COUNT(*) FROM memories WHERE <where_sql>
  -- then:
  SELECT id, org_id, content, context, tags, confidence, source, project,
         created_at, updated_at, expires_at, upvotes, downvotes,
         meta, importance_score, access_count, last_accessed_at
  FROM memories
  WHERE <where_sql>
  ORDER BY created_at DESC
  LIMIT $N OFFSET $M
  ```

  WHERE building (extends current `list_memories` builder):
  - `org_id = $1` (always)
  - `project = $N` if filter.project not None
  - `meta->>'type' = $N` if filter.type not None
  - `meta->>'tier' = $N` if filter.tier not None
  - `tags @> $N::jsonb` if filter.tags non-empty
  - `created_at >= $N` if filter.since not None
  - **NEW:** `(content ILIKE $N OR context ILIKE $N)` if filter.text_query not None — wrap in `%...%` and reuse the same param index.
  - **NEW:** `reputation_score >= $N` if filter.min_reputation not None.

  Returns `(total_count, tuple_of_StoredMemory)`.

  **Schema check:** verify `memories.reputation_score` column exists. If not, the implementer should run `docker exec lore-test-pg psql -U lore -d lore_test -c "\d memories" | grep reputation_score`. Per the existing `list_lessons` route at line 442 it's referenced, so it should exist via some migration. If missing, it's a pre-existing dead-code issue; document as a known follow-up.

- Stub the other 2 new methods with `NotImplementedError`.

Contract tests in NEW file `tests/persistence/test_contract_lessons.py`:
- `test_list_paginated_returns_total_and_rows`.
- `test_list_paginated_text_query_filters_by_content_or_context`.
- `test_list_paginated_min_reputation_filter` (skip if `reputation_score` column doesn't exist; mark as `pytest.skip` with reason).
- `test_list_paginated_offset_paging`.
- `test_list_paginated_org_isolation`.
- `test_list_paginated_combined_filters`.

Use the `store` fixture from `tests/persistence/conftest.py`.

Commit: `feat(persistence): MemoryOps.list_memories_paginated`

**T4 — `list_memories_with_embeddings` + contract tests**

- `list_memories_with_embeddings(filter: MemoryFilter) -> Sequence[ExportedMemory]`:

  ```sql
  SELECT id, org_id, content, context, tags, confidence, source, project,
         embedding, created_at, updated_at, expires_at, upvotes, downvotes, meta
  FROM memories
  WHERE <where_sql>
  ORDER BY created_at
  ```

  Build WHERE from MemoryFilter same way. No LIMIT (export is intentional bulk read).

  Return `tuple` of `ExportedMemory` via `_row_to_exported_memory` helper:
  ```python
  def _row_to_exported_memory(row: "asyncpg.Record") -> ExportedMemory:
      tags = row["tags"]
      if isinstance(tags, str):
          tags = json.loads(tags)
      meta = row["meta"]
      if isinstance(meta, str):
          meta = json.loads(meta)
      embedding = row["embedding"]
      if isinstance(embedding, str) and embedding:
          # pgvector text format '[0.1,0.2,...]'
          embedding = [float(x) for x in embedding.strip("[]").split(",")]
      return ExportedMemory(
          id=row["id"],
          org_id=row["org_id"],
          content=row["content"],
          context=row["context"] if row["context"] else None,
          tags=tuple(tags or ()),
          confidence=float(row["confidence"]),
          source=row["source"],
          project=row["project"],
          embedding=embedding if embedding is not None else None,
          created_at=row["created_at"],
          updated_at=row["updated_at"],
          expires_at=row["expires_at"],
          upvotes=row["upvotes"] or 0,
          downvotes=row["downvotes"] or 0,
          meta=dict(meta or {}),
      )
  ```

Contract tests:
- `test_list_with_embeddings_returns_full_shape` — insert 2 memories with embeddings; verify both returned with embedding parsed to list[float].
- `test_list_with_embeddings_handles_null_embedding` — insert one without embedding; verify `embedding is None`.
- `test_list_with_embeddings_org_isolation`.
- `test_list_with_embeddings_project_filter`.

Commit: `feat(persistence): MemoryOps.list_memories_with_embeddings`

**T5 — `upsert_memory_with_embedding` + contract tests**

- `upsert_memory_with_embedding(...) -> bool`:

  SQL — mirrors current `routes/lessons.py:552-578` (the import handler):

  ```sql
  INSERT INTO memories
      (id, org_id, content, context, tags, confidence, source, project,
       embedding, created_at, updated_at, expires_at,
       upvotes, downvotes, meta)
  VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9::vector, now(), now(),
          $10, $11, $12, $13::jsonb)
  ON CONFLICT (id) DO UPDATE SET
      content = EXCLUDED.content,
      context = EXCLUDED.context,
      tags = EXCLUDED.tags,
      confidence = EXCLUDED.confidence,
      source = EXCLUDED.source,
      project = EXCLUDED.project,
      embedding = EXCLUDED.embedding,
      updated_at = EXCLUDED.updated_at,
      expires_at = EXCLUDED.expires_at,
      upvotes = EXCLUDED.upvotes,
      downvotes = EXCLUDED.downvotes,
      meta = EXCLUDED.meta
  WHERE memories.org_id = EXCLUDED.org_id
  ```

  - Encode `tags` and `meta` via `json.dumps`.
  - Encode `embedding` via `json.dumps(list(embedding))` when not None, else NULL — pass as `$9::vector` (asyncpg will treat None as NULL).
  - Returns `True` if INSERTed (asyncpg result is `"INSERT 0 1"` and the row didn't exist), `False` if UPDATEd. Practically: detect via the result string — though both INSERT-new and ON-CONFLICT-UPDATE return `"INSERT 0 1"`, so we can't distinguish. Cleaner: pre-fetch whether the row exists to decide.

  **Decision**: simpler approach — return `True` if the INSERT path was taken (no pre-existing row), `False` if it was an UPDATE. To detect: `INSERT … RETURNING xmax = 0 AS inserted` (Postgres `xmax = 0` for newly-inserted rows). The query becomes:

  ```sql
  INSERT INTO memories (...) VALUES (...)
  ON CONFLICT (id) DO UPDATE SET ...
  WHERE memories.org_id = EXCLUDED.org_id
  RETURNING (xmax = 0) AS inserted
  ```

  Then `result = await conn.fetchval(query, ...)` returns the bool.

After T5: zero `NotImplementedError` stubs in `postgres.py`.

Contract tests:
- `test_upsert_inserts_new_id_returns_true` — call with a fresh id; verify True returned and row appears.
- `test_upsert_updates_existing_returns_false` — call twice with the same id and different content; verify True the first time, False the second; subsequent get_memory shows the UPDATEd content.
- `test_upsert_org_guard_blocks_cross_org_update` — INSERT with org_a, then call upsert with the same id but org_b; verify the org_a row's content is NOT changed (the WHERE clause guards). Also verify the returned bool reflects the no-op (this is a tricky case — the WHERE clause on DO UPDATE means the conflict happens but the update doesn't fire; need to think about what `xmax = 0` returns in that case).

  Actually the WHERE on DO UPDATE means: if the row exists but org doesn't match, the UPDATE is silently skipped. The INSERT also fails because the id exists. Result: nothing happens. The `RETURNING` clause won't return any row. `fetchval` would return None. Service should treat None as "no-op due to scope guard".

  Cleaner test name: `test_upsert_with_org_mismatch_is_silent_noop` — verify the original row unchanged AND the return value is consistent (probably None/False).

- `test_upsert_with_null_embedding`.
- `test_upsert_preserves_id_exactly`.

Commit: `feat(persistence): MemoryOps.upsert_memory_with_embedding`

### Service

**T6 — `services/lessons.py` + service tests**

Module structure (`src/lore/services/lessons.py`):

```python
"""Lessons service — wire-shape preservation over MemoryOps.

Lessons are memories. Migration 009 made the `lessons` table a view backed
by `memories` with column aliases (problem→content, resolution→context).
This service wraps MemoryOps with field translation at the boundary.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Sequence

from lore.persistence import (
    ExportedMemory,
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    Store,
    StoredMemory,
)
from lore.persistence.exceptions import StoreNotFoundError


logger = logging.getLogger(__name__)


# Type-specific decay half-lives (days), matching DECAY_HALF_LIVES in lore.types.
_HALF_LIVES = {
    "code": 14,
    "note": 21,
    "lesson": 30,
    "convention": 60,
}
_HALF_LIFE_DEFAULT = 30
```

### Functions (9 total — match the 9 route handlers)

1. **`async def create(store, *, org_id, problem, resolution, context, tags, confidence, source, project, embedding, expires_at, meta) -> str`**:
   - Build `NewMemory(content=problem, context=resolution if resolution else "", tags=tags, confidence=confidence, source=source, project=project, embedding=embedding or [0.0]*384, expires_at=expires_at, meta=meta)`.
   - Call `store.insert_memory(...)` and return `stored.id`.
   - **Note**: `body.context` from the route is NOT stored (matches pre-1H behavior — see "Out of scope" notes). Drop it silently. Document this in the function docstring.

2. **`async def search(store, *, org_id, embedding, project, tags, limit, min_confidence) -> list[dict]`**:
   - Build `RecallParams` (or whatever the existing recall_by_embedding signature accepts) with embedding + project + tags.
   - Call `store.recall_by_embedding(...)` to get scored candidates.
   - For each candidate, compute `time_decay = 0.5 ** (effective_age_days / half_life)` where:
     - `effective_age_days = min(age_since_created, age_since_last_accessed_or_created)`.
     - `half_life = _HALF_LIVES.get(meta.get("type"), _HALF_LIFE_DEFAULT)`.
   - Composite `score = cosine_similarity * importance_score * time_decay`.
   - Sort by score desc, filter `score >= min_confidence`, take `limit`.
   - Return list of dicts shaped for `LessonSearchResult` (the route translates).

3. **`async def record_access(store, *, org_id, lesson_id, project) -> dict`**:
   - Pre-fetch via `store.get_memory(org_id, lesson_id)`. If None or `existing.project != project` (when project is not None), raise `StoreNotFoundError`.
   - Call `store.record_memory_access(org_id, lesson_id)`. If None (race), raise `StoreNotFoundError`.
   - Return `{id, access_count, last_accessed_at, importance_score}`.

4. **`async def get(store, *, org_id, lesson_id, project) -> StoredMemory`**:
   - Pre-fetch + project check; raise `StoreNotFoundError` on miss.

5. **`async def update(store, *, org_id, lesson_id, project, confidence, tags, meta, upvotes, downvotes) -> StoredMemory`**:
   - Pre-fetch + project check.
   - Build `MemoryPatch(confidence=confidence, tags=tags, meta=meta)` — only set fields that aren't None.
   - If patch has any field set, call `store.update_memory(org_id, lesson_id, patch)`. (Existing `update_memory` handles empty patch with a no-op return.)
   - For votes: if `upvotes is not None`:
     - If `upvotes` is `"+1"` or `"-1"`: call `store.vote_memory(org_id=org_id, memory_id=lesson_id, direction="up" if "+1" else "down")` once. (Existing `vote_memory` only does +1; for "-1" we need to handle separately. Hmm.)
     
     **Subtlety**: existing `vote_memory(direction='up')` increments upvotes by 1. There's no `vote_memory(direction='-1')` for decrement. The lessons route's "+1"/"-1" semantics for both upvotes AND downvotes are richer than `vote_memory` supports.
     
     **Decision**: for now, use a quick-and-dirty workaround — if upvotes is `"+1"`, call vote_memory(direction="up"). If `"-1"`, call vote_memory(direction="down") (which decrements net reputation). If absolute int, use a separate UPDATE via... hmm, MemoryOps doesn't expose that.

     Cleanest solution: add a `MemoryPatch.upvotes` and `MemoryPatch.downvotes` (Optional[int]) that DO an absolute set (current MemoryPatch doesn't have these). Then absolute sets work via update_memory. For "+1"/"-1" deltas, the service can read current votes and compute the new absolute value (race-y), OR the service does a separate atomic increment via a new dedicated method.
     
     **Pragmatic compromise**: lessons-specific atomic-vote semantics are nice-to-have. The vast majority of clients send absolute values (numeric upvotes/downvotes). The "+1"/"-1" string semantics are for clients that want atomic increments. For Phase 1H:
     - Numeric absolute → MemoryPatch.upvotes/downvotes (extend MemoryPatch).
     - "+1"/"-1" → service calls vote_memory(direction="up"/"down"), which atomically increments (the existing implementation handles this).
     
     But vote_memory(direction="down") increments downvotes — does it also handle "-1" semantics for upvotes? Let me check: looking at the current `update_memory_atomic_upvote_x10` test in tests/services/test_memories.py. The vote_memory call `vote_memory(store, org_id, memory_id, direction="up")` increments upvotes by 1; `direction="down"` increments downvotes by 1.
     
     For lessons "+1" upvotes → vote_memory(direction="up") — matches.
     For lessons "-1" upvotes → ??? There's no decrement. The existing route does `upvotes = upvotes + (-1)` atomically. Need a different approach.
     
     Decision: extend `MemoryPatch` with optional `upvotes_delta: Optional[int]` and `downvotes_delta: Optional[int]` fields. When set, the existing `update_memory` SQL does `upvotes = upvotes + $delta` for these fields. This is a backward-compat MemoryPatch extension that supports atomic deltas natively. Combined with absolute `upvotes: Optional[int]` for absolute-set, both modes work.
     
     **OR even simpler**: just extend `MemoryPatch` with `upvotes_delta` and `downvotes_delta` only (no absolute set). The service maps:
     - `body.upvotes == "+1"` → patch.upvotes_delta = 1
     - `body.upvotes == "-1"` → patch.upvotes_delta = -1
     - `body.upvotes == 5` (int) → ??? — the existing route does `upvotes = $5` (absolute set). If we drop this, behavior changes.
     
     OK — let me go with the maximalist patch: MemoryPatch.upvotes (absolute), MemoryPatch.downvotes (absolute), MemoryPatch.upvotes_delta (atomic increment), MemoryPatch.downvotes_delta (atomic increment). Service uses whichever fits.
     
     This is real scope creep though. Let me cut it differently:
     
     **Simplest path**: extend MemoryPatch with `upvotes: Optional[int]` and `downvotes: Optional[int]` (absolute set only). For the "+1"/"-1" string case, the service does a fetch-then-set: read current votes, compute new value, set absolutely. NOT atomic — race-y. Document as a known concurrency relaxation.
     
     This is consistent with the design's "vote update non-atomicity tradeoff" point I already raised. Let's go with this.

6. **`async def delete(store, *, org_id, lesson_id, project) -> None`**:
   - Pre-fetch + project check.
   - Call `store.delete_memory(org_id, lesson_id)`.

7. **`async def list_lessons(store, *, org_id, project, query, category, since, min_reputation, limit, offset) -> tuple[int, Sequence[StoredMemory]]`**:
   - Build `MemoryFilter(org_id=org_id, project=project, since=since, text_query=query, min_reputation=min_reputation, tags=[category] if category else ())`.
   - Call `store.list_memories_paginated(filter, limit=limit, offset=offset)`.

8. **`async def export(store, *, org_id, project) -> Sequence[ExportedMemory]`**:
   - Build `MemoryFilter(org_id=org_id, project=project)`.
   - Call `store.list_memories_with_embeddings(filter)`.

9. **`async def import_lessons(store, *, org_id, lessons, project_override) -> int`**:
   - For each lesson in `lessons`:
     - `memory_id = lesson.id or str(ULID())`
     - `project = project_override if project_override else lesson.project`
     - Call `store.upsert_memory_with_embedding(memory_id=memory_id, org_id=org_id, content=lesson.problem, context=lesson.resolution or "", tags=lesson.tags or [], confidence=lesson.confidence, source=lesson.source, project=project, embedding=lesson.embedding, expires_at=lesson.expires_at, upvotes=lesson.upvotes, downvotes=lesson.downvotes, meta=lesson.meta or {})`.
   - Return count.

### Service tests (`tests/services/test_lessons.py`)

Use the `store` fixture from `tests/services/conftest.py` (real Postgres). ~15 tests:

- `test_create_inserts_with_field_translation` — call with problem="X", resolution="Y"; verify stored memory has content="X", context="Y".
- `test_create_drops_context_field_silently` — pass `context="legacy_context"`; verify it's NOT in the stored row's context (which gets resolution).
- `test_search_applies_time_decay_scoring` — insert two memories with different ages and importance_scores; mock `recall_by_embedding` to return both; verify the older/lower-importance one is scored lower.
- `test_search_filters_below_min_confidence`.
- `test_record_access_returns_dict`.
- `test_record_access_404_on_missing`.
- `test_record_access_404_on_project_mismatch`.
- `test_get_returns_stored_memory`.
- `test_get_404_on_project_mismatch`.
- `test_update_changes_confidence`.
- `test_update_with_string_vote_increment` — body.upvotes="+1"; verify after, upvotes increased by 1.
- `test_update_with_absolute_vote_set` — body.upvotes=5; verify after, upvotes=5.
- `test_delete_404_on_missing`.
- `test_list_returns_total_and_lessons`.
- `test_export_includes_embeddings`.
- `test_import_upserts`.
- `test_import_uses_project_override`.

Commit: `feat(services): lessons service + field translation + time-decay scoring`

### Route refactor

**T7 — Refactor `routes/lessons.py` (9 handlers)**

Each handler thin: parse → call service → translate fields back to LessonResponse.

### Things to DELETE

- `_row_to_response` (moved to service; route uses a thin `_to_response(StoredMemory) -> LessonResponse` translator).
- `_scope_filter` (moved to service as project-check helper).
- All inline SQL.
- All `pool = await get_pool()` calls.
- `from lore.server.db import get_pool` import.
- Local imports no longer needed by handlers.

### Things to KEEP

- All Pydantic models (already imported from `lore.server.models`).
- The 9 handler functions (rewritten thin).
- Auth/role decorators.
- `logger`.

### Imports to ADD

```python
from lore.persistence import ExportedMemory, Store, StoredMemory
from lore.persistence.exceptions import StoreNotFoundError
from lore.server.db import get_store
from lore.services import lessons as lessons_service
```

### Handler-by-handler mapping

1. **`POST /v1/lessons`** → `lessons_service.create(...)`. Build LessonCreateResponse(id).
2. **`POST /v1/lessons/search`** → `lessons_service.search(...)`. Convert each result dict to LessonSearchResult.
3. **`POST /v1/lessons/{id}/access`** → `lessons_service.record_access(...)`. Catch StoreNotFoundError → 404.
4. **`GET /v1/lessons/{id}`** → `lessons_service.get(...)`. Convert StoredMemory to LessonResponse via `_to_response`. Catch StoreNotFoundError → 404.
5. **`PATCH /v1/lessons/{id}`** → `lessons_service.update(...)`. 422 if no fields set in body (existing behavior). 404 on missing. Convert StoredMemory to LessonResponse.
6. **`DELETE /v1/lessons/{id}`** → `lessons_service.delete(...)`. 404 on missing.
7. **`GET /v1/lessons`** → `lessons_service.list_lessons(...)`. Build LessonListResponse(lessons=[_to_response(m) for m in memories], total, limit, offset).
8. **`POST /v1/lessons/export`** → `lessons_service.export(...)`. Convert each ExportedMemory to LessonExportItem (field translation).
9. **`POST /v1/lessons/import`** → `lessons_service.import_lessons(...)`. Build LessonImportResponse(imported=count).

### Translation helper at the route layer

```python
def _to_lesson_response(m: StoredMemory) -> LessonResponse:
    return LessonResponse(
        id=m.id,
        problem=m.content,
        resolution=m.context or "",
        context=None,                    # legacy field; not stored
        tags=list(m.tags),
        confidence=m.confidence,
        source=m.source,
        project=m.project,
        created_at=m.created_at,
        updated_at=m.updated_at,
        expires_at=m.expires_at,
        upvotes=m.upvotes,
        downvotes=m.downvotes,
        meta=dict(m.meta),
    )


def _to_export_item(em: ExportedMemory) -> LessonExportItem:
    return LessonExportItem(
        id=em.id,
        problem=em.content,
        resolution=em.context or "",
        context=None,
        tags=list(em.tags),
        confidence=em.confidence,
        source=em.source,
        project=em.project,
        embedding=list(em.embedding) if em.embedding else None,
        created_at=em.created_at,
        updated_at=em.updated_at,
        expires_at=em.expires_at,
        upvotes=em.upvotes,
        downvotes=em.downvotes,
        meta=dict(em.meta),
    )
```

After refactor: file should be ~250-300 LOC (was 592).

Existing `tests/test_lessons*.py` (if any) may need redirecting. Check via `grep -rn "from lore.server.routes.lessons" tests/`.

Commit: `refactor(routes): lessons.py uses lessons service`

### Tests + cleanup

**T8 — Add lessons route tests with FakeStore mocks**

`tests/server/test_lessons_routes.py`: ~12-15 tests covering all 9 handlers + key error paths (404, 422 empty patch, 400 invalid input).

Pattern matches `tests/server/test_recommendations_routes.py` (Phase 1F).

Commit: `test(server): add lessons route tests with FakeStore mocks`

**T9 — Update CI guard**

`scripts/check_routes_no_sql.py`:
- Add `"src/lore/server/routes/lessons.py"` to `MIGRATED_ROUTES` (alphabetized).
- Check for docstring false-positives (`"""Update a lesson..."""`); add a narrow allowlist entry like `"a lesson"` if needed.

After: `python3 scripts/check_routes_no_sql.py` reports `Routes-no-SQL guard: 14 files OK`.

Commit: `chore(ci): extend routes-no-SQL guard to lessons slice`

**T10 — Update CHANGELOG + architecture docs**

**WORKING-DIRECTORY NOTE:** Do this task DIRECTLY in the worktree without dispatching a subagent. The recurring docs-subagent-wrong-directory bug has bitten Phase 1C T13, Phase 1E T14, and Phase 1F T10. Phase 1F+1G+1H pattern: do T10 inline.

- `CHANGELOG.md` Unreleased section: lessons slice migration; 3 new MemoryOps methods; ExportedMemory dataclass; MemoryFilter extension. Vote update non-atomicity tradeoff documented.
- `docs/architecture.md` persistence-layer section: bump migrated-routes count from 13 → 14 with `lessons.py` added to the breakdown. Note that lessons reuse MemoryOps via field translation (no new Store group).

Commit: `docs: document lessons slice migration`

**T11 — Final verification**

- `pytest tests/` — all pass.
- `ruff check src/ tests/` — clean.
- `python3 scripts/check_routes_no_sql.py` — exit 0, 14 files OK.
- `grep -nE "get_pool|asyncpg" src/lore/server/routes/lessons.py` — empty.
- `grep "_row_to_response\|_scope_filter" src/lore/server/routes/lessons.py` — empty (both moved to service).

No commit.

---

## Self-review

- All 3 new MemoryOps methods + MemoryFilter extension implemented + contract-tested.
- All 9 lesson route handlers refactored to call services.
- One new service module + 4 test files match the plan.
- Wire-shape preserved (`/v1/lessons` URL + `problem`/`resolution` Pydantic fields).
- The `lessons` Postgres view stays; the route just stops querying it directly.
- CI guard grows from 13 → 14.

### Known risks (don't block this plan)

- **`reputation_score` column existence**: the existing `list_lessons` route filters on it but I haven't verified the column exists in the migrated schema. T3 contract test should `pytest.skip` cleanly if missing, with a TODO note.
- **Vote update non-atomicity**: the lessons route currently does a single SQL UPDATE that atomically applies confidence + tags + meta + vote changes. New service makes two calls (update_memory + vote_memory or absolute-set). Concurrency relaxation; documented.
- **`xmax = 0` returning logic for upsert**: the `upsert_memory_with_embedding` uses `RETURNING (xmax = 0) AS inserted` to distinguish INSERT vs UPDATE. When the org guard fires on conflict (different org_id), the ON CONFLICT WHERE clause skips the update; `RETURNING` returns nothing; `fetchval` returns None. The service should treat None the same as False (no-op). Test for this case explicitly.
- **`RecallParams` shape**: the existing `recall_by_embedding` signature might not have `tags` filter; check before assuming. If it doesn't, the service can post-filter in Python.
- **Existing tests** under `tests/test_lessons*.py` and `tests/server/test_lessons.py` may extensively mock the inline-SQL paths. T7 should redirect mocks to service-layer mocks; large test files may need a more careful migration.
- **`body.context` no-op**: the legacy `context` field on LessonCreateRequest is preserved for wire compat but never stored. Unsurprising — the existing route also doesn't store it (the `lessons.context` view column is hardcoded to NULL). Documented in the service docstring.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — Fresh implementer per task; controlling Claude provides per-task code at dispatch time using this plan as reference. Mirrors Phase 1B/1C/1D/1E/1F/1G execution.

**2. Inline Execution** — Apply tasks in this session via executing-plans.

Which approach?
