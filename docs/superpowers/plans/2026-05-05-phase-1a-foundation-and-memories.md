# Phase 1A — Foundation + Memories Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the persistence-layer abstraction that the SQLite solo-mode work will build on, and prove the architecture end-to-end against one slice of routes (`/v1/memories/*` + `/v1/retrieve`). After this plan, memories-related routes contain zero SQL; all DB access goes through `PostgresStore` via a typed `Store` protocol; a contract test suite enforces the abstraction.

**Architecture:** New `lore.persistence` package owns the `Store` Protocol, dataclasses, exceptions, and a `PostgresStore` implementation extracted from existing route SQL. New `lore.services` package owns business logic; functions take a `Store` + typed params and return dataclasses. Existing FastAPI route handlers shrink to "parse → call service → serialize." Other slices (graph, workspaces, etc.) stay on inline SQL until Phase 1B–1G migrate them; this plan makes both shapes coexist cleanly.

**Tech Stack:** Python 3.10+, asyncpg, FastAPI, pytest, pytest-asyncio. No new runtime deps. Contract tests require a Postgres+pgvector instance (URL via `LORE_TEST_DATABASE_URL`, default `postgresql://lore:lore@localhost:5432/lore_test`).

**Spec reference:** `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md` — sections "Components" (1, 2), "Data flow" (Path A), "Testing" (Layer 1).

**Naming correction from spec:** the spec proposed `lore.store.*` for the new abstraction, but `lore.store` already exists in this codebase (`lore.store.base.Store`, `MemoryStore`, `HttpStore`) as the SDK-level client store. To avoid a name collision, the new server-side persistence layer is named `lore.persistence` instead. All references in this plan use the new name.

---

## File structure

### Created in this plan

| Path | Responsibility |
|---|---|
| `src/lore/persistence/__init__.py` | Re-exports `Store`, `make_store`, dataclasses, exceptions |
| `src/lore/persistence/types.py` | Typed dataclasses: `NewMemory`, `StoredMemory`, `ScoredMemory`, `MemoryFilter`, `MemoryPatch`, `RecallParams` |
| `src/lore/persistence/exceptions.py` | `LoreError`, `StoreError`, `StoreNotFound`, `StoreBusy`, `ConfigError` (full hierarchy seeded; later phases extend) |
| `src/lore/persistence/protocol.py` | `Store` Protocol with `MemoryOps` slice (9 methods) — the only methods this plan implements; protocol is grown by later phases |
| `src/lore/persistence/postgres.py` | `PostgresStore` — owns `asyncpg.Pool`, implements `MemoryOps` |
| `src/lore/persistence/factory.py` | `make_store(database_url) -> Store` — picks impl from URL scheme |
| `src/lore/services/__init__.py` | Package marker |
| `src/lore/services/memories.py` | Service functions for the memory ops: `create_memory`, `get_memory`, `update_memory`, `delete_memory`, `list_memories`, `search_memories`, `vote_memory`, `bump_access` |
| `src/lore/services/retrieve.py` | Service function `retrieve(...)` — embeds query, calls `store.recall_by_embedding`, applies profile, formats output, records analytics |
| `tests/persistence/__init__.py` | Package marker |
| `tests/persistence/conftest.py` | Parametrized `store` fixture (Postgres now; SQLite skip-marked for later) and helpers |
| `tests/persistence/fixtures/embeddings.json` | 100 short strings + their precomputed 384-dim vectors (committed) |
| `tests/persistence/test_contract_memories.py` | Contract test class for `MemoryOps` |
| `tests/persistence/test_postgres_factory.py` | Tests `make_store("postgres://...")` returns `PostgresStore` |
| `scripts/check_routes_no_sql.py` | CI guard: routes/ files in the migrated slice cannot import asyncpg or contain raw SQL strings |

### Modified in this plan

| Path | Change |
|---|---|
| `src/lore/server/db.py` | Adds `init_store()` alongside `init_pool()`; lifespan creates a `PostgresStore` in app state |
| `src/lore/server/app.py` | Lifespan calls `init_store(database_url)`; exposes `Store` via `Depends(get_store)` |
| `src/lore/server/routes/memories.py` | Every handler shrinks to "parse → call `services.memories.*` → serialize." All inline SQL removed. |
| `src/lore/server/routes/retrieve.py` | Handler calls `services.retrieve.retrieve(...)`; inline SQL removed. |
| `pyproject.toml` | Adds `lore.persistence` and `lore.services` to `[tool.hatch.build.targets.wheel] packages` |
| `tests/conftest.py` | No change yet (CLI tests still use `MemoryStore`); contract tests have their own conftest |

### Out of scope for this plan (deferred to 1B–1G)

- Refactoring graph, workspaces, snapshots, analytics, SLO, profiles, review, conversations, ingest routes.
- Adding `MemoryStore` (in-process test fake) implementing the new persistence Protocol — this plan uses real Postgres for contract tests.
- `SqliteStore` implementation (Phase 3).
- `AsyncLore` embedded API (Phase 4).
- `lore migrate` command (Phase 5).
- Removing the existing `lore.store.*` SDK-side abstraction (long-term cleanup, not this phase).

---

## Test database setup

Contract tests need a real Postgres+pgvector instance. The plan uses an env var:

- `LORE_TEST_DATABASE_URL` — full connection string. Default: `postgresql://lore:lore@localhost:5432/lore_test`.
- If unset and the default URL refuses connection, tests skip with a clear message pointing at `docker compose up -d db && createdb -U lore lore_test`.
- Each test runs inside a transaction that is rolled back at teardown, so tests don't bleed into each other and don't need full schema reset between runs.

The plan's CI step adds a "contract tests" job that boots `pgvector/pgvector:pg16` via Docker Compose service, creates `lore_test`, runs migrations, then runs `pytest tests/persistence/`.

---

## Tasks

### Task 1: Create empty `lore.persistence` and `lore.services` packages

**Files:**
- Create: `src/lore/persistence/__init__.py`
- Create: `src/lore/services/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/persistence/test_imports.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/persistence/test_imports.py
"""Smoke test: persistence and services packages can be imported."""

def test_persistence_package_importable():
    import lore.persistence  # noqa: F401


def test_services_package_importable():
    import lore.services  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/persistence/test_imports.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lore.persistence'`.

- [ ] **Step 3: Create the empty packages**

```python
# src/lore/persistence/__init__.py
"""Server-side persistence layer.

Defines the Store protocol and its backend implementations. The persistence
layer is the only place in the codebase that touches raw SQL or DB drivers.

Names exported by re-export from submodules are added in later tasks.
"""
```

```python
# src/lore/services/__init__.py
"""Business-logic services.

Pure async functions: (store, params) -> dataclass result.
HTTP route handlers and the embedded API both call into services.
"""
```

- [ ] **Step 4: Update `pyproject.toml` packages**

Open `pyproject.toml` and update the `[tool.hatch.build.targets.wheel]` block:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/lore"]
```

If `packages = ["src/lore"]` already covers nested packages (it does, since hatchling discovers nested packages by default), no change is required. Confirm by inspection. If there is a manual list of subpackages elsewhere, append `lore.persistence` and `lore.services` to it.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/persistence/test_imports.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/lore/persistence/__init__.py src/lore/services/__init__.py tests/persistence/__init__.py tests/persistence/test_imports.py pyproject.toml
git commit -m "feat(persistence): create empty persistence and services packages"
```

(`tests/persistence/__init__.py` should be an empty file — create it as part of this commit so pytest can discover the package.)

---

### Task 2: Define core typed dataclasses

**Files:**
- Create: `src/lore/persistence/types.py`
- Test: `tests/persistence/test_types.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/persistence/test_types.py
"""Tests for persistence-layer dataclasses."""

from datetime import datetime, timezone

from lore.persistence.types import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    ScoredMemory,
    StoredMemory,
)


def test_new_memory_required_fields():
    nm = NewMemory(
        org_id="org_1",
        content="hello world",
        embedding=[0.0] * 384,
    )
    assert nm.content == "hello world"
    assert len(nm.embedding) == 384
    assert nm.tags == ()  # default empty
    assert nm.meta == {}


def test_stored_memory_round_trip():
    now = datetime.now(timezone.utc)
    m = StoredMemory(
        id="mem_01",
        org_id="org_1",
        content="hello",
        context=None,
        tags=("a", "b"),
        confidence=0.9,
        source=None,
        project="proj",
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={"type": "lesson"},
        importance_score=1.0,
        access_count=0,
        last_accessed_at=None,
    )
    assert m.id == "mem_01"
    assert m.tags == ("a", "b")


def test_scored_memory_extends_stored():
    now = datetime.now(timezone.utc)
    sm = ScoredMemory(
        id="mem_02",
        org_id="org_1",
        content="ranked",
        context=None,
        tags=(),
        confidence=1.0,
        source=None,
        project=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
        importance_score=1.0,
        access_count=0,
        last_accessed_at=None,
        score=0.87,
    )
    assert sm.score == 0.87


def test_memory_filter_defaults():
    f = MemoryFilter(org_id="org_1")
    assert f.project is None
    assert f.type is None
    assert f.tier is None
    assert f.limit is None
    assert f.include_expired is False


def test_memory_patch_partial_update():
    p = MemoryPatch(content="new text")
    assert p.content == "new text"
    assert p.tags is None  # explicit "no change"


def test_recall_params_required_query_vec():
    rp = RecallParams(
        org_id="org_1",
        query_vec=[0.0] * 384,
        limit=10,
        min_score=0.3,
    )
    assert rp.limit == 10
    assert rp.project is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/persistence/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lore.persistence.types'`.

- [ ] **Step 3: Implement `types.py`**

```python
# src/lore/persistence/types.py
"""Typed dataclasses for the persistence layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True, slots=True)
class NewMemory:
    org_id: str
    content: str
    embedding: Sequence[float]
    context: Optional[str] = None
    tags: Sequence[str] = ()
    confidence: float = 0.5
    source: Optional[str] = None
    project: Optional[str] = None
    expires_at: Optional[datetime] = None
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredMemory:
    id: str
    org_id: str
    content: str
    context: Optional[str]
    tags: Sequence[str]
    confidence: float
    source: Optional[str]
    project: Optional[str]
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]
    upvotes: int
    downvotes: int
    meta: Mapping[str, Any]
    importance_score: float
    access_count: int
    last_accessed_at: Optional[datetime]


@dataclass(frozen=True, slots=True)
class ScoredMemory(StoredMemory):
    score: float


@dataclass(frozen=True, slots=True)
class MemoryFilter:
    org_id: str
    project: Optional[str] = None
    type: Optional[str] = None
    tier: Optional[str] = None
    tags: Optional[Sequence[str]] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    limit: Optional[int] = None
    offset: int = 0
    include_expired: bool = False


@dataclass(frozen=True, slots=True)
class MemoryPatch:
    content: Optional[str] = None
    context: Optional[str] = None
    tags: Optional[Sequence[str]] = None
    confidence: Optional[float] = None
    source: Optional[str] = None
    project: Optional[str] = None
    expires_at: Optional[datetime] = None
    meta: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True, slots=True)
class RecallParams:
    org_id: str
    query_vec: Sequence[float]
    limit: int = 5
    min_score: float = 0.3
    project: Optional[str] = None
    half_life_days: int = 30
    exclude_expired: bool = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/persistence/test_types.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lore/persistence/types.py tests/persistence/test_types.py
git commit -m "feat(persistence): add typed dataclasses for memory ops"
```

---

### Task 3: Define typed exception hierarchy

**Files:**
- Create: `src/lore/persistence/exceptions.py`
- Test: `tests/persistence/test_exceptions.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/persistence/test_exceptions.py
"""Tests for the typed exception hierarchy."""

import pytest

from lore.persistence.exceptions import (
    BackendUnavailable,
    ConfigError,
    LoreError,
    StoreBusy,
    StoreError,
    StoreNotFound,
)


def test_hierarchy():
    assert issubclass(StoreError, LoreError)
    assert issubclass(StoreNotFound, StoreError)
    assert issubclass(StoreBusy, StoreError)
    assert issubclass(ConfigError, LoreError)
    assert issubclass(BackendUnavailable, ConfigError)


def test_store_not_found_message():
    with pytest.raises(StoreNotFound) as ei:
        raise StoreNotFound("memories", "mem_missing")
    assert "memories" in str(ei.value)
    assert "mem_missing" in str(ei.value)


def test_config_error_holds_value():
    err = ConfigError("bad scheme: foo://")
    assert "foo://" in str(err)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/persistence/test_exceptions.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `exceptions.py`**

```python
# src/lore/persistence/exceptions.py
"""Typed exception hierarchy for the persistence layer.

Later phases extend this hierarchy (e.g. StoreCorruption, EmbeddingDimMismatch
for SQLite). Phase 1A seeds the base set used by PostgresStore.
"""

from __future__ import annotations


class LoreError(Exception):
    """Base for all Lore errors."""


class StoreError(LoreError):
    """Base for any error raised by a Store implementation."""


class StoreNotFound(StoreError):
    """A row the caller asserted must exist was not found."""

    def __init__(self, entity: str, identifier: str):
        self.entity = entity
        self.identifier = identifier
        super().__init__(f"{entity} not found: id={identifier!r}")


class StoreBusy(StoreError):
    """Storage is temporarily contended; retry may succeed."""


class StoreSchemaMismatch(StoreError):
    """The DB's schema version does not match what this Lore expects."""


class ConfigError(LoreError):
    """Bad configuration: URL, env var, or flag combination."""


class BackendUnavailable(ConfigError):
    """The selected backend's runtime is not available (driver, extension)."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/persistence/test_exceptions.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lore/persistence/exceptions.py tests/persistence/test_exceptions.py
git commit -m "feat(persistence): seed typed exception hierarchy"
```

---

### Task 4: Define `Store` Protocol with `MemoryOps` slice

**Files:**
- Create: `src/lore/persistence/protocol.py`
- Modify: `src/lore/persistence/__init__.py`
- Test: `tests/persistence/test_protocol.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/persistence/test_protocol.py
"""Tests that the Store Protocol declares the MemoryOps slice."""

from __future__ import annotations

import inspect

from lore.persistence import Store


REQUIRED_MEMORY_OPS = {
    "insert_memory",
    "get_memory",
    "update_memory",
    "delete_memory",
    "list_memories",
    "recall_by_embedding",
    "expire_memories",
    "bump_access_counts",
    "vote_memory",
}


def test_store_declares_memory_ops():
    members = {name for name, _ in inspect.getmembers(Store)}
    missing = REQUIRED_MEMORY_OPS - members
    assert not missing, f"Store missing MemoryOps methods: {missing}"


def test_memory_ops_are_async():
    for name in REQUIRED_MEMORY_OPS:
        method = getattr(Store, name)
        assert inspect.iscoroutinefunction(method), (
            f"Store.{name} must be async"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/persistence/test_protocol.py -v`
Expected: FAIL with `ImportError: cannot import name 'Store' from 'lore.persistence'`.

- [ ] **Step 3: Implement `protocol.py`**

```python
# src/lore/persistence/protocol.py
"""Server-side Store Protocol.

The Store is the only place in the codebase that touches raw SQL or DB drivers.
Routes and services call typed methods declared here. Phase 1A defines the
MemoryOps slice; later phases extend the protocol with GraphOps, WorkspaceOps,
SnapshotOps, AnalyticsOps, PolicyOps, AuthOps, etc.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from lore.persistence.types import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    ScoredMemory,
    StoredMemory,
)


@runtime_checkable
class Store(Protocol):
    """The Store protocol.

    Implementations: PostgresStore (Phase 1A), SqliteStore (Phase 3).
    Method groups are added incrementally; Phase 1A defines MemoryOps.
    """

    # ── lifecycle ────────────────────────────────────────────────────

    async def close(self) -> None:
        """Release any underlying resources (pool, connection, file)."""
        ...

    # ── MemoryOps ────────────────────────────────────────────────────

    async def insert_memory(self, memory: NewMemory) -> StoredMemory:
        """Insert a memory; returns the stored row with server-generated id/timestamps."""
        ...

    async def get_memory(self, org_id: str, memory_id: str) -> Optional[StoredMemory]:
        """Return a memory by id within an org, or None if absent or expired."""
        ...

    async def update_memory(
        self, org_id: str, memory_id: str, patch: MemoryPatch
    ) -> StoredMemory:
        """Apply a patch and return the updated row. Raises StoreNotFound if missing."""
        ...

    async def delete_memory(self, org_id: str, memory_id: str) -> bool:
        """Delete a memory; returns True if a row was deleted."""
        ...

    async def list_memories(self, filter: MemoryFilter) -> Sequence[StoredMemory]:
        """List memories matching filter; ordered by created_at DESC."""
        ...

    async def recall_by_embedding(self, params: RecallParams) -> Sequence[ScoredMemory]:
        """Vector recall: returns memories ranked by combined score (similarity * importance * decay)."""
        ...

    async def expire_memories(self) -> int:
        """Delete rows with expires_at < now(); returns rowcount."""
        ...

    async def bump_access_counts(self, memory_ids: Sequence[str]) -> None:
        """Increment access_count + last_accessed_at + recompute importance_score."""
        ...

    async def vote_memory(
        self, org_id: str, memory_id: str, *, direction: str
    ) -> StoredMemory:
        """direction is 'up' or 'down'. Returns the updated memory."""
        ...
```

- [ ] **Step 4: Update `lore.persistence.__init__`**

```python
# src/lore/persistence/__init__.py
"""Server-side persistence layer."""

from lore.persistence.exceptions import (
    BackendUnavailable,
    ConfigError,
    LoreError,
    StoreBusy,
    StoreError,
    StoreNotFound,
    StoreSchemaMismatch,
)
from lore.persistence.protocol import Store
from lore.persistence.types import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    ScoredMemory,
    StoredMemory,
)

__all__ = [
    "BackendUnavailable",
    "ConfigError",
    "LoreError",
    "MemoryFilter",
    "MemoryPatch",
    "NewMemory",
    "RecallParams",
    "ScoredMemory",
    "Store",
    "StoreBusy",
    "StoreError",
    "StoreNotFound",
    "StoreSchemaMismatch",
    "StoredMemory",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/persistence/test_protocol.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/lore/persistence/protocol.py src/lore/persistence/__init__.py tests/persistence/test_protocol.py
git commit -m "feat(persistence): define Store protocol with MemoryOps slice"
```

---

### Task 5: Set up contract test infrastructure (parametrized store fixture)

**Files:**
- Create: `tests/persistence/conftest.py`
- Test: `tests/persistence/test_fixture.py` (new — proves the fixture works)

- [ ] **Step 1: Write the failing test**

```python
# tests/persistence/test_fixture.py
"""Smoke test: the parametrized store fixture provides a Store for each backend."""

from __future__ import annotations

import pytest

from lore.persistence import Store


@pytest.mark.asyncio
async def test_store_fixture_provides_store(store: Store):
    assert hasattr(store, "insert_memory")
    assert hasattr(store, "recall_by_embedding")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/persistence/test_fixture.py -v`
Expected: ERROR — fixture `store` not found.

- [ ] **Step 3: Create `conftest.py`**

```python
# tests/persistence/conftest.py
"""Contract-test fixtures.

Provides a parametrized `store` fixture that runs every test once per
implementation. Phase 1A wires PostgresStore; Phase 3 will add SqliteStore
to the params list.

Postgres setup:
- Reads LORE_TEST_DATABASE_URL (default: postgresql://lore:lore@localhost:5432/lore_test).
- Each test runs inside a transaction that is rolled back at teardown.
- If the DB cannot be reached, tests are skipped with a clear message.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio

DEFAULT_TEST_PG_URL = "postgresql://lore:lore@localhost:5432/lore_test"


def _test_pg_url() -> str:
    return os.environ.get("LORE_TEST_DATABASE_URL", DEFAULT_TEST_PG_URL)


@pytest_asyncio.fixture
async def _pg_pool() -> AsyncIterator:
    """Module-level pool for Postgres contract tests."""
    try:
        import asyncpg
    except ImportError:
        pytest.skip("asyncpg not installed")
    try:
        pool = await asyncpg.create_pool(_test_pg_url(), min_size=1, max_size=2)
    except (OSError, ConnectionRefusedError, Exception) as e:
        pytest.skip(
            f"Cannot reach LORE_TEST_DATABASE_URL ({_test_pg_url()}): {e}. "
            "Start it with: docker compose up -d db && createdb -U lore lore_test "
            "&& psql -U lore -d lore_test -f migrations/001_initial.sql ..."
        )
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(params=["postgres"])
async def store(request, _pg_pool):
    """A Store ready for use; rolled back at teardown.

    Each test gets its own connection acquired from the shared pool, wrapped
    in a transaction that is rolled back. This isolates tests without
    requiring schema reset between each one.
    """
    if request.param == "postgres":
        from lore.persistence.postgres import PostgresStore

        async with _pg_pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                pg_store = PostgresStore.from_connection(conn)
                yield pg_store
            finally:
                await tr.rollback()
    else:
        pytest.skip(f"Backend {request.param!r} not yet implemented (Phase 3+)")
```

- [ ] **Step 4: Note that `PostgresStore.from_connection` does not exist yet**

This test will still fail (`ModuleNotFoundError: lore.persistence.postgres`) — that is expected; the next task creates `PostgresStore`. We will run the smoke test from Task 6 once PostgresStore exists.

For now, mark this task partially complete and move to Task 6. Do not commit yet — keep the conftest changes staged with Task 6's commit.

- [ ] **Step 5: Stage but do not commit**

```bash
git add tests/persistence/conftest.py tests/persistence/test_fixture.py
# Do not commit yet — Task 6 implements PostgresStore and we commit them together.
```

---

### Task 6: Implement `PostgresStore` skeleton + `insert_memory` + `get_memory`

**Files:**
- Create: `src/lore/persistence/postgres.py`
- Test: `tests/persistence/test_contract_memories.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/persistence/test_contract_memories.py
"""Contract tests for the MemoryOps slice of Store.

These tests run against every Store implementation (Phase 1A: Postgres only).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

import pytest

from lore.persistence import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    Store,
    StoredMemory,
)
from lore.persistence.exceptions import StoreNotFound


def _vec(seed: int) -> Sequence[float]:
    """Deterministic 384-dim vector seeded by an int."""
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


@pytest.mark.asyncio
async def test_insert_and_get_round_trip(store: Store):
    nm = NewMemory(
        org_id="solo",
        content="how to use pgvector with asyncpg",
        embedding=_vec(1),
        tags=("postgres", "vectors"),
        project="lore",
        confidence=0.9,
        meta={"type": "lesson"},
    )
    inserted = await store.insert_memory(nm)
    assert isinstance(inserted, StoredMemory)
    assert inserted.id
    assert inserted.content == nm.content
    assert tuple(inserted.tags) == ("postgres", "vectors")
    assert inserted.confidence == pytest.approx(0.9)

    fetched = await store.get_memory("solo", inserted.id)
    assert fetched is not None
    assert fetched.id == inserted.id
    assert fetched.content == nm.content


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(store: Store):
    assert await store.get_memory("solo", "mem_does_not_exist") is None


@pytest.mark.asyncio
async def test_get_respects_org_isolation(store: Store):
    nm = NewMemory(org_id="org_a", content="alpha", embedding=_vec(2))
    inserted = await store.insert_memory(nm)
    # Fetching with a different org returns None
    assert await store.get_memory("org_b", inserted.id) is None
    # Fetching with the right org returns the row
    assert (await store.get_memory("org_a", inserted.id)) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/persistence/test_contract_memories.py -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'lore.persistence.postgres'`.

- [ ] **Step 3: Implement `PostgresStore` skeleton + the two methods**

```python
# src/lore/persistence/postgres.py
"""PostgresStore — asyncpg + pgvector implementation of Store.

Phase 1A implements only the MemoryOps slice. Other slices remain in the
existing route SQL until 1B–1G migrate them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]

from ulid import ULID

from lore.persistence.exceptions import BackendUnavailable, StoreNotFound
from lore.persistence.types import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    ScoredMemory,
    StoredMemory,
)


def _row_to_stored(row: "asyncpg.Record") -> StoredMemory:
    tags = row["tags"]
    if isinstance(tags, str):
        tags = json.loads(tags)
    meta = row["meta"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return StoredMemory(
        id=row["id"],
        org_id=row["org_id"],
        content=row["content"],
        context=row["context"],
        tags=tuple(tags or ()),
        confidence=float(row["confidence"]) if row["confidence"] is not None else 0.5,
        source=row["source"],
        project=row["project"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        upvotes=row["upvotes"] or 0,
        downvotes=row["downvotes"] or 0,
        meta=dict(meta or {}),
        importance_score=float(row["importance_score"]) if row["importance_score"] is not None else 1.0,
        access_count=row["access_count"] or 0,
        last_accessed_at=row["last_accessed_at"],
    )


class PostgresStore:
    """Store implementation backed by Postgres+pgvector."""

    def __init__(self, *, pool=None, conn=None):
        if asyncpg is None:
            raise BackendUnavailable(
                "asyncpg is not installed. Install with: pip install lore-sdk[server]"
            )
        if (pool is None) == (conn is None):
            raise ValueError("PostgresStore needs exactly one of pool=, conn=")
        self._pool = pool
        self._conn = conn

    @classmethod
    def from_pool(cls, pool) -> "PostgresStore":
        return cls(pool=pool)

    @classmethod
    def from_connection(cls, conn) -> "PostgresStore":
        """Bind to a specific connection (used by contract tests inside a transaction)."""
        return cls(conn=conn)

    def _acquire(self):
        """Return an async context manager that yields a connection.

        - Pool mode: returns ``self._pool.acquire()`` (asyncpg's PoolAcquireContext).
        - Bound mode: wraps the pre-acquired conn in ``_BoundConn`` so the
          ``async with`` site is identical regardless of mode.
        """
        if self._conn is not None:
            return _BoundConn(self._conn)
        return self._pool.acquire()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    # ── MemoryOps: insert, get ──────────────────────────────────────

    async def insert_memory(self, memory: NewMemory) -> StoredMemory:
        memory_id = f"mem_{ULID()}"
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO memories
                    (id, org_id, content, context, tags, confidence, source,
                     project, embedding, expires_at, meta)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9::vector, $10, $11::jsonb)
                RETURNING id, org_id, content, context, tags, confidence, source,
                          project, created_at, updated_at, expires_at, upvotes,
                          downvotes, meta, importance_score, access_count,
                          last_accessed_at
                """,
                memory_id,
                memory.org_id,
                memory.content,
                memory.context,
                json.dumps(list(memory.tags)),
                memory.confidence,
                memory.source,
                memory.project,
                json.dumps(list(memory.embedding)),
                memory.expires_at,
                json.dumps(dict(memory.meta)),
            )
        return _row_to_stored(row)

    async def get_memory(self, org_id: str, memory_id: str) -> Optional[StoredMemory]:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, org_id, content, context, tags, confidence, source,
                       project, created_at, updated_at, expires_at, upvotes,
                       downvotes, meta, importance_score, access_count,
                       last_accessed_at
                FROM memories
                WHERE id = $1
                  AND org_id = $2
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                memory_id,
                org_id,
            )
        return _row_to_stored(row) if row else None


class _BoundConn:
    """Async context manager that returns a pre-acquired connection without closing it."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/persistence/test_contract_memories.py tests/persistence/test_fixture.py -v`
Expected: 4 passed (3 from contract + 1 fixture smoke). If Postgres is not reachable, tests skip with the message documented in conftest — that is acceptable; ask the operator to start the test DB and re-run.

- [ ] **Step 5: Commit (bundles Task 5's staged conftest)**

```bash
git add src/lore/persistence/postgres.py tests/persistence/test_contract_memories.py tests/persistence/conftest.py tests/persistence/test_fixture.py
git commit -m "feat(persistence): PostgresStore skeleton + insert_memory/get_memory + contract test infra"
```

---

### Task 7: Add `update_memory` + contract test

**Files:**
- Modify: `src/lore/persistence/postgres.py`
- Modify: `tests/persistence/test_contract_memories.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/persistence/test_contract_memories.py

@pytest.mark.asyncio
async def test_update_memory_partial(store: Store):
    inserted = await store.insert_memory(
        NewMemory(org_id="solo", content="original", embedding=_vec(3))
    )
    updated = await store.update_memory(
        "solo",
        inserted.id,
        MemoryPatch(content="rewritten", tags=("edited",)),
    )
    assert updated.content == "rewritten"
    assert tuple(updated.tags) == ("edited",)
    # Confidence not in patch → preserved
    assert updated.confidence == inserted.confidence


@pytest.mark.asyncio
async def test_update_memory_raises_when_missing(store: Store):
    with pytest.raises(StoreNotFound):
        await store.update_memory("solo", "mem_missing", MemoryPatch(content="x"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/persistence/test_contract_memories.py::test_update_memory_partial tests/persistence/test_contract_memories.py::test_update_memory_raises_when_missing -v`
Expected: FAIL — `AttributeError: 'PostgresStore' object has no attribute 'update_memory'`.

- [ ] **Step 3: Implement `update_memory`**

Append to `src/lore/persistence/postgres.py` inside the `PostgresStore` class:

```python
    async def update_memory(
        self,
        org_id: str,
        memory_id: str,
        patch: "MemoryPatch",
    ) -> StoredMemory:
        # Build SET clause from non-None patch fields
        sets: list[str] = []
        params: list = [memory_id, org_id]
        if patch.content is not None:
            params.append(patch.content)
            sets.append(f"content = ${len(params)}")
        if patch.context is not None:
            params.append(patch.context)
            sets.append(f"context = ${len(params)}")
        if patch.tags is not None:
            params.append(json.dumps(list(patch.tags)))
            sets.append(f"tags = ${len(params)}::jsonb")
        if patch.confidence is not None:
            params.append(patch.confidence)
            sets.append(f"confidence = ${len(params)}")
        if patch.source is not None:
            params.append(patch.source)
            sets.append(f"source = ${len(params)}")
        if patch.project is not None:
            params.append(patch.project)
            sets.append(f"project = ${len(params)}")
        if patch.expires_at is not None:
            params.append(patch.expires_at)
            sets.append(f"expires_at = ${len(params)}")
        if patch.meta is not None:
            params.append(json.dumps(dict(patch.meta)))
            sets.append(f"meta = ${len(params)}::jsonb")

        if not sets:
            # No-op patch: just return the current row
            existing = await self.get_memory(org_id, memory_id)
            if existing is None:
                raise StoreNotFound("memories", memory_id)
            return existing

        sets.append("updated_at = now()")
        sql = (
            "UPDATE memories "
            f"SET {', '.join(sets)} "
            "WHERE id = $1 AND org_id = $2 "
            "RETURNING id, org_id, content, context, tags, confidence, source, "
            "project, created_at, updated_at, expires_at, upvotes, downvotes, "
            "meta, importance_score, access_count, last_accessed_at"
        )
        async with self._acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        if row is None:
            raise StoreNotFound("memories", memory_id)
        return _row_to_stored(row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/persistence/test_contract_memories.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lore/persistence/postgres.py tests/persistence/test_contract_memories.py
git commit -m "feat(persistence): MemoryOps.update_memory"
```

---

### Task 8: Add `delete_memory` + contract test

**Files:**
- Modify: `src/lore/persistence/postgres.py`
- Modify: `tests/persistence/test_contract_memories.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/persistence/test_contract_memories.py

@pytest.mark.asyncio
async def test_delete_memory(store: Store):
    inserted = await store.insert_memory(
        NewMemory(org_id="solo", content="to delete", embedding=_vec(4))
    )
    assert (await store.get_memory("solo", inserted.id)) is not None

    deleted = await store.delete_memory("solo", inserted.id)
    assert deleted is True

    assert (await store.get_memory("solo", inserted.id)) is None


@pytest.mark.asyncio
async def test_delete_returns_false_when_missing(store: Store):
    assert (await store.delete_memory("solo", "mem_missing")) is False
```

- [ ] **Step 2: Run tests, expect fail (no `delete_memory` method)**

Run: `pytest tests/persistence/test_contract_memories.py -v -k delete`
Expected: FAIL with AttributeError.

- [ ] **Step 3: Implement `delete_memory`**

Append to `PostgresStore`:

```python
    async def delete_memory(self, org_id: str, memory_id: str) -> bool:
        async with self._acquire() as conn:
            result = await conn.execute(
                "DELETE FROM memories WHERE id = $1 AND org_id = $2",
                memory_id,
                org_id,
            )
        # asyncpg returns "DELETE n"
        return result.endswith(" 1")
```

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest tests/persistence/test_contract_memories.py -v -k delete`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lore/persistence/postgres.py tests/persistence/test_contract_memories.py
git commit -m "feat(persistence): MemoryOps.delete_memory"
```

---

### Task 9: Add `list_memories` + contract test

**Files:**
- Modify: `src/lore/persistence/postgres.py`
- Modify: `tests/persistence/test_contract_memories.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/persistence/test_contract_memories.py

@pytest.mark.asyncio
async def test_list_memories_filters_by_project(store: Store):
    await store.insert_memory(
        NewMemory(org_id="solo", content="a", project="x", embedding=_vec(5))
    )
    await store.insert_memory(
        NewMemory(org_id="solo", content="b", project="y", embedding=_vec(6))
    )
    only_x = await store.list_memories(MemoryFilter(org_id="solo", project="x"))
    assert {m.content for m in only_x} == {"a"}


@pytest.mark.asyncio
async def test_list_memories_respects_limit_and_order(store: Store):
    for i in range(3):
        await store.insert_memory(
            NewMemory(org_id="solo", content=f"item-{i}", embedding=_vec(10 + i))
        )
    rows = await store.list_memories(MemoryFilter(org_id="solo", limit=2))
    assert len(rows) == 2
    # ordered by created_at DESC
    assert rows[0].created_at >= rows[1].created_at


@pytest.mark.asyncio
async def test_list_memories_excludes_expired_by_default(store: Store):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = await store.insert_memory(
        NewMemory(org_id="solo", content="expired", embedding=_vec(20), expires_at=past)
    )
    fresh = await store.insert_memory(
        NewMemory(org_id="solo", content="fresh", embedding=_vec(21))
    )
    visible = await store.list_memories(MemoryFilter(org_id="solo"))
    ids = {m.id for m in visible}
    assert fresh.id in ids
    assert expired.id not in ids

    with_expired = await store.list_memories(
        MemoryFilter(org_id="solo", include_expired=True)
    )
    assert {m.id for m in with_expired} >= {fresh.id, expired.id}
```

- [ ] **Step 2: Run tests, expect fail**

Run: `pytest tests/persistence/test_contract_memories.py -v -k list_memories`
Expected: FAIL with AttributeError.

- [ ] **Step 3: Implement `list_memories`**

Append to `PostgresStore`:

```python
    async def list_memories(
        self, filter: "MemoryFilter"
    ) -> Sequence[StoredMemory]:
        where: list[str] = ["org_id = $1"]
        params: list[Any] = [filter.org_id]
        if filter.project is not None:
            params.append(filter.project)
            where.append(f"project = ${len(params)}")
        if filter.type is not None:
            params.append(filter.type)
            where.append(f"meta->>'type' = ${len(params)}")
        if filter.tier is not None:
            params.append(filter.tier)
            where.append(f"meta->>'tier' = ${len(params)}")
        if filter.tags:
            params.append(json.dumps(list(filter.tags)))
            where.append(f"tags @> ${len(params)}::jsonb")
        if filter.since is not None:
            params.append(filter.since)
            where.append(f"created_at >= ${len(params)}")
        if filter.until is not None:
            params.append(filter.until)
            where.append(f"created_at < ${len(params)}")
        if not filter.include_expired:
            where.append("(expires_at IS NULL OR expires_at > now())")

        sql = (
            "SELECT id, org_id, content, context, tags, confidence, source, "
            "project, created_at, updated_at, expires_at, upvotes, downvotes, "
            "meta, importance_score, access_count, last_accessed_at "
            "FROM memories "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC"
        )
        if filter.limit is not None:
            params.append(filter.limit)
            sql += f" LIMIT ${len(params)}"
        if filter.offset:
            params.append(filter.offset)
            sql += f" OFFSET ${len(params)}"

        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_stored(r) for r in rows]
```

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest tests/persistence/test_contract_memories.py -v -k list_memories`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lore/persistence/postgres.py tests/persistence/test_contract_memories.py
git commit -m "feat(persistence): MemoryOps.list_memories"
```

---

### Task 10: Add embedding fixtures + `recall_by_embedding` + contract test

**Files:**
- Create: `tests/persistence/fixtures/embeddings.json`
- Modify: `src/lore/persistence/postgres.py`
- Modify: `tests/persistence/test_contract_memories.py`

- [ ] **Step 1: Generate the embedding fixture file**

The fixture is 100 strings + their precomputed 384-dim vectors using the existing local embedder. Generate once and commit. The contract tests use these to validate ranking is deterministic.

```python
# scripts/generate_embedding_fixtures.py — one-off; run manually
"""Generate tests/persistence/fixtures/embeddings.json."""

import json
from pathlib import Path

from lore.embed.local import LocalEmbedder

STRINGS = [
    "pgvector cosine distance operator", "asyncpg connection pool sizing",
    "fastapi dependency injection test fixture", "rate limit token bucket",
    "openai gpt-4o-mini cost", "postgres jsonb GIN index",
    "ulid vs uuid comparison", "vector embedding 384 dimensions",
    "knn nearest neighbor search", "claude code mcp protocol",
    # … total 100 strings, deduplicated, varied topics
]

# In actual implementation include all 100 strings here. For brevity in
# this plan, 10 are shown. The real list lives in this script when run.

emb = LocalEmbedder()
out = []
for s in STRINGS:
    v = emb.embed(s)
    out.append({"text": s, "embedding": list(v)})

fixture_path = Path(__file__).parent.parent / "tests/persistence/fixtures/embeddings.json"
fixture_path.parent.mkdir(parents=True, exist_ok=True)
fixture_path.write_text(json.dumps(out, indent=2))
print(f"Wrote {len(out)} fixtures to {fixture_path}")
```

Run it: `python scripts/generate_embedding_fixtures.py`. Commit the generated JSON.

(If the contract test only needs a few items for ranking validation, 20 strings is enough. Pick a number; the test asserts deterministic ranking, not coverage.)

- [ ] **Step 2: Append the failing tests**

```python
# Append to tests/persistence/test_contract_memories.py
import json as _json
from pathlib import Path as _Path

_FIXTURES = _json.loads(
    (_Path(__file__).parent / "fixtures" / "embeddings.json").read_text()
)


@pytest.mark.asyncio
async def test_recall_by_embedding_returns_ranked_results(store: Store):
    # Insert 5 fixture memories
    inserted = []
    for i, item in enumerate(_FIXTURES[:5]):
        m = await store.insert_memory(
            NewMemory(
                org_id="solo",
                content=item["text"],
                embedding=item["embedding"],
            )
        )
        inserted.append((m, item))

    # Query with the embedding of the first item — it should rank #1
    target = inserted[0][1]
    results = await store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target["embedding"],
            limit=5,
            min_score=0.0,
        )
    )
    assert len(results) >= 1
    assert results[0].content == target["text"]
    # Score is in [0, 1] for cosine-similarity-derived score
    assert 0.0 <= results[0].score <= 1.0


@pytest.mark.asyncio
async def test_recall_respects_min_score(store: Store):
    target = _FIXTURES[0]
    other = _FIXTURES[5] if len(_FIXTURES) > 5 else _FIXTURES[1]
    await store.insert_memory(
        NewMemory(org_id="solo", content=target["text"], embedding=target["embedding"])
    )
    await store.insert_memory(
        NewMemory(org_id="solo", content=other["text"], embedding=other["embedding"])
    )
    # min_score=0.999 should exclude the unrelated entry
    results = await store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target["embedding"],
            limit=10,
            min_score=0.999,
        )
    )
    assert all(r.score >= 0.999 for r in results)
```

- [ ] **Step 3: Run tests, expect fail**

Run: `pytest tests/persistence/test_contract_memories.py -v -k recall`
Expected: FAIL with AttributeError.

- [ ] **Step 4: Implement `recall_by_embedding`**

The SQL is the same shape as today's `routes/retrieve.py`, but moved into the Store. Append to `PostgresStore`:

```python
    async def recall_by_embedding(
        self, params: "RecallParams"
    ) -> Sequence[ScoredMemory]:
        where: list[str] = ["org_id = $1"]
        sql_params: list[Any] = [params.org_id]
        if params.project is not None:
            sql_params.append(params.project)
            where.append(f"project = ${len(sql_params)}")
        if params.exclude_expired:
            where.append("(expires_at IS NULL OR expires_at > now())")
        where.append("embedding IS NOT NULL")

        sql_params.append(json.dumps(list(params.query_vec)))
        emb_idx = len(sql_params)
        sql_params.append(params.min_score)
        score_idx = len(sql_params)
        sql_params.append(params.limit)
        limit_idx = len(sql_params)

        sql = f"""
            SELECT id, org_id, content, context, tags, confidence, source, project,
                   created_at, updated_at, expires_at, upvotes, downvotes, meta,
                   importance_score, access_count, last_accessed_at,
                   (1 - (embedding <=> ${emb_idx}::vector)) *
                   COALESCE(importance_score, 1.0) *
                   power(0.5,
                       LEAST(
                           EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0,
                           COALESCE(
                               EXTRACT(EPOCH FROM (now() - last_accessed_at)) / 86400.0,
                               EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0
                           )
                       )
                       / {params.half_life_days}
                   ) AS score
            FROM memories
            WHERE {' AND '.join(where)}
              AND (1 - (embedding <=> ${emb_idx}::vector)) >= ${score_idx}
            ORDER BY score DESC
            LIMIT ${limit_idx}
        """
        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *sql_params)
        scored: list[ScoredMemory] = []
        for r in rows:
            sm = _row_to_stored(r)
            scored.append(
                ScoredMemory(
                    id=sm.id,
                    org_id=sm.org_id,
                    content=sm.content,
                    context=sm.context,
                    tags=sm.tags,
                    confidence=sm.confidence,
                    source=sm.source,
                    project=sm.project,
                    created_at=sm.created_at,
                    updated_at=sm.updated_at,
                    expires_at=sm.expires_at,
                    upvotes=sm.upvotes,
                    downvotes=sm.downvotes,
                    meta=sm.meta,
                    importance_score=sm.importance_score,
                    access_count=sm.access_count,
                    last_accessed_at=sm.last_accessed_at,
                    score=float(r["score"]),
                )
            )
        return scored
```

(`StoredMemory` and `ScoredMemory` are `@dataclass(frozen=True, slots=True)` — slot classes don't expose `__dict__`, so explicit field copying is the correct form.)

- [ ] **Step 5: Run tests, expect pass**

Run: `pytest tests/persistence/test_contract_memories.py -v -k recall`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/persistence/fixtures/embeddings.json scripts/generate_embedding_fixtures.py src/lore/persistence/postgres.py tests/persistence/test_contract_memories.py
git commit -m "feat(persistence): MemoryOps.recall_by_embedding + embedding fixtures"
```

---

### Task 11: Add `expire_memories` + contract test

**Files:**
- Modify: `src/lore/persistence/postgres.py`
- Modify: `tests/persistence/test_contract_memories.py`

- [ ] **Step 1: Append the failing test**

```python
# Append to tests/persistence/test_contract_memories.py

@pytest.mark.asyncio
async def test_expire_memories_deletes_past_expiry(store: Store):
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = await store.insert_memory(
        NewMemory(org_id="solo", content="expired", embedding=_vec(30), expires_at=past)
    )
    keep = await store.insert_memory(
        NewMemory(org_id="solo", content="alive", embedding=_vec(31))
    )
    n = await store.expire_memories()
    assert n >= 1
    assert (await store.get_memory("solo", expired.id)) is None
    assert (await store.get_memory("solo", keep.id)) is not None
```

- [ ] **Step 2: Run, expect fail**

Run: `pytest tests/persistence/test_contract_memories.py -v -k expire`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
    async def expire_memories(self) -> int:
        async with self._acquire() as conn:
            result = await conn.execute(
                "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < now()"
            )
        # asyncpg "DELETE n"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/persistence/test_contract_memories.py -v -k expire`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lore/persistence/postgres.py tests/persistence/test_contract_memories.py
git commit -m "feat(persistence): MemoryOps.expire_memories"
```

---

### Task 12: Add `bump_access_counts` + contract test

**Files:**
- Modify: `src/lore/persistence/postgres.py`
- Modify: `tests/persistence/test_contract_memories.py`

- [ ] **Step 1: Append the failing test**

```python
# Append to tests/persistence/test_contract_memories.py

@pytest.mark.asyncio
async def test_bump_access_counts_increments(store: Store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="popular", embedding=_vec(40))
    )
    assert m.access_count == 0
    await store.bump_access_counts([m.id])
    after = await store.get_memory("solo", m.id)
    assert after is not None
    assert after.access_count == 1
    assert after.last_accessed_at is not None
```

- [ ] **Step 2: Run, expect fail**

Run: `pytest tests/persistence/test_contract_memories.py -v -k bump`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
    async def bump_access_counts(self, memory_ids: Sequence[str]) -> None:
        if not memory_ids:
            return
        async with self._acquire() as conn:
            await conn.execute(
                """
                UPDATE memories
                SET access_count = COALESCE(access_count, 0) + 1,
                    last_accessed_at = now(),
                    importance_score = COALESCE(confidence, 1.0)
                        * GREATEST(0.1, 1.0 + (COALESCE(upvotes, 0) - COALESCE(downvotes, 0)) * 0.1)
                        * (1.0 + ln(COALESCE(access_count, 0) + 2) / ln(2) * 0.1)
                WHERE id = ANY($1)
                """,
                list(memory_ids),
            )
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/persistence/test_contract_memories.py -v -k bump`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lore/persistence/postgres.py tests/persistence/test_contract_memories.py
git commit -m "feat(persistence): MemoryOps.bump_access_counts"
```

---

### Task 13: Add `vote_memory` + contract test

**Files:**
- Modify: `src/lore/persistence/postgres.py`
- Modify: `tests/persistence/test_contract_memories.py`

- [ ] **Step 1: Append the failing test**

```python
# Append to tests/persistence/test_contract_memories.py

@pytest.mark.asyncio
async def test_vote_memory_up_and_down(store: Store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="rate me", embedding=_vec(50))
    )
    after_up = await store.vote_memory("solo", m.id, direction="up")
    assert after_up.upvotes == 1

    after_down = await store.vote_memory("solo", m.id, direction="down")
    assert after_down.downvotes == 1


@pytest.mark.asyncio
async def test_vote_memory_invalid_direction(store: Store):
    m = await store.insert_memory(
        NewMemory(org_id="solo", content="x", embedding=_vec(51))
    )
    with pytest.raises(ValueError):
        await store.vote_memory("solo", m.id, direction="sideways")


@pytest.mark.asyncio
async def test_vote_memory_raises_when_missing(store: Store):
    with pytest.raises(StoreNotFound):
        await store.vote_memory("solo", "mem_missing", direction="up")
```

- [ ] **Step 2: Run, expect fail**

Run: `pytest tests/persistence/test_contract_memories.py -v -k vote`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
    async def vote_memory(
        self,
        org_id: str,
        memory_id: str,
        *,
        direction: str,
    ) -> StoredMemory:
        if direction == "up":
            column = "upvotes"
        elif direction == "down":
            column = "downvotes"
        else:
            raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")

        async with self._acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE memories
                SET {column} = COALESCE({column}, 0) + 1,
                    updated_at = now()
                WHERE id = $1 AND org_id = $2
                RETURNING id, org_id, content, context, tags, confidence, source,
                          project, created_at, updated_at, expires_at, upvotes,
                          downvotes, meta, importance_score, access_count,
                          last_accessed_at
                """,
                memory_id,
                org_id,
            )
        if row is None:
            raise StoreNotFound("memories", memory_id)
        return _row_to_stored(row)
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/persistence/test_contract_memories.py -v -k vote`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lore/persistence/postgres.py tests/persistence/test_contract_memories.py
git commit -m "feat(persistence): MemoryOps.vote_memory"
```

---

### Task 14: Implement factory + URL parsing

**Files:**
- Create: `src/lore/persistence/factory.py`
- Create: `tests/persistence/test_factory.py`
- Modify: `src/lore/persistence/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/persistence/test_factory.py
"""Tests for make_store URL routing."""

from __future__ import annotations

import pytest

from lore.persistence import ConfigError
from lore.persistence.factory import make_store
from lore.persistence.postgres import PostgresStore


def test_postgres_url_returns_postgres_store(monkeypatch):
    # Build a store synchronously from URL — for Postgres this requires a pool,
    # but the factory's contract is to return the right *type*; pool creation
    # is deferred to first use OR done eagerly. The factory is async because
    # asyncpg.create_pool is async.
    import asyncio

    async def _go():
        store = await make_store("postgresql://lore:lore@localhost:5432/lore_test")
        try:
            assert isinstance(store, PostgresStore)
        finally:
            await store.close()

    try:
        asyncio.run(_go())
    except (OSError, ConnectionRefusedError, Exception) as e:
        if "lore_test" in str(e) or "Connection refused" in str(e):
            pytest.skip(f"Test DB not reachable: {e}")
        raise


@pytest.mark.asyncio
async def test_unknown_scheme_raises_config_error():
    with pytest.raises(ConfigError) as ei:
        await make_store("mongodb://localhost/foo")
    assert "scheme" in str(ei.value).lower()
    assert "mongodb" in str(ei.value)


@pytest.mark.asyncio
async def test_sqlite_scheme_raises_until_phase_3():
    with pytest.raises(ConfigError) as ei:
        await make_store("sqlite:///./test.db")
    assert "Phase 3" in str(ei.value) or "not yet" in str(ei.value).lower()
```

- [ ] **Step 2: Run, expect fail**

Run: `pytest tests/persistence/test_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: lore.persistence.factory`.

- [ ] **Step 3: Implement `factory.py`**

```python
# src/lore/persistence/factory.py
"""Pick a Store implementation from a database URL."""

from __future__ import annotations

from urllib.parse import urlparse

from lore.persistence.exceptions import ConfigError
from lore.persistence.protocol import Store


async def make_store(database_url: str) -> Store:
    """Build a Store from a database URL.

    Supported schemes:
    - postgresql://..., postgres://...    -> PostgresStore (requires lore-sdk[server])
    - sqlite:///path/to/file.db            -> SqliteStore (Phase 3+; not yet implemented)

    Raises ConfigError on unknown or unsupported schemes.
    """
    scheme = urlparse(database_url).scheme.lower()
    if scheme in ("postgres", "postgresql"):
        try:
            import asyncpg
        except ImportError as e:
            raise ConfigError(
                "asyncpg is required for postgres URLs. "
                "Install with: pip install lore-sdk[server]"
            ) from e
        from lore.persistence.postgres import PostgresStore

        pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
        return PostgresStore.from_pool(pool)
    if scheme == "sqlite":
        raise ConfigError(
            "sqlite:// URLs are not yet supported (Phase 3 of solo-mode work). "
            "Use a postgresql:// URL until then."
        )
    raise ConfigError(
        f"Unsupported database_url scheme: {scheme!r}. "
        "Supported schemes: postgresql://, sqlite:// (coming in Phase 3)."
    )
```

- [ ] **Step 4: Update `__init__.py` to re-export**

Add `make_store` to the imports and `__all__`:

```python
# src/lore/persistence/__init__.py — add to imports
from lore.persistence.factory import make_store

# add to __all__
__all__ = [
    # … existing entries …
    "make_store",
]
```

- [ ] **Step 5: Run, expect pass**

Run: `pytest tests/persistence/test_factory.py -v`
Expected: 3 passed (or 1 passed + 2 if Postgres unreachable).

- [ ] **Step 6: Commit**

```bash
git add src/lore/persistence/factory.py src/lore/persistence/__init__.py tests/persistence/test_factory.py
git commit -m "feat(persistence): make_store factory keyed by URL scheme"
```

---

### Task 15: Wire the Store into FastAPI lifespan via DI

**Files:**
- Modify: `src/lore/server/db.py`
- Modify: `src/lore/server/app.py`
- Test: `tests/server/test_store_di.py` (new)

The server today exposes `get_pool()` from `lore.server.db`. We add a parallel `get_store()` for the new abstraction without removing `get_pool()` — other routes still use it until they migrate in 1B+.

- [ ] **Step 1: Write the failing test**

```python
# tests/server/test_store_di.py
"""Tests that lifespan creates a Store and exposes it via Depends."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_store_raises_before_init():
    # Ensure clean module state
    import importlib

    from lore.server import db as server_db

    importlib.reload(server_db)
    with pytest.raises(RuntimeError):
        await server_db.get_store()


@pytest.mark.asyncio
async def test_init_store_then_get_store():
    import os
    from lore.server import db as server_db
    from lore.persistence.postgres import PostgresStore

    db_url = os.environ.get(
        "LORE_TEST_DATABASE_URL", "postgresql://lore:lore@localhost:5432/lore_test"
    )
    try:
        await server_db.init_store(db_url)
    except (OSError, ConnectionRefusedError, Exception) as e:
        pytest.skip(f"DB not reachable: {e}")
    try:
        store = await server_db.get_store()
        assert isinstance(store, PostgresStore)
    finally:
        await server_db.close_store()
```

- [ ] **Step 2: Run, expect fail**

Run: `pytest tests/server/test_store_di.py -v`
Expected: FAIL — `init_store` and `get_store` don't exist.

- [ ] **Step 3: Update `lore/server/db.py`**

Add the store-related globals alongside existing pool functions:

```python
# Append to src/lore/server/db.py

_store: "Store | None" = None  # type: ignore[assignment]


async def init_store(database_url: str) -> "Store":
    """Create and store the global Store. Idempotent."""
    global _store
    from lore.persistence.factory import make_store

    if _store is None:
        _store = await make_store(database_url)
        logger.info("Store initialized: %s", type(_store).__name__)
    return _store


async def get_store() -> "Store":
    if _store is None:
        raise RuntimeError("Store not initialized. Call init_store() first.")
    return _store


async def close_store() -> None:
    global _store
    if _store is not None:
        await _store.close()
        _store = None
        logger.info("Store closed")
```

Add the import line at the top of the file:

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from lore.persistence.protocol import Store
```

- [ ] **Step 4: Update `lore/server/app.py` lifespan**

Find the existing lifespan (or `@app.on_event("startup")` / `("shutdown")`) and add the store init/close. Concretely:

```python
# In src/lore/server/app.py — lifespan or startup/shutdown handlers
# Add the store creation alongside the existing pool init.

from lore.server.db import close_store, init_store, init_pool, close_pool

@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = settings.database_url
    await init_pool(database_url)        # existing
    await init_store(database_url)       # NEW
    try:
        yield
    finally:
        await close_store()              # NEW
        await close_pool()               # existing
```

If the existing app uses event handlers instead of `lifespan`, mirror the existing style — add `init_store` next to `init_pool` in startup, and `close_store` next to `close_pool` in shutdown.

- [ ] **Step 5: Run, expect pass**

Run: `pytest tests/server/test_store_di.py -v`
Expected: 2 passed (or 1 + skip if DB not reachable).

- [ ] **Step 6: Commit**

```bash
git add src/lore/server/db.py src/lore/server/app.py tests/server/test_store_di.py
git commit -m "feat(server): expose Store via lifespan + get_store dependency"
```

---

### Task 16: Build the memories service layer

**Files:**
- Create: `src/lore/services/memories.py`
- Test: `tests/services/__init__.py`, `tests/services/test_memories.py`

The service module wraps Store calls with the bits today's route does inline: enrichment kickoff, tag normalization, expires-at computation, plugin hooks. For Phase 1A, services keep the same observable behavior as today's route.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_memories.py
"""Service-level tests using a real Postgres store."""

from __future__ import annotations

import pytest

from lore.persistence import MemoryFilter, NewMemory
from lore.services.memories import (
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    search_memories,
    update_memory,
    vote_memory,
)


@pytest.mark.asyncio
async def test_create_then_get(store):
    created = await create_memory(
        store,
        org_id="solo",
        content="hello world",
        embedding=[0.0] * 384,
        tags=["a", "b"],
        project="proj",
    )
    fetched = await get_memory(store, "solo", created.id)
    assert fetched is not None
    assert fetched.content == "hello world"
    assert tuple(fetched.tags) == ("a", "b")


@pytest.mark.asyncio
async def test_update_then_get(store):
    created = await create_memory(
        store, org_id="solo", content="orig", embedding=[0.0] * 384
    )
    updated = await update_memory(
        store, org_id="solo", memory_id=created.id, content="updated"
    )
    assert updated.content == "updated"


@pytest.mark.asyncio
async def test_list_filters(store):
    await create_memory(store, org_id="solo", content="a", embedding=[0.0] * 384, project="x")
    await create_memory(store, org_id="solo", content="b", embedding=[0.0] * 384, project="y")
    only_x = await list_memories(store, org_id="solo", project="x")
    assert {m.content for m in only_x} == {"a"}


@pytest.mark.asyncio
async def test_delete(store):
    created = await create_memory(
        store, org_id="solo", content="bye", embedding=[0.0] * 384
    )
    deleted = await delete_memory(store, org_id="solo", memory_id=created.id)
    assert deleted is True
    assert (await get_memory(store, "solo", created.id)) is None


@pytest.mark.asyncio
async def test_vote(store):
    created = await create_memory(
        store, org_id="solo", content="rate me", embedding=[0.0] * 384
    )
    after = await vote_memory(store, org_id="solo", memory_id=created.id, direction="up")
    assert after.upvotes == 1
```

(Reuse the same `store` fixture from `tests/persistence/conftest.py` — make services tests share that conftest by symlinking, importing it explicitly, or moving it up. The simplest path: add `from tests.persistence.conftest import store, _pg_pool  # noqa: F401` in `tests/services/conftest.py`.)

Create `tests/services/__init__.py` (empty) and `tests/services/conftest.py`:

```python
# tests/services/conftest.py
"""Reuse the persistence-layer store fixture for service tests."""

from tests.persistence.conftest import _pg_pool, store  # noqa: F401
```

- [ ] **Step 2: Run, expect fail**

Run: `pytest tests/services/test_memories.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lore.services.memories'`.

- [ ] **Step 3: Implement `services/memories.py`**

```python
# src/lore/services/memories.py
"""Memory CRUD + search service functions.

Pure async functions: take a Store and typed params, return dataclasses.
Routes and AsyncLore both call into here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from lore.persistence import (
    MemoryFilter,
    MemoryPatch,
    NewMemory,
    RecallParams,
    ScoredMemory,
    Store,
    StoredMemory,
)


async def create_memory(
    store: Store,
    *,
    org_id: str,
    content: str,
    embedding: Sequence[float],
    context: Optional[str] = None,
    tags: Sequence[str] = (),
    confidence: float = 0.5,
    source: Optional[str] = None,
    project: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    meta: Optional[Mapping[str, Any]] = None,
) -> StoredMemory:
    """Insert a memory. Tag normalization and meta defaulting happen here."""
    normalized_tags = tuple(t.strip() for t in tags if t and t.strip())
    return await store.insert_memory(
        NewMemory(
            org_id=org_id,
            content=content,
            embedding=embedding,
            context=context,
            tags=normalized_tags,
            confidence=confidence,
            source=source,
            project=project,
            expires_at=expires_at,
            meta=dict(meta or {}),
        )
    )


async def get_memory(
    store: Store, org_id: str, memory_id: str
) -> Optional[StoredMemory]:
    return await store.get_memory(org_id, memory_id)


async def update_memory(
    store: Store,
    *,
    org_id: str,
    memory_id: str,
    content: Optional[str] = None,
    context: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    confidence: Optional[float] = None,
    source: Optional[str] = None,
    project: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    meta: Optional[Mapping[str, Any]] = None,
) -> StoredMemory:
    patch = MemoryPatch(
        content=content,
        context=context,
        tags=tuple(tags) if tags is not None else None,
        confidence=confidence,
        source=source,
        project=project,
        expires_at=expires_at,
        meta=dict(meta) if meta is not None else None,
    )
    return await store.update_memory(org_id, memory_id, patch)


async def delete_memory(
    store: Store, *, org_id: str, memory_id: str
) -> bool:
    return await store.delete_memory(org_id, memory_id)


async def list_memories(
    store: Store,
    *,
    org_id: str,
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    include_expired: bool = False,
) -> Sequence[StoredMemory]:
    return await store.list_memories(
        MemoryFilter(
            org_id=org_id,
            project=project,
            type=type,
            tier=tier,
            tags=tuple(tags) if tags is not None else None,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
            include_expired=include_expired,
        )
    )


async def search_memories(
    store: Store,
    *,
    org_id: str,
    query_vec: Sequence[float],
    limit: int = 5,
    min_score: float = 0.3,
    project: Optional[str] = None,
    half_life_days: int = 30,
) -> Sequence[ScoredMemory]:
    return await store.recall_by_embedding(
        RecallParams(
            org_id=org_id,
            query_vec=query_vec,
            limit=limit,
            min_score=min_score,
            project=project,
            half_life_days=half_life_days,
        )
    )


async def vote_memory(
    store: Store, *, org_id: str, memory_id: str, direction: str
) -> StoredMemory:
    return await store.vote_memory(org_id, memory_id, direction=direction)
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/services/test_memories.py -v`
Expected: 5 passed (or skip-blanket if DB not reachable).

- [ ] **Step 5: Commit**

```bash
git add src/lore/services/memories.py tests/services/__init__.py tests/services/conftest.py tests/services/test_memories.py
git commit -m "feat(services): add memories service layer over Store"
```

---

### Task 17: Refactor `routes/memories.py` POST handler to call service

The route file is 484 lines. We migrate handlers one at a time, starting with `POST /v1/memories` (create). Each migration is its own commit so regressions can be bisected.

**Files:**
- Modify: `src/lore/server/routes/memories.py`

- [ ] **Step 1: Identify the existing handler**

Open `src/lore/server/routes/memories.py` and find the create handler (around line 100–180; it's the function decorated with `@router.post("")` or `@router.post("/")`). It currently builds an INSERT statement inline using `pool` from `get_pool()`.

- [ ] **Step 2: Replace handler body with service call**

The handler keeps its decorator, FastAPI request model, response model, and auth dependency. Only the body changes — from "build SQL + execute" to "call service + serialize."

```python
# Inside src/lore/server/routes/memories.py — replace the create handler

from lore.server.db import get_store
from lore.services.memories import create_memory as _create_memory

# Keep imports of MemoryCreateRequest, MemoryCreateResponse, _row_to_response
# (already in this file)

@router.post("", response_model=MemoryCreateResponse, status_code=201)
async def create_memory(
    body: MemoryCreateRequest,
    auth: AuthContext = Depends(require_role("writer")),
):
    """Create a memory. Routes layer: parse → call service → serialize."""
    store = await get_store()

    # Embedding stays at this layer for now — the route already owns the
    # embedder singleton. Phase 1B will move embedding into the service or
    # into a `lore.embed.async_embedder` shared dependency. For now:
    from lore.server.routes.retrieve import _get_embedder
    embedder = _get_embedder()
    embedding = embedder.embed(body.content)

    stored = await _create_memory(
        store,
        org_id=auth.org_id,
        content=body.content,
        context=body.context,
        embedding=embedding,
        tags=body.tags or [],
        confidence=body.confidence if body.confidence is not None else 0.5,
        source=body.source,
        project=auth.project or body.project,
        expires_at=body.expires_at,
        meta=body.meta or {},
    )

    # Fire-and-forget enrichment unchanged from today — keep call site
    asyncio.create_task(_enrich_memory(stored.id, stored.content, stored.context))

    return MemoryCreateResponse(id=stored.id, created_at=stored.created_at)
```

Remove the now-unused inline SQL block and the helper `_scope_filter` if its only callers in this file have been refactored — leave it for later handlers that still need it.

- [ ] **Step 3: Run existing route tests, expect pass**

Run: `pytest tests/server/ -v -k memories or memory`
Expected: existing tests pass (response shape is unchanged).

If any test fails because it asserted internal SQL structure (it shouldn't — tests assert HTTP responses), revisit.

- [ ] **Step 4: Run integration tests for the create path**

Run: `pytest tests/integration/ -v -k create or memory` (or the equivalent in this repo's layout).
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/lore/server/routes/memories.py
git commit -m "refactor(routes): POST /v1/memories uses memories service"
```

---

### Task 18: Refactor `routes/memories.py` GET (single) handler

**Files:**
- Modify: `src/lore/server/routes/memories.py`

- [ ] **Step 1: Find the GET /{id} handler**

Find `@router.get("/{memory_id}", ...)`.

- [ ] **Step 2: Replace body with service call**

```python
from lore.services.memories import get_memory as _get_memory


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory_endpoint(
    memory_id: str,
    auth: AuthContext = Depends(require_role("reader")),
):
    store = await get_store()
    m = await _get_memory(store, auth.org_id, memory_id)
    if m is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return MemoryResponse(
        id=m.id,
        content=m.content,
        context=m.context,
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/server/ tests/integration/ -v -k memory`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add src/lore/server/routes/memories.py
git commit -m "refactor(routes): GET /v1/memories/{id} uses memories service"
```

---

### Task 19: Refactor `routes/memories.py` LIST handler

**Files:**
- Modify: `src/lore/server/routes/memories.py`

- [ ] **Step 1: Find `@router.get("")` LIST handler**

- [ ] **Step 2: Replace body with service call**

```python
from lore.services.memories import list_memories as _list_memories


@router.get("", response_model=MemoryListResponse)
async def list_memories_endpoint(
    project: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_expired: bool = Query(False),
    auth: AuthContext = Depends(require_role("reader")),
):
    store = await get_store()
    rows = await _list_memories(
        store,
        org_id=auth.org_id,
        project=auth.project or project,
        type=type,
        tier=tier,
        limit=limit,
        offset=offset,
        include_expired=include_expired,
    )
    return MemoryListResponse(
        memories=[_stored_to_memory_response(m) for m in rows],
        total=len(rows),
    )


def _stored_to_memory_response(m) -> MemoryResponse:
    return MemoryResponse(
        id=m.id, content=m.content, context=m.context, tags=list(m.tags),
        confidence=m.confidence, source=m.source, project=m.project,
        created_at=m.created_at, updated_at=m.updated_at, expires_at=m.expires_at,
        upvotes=m.upvotes, downvotes=m.downvotes, meta=dict(m.meta),
    )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/server/ tests/integration/ -v -k memory or list`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add src/lore/server/routes/memories.py
git commit -m "refactor(routes): GET /v1/memories uses memories service"
```

---

### Task 20: Refactor PATCH/DELETE handlers

**Files:**
- Modify: `src/lore/server/routes/memories.py`

- [ ] **Step 1: Replace PATCH handler body**

```python
from lore.persistence.exceptions import StoreNotFound
from lore.services.memories import update_memory as _update_memory


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory_endpoint(
    memory_id: str,
    body: MemoryUpdateRequest,
    auth: AuthContext = Depends(require_role("writer")),
):
    store = await get_store()
    try:
        updated = await _update_memory(
            store,
            org_id=auth.org_id,
            memory_id=memory_id,
            content=body.content,
            context=body.context,
            tags=body.tags,
            confidence=body.confidence,
            source=body.source,
            project=body.project,
            expires_at=body.expires_at,
            meta=body.meta,
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="memory not found")
    return _stored_to_memory_response(updated)
```

- [ ] **Step 2: Replace DELETE handler body**

```python
from lore.services.memories import delete_memory as _delete_memory


@router.delete("/{memory_id}", status_code=204)
async def delete_memory_endpoint(
    memory_id: str,
    auth: AuthContext = Depends(require_role("writer")),
):
    store = await get_store()
    deleted = await _delete_memory(store, org_id=auth.org_id, memory_id=memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found")
```

- [ ] **Step 3: Run tests, expect pass**

Run: `pytest tests/server/ tests/integration/ -v -k "memor and (update or patch or delete)"`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add src/lore/server/routes/memories.py
git commit -m "refactor(routes): PATCH/DELETE /v1/memories/{id} use memories service"
```

---

### Task 21: Refactor SEARCH (POST /v1/memories/search) handler

**Files:**
- Modify: `src/lore/server/routes/memories.py`

- [ ] **Step 1: Replace handler body**

```python
from lore.services.memories import search_memories as _search_memories


@router.post("/search", response_model=MemorySearchResponse)
async def search_memories_endpoint(
    body: MemorySearchRequest,
    auth: AuthContext = Depends(require_role("reader")),
):
    store = await get_store()

    from lore.server.routes.retrieve import _get_embedder
    embedder = _get_embedder()
    query_vec = embedder.embed(body.query)

    results = await _search_memories(
        store,
        org_id=auth.org_id,
        query_vec=query_vec,
        limit=body.limit or 5,
        min_score=body.min_score if body.min_score is not None else 0.3,
        project=auth.project or body.project,
    )
    return MemorySearchResponse(
        results=[
            MemorySearchResult(
                id=r.id, content=r.content, score=r.score, project=r.project,
                tags=list(r.tags), source=r.source, created_at=r.created_at,
            )
            for r in results
        ],
        count=len(results),
    )
```

- [ ] **Step 2: Run tests, expect pass**

Run: `pytest tests/server/ tests/integration/ -v -k search`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add src/lore/server/routes/memories.py
git commit -m "refactor(routes): POST /v1/memories/search uses memories service"
```

---

### Task 22: Refactor vote endpoints

**Files:**
- Modify: `src/lore/server/routes/memories.py`

- [ ] **Step 1: Replace upvote/downvote handlers**

```python
from lore.services.memories import vote_memory as _vote_memory


@router.post("/{memory_id}/upvote")
async def upvote_memory_endpoint(
    memory_id: str,
    auth: AuthContext = Depends(require_role("writer")),
):
    store = await get_store()
    try:
        updated = await _vote_memory(
            store, org_id=auth.org_id, memory_id=memory_id, direction="up"
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"id": updated.id, "upvotes": updated.upvotes, "downvotes": updated.downvotes}


@router.post("/{memory_id}/downvote")
async def downvote_memory_endpoint(
    memory_id: str,
    auth: AuthContext = Depends(require_role("writer")),
):
    store = await get_store()
    try:
        updated = await _vote_memory(
            store, org_id=auth.org_id, memory_id=memory_id, direction="down"
        )
    except StoreNotFound:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"id": updated.id, "upvotes": updated.upvotes, "downvotes": updated.downvotes}
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/server/ tests/integration/ -v -k vote`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add src/lore/server/routes/memories.py
git commit -m "refactor(routes): vote endpoints use memories service"
```

---

### Task 23: Build retrieve service (used by GET /v1/retrieve)

**Files:**
- Create: `src/lore/services/retrieve.py`
- Test: `tests/services/test_retrieve.py`

The retrieve service is more involved than the memories service because it: embeds, applies a profile, calls the store, optionally appends session-snapshot context, and records analytics. Phase 1A keeps analytics recording inline at the route layer (it touches a different table and Phase 1A's Store doesn't yet expose `write_analytics_row`); the retrieve service handles everything else.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_retrieve.py
"""Tests for the retrieve service (without analytics — that's left at the route)."""

from __future__ import annotations

import pytest

from lore.services.memories import create_memory
from lore.services.retrieve import retrieve, RetrieveOutput


@pytest.mark.asyncio
async def test_retrieve_returns_ranked_memories(store):
    # Insert one memory; query with the same embedding
    embed = [0.1] * 384
    await create_memory(
        store, org_id="solo", content="alpha doc", embedding=embed
    )
    out: RetrieveOutput = await retrieve(
        store,
        org_id="solo",
        query_text="alpha",
        query_vec=embed,
        limit=5,
        min_score=0.0,
    )
    assert out.count >= 1
    assert any(m.content == "alpha doc" for m in out.memories)
    assert isinstance(out.formatted, str)


@pytest.mark.asyncio
async def test_retrieve_format_xml(store):
    embed = [0.2] * 384
    await create_memory(
        store, org_id="solo", content="xml me", embedding=embed
    )
    out = await retrieve(
        store, org_id="solo", query_text="xml", query_vec=embed,
        limit=5, min_score=0.0, format="xml",
    )
    assert "<memories" in out.formatted


@pytest.mark.asyncio
async def test_retrieve_invalid_format_raises():
    with pytest.raises(ValueError):
        await retrieve(
            store=None,  # type: ignore[arg-type]
            org_id="solo",
            query_text="x",
            query_vec=[0.0] * 384,
            limit=5,
            min_score=0.0,
            format="bogus",
        )
```

- [ ] **Step 2: Run, expect fail**

Run: `pytest tests/services/test_retrieve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lore.services.retrieve'`.

- [ ] **Step 3: Implement `services/retrieve.py`**

```python
# src/lore/services/retrieve.py
"""Retrieve service: vector recall + formatting + session-context injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from lore.persistence import RecallParams, ScoredMemory, Store

VALID_FORMATS = {"xml", "markdown", "raw"}


@dataclass(frozen=True)
class RetrieveOutput:
    memories: Sequence[ScoredMemory]
    formatted: str
    count: int


def _format_xml(memories: Sequence[ScoredMemory], query: str) -> str:
    if not memories:
        return ""
    lines = [f'<memories query="{query}">']
    for m in memories:
        m_type = (m.meta or {}).get("type", "unknown")
        lines.append(f'  <memory id="{m.id}" score="{m.score:.2f}" type="{m_type}">')
        lines.append(f"    {m.content}")
        lines.append("  </memory>")
    lines.append("</memories>")
    return "\n".join(lines)


def _format_markdown(memories: Sequence[ScoredMemory], query: str) -> str:
    if not memories:
        return ""
    lines = [f"## Relevant Memories ({len(memories)})\n"]
    for m in memories:
        lines.append(f"- **[{m.score:.2f}]** {m.content}")
    return "\n".join(lines)


def _format_raw(memories: Sequence[ScoredMemory], query: str) -> str:
    return "\n".join(m.content for m in memories) if memories else ""


_FORMATTERS = {
    "xml": _format_xml,
    "markdown": _format_markdown,
    "raw": _format_raw,
}


async def retrieve(
    store: Store,
    *,
    org_id: str,
    query_text: str,
    query_vec: Sequence[float],
    limit: int = 5,
    min_score: float = 0.3,
    project: Optional[str] = None,
    format: str = "xml",
    half_life_days: int = 30,
) -> RetrieveOutput:
    """Vector recall + formatting. Returns a typed RetrieveOutput.

    Note: analytics recording and access-count bumping are intentionally
    left at the route layer for Phase 1A; they will move into this service
    once AnalyticsOps lands on the Store (Phase 1F).
    """
    if format not in VALID_FORMATS:
        raise ValueError(
            f"Invalid format {format!r}. Must be one of: {sorted(VALID_FORMATS)}"
        )

    results = await store.recall_by_embedding(
        RecallParams(
            org_id=org_id,
            query_vec=query_vec,
            limit=limit,
            min_score=min_score,
            project=project,
            half_life_days=half_life_days,
        )
    )
    formatted = _FORMATTERS[format](results, query_text)
    return RetrieveOutput(memories=results, formatted=formatted, count=len(results))
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/services/test_retrieve.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/lore/services/retrieve.py tests/services/test_retrieve.py
git commit -m "feat(services): add retrieve service (recall + format)"
```

---

### Task 24: Refactor `routes/retrieve.py` to use the service

**Files:**
- Modify: `src/lore/server/routes/retrieve.py`

- [ ] **Step 1: Replace the body of `retrieve()` route**

The route keeps:
- request validation (format whitelist, profile resolution)
- auth + project scoping
- analytics recording (`_record_retrieval_event`) — unchanged for Phase 1A
- access-count bumping (`_bump_access_counts`) — unchanged for Phase 1A
- session-snapshot injection (`_fetch_session_snapshots`) — unchanged for Phase 1A; will become a separate Store method in Phase 1F

The route loses:
- inline SQL for the main recall
- direct pool access for that recall

```python
# src/lore/server/routes/retrieve.py — replace the SQL block in the route body

from lore.server.db import get_store
from lore.services.retrieve import retrieve as _retrieve_service


@router.get("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    query: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=50),
    min_score: float = Query(0.3, ge=0.0, le=1.0),
    format: str = Query("xml"),
    project: Optional[str] = Query(None),
    profile: Optional[str] = Query(None),
    include_session_context: bool = Query(True),
    auth: AuthContext = Depends(get_auth_context),
) -> RetrieveResponse:
    start = time.monotonic()

    # Resolve profile (unchanged from today)
    if profile:
        from lore.server.routes.profiles import resolve_profile as _resolve_profile

        pool = await get_pool()
        async with pool.acquire() as conn:
            resolved = await _resolve_profile(conn, auth.org_id, profile, None)
        if resolved:
            if resolved.get("k") is not None:
                limit = resolved["k"]
            elif resolved.get("max_results") is not None:
                limit = resolved["max_results"]
            if resolved.get("threshold") is not None:
                min_score = resolved["threshold"]
            elif resolved.get("min_score") is not None:
                min_score = resolved["min_score"]
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile}' not found.",
            )

    # Validate format (service does it too, but raise as 422 here)
    if format not in VALID_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid format '{format}'. Must be one of: {', '.join(sorted(VALID_FORMATS))}",
        )

    embedder = _get_embedder()
    query_vec = embedder.embed(query)

    effective_project = project
    if auth.project is not None:
        effective_project = auth.project

    store = await get_store()
    out = await _retrieve_service(
        store,
        org_id=auth.org_id,
        query_text=query,
        query_vec=query_vec,
        limit=limit,
        min_score=min_score,
        project=effective_project,
        format=format,
    )

    # Convert ScoredMemory dataclasses to RetrieveMemory pydantic models
    memories: List[RetrieveMemory] = [
        RetrieveMemory(
            id=m.id,
            content=m.content,
            type=(m.meta or {}).get("type", "unknown"),
            tier=(m.meta or {}).get("tier", "long"),
            score=round(float(m.score), 4),
            created_at=m.created_at.isoformat() if hasattr(m.created_at, "isoformat") else str(m.created_at),
            source=m.source,
            project=m.project,
            tags=list(m.tags),
        )
        for m in out.memories
    ]

    # Session snapshot injection unchanged
    if include_session_context:
        existing_ids = {m.id for m in memories}
        session_memories = await _fetch_session_snapshots(
            auth=auth,
            effective_project=effective_project,
            exclude_ids=existing_ids,
        )
        memories.extend(session_memories)

    # Re-format if session memories were appended (otherwise out.formatted is fine)
    if include_session_context and session_memories:
        formatter = _FORMATTERS[format]
        formatted = formatter(memories, query)
    else:
        formatted = out.formatted

    elapsed_ms = round((time.monotonic() - start) * 1000, 2)

    # Analytics + access count bump — unchanged from today
    asyncio.create_task(_record_retrieval_event(
        auth=auth, query_text=query, memories=memories, min_score=min_score,
        elapsed_ms=elapsed_ms, fmt=format, effective_project=effective_project,
    ))
    if memories:
        asyncio.create_task(_bump_access_counts([m.id for m in memories]))

    return RetrieveResponse(
        memories=memories, formatted=formatted, count=len(memories),
        query_time_ms=elapsed_ms,
    )
```

- [ ] **Step 2: Run all server + integration tests**

Run: `pytest tests/server/ tests/integration/ -v`
Expected: all previously-passing tests still pass. The retrieve route's externally visible behavior is unchanged.

- [ ] **Step 3: Commit**

```bash
git add src/lore/server/routes/retrieve.py
git commit -m "refactor(routes): GET /v1/retrieve uses retrieve service for recall"
```

---

### Task 25: CI guard — migrated routes must not import asyncpg

**Files:**
- Create: `scripts/check_routes_no_sql.py`
- Test: run the check on the current tree

This script enforces the architectural invariant going forward. For now it checks just the migrated files (`routes/memories.py`, `routes/retrieve.py`); it grows to cover more files as Phase 1B+ migrate them. A file is migrated if listed in `MIGRATED_ROUTES`.

- [ ] **Step 1: Write the check script**

```python
# scripts/check_routes_no_sql.py
"""CI guard: migrated route files must not import asyncpg or contain raw SQL strings.

Add a route to MIGRATED_ROUTES once it has been refactored to call services
exclusively. The script fails CI if a migrated route reintroduces direct DB
access.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATED_ROUTES = {
    "src/lore/server/routes/memories.py",
    "src/lore/server/routes/retrieve.py",
}

FORBIDDEN_PATTERNS = [
    re.compile(r"^\s*import asyncpg", re.MULTILINE),
    re.compile(r"^\s*from asyncpg", re.MULTILINE),
    re.compile(r"\bget_pool\s*\(", re.MULTILINE),
    # Raw SQL heuristic: lines with SELECT/INSERT/UPDATE/DELETE inside a string literal
    re.compile(
        r'"""\s*\n?\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b',
        re.IGNORECASE | re.MULTILINE,
    ),
]

# Allowlist: known-OK references that match the patterns but are intentional
# (e.g. retrieve.py still uses get_pool for profile resolution and analytics
# until those are migrated in Phase 1F). List exact line numbers or markers.
ALLOWLIST = {
    "src/lore/server/routes/retrieve.py": [
        "pool = await get_pool()",  # used by profile resolution + analytics
    ],
}


def main() -> int:
    failures: list[str] = []
    for path_str in sorted(MIGRATED_ROUTES):
        path = Path(path_str)
        if not path.exists():
            failures.append(f"{path_str}: file not found")
            continue
        text = path.read_text()
        allow = ALLOWLIST.get(path_str, [])
        for pattern in FORBIDDEN_PATTERNS:
            for match in pattern.finditer(text):
                snippet = text[max(0, match.start() - 30):match.end() + 30]
                if any(a in snippet for a in allow):
                    continue
                line_no = text[:match.start()].count("\n") + 1
                failures.append(
                    f"{path_str}:{line_no} forbidden pattern matched: {match.group(0)!r}"
                )

    if failures:
        print("Routes-no-SQL guard FAILED:")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"Routes-no-SQL guard: {len(MIGRATED_ROUTES)} files OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it on the current tree**

Run: `python scripts/check_routes_no_sql.py`
Expected: exit 0; prints "Routes-no-SQL guard: 2 files OK".

If it fails, find the lingering pattern in the migrated route, refactor or add a justified allowlist entry, and re-run.

- [ ] **Step 3: Wire into pyproject.toml as a pytest entry**

Append to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
# … existing entries …
addopts = "--tb=short -p no:cacheprovider"
```

(If `addopts` already exists, leave it alone; the guard runs as a standalone CI step, not via pytest.)

- [ ] **Step 4: Add CI step (document only — actual CI YAML is out of repo)**

Open `CONTRIBUTING.md` (or create a section if absent) and add:

```markdown
## Architecture invariants

Routes that have been migrated to the service layer must not contain raw SQL
or import the DB driver directly. Run before pushing:

```bash
python scripts/check_routes_no_sql.py
```

The CI pipeline runs this automatically; a failure means a migrated route
has regressed back to inline SQL.
```

- [ ] **Step 5: Commit**

```bash
git add scripts/check_routes_no_sql.py CONTRIBUTING.md
git commit -m "chore(ci): guard migrated routes against direct DB access"
```

---

### Task 26: Update CHANGELOG and docs

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md` (or create a stub section)

- [ ] **Step 1: Append to CHANGELOG.md**

Open `CHANGELOG.md`, find the unreleased section (or add one if absent), and add:

```markdown
## Unreleased

### Added
- Server-side persistence layer (`lore.persistence`) defining the `Store` protocol with the `MemoryOps` slice. New `PostgresStore` implementation extracted from route SQL. Contract test suite at `tests/persistence/` runs against every Store implementation. (Foundation for SQLite solo mode — see `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md`.)
- Service layer (`lore.services`) for memory ops and retrieve. Routes call services; services call Store. No HTTP behavior changes.

### Internal
- `routes/memories.py` and `routes/retrieve.py` no longer contain raw SQL. CI guard `scripts/check_routes_no_sql.py` enforces this for migrated routes.
```

- [ ] **Step 2: Update or create `docs/architecture.md` section**

Append a new section:

```markdown
## Persistence layer

Lore's server-side persistence is defined by the `Store` protocol in
`lore.persistence.protocol`. Implementations:

- `PostgresStore` — asyncpg + pgvector. Production default.
- (Coming in Phase 3) `SqliteStore` — aiosqlite + sqlite-vec. For solo / embedded use.

The protocol is grown slice-by-slice. Phase 1A shipped the `MemoryOps` slice;
Phase 1B–1G migrate the remaining route SQL into the protocol. Until a slice
is migrated, those routes still call `asyncpg` directly via `get_pool()`.

### Architectural invariants
1. Routes contain zero SQL. Services contain zero SQL. SQL lives only in Store implementations.
2. The Service layer is the only place business logic exists once. The HTTP front-end and the embedded API both call into services.
3. Backend chosen by `database_url` URL scheme. `LORE_BACKEND` env var is just a shortcut.

These invariants are guarded by `scripts/check_routes_no_sql.py` for the migrated slice; coverage grows as more routes migrate.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/architecture.md
git commit -m "docs: document persistence layer and Phase 1A scope"
```

---

### Task 27: Final verification — full suite passes

- [ ] **Step 1: Run the full test matrix locally**

```bash
pytest tests/ -v --tb=short
python scripts/check_routes_no_sql.py
```

Expected: full pass; the guard exits 0.

- [ ] **Step 2: Sanity check the live server with a real DB**

```bash
docker compose up -d db
LORE_DATABASE_URL=postgresql://lore:lore@localhost:5432/lore python -m lore.server &
SERVER_PID=$!
sleep 2

# Insert a memory
curl -s -X POST http://localhost:8765/v1/memories \
  -H "Authorization: Bearer $LORE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content": "phase 1a smoke test"}' | jq .

# List memories
curl -s -H "Authorization: Bearer $LORE_API_KEY" http://localhost:8765/v1/memories | jq .

# Retrieve
curl -s -H "Authorization: Bearer $LORE_API_KEY" \
  "http://localhost:8765/v1/retrieve?query=phase&limit=5&format=xml" | jq .

kill $SERVER_PID
```

Expected: insert returns id; list shows the memory; retrieve returns it with a score and formatted XML.

If any call fails, debug at the route layer (most likely a serialization mismatch between dataclass fields and pydantic response model).

- [ ] **Step 3: No commit — this task is verification only.**

---

## Self-review

Run this checklist after the plan is complete and before handing it off.

### Spec coverage

| Spec section | Covered by |
|---|---|
| Component 1 (`lore.store` / persistence package + Store protocol) | Tasks 1, 4 |
| Component 2 (`lore.services` business logic) | Tasks 1, 16, 23 |
| Component 5 (parallel migrations trees) | **Out of scope for 1A** — Phase 3 |
| Component 6 (vector layer for SQLite) | **Out of scope for 1A** — Phase 3 |
| Component 7 (bootstrap layer) | **Out of scope for 1A** — Phase 3 |
| Component 8 (`lore migrate`) | **Out of scope for 1A** — Phase 5 |
| Component 9 (config & packaging) | Tasks 1 (package), 14 (factory + URL scheme) |
| Path A data flow (HTTP) | Tasks 15, 17–22, 24 |
| Path B data flow (embedded) | **Out of scope for 1A** — Phase 4 |
| Storage error: `StoreNotFound` | Task 3 (defined), Tasks 7, 13 (test), Tasks 18, 20, 22 (route mapping to 404) |
| Storage error: `StoreBusy` | Task 3 (defined); retry logic ships with SQLite work in Phase 3 — Postgres busy is rare and asyncpg surfaces it natively |
| Configuration error: `BackendUnavailable` | Task 3 (defined), Task 14 (raised by factory) |
| Layer 1 testing (Store contract suite) | Tasks 5–13 |
| Layer 2 testing (service tests) | Tasks 16, 23 |
| Layer 4 testing (HTTP integration) | Existing tests; verified to still pass at Tasks 17–24 step 3, Task 27 |

Phase 1A leaves later slices on inline SQL. That's expected — every subsequent slice ships its own Phase 1B–1G plan that follows this same template.

### Placeholder scan

Searched for "TBD", "TODO", "implement later", "fill in details", "appropriate error handling", "similar to Task". None found. The two non-trivial scope deferrals (analytics in Task 23, schema-version checks) are explicit and tied to named future phases, not vague placeholders.

### Type consistency

- `Store.recall_by_embedding` returns `Sequence[ScoredMemory]` everywhere it's mentioned (protocol, contract test, service, route).
- `MemoryFilter` field names match between `types.py` definition (Task 2), `Store.list_memories` callers (Task 9), and the `list_memories` service (Task 16).
- `MemoryPatch` fields are consistent across types (Task 2), `update_memory` impl (Task 7), service wrapper (Task 16), and route caller (Task 20).
- `StoredMemory.tags` is `Sequence[str]` everywhere, normalized to a tuple inside `_row_to_stored`. The route serializers convert to `list[str]` for pydantic — that conversion happens in `_stored_to_memory_response` (Task 19) and inline in retrieve (Task 24).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-05-phase-1a-foundation-and-memories.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for a 27-task plan where each task is small and the boundaries are clean.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
