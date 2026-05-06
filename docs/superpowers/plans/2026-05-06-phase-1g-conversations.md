# Phase 1G — Conversations Slice (ConversationOps) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task below is dispatched to a fresh implementer subagent with task-specific code spelled out in the dispatch prompt.

**Goal:** Apply the Phase 1A–1F pattern (Store abstraction + Service layer + route refactor) to the conversations slice. After this plan: every handler in `routes/conversations.py` calls services exclusively; the background extraction task lives in the service layer; all conversation SQL lives in `PostgresStore`'s new `ConversationOps` slice + one `MemoryOps` extension.

After 1G, **`routes/` is fully migrated**. The only remaining inline-SQL site is the `lore/server/auth.py` middleware (key lookup + last_used_at update), reserved for a future "auth middleware" slice.

**Architecture:** No new architecture. Same Store / Services / Routes layering as 1A–1F. 5 new methods on `Store` (ConversationOps slice) plus 1 new `MemoryOps` extension; one new service module (`services/conversations.py`); 2 route handlers refactored.

**Tech Stack:** Same as 1A–1F. No new runtime deps. Postgres test DB at `localhost:5432` / `lore_test` reused.

**Spec reference:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`. Section "Components" (1, 2). Phase 1F plan: `docs/superpowers/plans/2026-05-06-phase-1f-recommendations.md` — the immediate template.

**Structurally distinct from prior phases:** the conversations slice has a long-running async background task (`_process_job`) that orchestrates an in-process `Lore` instance + `ConversationExtractor` pipeline + persistent memory writes. The plan moves the orchestration to `services/conversations.py.process_job_async` and the SQL touches to the Store. The legacy in-process `Lore`/`MemoryStore` instances stay (they're the extraction pipeline's interface).

---

## File structure

### Created in this plan

| Path | Responsibility |
|---|---|
| `src/lore/services/conversations.py` | Conversation service module — `create_job` (validates + persists), `get_job_status` (404-aware fetch), `process_job_async` (background-task orchestration: mark processing → run extractor → import memories → mark complete/failed), and the `_get_server_lore` helper |
| `tests/persistence/test_contract_conversations.py` | Contract tests for the 5 `ConversationOps` methods + the `MemoryOps.import_extracted_memory` extension |
| `tests/services/test_conversations.py` | Service tests for create/get + the process_job_async orchestration (with mocked extractor) |
| `tests/server/test_conversations_routes.py` | Route tests for both handlers using `FakeStore` mocks |

### Modified in this plan

| Path | Change |
|---|---|
| `src/lore/persistence/types.py` | Add `NewConversationJob`, `StoredConversationJob` dataclasses |
| `src/lore/persistence/protocol.py` | Add 5 `ConversationOps` methods + 1 `MemoryOps` extension (`import_extracted_memory`) to `Store` Protocol |
| `src/lore/persistence/postgres.py` | Implement all 6 new methods on `PostgresStore` |
| `src/lore/persistence/__init__.py` | Re-export the 2 new dataclasses |
| `src/lore/server/routes/conversations.py` | Both handlers call services; drop `_process_job` (moved to service), `_get_server_lore` (moved to service), all inline SQL |
| `scripts/check_routes_no_sql.py` | Add `routes/conversations.py` to `MIGRATED_ROUTES` (12 → 13) |
| `tests/persistence/test_types.py`, `tests/persistence/test_protocol.py` | Extend to cover new dataclasses + protocol methods |
| `tests/test_conversation_server.py` (if it exists) | Redirect mocks if any depend on inline-SQL paths |
| `CHANGELOG.md`, `docs/architecture.md` | Note `ConversationOps` slice landed; routes/ is fully migrated |

### Out of scope (deferred)

- **`lore/server/auth.py` middleware** — Phase 1H or fold into a future phase.
- **`lore/conversation/extractor.py` module** — the extraction pipeline itself stays as-is; Phase 1G migrates only the SQL touches around it.
- **Replacing the in-process `Lore`/`MemoryStore` extraction driver** — the legacy embedded-API entry point remains the way `ConversationExtractor` interfaces with stored memories during extraction. Phase 4 (`AsyncLore`) revisits this.
- **Embedding for extracted memories** — current INSERT skips the embedding column (NULL); refactor preserves that. Conversation-extracted memories aren't recall targets at insert time.

---

## Tasks (one task = one commit)

Each task follows the Phase 1A–1F discipline: failing test first, run pytest, implement, run pytest, commit.

### Foundation — types, protocol

**T1 — Add conversation dataclasses to `lore.persistence.types`**

Add two `@dataclass(frozen=True, slots=True)` classes:

```python
@dataclass(frozen=True, slots=True)
class NewConversationJob:
    org_id: str
    message_count: int
    messages_json: str           # JSON-serialized list of {"role","content"} dicts
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    project: Optional[str] = None


@dataclass(frozen=True, slots=True)
class StoredConversationJob:
    id: str
    org_id: str
    status: str                  # "accepted" | "processing" | "completed" | "failed"
    message_count: int
    messages_json: str
    user_id: Optional[str]
    session_id: Optional[str]
    project: Optional[str]
    memory_ids: Sequence[str]    # parsed from JSONB text array
    memories_extracted: int
    duplicates_skipped: int
    error: Optional[str]
    processing_time_ms: int
    created_at: datetime
    completed_at: Optional[datetime]
```

Place under a new `# ── Conversations slice dataclasses ───` section comment.

Re-export from `__init__.py` (alphabetized in import block + `__all__`).

Add tests in `tests/persistence/test_types.py` for each dataclass: defaults, full population, frozen, slots.

Commit message: `feat(persistence): add conversation dataclasses`

**T2 — Extend `Store` Protocol with `ConversationOps` slice + `MemoryOps.import_extracted_memory`**

Under a new `# ── ConversationOps ────` section AFTER `# ── RecommendationOps ────`:

```python
async def create_conversation_job(self, job: NewConversationJob) -> StoredConversationJob: ...

async def get_conversation_job(
    self, job_id: str, org_id: str,
) -> Optional[StoredConversationJob]: ...

async def mark_conversation_job_processing(
    self, job_id: str,
) -> Optional[StoredConversationJob]: ...

async def complete_conversation_job(
    self,
    job_id: str,
    *,
    memory_ids: Sequence[str],
    memories_extracted: int,
    duplicates_skipped: int,
    processing_time_ms: int,
) -> None: ...

async def fail_conversation_job(
    self,
    job_id: str,
    *,
    error: str,
    processing_time_ms: int,
) -> None: ...
```

Inside the existing `# ── MemoryOps ────` section, add ONE new method:

```python
async def import_extracted_memory(
    self,
    *,
    memory_id: str,
    org_id: str,
    content: str,
    context: str,
    tags: Sequence[str],
    source: str,
    meta: Mapping[str, Any],
    confidence: float,
) -> bool: ...
```

Add `NewConversationJob`, `StoredConversationJob` to protocol.py imports.

Update `tests/persistence/test_protocol.py`:
- Add `REQUIRED_CONVERSATION_OPS = {"create_conversation_job", "get_conversation_job", "mark_conversation_job_processing", "complete_conversation_job", "fail_conversation_job"}` set + 2 new tests.
- Extend `REQUIRED_MEMORY_OPS` to include `"import_extracted_memory"`.

Commit message: `feat(persistence): extend Store protocol with ConversationOps slice + MemoryOps.import_extracted_memory`

### PostgresStore — ConversationOps

**T3 — `create_conversation_job` + `get_conversation_job` + contract tests**

- `create_conversation_job`: store generates ID `f"convjob_{ULID()}"`. SQL:
  ```sql
  INSERT INTO conversation_jobs
      (id, org_id, status, message_count, messages_json,
       user_id, session_id, project, created_at)
  VALUES ($1, $2, 'accepted', $3, $4, $5, $6, $7, now())
  RETURNING id, org_id, status, message_count, messages_json,
            user_id, session_id, project, memory_ids,
            memories_extracted, duplicates_skipped, error,
            processing_time_ms, created_at, completed_at
  ```

  Wraps the result in `StoredConversationJob`. Note: `memory_ids` column is `TEXT DEFAULT '[]'` (per migration 008) — JSON-encoded list. Decode in `_row_to_conversation_job`.

  Note: the existing route at `routes/conversations.py:73` generates `str(ULID())` (unprefixed). The new method uses `convjob_{ULID()}` for consistency with other prefixed ids in the codebase. **This is a behavioral change** — IDs returned to API clients will now have a `convjob_` prefix. Document it as known behavior change in the CHANGELOG (T13).

  ↑ **Decision needed**: preserve `str(ULID())` to avoid the wire-shape change, OR adopt `convjob_` prefix? **Pick: preserve `str(ULID())`** — wire-shape stability matters for any in-flight job IDs. Update the spec + implementer prompt to use unprefixed IDs.

- `get_conversation_job`: `SELECT … FROM conversation_jobs WHERE id = $1 AND org_id = $2`. Returns `Optional[StoredConversationJob]`.

- Stub the other 3 ConversationOps methods (`mark_processing`, `complete`, `fail`) and the MemoryOps `import_extracted_memory` with `NotImplementedError`.

Add `_row_to_conversation_job` helper:

```python
def _row_to_conversation_job(row: "asyncpg.Record") -> StoredConversationJob:
    memory_ids_raw = row["memory_ids"]
    if isinstance(memory_ids_raw, str):
        memory_ids = tuple(json.loads(memory_ids_raw or "[]"))
    elif memory_ids_raw is None:
        memory_ids = ()
    else:
        memory_ids = tuple(memory_ids_raw)
    return StoredConversationJob(
        id=row["id"],
        org_id=row["org_id"],
        status=row["status"],
        message_count=row["message_count"] or 0,
        messages_json=row["messages_json"] or "[]",
        user_id=row["user_id"],
        session_id=row["session_id"],
        project=row["project"],
        memory_ids=memory_ids,
        memories_extracted=row["memories_extracted"] or 0,
        duplicates_skipped=row["duplicates_skipped"] or 0,
        error=row["error"],
        processing_time_ms=row["processing_time_ms"] or 0,
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )
```

Contract tests in NEW file `tests/persistence/test_contract_conversations.py`:
- `test_create_job_round_trip` — create with required fields; round-trip via `get_conversation_job`.
- `test_create_job_with_optional_fields` — pass user_id, session_id, project; verify they round-trip.
- `test_get_job_returns_none_when_missing`.
- `test_get_job_org_isolation` — create in "org_a", fetch with "org_b" returns None.
- `test_create_job_initial_status_is_accepted`.
- `test_create_job_initial_memory_ids_empty` — `memory_ids` parses to empty tuple.

Commit message: `feat(persistence): ConversationOps.create_conversation_job + get_conversation_job`

**T4 — `mark_conversation_job_processing` + `complete_conversation_job` + `fail_conversation_job` + contract tests**

- `mark_conversation_job_processing(job_id)`:
  ```sql
  UPDATE conversation_jobs SET status = 'processing'
  WHERE id = $1
  RETURNING id, org_id, status, message_count, messages_json,
            user_id, session_id, project, memory_ids,
            memories_extracted, duplicates_skipped, error,
            processing_time_ms, created_at, completed_at
  ```
  Returns `Optional[StoredConversationJob]` — None if job missing.

- `complete_conversation_job(job_id, *, memory_ids, memories_extracted, duplicates_skipped, processing_time_ms)`:
  ```sql
  UPDATE conversation_jobs SET
      status = 'completed',
      memory_ids = $2,
      memories_extracted = $3,
      duplicates_skipped = $4,
      processing_time_ms = $5,
      completed_at = now()
  WHERE id = $1
  ```
  `memory_ids` parameter passed as `json.dumps(list(memory_ids))`. No return value. No-op when row missing (UPDATE matches 0 rows; no exception).

- `fail_conversation_job(job_id, *, error, processing_time_ms)`:
  ```sql
  UPDATE conversation_jobs SET
      status = 'failed',
      error = $2,
      processing_time_ms = $3,
      completed_at = now()
  WHERE id = $1
  ```
  No return value.

Contract tests:
- `test_mark_processing_updates_status` — create, mark, verify `status == 'processing'` and the returned row's other fields match.
- `test_mark_processing_returns_none_when_missing`.
- `test_complete_job_sets_status_and_payload` — create, mark processing, complete with memory_ids; verify all fields.
- `test_complete_job_silent_on_missing_id`.
- `test_fail_job_sets_error_and_status`.
- `test_fail_job_silent_on_missing_id`.

Commit message: `feat(persistence): ConversationOps.mark_processing + complete + fail`

**T5 — `MemoryOps.import_extracted_memory` + contract tests**

- `import_extracted_memory(*, memory_id, org_id, content, context, tags, source, meta, confidence) -> bool`:
  ```sql
  INSERT INTO memories
      (id, org_id, content, context, tags, source, meta, confidence,
       created_at, updated_at)
  VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8, now(), now())
  ON CONFLICT (id) DO NOTHING
  ```
  `tags` and `meta` JSONB-encoded via `json.dumps`.
  Returns `True` if inserted (asyncpg `result == "INSERT 0 1"`), `False` on conflict (`"INSERT 0 0"`).

  Note: this matches the current `routes/conversations.py:171-182` SQL exactly. Embedding column is omitted (NULL).

Contract tests:
- `test_import_inserts_when_id_is_new` — call with a fresh `mem_<ULID>`-style id; verify `True` returned and row appears via `store.get_memory`.
- `test_import_returns_false_on_conflict` — call twice with the same id; second returns `False`.
- `test_import_preserves_specified_id` — verify the row's id matches the passed `memory_id` exactly (no regeneration).
- `test_import_jsonb_roundtrip_for_tags_and_meta` — pass tags=["a","b"] and meta={"foo":"bar"}; verify they round-trip.
- `test_import_org_isolation_distinct_from_get` — import under "org_a", call `get_memory("org_b", id)` → None.

After T5: zero `NotImplementedError` stubs in `postgres.py`.

Commit message: `feat(persistence): MemoryOps.import_extracted_memory`

### Service

**T6 — `services/conversations.py` + service tests**

Module structure:

```python
"""Conversations service — async job creation, status fetch, and background extraction orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Dict, List, Optional, Sequence

from lore.persistence import (
    NewConversationJob,
    Store,
    StoredConversationJob,
)
from lore.persistence.exceptions import StoreNotFoundError


logger = logging.getLogger(__name__)


def _validate_messages(messages: List[Dict[str, str]]) -> None:
    """Validate the messages list. Raises ValueError on invalid input."""
    if not messages:
        raise ValueError("messages must be non-empty")
    for msg in messages:
        if "role" not in msg or "content" not in msg:
            raise ValueError("Each message must have 'role' and 'content'")


def _get_server_lore(org_id: str):
    """Create an in-process Lore instance for server-side extraction.

    Lifted verbatim from routes/conversations.py:215-228 (pre-1G).
    Imports are deferred to avoid loading ML modules at module import time.
    """
    from lore.lore import Lore
    from lore.store.memory import MemoryStore

    enrichment_model = os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")

    return Lore(
        store=MemoryStore(),
        enrichment=True,
        enrichment_model=enrichment_model,
    )
```

### Functions (3 total)

1. **`async def create_job(store, *, org_id, messages, user_id=None, session_id=None, project=None) -> StoredConversationJob`**:
   - `_validate_messages(messages)` — raises ValueError if invalid.
   - Build `NewConversationJob(org_id=org_id, message_count=len(messages), messages_json=json.dumps(messages), user_id=user_id, session_id=session_id, project=project)`.
   - Call `store.create_conversation_job(...)`.

2. **`async def get_job_status(store, job_id, org_id) -> StoredConversationJob`**:
   - Call `store.get_conversation_job(job_id, org_id)`.
   - If None, raise `StoreNotFoundError("conversation_jobs", job_id)`.
   - Return the row.

3. **`async def process_job_async(store, job_id, org_id) -> None`**:
   - The orchestration entry point for the background task.
   - `start = time.monotonic()`.
   - `try`:
     - `job = await store.mark_conversation_job_processing(job_id)`.
     - If `job is None`: log + return (job vanished mid-flight; nothing to do).
     - Imports deferred:
       ```python
       from lore.conversation import ConversationExtractor
       from lore.types import ConversationMessage
       ```
     - `messages = json.loads(job.messages_json)`.
     - `conv_messages = [ConversationMessage(role=m["role"], content=m["content"]) for m in messages]`.
     - `lore = _get_server_lore(org_id)`.
     - `try`:
       - `result = extractor.extract(conv_messages, user_id=job.user_id, session_id=job.session_id, project=job.project)`.
       - For each `mid` in `result.memory_ids`:
         - `mem = lore._store.get(mid)`; if None, skip.
         - `meta = {**(mem.metadata or {}), "type": mem.type or "fact", "source": mem.source or "conversation"}`.
         - `await store.import_extracted_memory(memory_id=mem.id, org_id=org_id, content=mem.content, context=mem.content, tags=list(mem.tags or []), source=mem.source or "conversation", meta=meta, confidence=mem.confidence)`.
       - `elapsed_ms = int((time.monotonic() - start) * 1000)`.
       - `await store.complete_conversation_job(job_id, memory_ids=list(result.memory_ids), memories_extracted=result.memories_extracted, duplicates_skipped=result.duplicates_skipped, processing_time_ms=elapsed_ms)`.
     - `finally`:
       - `lore.close()`.
   - `except Exception as e`:
     - `elapsed_ms = int((time.monotonic() - start) * 1000)`.
     - `logger.exception("Conversation job %s failed", job_id)`.
     - `await store.fail_conversation_job(job_id, error=str(e), processing_time_ms=elapsed_ms)`.

### Service tests (`tests/services/test_conversations.py`)

Use the `store` fixture from `tests/services/conftest.py`. Tests:

- `test_create_job_validates_empty_messages` — `messages=[]` → `ValueError`.
- `test_create_job_validates_message_shape` — message missing `role` → `ValueError`.
- `test_create_job_persists_and_returns_stored` — happy path; verify returned StoredConversationJob has status="accepted".
- `test_get_job_status_returns_stored_job`.
- `test_get_job_status_raises_not_found`.
- `test_get_job_status_org_mismatch_raises_not_found`.
- `test_process_job_async_marks_complete_on_success` — monkeypatch `ConversationExtractor.extract` to return a fake result with `memory_ids=[]`, `memories_extracted=0`, `duplicates_skipped=0`. Verify the job ends in `status="completed"` via `store.get_conversation_job`.
- `test_process_job_async_marks_failed_on_exception` — monkeypatch extractor to raise; verify job ends in `status="failed"` with the error message.
- `test_process_job_async_imports_extracted_memories` — monkeypatch extractor to return memory_ids=["mem_x"]; monkeypatch `_get_server_lore` to return a stub Lore whose `_store.get(mid)` returns a stub memory with content/tags/etc.; verify `store.import_extracted_memory` was called for each id (use `monkeypatch.setattr(store, "import_extracted_memory", AsyncMock(...))`).
- `test_process_job_async_skips_missing_id` — extractor returns id="mem_missing"; lore._store.get returns None; verify no import call for that id.

For monkey-patching the extractor:
```python
class _FakeResult:
    memory_ids = []
    memories_extracted = 0
    duplicates_skipped = 0

class _FakeExtractor:
    def __init__(self, *_, **__): pass
    def extract(self, *_, **__): return _FakeResult()

monkeypatch.setattr("lore.conversation.ConversationExtractor", _FakeExtractor)
```

Commit message: `feat(services): conversations service + background-task orchestration`

### Route refactor

**T7 — Refactor `routes/conversations.py` (2 handlers)**

After this commit, the route file is SQL-free.

### Things to DELETE

- `_process_job` function (moved to service).
- `_get_server_lore` helper (moved to service).
- All inline SQL.
- All `pool = await get_pool()` calls.
- `from lore.server.db import get_pool` import.
- Local imports: `import asyncio`, `import json`, `import time`, `from datetime import datetime, timezone`, `from ulid import ULID`. (Keep imports that the handlers themselves still use after the refactor; the bulk move to the service.)

### Things to KEEP

- Pydantic models: `ConversationRequest`, `ConversationAcceptedResponse`, `ConversationStatusResponse`.
- Both handler functions (rewritten thin).
- `from lore.server.auth import AuthContext, get_auth_context, require_role`.
- `logger = logging.getLogger(__name__)`.

### Imports to ADD

```python
import asyncio
from lore.persistence import Store
from lore.persistence.exceptions import StoreNotFoundError
from lore.server.db import get_store
from lore.services import conversations as conversations_service
```

(`asyncio` stays for the `asyncio.create_task` call in the POST handler.)

### Handler-by-handler mapping

1. **`POST /v1/conversations`**:
   ```python
   @router.post("", response_model=ConversationAcceptedResponse, status_code=202)
   async def create_conversation_job(
       body: ConversationRequest,
       auth: AuthContext = Depends(require_role("writer", "admin")),
       store: Store = Depends(get_store),
   ) -> ConversationAcceptedResponse:
       try:
           job = await conversations_service.create_job(
               store,
               org_id=auth.org_id,
               messages=body.messages,
               user_id=body.user_id,
               session_id=body.session_id,
               project=body.project or auth.project,
           )
       except ValueError as exc:
           raise HTTPException(400, str(exc))

       asyncio.create_task(conversations_service.process_job_async(store, job.id, auth.org_id))

       return ConversationAcceptedResponse(
           job_id=job.id,
           status=job.status,
           message_count=job.message_count,
       )
   ```

2. **`GET /v1/conversations/{job_id}`**:
   ```python
   @router.get("/{job_id}", response_model=ConversationStatusResponse)
   async def get_conversation_status(
       job_id: str,
       auth: AuthContext = Depends(get_auth_context),
       store: Store = Depends(get_store),
   ) -> ConversationStatusResponse:
       try:
           job = await conversations_service.get_job_status(store, job_id, auth.org_id)
       except StoreNotFoundError:
           raise HTTPException(404, "Job not found")

       return ConversationStatusResponse(
           job_id=job.id,
           status=job.status,
           message_count=job.message_count,
           memories_extracted=job.memories_extracted,
           memory_ids=list(job.memory_ids),
           duplicates_skipped=job.duplicates_skipped,
           processing_time_ms=job.processing_time_ms,
           error=job.error,
       )
   ```

After refactor: file should be ~75-90 LOC (was 228).

### Existing tests

`tests/test_conversation_server.py` may exist with mocks. Check via `grep -rn "conversation\|conversations" tests/test_conversation_server.py 2>/dev/null` and the wider `grep -rn "_process_job\|_get_server_lore\|conversation_jobs" tests/`. Redirect any inline-SQL mocks.

Commit message: `refactor(routes): conversations.py uses conversations service`

### Tests + cleanup

**T8 — Add conversations route tests with FakeStore mocks**

`tests/server/test_conversations_routes.py`: 6-8 tests covering both handlers + key error paths + the background-task scheduling.

Pattern matches `tests/server/test_snapshots_routes.py` (Phase 1E) and `tests/server/test_recommendations_routes.py` (Phase 1F).

Tests:
1. `test_post_returns_202_and_schedules_processing` — service.create_job mock returns a fake StoredConversationJob; verify 202 + JSON; verify `services.conversations.process_job_async` was called (via monkeypatch with an AsyncMock).
2. `test_post_400_on_empty_messages` — service raises `ValueError("messages must be non-empty")`; assert 400.
3. `test_post_400_on_missing_role` — service raises `ValueError("Each message must have 'role' and 'content'")`; assert 400.
4. `test_post_403_when_role_not_writer_or_admin` — caller has role="reader"; verify 403 (require_role check fires).
5. `test_get_returns_status_response` — service.get_job_status mock returns a StoredConversationJob; verify 200 + correct JSON shape.
6. `test_get_404_when_job_missing` — service raises StoreNotFoundError; assert 404.
7. `test_get_includes_memory_ids_array` — verify the response's memory_ids comes through as a list.

For the `process_job_async` scheduling test: monkeypatch the function with an `AsyncMock`, then verify `mock.assert_called_with(store, job.id, auth.org_id)`. The route uses `asyncio.create_task(...)` which fires-and-forgets; the test should wait briefly (e.g., `await asyncio.sleep(0)` or call `await mock` directly) to ensure the call happened.

Commit message: `test(server): add conversations route tests with FakeStore mocks`

**T9 — Update CI guard**

`scripts/check_routes_no_sql.py`:
- Add `"src/lore/server/routes/conversations.py"` to `MIGRATED_ROUTES` (alphabetized).
- Check for docstring false-positives (e.g., `"""Update conversation job"""`); if any, add a narrow allowlist entry similar to recommendations.py's "recommendation config" entry. Likely the docstrings in the new thin handlers are simple ("Accept conversation for async extraction.", "Check status of a conversation extraction job.") and don't trip the regex.

After this change: `python3 scripts/check_routes_no_sql.py` reports `Routes-no-SQL guard: 13 files OK` and exits 0.

Commit message: `chore(ci): extend routes-no-SQL guard to conversations slice`

**T10 — Update CHANGELOG + architecture docs**

**WORKING-DIRECTORY NOTE:** Do this task DIRECTLY in the worktree without dispatching a subagent. Phase 1C T13, Phase 1E T14, and Phase 1F T10 all hit a recurring bug where the docs-implementer subagent committed to local main instead of the worktree branch. Phase 1F worked around it by doing T10 inline; do the same here.

- `CHANGELOG.md` Unreleased section: ConversationOps slice (5 methods) + MemoryOps.import_extracted_memory extension + services/conversations.py + the routes/ slice now fully migrated. New typed dataclasses: NewConversationJob, StoredConversationJob.
- `docs/architecture.md` persistence-layer section: add ConversationOps to the slice list (count: seven → eight slices); update slice progression sentence ("Phase 1G added ConversationOps. The routes/ slice migration is complete; the only remaining inline-SQL site is the auth middleware (future phase)."); bump migrated-routes count from 12 → 13 with the conversations.py addition.

Commit message: `docs: document conversations slice migration; routes/ fully migrated`

**T11 — Final verification**

- `pytest tests/` — all pass.
- `ruff check src/ tests/` — clean.
- `python3 scripts/check_routes_no_sql.py` — exit 0, 13 files OK.
- `grep -nE "get_pool|asyncpg" src/lore/server/routes/conversations.py` — empty.
- `grep "_process_job\|_get_server_lore" src/lore/server/routes/conversations.py` — empty (both moved to service).
- `grep -rn "from lore.server.routes.conversations import" src/ tests/ | grep -v "router"` — only `router` re-imports allowed.
- Confirm: `grep -rn "get_pool" src/lore/server/routes/` should now show ONLY `lore/server/auth.py`'s usage (which is in `lore/server/`, not `lore/server/routes/`). Wait — `auth.py` lives at `lore/server/auth.py`, not under `routes/`. So `routes/` is fully SQL-free after this. Verify with `grep -rln "get_pool" src/lore/server/routes/` — empty.

No commit.

---

## Self-review

- All 5 ConversationOps methods + 1 MemoryOps extension implemented + contract-tested.
- Both conversation route handlers refactored.
- Background-task orchestration moves cleanly to `services/conversations.py.process_job_async`.
- The `_get_server_lore` helper moves to the service module (deferred imports preserved).
- CI guard grows from 12 → 13. **`routes/` is fully migrated after this phase.**

### Known risks (don't block this plan)

- **Background task race**: the test for `test_post_returns_202_and_schedules_processing` needs to assert `asyncio.create_task` was called without blocking on the coroutine. The route fires-and-forgets; the test mock should record the call without executing the (async) extractor pipeline. Typical pattern: `monkeypatch.setattr("lore.server.routes.conversations.conversations_service.process_job_async", AsyncMock())`, then post the request, then `await asyncio.sleep(0)` to yield once so the task runs the mock. AsyncMock's `assert_called_with` works on the recorded call.

- **`_get_server_lore` is process-local**: each call instantiates a fresh `Lore` with an in-memory `MemoryStore`. The conversation extraction pipeline writes to that in-memory store; the persistent writes happen via `store.import_extracted_memory`. This means the in-memory store is discarded after each job. Preserve current behavior — no change.

- **Wire-shape stability for job_ids**: the existing route generates `str(ULID())` (unprefixed). The new method also uses `str(ULID())` (no `convjob_` prefix) to preserve wire-shape stability for any in-flight job ids. Spec adjusted from initial `convjob_{ULID()}` proposal.

- **`messages_json` storage**: schema column is `TEXT` (not JSONB). Store as raw JSON string via `json.dumps(messages)`; decode at the service layer via `json.loads`. The `StoredConversationJob.messages_json` field is `str`; the service is responsible for parsing.

- **`memory_ids` storage**: schema column is `TEXT DEFAULT '[]'`. Same as `messages_json` — JSON-encoded text. Decode in `_row_to_conversation_job`.

- **Existing `tests/test_conversation_server.py`** likely exists and may mock the inline-SQL paths or `_process_job` directly. T7 should redirect mocks; if any tests are fundamentally written against the pre-1G shape (e.g., asserting on `_process_job`'s internals), mark them xfail with a TODO referencing T8's new tests.

- **`asyncio.create_task` on a service function is OK**: the service's `process_job_async` is async and self-contained (its own try/except catches all errors). The created task is fire-and-forget from the route's perspective.

---

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — Fresh implementer per task; controlling Claude provides per-task code at dispatch time using this plan as reference. Mirrors Phase 1B/1C/1D/1E/1F execution.

**2. Inline Execution** — Apply tasks in this session via executing-plans.

Which approach?
