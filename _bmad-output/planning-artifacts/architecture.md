# Architecture: Lore HTTP Store

**Version:** 1.0
**Author:** Winston (Architect)
**Date:** 2026-03-05
**Status:** Draft
**PRD:** [prd.md](./prd.md)

---

## 1. Overview

This document defines the technical architecture for adding an `HttpStore` to the Lore SDK, enabling the MCP server (and any SDK consumer) to use a shared Postgres-backed REST API as its storage backend. The design is a thin HTTP client that implements the existing `Store` ABC, translating between the SDK's `Memory` model and the server's `Lesson` model.

### 1.1 Design Principles

1. **Drop-in Store** — `HttpStore` implements the same 7-method `Store` ABC as `SqliteStore` and `MemoryStore`. No changes to `Lore`'s core logic beyond wiring it up.
2. **Local embedding, remote storage** — The SDK computes 384-dim embeddings locally via `LocalEmbedder`/`EmbeddingRouter`. The server stores and searches them. No embedding computation moves to the server.
3. **Thin translation layer** — The only new complexity is the Memory <-> Lesson field mapping. All business logic (decay scoring, freshness detection) stays in the server's search SQL or the SDK's `_recall_local`.
4. **Fail fast, fail loud** — HTTP errors surface as typed exceptions (`LoreConnectionError`, `LoreAuthError`, `MemoryNotFoundError`), not silent swallowed failures.

---

## 2. Component Design

### 2.1 HttpStore Class

**File:** `src/lore/store/http.py` (NEW)

```
HttpStore(Store)
├── __init__(api_url, api_key, timeout, verify_ssl)
├── save(memory) -> None          # POST /v1/lessons
├── get(memory_id) -> Memory?     # GET /v1/lessons/{id}
├── list(project, type, limit)    # GET /v1/lessons?...
├── update(memory) -> bool        # PATCH /v1/lessons/{id}
├── delete(memory_id) -> bool     # DELETE /v1/lessons/{id}
├── count(project, type) -> int   # GET /v1/lessons?limit=1 -> total
├── cleanup_expired() -> int      # No-op (server handles expiry)
├── search(embedding, ...) -> List[LessonSearchResult]  # POST /v1/lessons/search
├── close() -> None               # Close httpx.Client
├── _request(method, path, **kw)  # Central HTTP dispatch with retry
├── _memory_to_lesson(memory)     # Memory -> LessonCreateRequest dict
├── _lesson_to_memory(data)       # LessonResponse dict -> Memory
└── _check_health()               # GET /health on init
```

**Key decisions:**

- **Synchronous `httpx.Client`** — The MCP server runs synchronously (FastMCP tool functions are sync). `httpx.Client` provides connection pooling via its session (R14).
- **`search()` is not part of Store ABC** — It's an additional method needed by `Lore.recall()`. The `Lore` class will call `store.search()` when the store is an `HttpStore`, bypassing the local `_recall_local` path entirely. This is because the server's Postgres/pgvector handles cosine similarity, decay scoring, and filtering in SQL — duplicating that client-side would be wasteful and inconsistent.
- **No local embedding storage** — `HttpStore` sends embedding vectors as `List[float]` in JSON. It does not store or receive raw bytes. The `Memory.embedding` field will be `None` for memories retrieved from the server (the server's `LessonResponse` does not include embeddings).

### 2.2 Field Mapping Module

The Memory <-> Lesson translation is encapsulated in two private methods on `HttpStore`:

#### `_memory_to_lesson(memory: Memory) -> dict`

| Memory field | Lesson field | Transformation |
|---|---|---|
| `content` | `problem` | Direct |
| `content` | `resolution` | Mirror (server requires both) |
| `context` | `context` | Direct |
| `tags` | `tags` | Direct |
| `confidence` | `confidence` | Direct |
| `source` | `source` | Direct |
| `project` | `project` | Direct |
| `embedding` (bytes) | `embedding` (List[float]) | `struct.unpack('Nf', bytes)` -> list. `None` if no embedding. |
| `expires_at` | `expires_at` | Direct (ISO 8601 string) |
| `metadata` | `meta` | Copy dict. Insert `meta["type"] = memory.type` for round-trip. |
| `type` | `meta.type` | Stored inside meta dict |
| `ttl` | `expires_at` | If `ttl` set and `expires_at` not, compute `now() + ttl` |

#### `_lesson_to_memory(data: dict) -> Memory`

| Lesson field | Memory field | Transformation |
|---|---|---|
| `problem` | `content` | Direct |
| `resolution` | `metadata["_resolution"]` | Only if `resolution != problem` (lossless round-trip) |
| `context` | `context` | Direct |
| `tags` | `tags` | Direct |
| `confidence` | `confidence` | Direct |
| `source` | `source` | Direct |
| `project` | `project` | Direct |
| `created_at` | `created_at` | `str(datetime)` -> ISO string |
| `updated_at` | `updated_at` | `str(datetime)` -> ISO string |
| `expires_at` | `expires_at` | `str(datetime)` or `None` |
| `upvotes` | `upvotes` | Direct |
| `downvotes` | `downvotes` | Direct |
| `meta` | `metadata` | Direct dict copy |
| `meta.type` | `type` | Extract, default `"general"` |
| — | `embedding` | `None` (server responses omit embeddings) |
| — | `ttl` | `None` (not stored server-side) |

### 2.3 Embedding Pipeline (Unchanged)

The embedding pipeline is **not modified**. The flow:

1. `Lore.remember()` calls `self._embedder.embed(text)` -> `List[float]`
2. `_serialize_embedding(vec)` -> `bytes` stored as `Memory.embedding`
3. `HttpStore.save()` deserializes bytes back to `List[float]` for JSON payload
4. Server stores as `jsonb` (or pgvector column)

For recall:
1. `Lore.recall()` calls `self._embedder.embed(query)` -> `List[float]`
2. Instead of `_recall_local`, calls `HttpStore.search(embedding=query_vec, ...)`
3. Server performs pgvector cosine similarity + decay scoring in SQL
4. Results returned as `LessonSearchResult` objects, mapped to `RecallResult`

---

## 3. Data Flow Diagrams

### 3.1 Remember Flow (MCP -> HttpStore -> Server -> Postgres)

```
MCP Tool: remember(content, type, tags, ...)
    │
    ▼
Lore.remember()
    ├── RedactionPipeline.scan(content)     # security scan
    ├── Embedder.embed(content)             # local 384-dim embedding
    ├── _serialize_embedding(vec) -> bytes
    ├── Memory(id=ULID(), embedding=bytes, ...)
    │
    ▼
HttpStore.save(memory)
    ├── _memory_to_lesson(memory)
    │   ├── struct.unpack embedding bytes -> List[float]
    │   ├── content -> problem + resolution
    │   └── type -> meta.type
    ├── POST /v1/lessons
    │   Headers: Authorization: Bearer <api_key>
    │   Body: {problem, resolution, context, tags, confidence,
    │          source, project, embedding: [...], meta: {type: ...}}
    │
    ▼
Server: create_lesson()
    ├── Auth: validate API key, resolve org_id
    ├── INSERT INTO lessons (...) VALUES (...)
    └── Return {id: "01JM..."}
```

### 3.2 Recall Flow (MCP -> HttpStore -> Server -> pgvector)

```
MCP Tool: recall(query, tags, type, limit)
    │
    ▼
Lore.recall()
    ├── _maybe_cleanup_expired()            # no-op for HttpStore
    ├── Embedder.embed(query) -> List[float]
    │
    ▼  (HttpStore detected — skip _recall_local)
HttpStore.search(embedding=query_vec, tags, project, limit, min_confidence)
    ├── POST /v1/lessons/search
    │   Body: {embedding: [384 floats], tags, project, limit, min_confidence}
    │
    ▼
Server: search_lessons()
    ├── pgvector: 1 - (embedding <=> query_vec) = cosine_similarity
    ├── Score = 0.7 * (cosine * confidence * vote_factor)
    │         + 0.3 * power(0.5, age_days / half_life)
    ├── WHERE org_id = ... AND expires_at > now() AND embedding IS NOT NULL
    ├── ORDER BY score DESC LIMIT N
    └── Return {lessons: [{id, problem, resolution, score, ...}]}
    │
    ▼
HttpStore._lesson_to_memory() for each result
    └── Return List[RecallResult(memory=Memory(...), score=score)]
```

### 3.3 Upvote/Downvote Flow

```
MCP Tool: upvote_memory(memory_id)
    │
    ▼
Lore.upvote(memory_id)
    ├── self._store.get(memory_id)          # GET /v1/lessons/{id}
    ├── memory.upvotes += 1                 # local increment
    ├── self._store.update(memory)          # PATCH /v1/lessons/{id}
    │
    ▼
HttpStore.update(memory)
    ├── PATCH /v1/lessons/{id}
    │   Body: {upvotes: "+1"}              # atomic server-side increment
    └── Return True
```

**Note on upvote/downvote:** The current `Lore.upvote()` does get-increment-update. For `HttpStore.update()`, we send `upvotes: "+1"` to leverage the server's atomic increment (PATCH handler supports `"+1"`/`"-1"` strings). This avoids race conditions. The `HttpStore.update()` method detects vote changes by comparing the memory's current votes against the last-known values.

**Simpler approach:** `HttpStore` will provide dedicated `upvote(id)` and `downvote(id)` convenience methods that send `PATCH {upvotes: "+1"}` directly. The `Lore` class will be updated to call `store.upvote(id)` / `store.downvote(id)` when those methods exist (duck-typing check), falling back to the current get-increment-update pattern for stores that don't have them.

---

## 4. Error Handling Strategy

### 4.1 Exception Hierarchy

Existing exceptions in `src/lore/exceptions.py` already cover the needed cases:

| Exception | Trigger | HTTP Status |
|---|---|---|
| `LoreConnectionError` | Network unreachable, DNS failure, timeout | `httpx.ConnectError`, `httpx.TimeoutException` |
| `LoreAuthError` | Invalid or expired API key | 401, 403 |
| `MemoryNotFoundError` | Lesson not found | 404 |
| `ValueError` | Invalid parameters (bad embedding dim) | 422 |

No new exception classes needed.

### 4.2 Central `_request()` Method

All HTTP calls go through a single `_request()` method that handles:

```python
def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
    """Execute an HTTP request with error handling and retry."""
    url = f"{self._api_url}{path}"
    last_exc = None
    for attempt in range(self._max_retries + 1):
        try:
            response = self._client.request(method, url, **kwargs)
            if response.status_code == 401:
                raise LoreAuthError("Invalid API key")
            if response.status_code == 403:
                raise LoreAuthError("Insufficient permissions")
            if response.status_code == 404:
                return response  # caller handles 404 semantics
            if response.status_code == 422:
                detail = response.json().get("detail", "Validation error")
                raise ValueError(f"Server validation error: {detail}")
            if response.status_code >= 500:
                last_exc = LoreConnectionError(
                    f"Server error {response.status_code}: {response.text[:200]}"
                )
                if attempt < self._max_retries:
                    time.sleep(0.5 * (2 ** attempt))  # exponential backoff
                    continue
                raise last_exc
            response.raise_for_status()  # catch any other 4xx
            return response
        except httpx.ConnectError as e:
            last_exc = LoreConnectionError(f"Cannot connect to {self._api_url}: {e}")
            if attempt < self._max_retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise last_exc from e
        except httpx.TimeoutException as e:
            last_exc = LoreConnectionError(f"Request timed out after {self._timeout}s")
            if attempt < self._max_retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise last_exc from e
    raise last_exc  # unreachable but satisfies type checker
```

### 4.3 Retry Policy (R11)

- **Retry on:** 5xx, `httpx.ConnectError`, `httpx.TimeoutException`
- **No retry on:** 4xx (401, 403, 404, 422)
- **Max retries:** 2 (3 total attempts)
- **Backoff:** exponential — 0.5s, 1.0s
- **Configurable:** `max_retries` constructor param, default 2

### 4.4 Health Check on Init (R10)

```python
def _check_health(self) -> None:
    """Validate server connectivity. Called during __init__."""
    try:
        resp = self._client.get(f"{self._api_url}/health", timeout=5.0)
        resp.raise_for_status()
    except httpx.ConnectError:
        raise LoreConnectionError(
            f"Cannot connect to Lore server at {self._api_url}. "
            "Is the server running?"
        )
    except httpx.TimeoutException:
        raise LoreConnectionError(
            f"Lore server at {self._api_url} did not respond within 5s."
        )
    except httpx.HTTPStatusError as e:
        raise LoreConnectionError(
            f"Lore server at {self._api_url} returned {e.response.status_code}."
        )
```

### 4.5 API Key Safety

- The API key is **never logged**. Error messages use `api_url` only.
- `repr()` of `HttpStore` masks the key: `HttpStore(api_url='...', api_key='lore_sk_***')`
- The key is passed only in the `Authorization: Bearer` header via `httpx.Client(headers=...)`.

---

## 5. Configuration Design

### 5.1 Constructor Parameters

```python
class HttpStore(Store):
    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        verify_ssl: bool = True,
    ) -> None:
```

| Param | Env Var Fallback | Default | Notes |
|---|---|---|---|
| `api_url` | `LORE_API_URL` | *required* | Base URL, no trailing slash |
| `api_key` | `LORE_API_KEY` | *required* | `lore_sk_...` format |
| `timeout` | `LORE_HTTP_TIMEOUT` | `30.0` | Per-request timeout in seconds |
| `max_retries` | — | `2` | Retry count for transient failures |
| `verify_ssl` | — | `True` | Disable for local dev with self-signed certs |

**Resolution order:** explicit param > env var > error.

```python
self._api_url = (api_url or os.environ.get("LORE_API_URL", "")).rstrip("/")
self._api_key = api_key or os.environ.get("LORE_API_KEY", "")
self._timeout = float(os.environ.get("LORE_HTTP_TIMEOUT", "")) if not timeout else timeout
# ... validation ...
if not self._api_url:
    raise ValueError("api_url is required (or set LORE_API_URL)")
if not self._api_key:
    raise ValueError("api_key is required (or set LORE_API_KEY)")
```

### 5.2 httpx.Client Configuration

```python
self._client = httpx.Client(
    base_url=self._api_url,
    headers={"Authorization": f"Bearer {self._api_key}"},
    timeout=httpx.Timeout(self._timeout),
    verify=verify_ssl,
)
```

Using `base_url` on the client simplifies all subsequent calls to relative paths.

### 5.3 Lore Class Wiring

In `src/lore/lore.py` lines 114-124, the current code raises `ValueError("Remote store is not supported...")`. This block is replaced:

```python
if isinstance(store, str) and store == "remote":
    from lore.store.http import HttpStore
    self._store = HttpStore(api_url=api_url, api_key=api_key)
```

The `api_url` and `api_key` params already exist on `Lore.__init__` (lines 90-91).

### 5.4 MCP Server Wiring

In `src/lore/mcp/server.py`, `_get_lore()` reads env vars:

```python
def _get_lore() -> Lore:
    global _lore
    if _lore is not None:
        return _lore

    project = os.environ.get("LORE_PROJECT") or None
    store_type = os.environ.get("LORE_STORE", "local")

    if store_type == "remote":
        api_url = os.environ.get("LORE_API_URL")
        api_key = os.environ.get("LORE_API_KEY")
        _lore = Lore(
            project=project,
            store="remote",
            api_url=api_url,
            api_key=api_key,
        )
    else:
        _lore = Lore(project=project)

    return _lore
```

---

## 6. Recall Path: HttpStore vs Local

The critical architectural decision is how `Lore.recall()` works with `HttpStore`.

### 6.1 Problem

`Lore._recall_local()` (lines 286-379) does client-side cosine similarity using numpy. This requires:
- Loading ALL memories with embeddings from the store
- Deserializing embedding bytes
- Computing cosine similarity in numpy
- Applying decay scoring

This is correct for SQLite (local data, fast reads). For `HttpStore`, it would mean:
1. `GET /v1/lessons` — returns ALL lessons (no embedding in response!)
2. The server's `LessonResponse` does **not** include embeddings
3. Even if it did, downloading all embeddings over HTTP would be prohibitively slow

### 6.2 Solution

`Lore.recall()` checks if the store has a `search()` method (duck typing). If so, it delegates entirely to the server:

```python
def recall(self, query, *, tags=None, type=None, limit=5, min_confidence=0.0,
           check_freshness=False, repo_path=None):
    self._maybe_cleanup_expired()

    query_vec = self._embedder.embed(query)

    # Remote store: delegate search to server
    if hasattr(self._store, 'search'):
        results = self._store.search(
            embedding=query_vec,
            tags=tags,
            project=self.project,
            limit=limit,
            min_confidence=min_confidence,
        )
    else:
        # Local store: existing _recall_local path
        results = self._recall_local(query_vec, tags=tags, type=type, limit=limit,
                                      min_confidence=min_confidence)

    if check_freshness and repo_path:
        from lore.freshness.detector import FreshnessDetector
        detector = FreshnessDetector(repo_path)
        for r in results:
            r.staleness = detector.check(r.memory)

    return results
```

`HttpStore.search()` maps to `POST /v1/lessons/search`:

```python
def search(self, embedding, *, tags=None, project=None,
           limit=5, min_confidence=0.0) -> List[RecallResult]:
    payload = {
        "embedding": embedding,  # List[float], already deserialized
        "limit": limit,
        "min_confidence": min_confidence,
    }
    if tags:
        payload["tags"] = tags
    if project:
        payload["project"] = project

    resp = self._request("POST", "/v1/lessons/search", json=payload)
    data = resp.json()
    results = []
    for item in data["lessons"]:
        memory = self._lesson_to_memory(item)
        results.append(RecallResult(memory=memory, score=item["score"]))
    return results
```

### 6.3 Dual Embedding Consideration

The `EmbeddingRouter` (dual embedding) classifies content as code vs prose and uses different query vectors. The server's search endpoint accepts a single embedding vector and does not support dual-model search.

**Decision:** When using `HttpStore` with `EmbeddingRouter`, use the **prose embedding** for search queries. This is the default/general-purpose model. The server scores all results against this single vector. This is a known limitation — dual embedding provides marginal benefit for server-side search because pgvector doesn't know which model embedded each row. This can be revisited when the server supports model-specific search columns.

---

## 7. File-by-File Change List

### 7.1 New Files

#### `src/lore/store/http.py` (NEW — ~200 lines)

```
class HttpStore(Store):
    # Constructor: api_url, api_key, timeout, max_retries, verify_ssl
    # Env var fallbacks for api_url, api_key, timeout
    # Creates httpx.Client with Bearer auth header
    # Calls _check_health() on init

    # Store ABC implementation (7 methods):
    def save(memory) -> None
        # Deserialize embedding bytes -> List[float]
        # Build LessonCreateRequest-compatible dict
        # POST /v1/lessons
        # Server assigns its own ID; we keep the Memory's ULID as-is
        # Note: server generates a new ID — we do NOT use the Memory.id
        # The returned ID is not currently used (fire-and-forget save)

    def get(memory_id) -> Optional[Memory]
        # GET /v1/lessons/{memory_id}
        # 404 -> return None
        # Map LessonResponse -> Memory

    def list(project, type, limit) -> List[Memory]
        # GET /v1/lessons?project=...&limit=...
        # type filter: pass as category query param (tag-based on server)
        # Map each LessonResponse -> Memory

    def update(memory) -> bool
        # PATCH /v1/lessons/{memory_id}
        # Send only mutable fields: confidence, tags, meta, upvotes, downvotes
        # 404 -> return False

    def delete(memory_id) -> bool
        # DELETE /v1/lessons/{memory_id}
        # 204 -> True, 404 -> False

    def count(project, type) -> int
        # GET /v1/lessons?project=...&limit=1
        # Return total from LessonListResponse

    def cleanup_expired() -> int
        # No-op, return 0. Server filters expired in WHERE clauses.

    # Additional methods:
    def search(embedding, tags, project, limit, min_confidence) -> List[RecallResult]
        # POST /v1/lessons/search
        # Map results to RecallResult objects

    def close() -> None
        # Close httpx.Client

    def __repr__() -> str
        # Mask api_key in repr

    # Private helpers:
    def _request(method, path, **kwargs) -> httpx.Response
    def _memory_to_lesson(memory) -> dict
    def _lesson_to_memory(data) -> Memory
    def _check_health() -> None
```

### 7.2 Modified Files

#### `src/lore/lore.py`

**Lines 114-124:** Replace the `ValueError("Remote store is not supported...")` block:

```python
# BEFORE (lines 116-124):
if isinstance(store, str) and store == "remote":
    if not api_url or not api_key:
        raise ValueError("api_url and api_key are required when store='remote'")
    raise ValueError("Remote store is not supported in this version. ...")

# AFTER:
if isinstance(store, str) and store == "remote":
    from lore.store.http import HttpStore
    self._store = HttpStore(api_url=api_url, api_key=api_key)
```

Note: `HttpStore.__init__` handles env var fallback and validation internally. The `api_url`/`api_key` can be `None` here — `HttpStore` will check env vars.

**Lines 261-275 (recall method):** Add `search()` dispatch:

```python
# After computing query_vec, before _recall_local:
if hasattr(self._store, 'search'):
    results = self._store.search(
        embedding=query_vec,
        tags=tags,
        project=self.project,
        limit=limit,
        min_confidence=min_confidence,
    )
else:
    # existing _recall_local path (unchanged)
    ...
```

**Lines 430-446 (upvote/downvote):** Add duck-typing for atomic votes:

```python
def upvote(self, memory_id: str) -> None:
    if hasattr(self._store, 'upvote'):
        self._store.upvote(memory_id)
        return
    # existing get-increment-update path
    ...

def downvote(self, memory_id: str) -> None:
    if hasattr(self._store, 'downvote'):
        self._store.downvote(memory_id)
        return
    # existing get-increment-update path
    ...
```

#### `src/lore/mcp/server.py`

**Lines 32-40 (`_get_lore`):** Add `LORE_STORE` / `LORE_API_URL` / `LORE_API_KEY` env var handling:

```python
def _get_lore() -> Lore:
    global _lore
    if _lore is not None:
        return _lore

    project = os.environ.get("LORE_PROJECT") or None
    store_type = os.environ.get("LORE_STORE", "local")

    if store_type == "remote":
        _lore = Lore(
            project=project,
            store="remote",
            api_url=os.environ.get("LORE_API_URL"),
            api_key=os.environ.get("LORE_API_KEY"),
        )
    else:
        _lore = Lore(project=project)

    return _lore
```

#### `src/lore/store/__init__.py`

Add `HttpStore` to exports (lazy import to avoid requiring `httpx` at import time):

```python
from lore.store.base import Store
from lore.store.memory import MemoryStore
from lore.store.sqlite import SqliteStore

__all__ = ["Store", "MemoryStore", "SqliteStore", "HttpStore"]

def __getattr__(name: str):
    if name == "HttpStore":
        from lore.store.http import HttpStore
        return HttpStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

This ensures `httpx` is only imported when `HttpStore` is actually used, preserving the `remote` optional dependency.

### 7.3 New Test Files

#### `tests/test_http_store.py` (NEW — ~250 lines)

Unit tests with mocked `httpx` responses. See Section 8.

#### `tests/test_http_store_integration.py` (NEW — ~100 lines)

Integration tests against a live server. See Section 8.

---

## 8. Testing Strategy

### 8.1 Unit Tests (`tests/test_http_store.py`)

Mock `httpx.Client` at the transport level using `httpx.MockTransport` or by patching `HttpStore._client`.

**Test cases:**

| Test | What it validates |
|---|---|
| `test_save_posts_lesson` | `save()` sends correct POST body with field mapping |
| `test_save_embedding_serialization` | Embedding bytes -> List[float] conversion in request |
| `test_save_type_in_meta` | `Memory.type` stored in `meta.type` |
| `test_save_ttl_to_expires_at` | TTL converted to ISO datetime |
| `test_get_returns_memory` | `get()` maps LessonResponse to Memory correctly |
| `test_get_not_found` | `get()` returns `None` on 404 |
| `test_get_resolution_roundtrip` | `resolution` stored in `metadata._resolution` when different |
| `test_list_with_filters` | Query params for project, category, limit |
| `test_list_type_maps_to_category` | `type` filter sent as `category` query param |
| `test_update_sends_patch` | `update()` sends only mutable fields |
| `test_update_not_found` | Returns `False` on 404 |
| `test_update_atomic_upvote` | Sends `upvotes: "+1"` for vote changes |
| `test_delete_success` | Returns `True` on 204 |
| `test_delete_not_found` | Returns `False` on 404 |
| `test_count_uses_total` | Extracts `total` from list response |
| `test_cleanup_expired_noop` | Returns 0 always |
| `test_search_posts_embedding` | `search()` sends 384-dim vector, maps results |
| `test_search_with_filters` | Tags, project, limit, min_confidence in request |
| `test_health_check_on_init` | Constructor calls `/health` |
| `test_health_check_failure` | Raises `LoreConnectionError` on connect failure |
| `test_auth_error_on_401` | Raises `LoreAuthError` |
| `test_auth_error_on_403` | Raises `LoreAuthError` |
| `test_retry_on_500` | Retries twice with backoff |
| `test_retry_on_connect_error` | Retries on network failure |
| `test_no_retry_on_4xx` | No retry on client errors |
| `test_timeout_raises_connection_error` | Timeout -> `LoreConnectionError` |
| `test_env_var_fallback` | Uses `LORE_API_URL`/`LORE_API_KEY` env vars |
| `test_repr_masks_key` | API key masked in `repr()` |
| `test_close` | `close()` closes `httpx.Client` |

**Lore integration tests (in same file or `tests/test_lore.py`):**

| Test | What it validates |
|---|---|
| `test_lore_remote_store_init` | `Lore(store='remote', ...)` creates `HttpStore` |
| `test_lore_recall_delegates_to_search` | `recall()` calls `store.search()` not `_recall_local()` |
| `test_lore_upvote_atomic` | `upvote()` uses `store.upvote()` when available |
| `test_mcp_server_remote_config` | `_get_lore()` reads env vars correctly |

### 8.2 Integration Tests (`tests/test_http_store_integration.py`)

Require a running Lore server (Docker Compose with Postgres).

```python
@pytest.mark.integration
class TestHttpStoreIntegration:
    """Integration tests against a live Lore server.

    Requires: docker compose up -d
    Server at: http://localhost:8765
    API key: set LORE_API_KEY env var
    """

    @pytest.fixture
    def store(self):
        store = HttpStore(
            api_url="http://localhost:8765",
            api_key=os.environ["LORE_API_KEY"],
        )
        yield store
        store.close()

    def test_full_crud_cycle(self, store): ...
    def test_save_and_search(self, store): ...
    def test_round_trip_fidelity(self, store): ...
    def test_concurrent_upvotes(self, store): ...
    def test_cross_instance_visibility(self, store): ...
```

Marked with `@pytest.mark.integration` so they're skipped by default:
```
pytest -m "not integration"   # CI default
pytest -m integration          # with live server
```

### 8.3 Test Pattern

Follow the existing parametrized pattern from `tests/test_stores.py` — the `store` fixture cycles through `["memory", "sqlite"]`. After this change, add `"http"` (mocked) to the parametrized list for the subset of tests that make sense (CRUD operations).

---

## 9. Risks and Trade-offs

### 9.1 Memory ID Mismatch

**Risk:** The SDK generates a ULID in `Lore.remember()` and stores it as `Memory.id`. The server also generates its own ULID in `create_lesson()`. The `save()` method does not pass the SDK's ID to the server.

**Mitigation:** The server's `LessonCreateRequest` does not accept an `id` field. The SDK returns the server-generated ID from `POST /v1/lessons` response. Update `HttpStore.save()` to **overwrite** `memory.id` with the server's returned ID. Since `Lore.remember()` returns `memory.id` after `store.save()`, callers get the server ID. Alternatively, use the import endpoint which accepts an `id` field — but that's heavier. The simpler approach: accept the server's ID.

**Implementation:** `HttpStore.save()` will extract the returned `{id}` and set it on the `memory` object (the `Memory` dataclass is mutable). `Lore.remember()` at line 236-237 does `self._store.save(memory); return memory.id` — this naturally returns the server's ID.

### 9.2 Scoring Inconsistency

**Risk:** `_recall_local()` (for SQLite) and the server's SQL use the same scoring formula (0.7 * similarity + 0.3 * freshness), but they may drift over time.

**Trade-off:** Accepted. The server's scoring is authoritative for remote stores. Local scoring is authoritative for local stores. They're intentionally independent.

### 9.3 No Embedding in GET Response

**Risk:** `LessonResponse` does not include embeddings. Memories retrieved via `get()` or `list()` from `HttpStore` have `embedding=None`. This means `Lore._recall_local()` would fail if called on these memories — but it won't be, because `HttpStore` has `search()`.

**Residual risk:** `Lore.reindex()` (lines 452-506) iterates all memories and re-embeds them. This won't work with `HttpStore` because fetched memories have no embeddings to compare. **Mitigation:** `reindex()` should be disabled for remote stores (raise `NotImplementedError` or skip gracefully). Reindexing is a server-side concern for Postgres.

### 9.4 `type` Filter Mapping

**Risk:** The `Store.list(type=...)` filter maps to the server's `category` query param, which filters by tag. But `type` is stored in `meta.type`, not as a tag.

**Mitigation:** For `HttpStore.list()`, we cannot filter by `meta.type` server-side (the list endpoint doesn't support that). Two options:
1. Client-side post-filter: fetch all, filter by `meta.type` locally. Workable for small datasets.
2. Store `type` as a tag as well (e.g., `tags: ["_type:code"]`). Complicates the model.

**Decision:** Option 1 (client-side post-filter). The `list()` method already fetches paginated results. For the `count()` method, which also uses `type`, we accept that the count may be approximate (counts all types when a specific type is requested). This is a known limitation documented in the code.

### 9.5 `httpx` Optional Dependency

**Risk:** `httpx` is in the `remote` optional extra. Importing `HttpStore` without `httpx` installed will fail.

**Mitigation:** Lazy import in `__init__.py` (via `__getattr__`). The `from lore.store.http import HttpStore` in `Lore.__init__` is inside the `store == "remote"` branch, so it's only triggered when remote is explicitly requested. If `httpx` is missing, the import error will have a clear message.

### 9.6 Server Health Endpoint

**Risk:** The PRD mentions `/health` or `/ready` but we haven't verified these exist on the server.

**Mitigation:** Check for `/health` first. If 404, try `/v1/lessons?limit=1` as a fallback health check. If both fail, raise `LoreConnectionError`.

---

## 10. Dependency Summary

| Dependency | Version | Extra | Notes |
|---|---|---|---|
| `httpx` | `>=0.24.0` | `remote` | Already declared in `pyproject.toml` |

No new dependencies required.

---

## 11. Implementation Sequence

Recommended build order (each step is independently testable):

1. **`src/lore/store/http.py`** — Core `HttpStore` class with all 7 ABC methods + `search()`. Write unit tests in parallel.
2. **`src/lore/lore.py`** — Remove "not supported" error, wire `store='remote'` to `HttpStore`. Add `search()` dispatch in `recall()`. Add atomic vote duck-typing.
3. **`src/lore/mcp/server.py`** — Add `LORE_STORE` env var handling in `_get_lore()`.
4. **`src/lore/store/__init__.py`** — Lazy export.
5. **`tests/test_http_store.py`** — Full unit test suite.
6. **`tests/test_http_store_integration.py`** — Integration tests (manual verification against Docker server).

---

## 12. Non-Goals (Deferred)

- Async `HttpStore` variant (P2)
- Bulk import/export via `HttpStore` (P2)
- SQLite-to-remote migration tooling
- Client-side caching/fallback on server downtime
- `type`-aware server-side filtering (requires server API change)
