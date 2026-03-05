# PRD: Lore HTTP Store Backend

**Version:** 1.0
**Author:** John (Product Manager)
**Date:** 2026-03-05
**Status:** Draft

---

## 1. Problem Statement

Lore v0.5.0 is a cross-agent memory SDK that currently only supports local SQLite storage via its MCP server. A Postgres-backed REST API server already exists and runs in Docker (localhost:8765), but the MCP server cannot use it — attempting `store='remote'` raises a "not supported" error.

This means **multiple AI tools (Claude Code, OpenClaw, etc.) cannot share the same memory store**. Each tool maintains its own isolated SQLite database, leading to:

- **Fragmented knowledge** — a lesson learned in Claude Code is invisible to OpenClaw and vice versa.
- **No team sharing** — memories are locked to a single machine and process.
- **Wasted re-discovery** — agents solve the same problems repeatedly because they can't access each other's memories.

The server infrastructure is already built. The gap is a Store implementation that bridges the SDK to the HTTP API.

## 2. Goals

1. **Shared memory across agents** — Any MCP-connected tool using `store='remote'` talks to the same Postgres-backed server, enabling cross-agent knowledge sharing.
2. **Drop-in replacement** — The HTTP store implements the existing `Store` ABC so the MCP server (and any SDK consumer) can use it without changes to the tool layer.
3. **Local embedding, remote storage** — The SDK continues to compute 384-dim embeddings locally via `OnnxEmbedder`; the server stores and searches them. No embedding computation moves to the server.
4. **Zero breaking changes** — Default behavior (`store=None` -> SQLite) is unchanged. HTTP store is opt-in.

## 3. Requirements

### 3.1 Must-Have (P0)

| ID | Requirement | Notes |
|----|-------------|-------|
| R1 | **`HttpStore` class** implementing `Store` ABC | All 7 abstract methods: `save`, `get`, `list`, `update`, `delete`, `count`, `cleanup_expired` |
| R2 | **Data mapping: Memory <-> Lesson** | SDK `Memory.content` maps to server `problem + resolution`. See [Section 5](#5-data-model-mapping) for full mapping. |
| R3 | **Embedding passthrough** | `save()` and search send 384-dim float32 vectors to the server; embeddings are computed locally before calling the store. |
| R4 | **`Lore(store='remote', api_url=..., api_key=...)` works** | Remove the "not supported" error. Construct `HttpStore` from the provided URL and API key. |
| R5 | **MCP server supports remote store via env vars** | `LORE_STORE=remote`, `LORE_API_URL`, `LORE_API_KEY` environment variables configure the MCP server to use `HttpStore`. |
| R6 | **Search via `/v1/lessons/search`** | The `recall()` path must embed the query locally, then POST the embedding vector to the server's search endpoint. |
| R7 | **Auth via API key header** | All HTTP requests include the API key in the `Authorization: Bearer <key>` header (matching server's auth scheme). |
| R8 | **Uses `httpx` (already an optional dep)** | The `remote` extra in pyproject.toml already declares `httpx>=0.24.0`. Use synchronous `httpx.Client` for the store. |
| R9 | **Error handling** | HTTP errors (4xx, 5xx, network) surface as clear exceptions, not silent failures. Timeout configurable with sensible default (30s). |

### 3.2 Should-Have (P1)

| ID | Requirement | Notes |
|----|-------------|-------|
| R10 | **Connection health check** | `HttpStore` validates connectivity on init (hit `/health` or `/ready`). Fail fast with actionable error message. |
| R11 | **Retry on transient failures** | Retry 5xx and connection errors up to 2 times with backoff. No retry on 4xx. |
| R12 | **`LORE_API_URL` / `LORE_API_KEY` env var fallbacks** | If not passed as constructor args, read from environment variables. |

### 3.3 Nice-to-Have (P2)

| ID | Requirement | Notes |
|----|-------------|-------|
| R13 | **Async `HttpStore` variant** | For consumers that want `async/await`. Not needed for MCP server (which is sync). |
| R14 | **Connection pooling** | `httpx.Client` session reuse for connection pooling. |
| R15 | **Bulk operations** | Use `/v1/lessons/import` and `/v1/lessons/export` for batch save/load. |

## 4. API Endpoint Mapping

How each `Store` ABC method maps to server endpoints:

| Store Method | HTTP Method | Endpoint | Request Body | Notes |
|---|---|---|---|---|
| `save(memory)` | POST | `/v1/lessons` | `LessonCreateRequest` with embedding | Returns `{id}`. Memory content split into problem/resolution (see mapping). |
| `get(id)` | GET | `/v1/lessons/{id}` | — | Returns `LessonResponse`. Map back to `Memory`. |
| `list(project, type, limit)` | GET | `/v1/lessons?project=...&limit=...` | — | `type` filter maps to `category` query param (tag-based). |
| `update(memory)` | PATCH | `/v1/lessons/{id}` | `LessonUpdateRequest` | Only sends changed fields (confidence, tags, meta, votes). |
| `delete(id)` | DELETE | `/v1/lessons/{id}` | — | Returns 204 on success, 404 if missing. |
| `count(project, type)` | GET | `/v1/lessons?project=...&limit=1` | — | Use `total` from `LessonListResponse`. |
| `cleanup_expired()` | — | — | Server handles expiry in search WHERE clause. Return 0 (no-op) or call list + delete. |
| `search(embedding, ...)` | POST | `/v1/lessons/search` | `LessonSearchRequest` with 384-dim vector | Used by `Lore.recall()`. Not part of Store ABC but needed for recall path. |

## 5. Data Model Mapping

The SDK uses `Memory` (content-centric) while the server uses `Lesson` (problem/resolution-centric). This is the key translation layer.

### Memory -> Lesson (save)

| Memory field | Lesson field | Transformation |
|---|---|---|
| `content` | `problem` | Store full content as `problem`. |
| — | `resolution` | Set to `content` (mirror). The server requires both; for general memories they're equivalent. |
| `context` | `context` | Direct map. |
| `tags` | `tags` | Direct map. |
| `confidence` | `confidence` | Direct map. |
| `source` | `source` | Direct map. |
| `project` | `project` | Direct map. |
| `embedding` | `embedding` | Deserialize bytes -> `List[float]`. |
| `expires_at` | `expires_at` | Direct map (ISO 8601). |
| `metadata` | `meta` | Direct map. Also store `type` in `meta.type` for round-tripping. |
| `type` | `meta.type` | Stored inside meta dict for round-trip fidelity. |
| `ttl` | `expires_at` | Compute: `now() + ttl` seconds -> ISO datetime. |
| `upvotes` | `upvotes` | Direct map (on update, use "+1"/"-1" for atomic). |
| `downvotes` | `downvotes` | Direct map (on update, use "+1"/"-1" for atomic). |

### Lesson -> Memory (get/list/search)

| Lesson field | Memory field | Transformation |
|---|---|---|
| `problem` | `content` | Direct map (problem is the canonical content). |
| `resolution` | — | Stored in `metadata._resolution` if different from problem, for lossless round-trip. |
| `context` | `context` | Direct map. |
| `tags` | `tags` | Direct map. |
| `confidence` | `confidence` | Direct map. |
| `source` | `source` | Direct map. |
| `project` | `project` | Direct map. |
| `created_at` | `created_at` | Direct map. |
| `updated_at` | `updated_at` | Direct map. |
| `expires_at` | `expires_at` | Direct map. |
| `upvotes` | `upvotes` | Direct map. |
| `downvotes` | `downvotes` | Direct map. |
| `meta` | `metadata` | Direct map. Extract `meta.type` -> `Memory.type`. |
| `meta.type` | `type` | Extract from meta, default to `"general"`. |

## 6. Configuration

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LORE_STORE` | No | `local` | `"local"` for SQLite (default), `"remote"` for HTTP store. |
| `LORE_API_URL` | When remote | — | Base URL of Lore server (e.g. `http://localhost:8765`). |
| `LORE_API_KEY` | When remote | — | API key for server authentication. |
| `LORE_HTTP_TIMEOUT` | No | `30` | HTTP request timeout in seconds. |
| `LORE_PROJECT` | No | — | Default project scope (existing, unchanged). |

### Programmatic Usage

```python
# Explicit remote store
lore = Lore(store='remote', api_url='http://localhost:8765', api_key='lore_...')

# Or via HttpStore directly
from lore.store.http import HttpStore
store = HttpStore(api_url='http://localhost:8765', api_key='lore_...')
lore = Lore(store=store)
```

## 7. File Changes

| File | Change |
|---|---|
| `src/lore/store/http.py` | **NEW** — `HttpStore(Store)` implementation |
| `src/lore/lore.py` | Remove "not supported" error, wire up `HttpStore` construction |
| `src/lore/mcp/server.py` | Read `LORE_STORE`, `LORE_API_URL`, `LORE_API_KEY` env vars in `_get_lore()` |
| `tests/test_http_store.py` | **NEW** — Unit tests with mocked HTTP responses |
| `tests/test_http_store_integration.py` | **NEW** — Integration tests against live server (skipped in CI without server) |

## 8. Success Criteria

1. **Functional:** `lore.remember()` and `lore.recall()` work end-to-end through the HTTP store against a running Postgres server.
2. **Cross-agent:** Two separate MCP server instances (different processes) using the same `LORE_API_URL` can read each other's memories.
3. **Backward compatible:** Default behavior (no env vars set) continues to use SQLite with zero changes.
4. **Tests pass:** Unit tests with mocked HTTP, integration tests against a live server.
5. **Round-trip fidelity:** `remember()` -> `recall()` preserves all Memory fields (type, tags, metadata, confidence, votes).

## 9. Out of Scope

- **Server-side changes** — The Postgres server API is frozen for this feature. No new endpoints.
- **Embedding on server** — The server does not compute embeddings. Local `OnnxEmbedder` remains the embedding source.
- **Async store** — P2; sync `httpx.Client` is sufficient for MCP server.
- **Migration tooling** — No SQLite-to-remote migration utility in this iteration.
- **Multi-org support in SDK** — `org_id` is managed by the server via API key scoping, not the SDK.
- **WebSocket/streaming** — Not needed for CRUD + search operations.
- **MCP server protocol changes** — Tool signatures and descriptions remain unchanged.
- **Rate limiting client-side** — Server handles rate limiting; client trusts 429 responses.

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Memory<->Lesson model mismatch | Data loss on round-trip | Store `type` in `meta.type`; store `resolution` in `metadata._resolution` if different. Test round-trip fidelity explicitly. |
| Server downtime breaks all agents | No memory access | Agents degrade gracefully (MCP tools return error strings, not crashes). Consider P2 local cache fallback in future. |
| Embedding dimension mismatch | Search fails with 422 | Server validates 384-dim; SDK always produces 384-dim. Fail fast with clear error. |
| API key leaked in logs | Security breach | Never log API key values. Use `repr()` masking in error messages. |
