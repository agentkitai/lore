# Implementation Stories: Lore HTTP Store

**Feature:** HttpStore — bridge SDK to Postgres-backed REST API
**PRD:** [prd.md](../planning-artifacts/prd.md)
**Architecture:** [architecture.md](../planning-artifacts/architecture.md)

---

## Story 1: HttpStore Core — Constructor, Health Check, and HTTP Dispatch

**Description:** Create the `HttpStore` class with constructor (env var fallbacks, httpx.Client setup, health check on init) and the central `_request()` method with retry/error handling.

**Acceptance Criteria:**
- [ ] `HttpStore(api_url, api_key)` creates a configured `httpx.Client` with `Authorization: Bearer <key>` header
- [ ] Constructor reads `LORE_API_URL`, `LORE_API_KEY`, `LORE_HTTP_TIMEOUT` from env vars when args are `None`
- [ ] Resolution order: explicit param > env var > `ValueError`
- [ ] `_check_health()` hits `/health` on init; raises `LoreConnectionError` with actionable message on failure
- [ ] `_request()` retries on 5xx and connection/timeout errors (max 2 retries, exponential backoff 0.5s, 1.0s)
- [ ] `_request()` raises `LoreAuthError` on 401/403 (no retry)
- [ ] `_request()` raises `ValueError` on 422 with server detail
- [ ] `_request()` returns response on 404 (caller decides semantics)
- [ ] `repr()` masks api_key (e.g., `lore_sk_***`)
- [ ] `close()` closes the `httpx.Client`
- [ ] API key never appears in error messages or logs

**Tasks:**
1. Create `src/lore/store/http.py` with `HttpStore(Store)` class
2. Implement `__init__` with param/env resolution, `httpx.Client` setup, `_check_health()` call
3. Implement `_request(method, path, **kwargs)` with retry logic and error mapping
4. Implement `close()`, `__repr__()`, and `__del__` (calls close)
5. Stub all 7 ABC methods with `raise NotImplementedError` (filled in Story 2)
6. Write unit tests for constructor, env var fallback, health check, retry, error mapping, repr masking

**Files:**
- `src/lore/store/http.py` — **NEW**
- `tests/test_http_store.py` — **NEW** (partial: constructor + request tests)

**Dependencies:** None

---

## Story 2: HttpStore CRUD — Field Mapping and Store ABC Methods

**Description:** Implement the 7 `Store` ABC methods (`save`, `get`, `list`, `update`, `delete`, `count`, `cleanup_expired`) and the Memory <-> Lesson field mapping helpers.

**Acceptance Criteria:**
- [ ] `_memory_to_lesson(memory)` correctly maps all fields per architecture spec:
  - `content` -> `problem` + `resolution` (mirrored)
  - `type` -> `meta.type`
  - `embedding` bytes -> `List[float]` via `struct.unpack`
  - `ttl` -> computed `expires_at` (ISO 8601) when `expires_at` is not already set
  - All direct-map fields: `context`, `tags`, `confidence`, `source`, `project`, `expires_at`, `metadata` -> `meta`
- [ ] `_lesson_to_memory(data)` correctly maps back:
  - `problem` -> `content`
  - `resolution` stored in `metadata["_resolution"]` only when different from `problem`
  - `meta.type` -> `type` (default `"general"`)
  - `embedding` set to `None` (server responses omit embeddings)
- [ ] `save(memory)` POSTs to `/v1/lessons`, overwrites `memory.id` with server-returned ID
- [ ] `get(id)` GETs `/v1/lessons/{id}`, returns `Memory` or `None` on 404
- [ ] `list(project, type, limit)` GETs `/v1/lessons` with query params; client-side post-filters by `type` (via `meta.type`)
- [ ] `update(memory)` PATCHes `/v1/lessons/{id}` with mutable fields only; returns `True`/`False`
- [ ] `delete(id)` DELETEs `/v1/lessons/{id}`; returns `True` on 204, `False` on 404
- [ ] `count(project, type)` GETs `/v1/lessons?limit=1` and extracts `total` from response
- [ ] `cleanup_expired()` returns `0` (no-op; server handles expiry)

**Tasks:**
1. Implement `_memory_to_lesson()` with embedding deserialization, type-in-meta, ttl->expires_at
2. Implement `_lesson_to_memory()` with resolution round-trip handling, type extraction
3. Implement `save()` — POST `/v1/lessons`, overwrite `memory.id`
4. Implement `get()` — GET with 404 handling
5. Implement `list()` — GET with query params, client-side type post-filter
6. Implement `update()` — PATCH with mutable fields only
7. Implement `delete()` — DELETE with 204/404 handling
8. Implement `count()` — GET with limit=1, extract total
9. Implement `cleanup_expired()` — return 0
10. Write unit tests with mocked HTTP for each method and both mapping helpers

**Files:**
- `src/lore/store/http.py` — MODIFY (fill in stubs from Story 1)
- `tests/test_http_store.py` — MODIFY (add CRUD + mapping tests)

**Dependencies:** Story 1

---

## Story 3: HttpStore Search and Recall Path Integration

**Description:** Implement `HttpStore.search()` for server-side semantic search and wire `Lore.recall()` to delegate to it when the store supports `search()`.

**Acceptance Criteria:**
- [ ] `HttpStore.search(embedding, tags, project, limit, min_confidence)` POSTs to `/v1/lessons/search` with 384-dim float vector
- [ ] Search results mapped to `List[RecallResult]` via `_lesson_to_memory()`
- [ ] `Lore.recall()` detects `hasattr(self._store, 'search')` and delegates to server (skips `_recall_local`)
- [ ] When using `EmbeddingRouter`, the prose embedding vector is sent for search queries
- [ ] Freshness check (`check_freshness=True`) still works on results from remote search
- [ ] Search with filters (tags, project, min_confidence, limit) passes them to the server

**Tasks:**
1. Implement `search()` method on `HttpStore` — POST `/v1/lessons/search`, map results
2. Modify `Lore.recall()` (~lines 260-284) to check for `search()` method and delegate
3. Ensure prose vector is used when `EmbeddingRouter` is active
4. Write unit tests for `search()` with mocked HTTP responses
5. Write unit tests for `Lore.recall()` dispatch (mock store with/without `search`)

**Files:**
- `src/lore/store/http.py` — MODIFY (add `search()`)
- `src/lore/lore.py` — MODIFY (recall dispatch, ~lines 260-284)
- `tests/test_http_store.py` — MODIFY (search tests)

**Dependencies:** Story 2

---

## Story 4: Lore Wiring — Remote Store Construction and Atomic Votes

**Description:** Remove the "not supported" error in `Lore.__init__`, wire `store='remote'` to construct `HttpStore`, add atomic upvote/downvote via duck-typing, and update `store/__init__.py` for lazy export.

**Acceptance Criteria:**
- [ ] `Lore(store='remote', api_url=..., api_key=...)` constructs an `HttpStore` (no error)
- [ ] `Lore(store='remote')` without explicit url/key works if env vars are set (HttpStore reads them)
- [ ] Default behavior (`store=None`) unchanged — still creates `SqliteStore`
- [ ] `HttpStore` has `upvote(id)` and `downvote(id)` methods that send `PATCH {upvotes: "+1"}` / `{downvotes: "+1"}`
- [ ] `Lore.upvote()` calls `store.upvote(id)` when method exists; falls back to get-increment-update
- [ ] `Lore.downvote()` calls `store.downvote(id)` when method exists; falls back to get-increment-update
- [ ] `from lore.store import HttpStore` works (lazy import, no `httpx` at import time)
- [ ] Importing `lore.store` without `httpx` installed does not raise (only fails when `HttpStore` is accessed)

**Tasks:**
1. Replace `ValueError("Remote store is not supported...")` block in `lore.py` (~lines 114-124) with `HttpStore` construction
2. Add `upvote(id)` and `downvote(id)` convenience methods to `HttpStore`
3. Modify `Lore.upvote()` / `Lore.downvote()` (~lines 430-446) to duck-type check for atomic vote methods
4. Update `src/lore/store/__init__.py` with lazy `__getattr__` for `HttpStore`
5. Write unit tests: remote store init, upvote/downvote dispatch, lazy import

**Files:**
- `src/lore/lore.py` — MODIFY (lines 114-124, 430-446)
- `src/lore/store/http.py` — MODIFY (add `upvote`/`downvote`)
- `src/lore/store/__init__.py` — MODIFY (lazy export)
- `tests/test_http_store.py` — MODIFY (wiring + vote tests)

**Dependencies:** Story 2

---

## Story 5: MCP Server Remote Store Configuration

**Description:** Update the MCP server's `_get_lore()` to read `LORE_STORE`, `LORE_API_URL`, and `LORE_API_KEY` environment variables and construct a remote-backed `Lore` instance when `LORE_STORE=remote`.

**Acceptance Criteria:**
- [ ] `LORE_STORE=remote` + `LORE_API_URL` + `LORE_API_KEY` env vars cause `_get_lore()` to create `Lore(store='remote', ...)`
- [ ] `LORE_STORE=local` (or unset) continues to create default `Lore(project=...)` — backward compatible
- [ ] Invalid `LORE_STORE` value raises a clear error
- [ ] `LORE_PROJECT` still works in combination with remote store
- [ ] MCP tool signatures and descriptions remain unchanged

**Tasks:**
1. Modify `_get_lore()` in `src/lore/mcp/server.py` (~lines 32-40) to read `LORE_STORE` and branch
2. Add validation for unknown `LORE_STORE` values
3. Write unit tests for `_get_lore()` with various env var combinations (mock `Lore` constructor)

**Files:**
- `src/lore/mcp/server.py` — MODIFY (lines 32-40)
- `tests/test_http_store.py` — MODIFY (or new test in `tests/test_mcp_server.py`)

**Dependencies:** Story 4

---

## Story 6: Integration Tests Against Live Server

**Description:** Write integration tests that exercise the full remember/recall/CRUD cycle against a running Lore server (Docker Compose with Postgres). These tests are skipped in CI without a server.

**Acceptance Criteria:**
- [ ] All tests marked with `@pytest.mark.integration` and skipped when server is unavailable
- [ ] `test_full_crud_cycle` — save, get, update, delete a memory through HttpStore
- [ ] `test_save_and_search` — remember content, recall with semantic query, verify results
- [ ] `test_round_trip_fidelity` — remember a memory with all fields (type, tags, metadata, confidence), recall it, verify all fields preserved
- [ ] `test_cross_instance_visibility` — two HttpStore instances pointing at same server can see each other's memories
- [ ] `test_upvote_downvote` — atomic vote operations work correctly
- [ ] Test cleanup: all test-created memories are deleted in teardown
- [ ] `pytest.ini` or `pyproject.toml` registers the `integration` marker

**Tasks:**
1. Create `tests/test_http_store_integration.py` with `@pytest.mark.integration`
2. Create fixture that constructs `HttpStore` from env vars with cleanup
3. Implement CRUD cycle test
4. Implement search/recall test
5. Implement round-trip fidelity test (all Memory fields)
6. Implement cross-instance test
7. Implement vote test
8. Register `integration` marker in pytest config

**Files:**
- `tests/test_http_store_integration.py` — **NEW**
- `pyproject.toml` — MODIFY (add marker, if not already present)

**Dependencies:** Story 4, Story 3

---

## Story Dependency Graph

```
Story 1: HttpStore Core (constructor, health, _request)
    │
    ▼
Story 2: HttpStore CRUD (7 ABC methods + field mapping)
    │
    ├──────────────────┐
    ▼                  ▼
Story 3: Search +    Story 4: Lore Wiring
  Recall Path          (remote init, votes, exports)
    │                  │
    │                  ▼
    │              Story 5: MCP Server Config
    │                  │
    └──────┬───────────┘
           ▼
       Story 6: Integration Tests
```

## Estimated Scope

| Story | New/Modified Lines (est.) | Test Count (est.) |
|-------|--------------------------|-------------------|
| 1     | ~100 new                 | ~10               |
| 2     | ~150 new                 | ~15               |
| 3     | ~50 new, ~15 modified    | ~6                |
| 4     | ~30 new, ~20 modified    | ~6                |
| 5     | ~15 modified             | ~3                |
| 6     | ~120 new                 | ~6                |
| **Total** | **~500**             | **~46**           |
