# QA Report: Lore HTTP Store

**Date:** 2026-03-05
**Reviewer:** Quinn (QA Engineer)
**Feature:** HttpStore -- bridge SDK to Postgres-backed REST API
**Branch:** `feature/http-store`

---

## Overall Verdict: CONDITIONAL PASS

All unit tests pass (71/71 HttpStore tests, 583/583 total suite). Architecture compliance is solid. Code quality is high. Several minor issues and one moderate gap identified below. Integration tests could not be verified (server not running).

---

## Story-by-Story Verdicts

### Story 1: HttpStore Core -- Constructor, Health Check, HTTP Dispatch

**Verdict: PASS**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| `HttpStore(api_url, api_key)` creates configured `httpx.Client` with Bearer header | PASS | `http.py:46-51`, test `test_creates_client_with_auth_header` |
| Constructor reads env vars when args are `None` | PASS | `http.py:29-36`, test `test_env_var_fallback` |
| Resolution order: explicit > env > ValueError | PASS | `http.py:29-41`, test `test_explicit_params_override_env` |
| `_check_health()` hits `/health` on init with actionable error | PASS | `http.py:59-75`, tests `TestHealthCheck` (4 tests) |
| `_request()` retries 5xx + connect/timeout (max 2, backoff) | PASS | `http.py:81-133`, tests `test_retry_on_500`, `test_retry_exhausted_on_500`, `test_retry_on_connect_error`, `test_retry_on_timeout` |
| `_request()` raises `LoreAuthError` on 401/403 (no retry) | PASS | `http.py:87-93`, tests `test_auth_error_on_401`, `test_auth_error_on_403`, `test_no_retry_on_4xx` |
| `_request()` raises `ValueError` on 422 | PASS | `http.py:98-100`, test `test_422_raises_value_error` |
| `_request()` returns response on 404 | PASS | `http.py:95-96`, test `test_returns_404_response` |
| `repr()` masks api_key | PASS | `http.py:355-359`, tests `test_repr_masks_key`, `test_repr_short_key` |
| `close()` closes `httpx.Client` | PASS | `http.py:346-349`, tests `test_close_closes_client`, `test_close_idempotent` |
| API key never in error messages | PASS | `http.py:87-93,115-131` -- errors use URL not key; test `test_api_key_not_in_error_messages` |

---

### Story 2: HttpStore CRUD -- Field Mapping and Store ABC Methods

**Verdict: PASS**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| `_memory_to_lesson()` maps all fields per spec | PASS | `http.py:139-172`, tests `TestMemoryToLesson` (7 tests) |
| `_lesson_to_memory()` maps back correctly | PASS | `http.py:174-218`, tests `TestLessonToMemory` (6 tests) |
| `save()` POSTs to `/v1/lessons`, overwrites `memory.id` | PASS | `http.py:224-228`, tests `test_save_posts_lesson`, `test_save_overwrites_id` |
| `get()` GETs, returns Memory or None on 404 | PASS | `http.py:230-234`, tests `test_get_returns_memory`, `test_get_not_found` |
| `list()` GETs with params, client-side type filter | PASS | `http.py:236-257`, tests `test_list_with_filters`, `test_list_type_postfilter` |
| `update()` PATCHes with mutable fields, True/False | PASS | `http.py:259-270`, tests `test_update_sends_patch`, `test_update_not_found` |
| `delete()` DELETEs, True on 204, False on 404 | PASS | `http.py:272-274`, tests `test_delete_success`, `test_delete_not_found` |
| `count()` extracts `total` from list response | PASS | `http.py:276-286`, test `test_count_uses_total` |
| `cleanup_expired()` returns 0 (no-op) | PASS | `http.py:288-289`, test `test_returns_zero` |

**Notes:**
- `_lesson_to_memory()` at line 214: `_to_iso(data.get("expires_at")) or None` -- when `expires_at` is `None`, `_to_iso` returns `""`, which is falsy, so `or None` kicks in correctly. Technically correct but fragile -- if the server ever sends an empty string, it would also become `None`. Acceptable.
- `update()` at lines 259-270: Does NOT send `upvotes`/`downvotes` fields. This is by design since atomic vote methods handle those. Confirmed correct per architecture.

---

### Story 3: HttpStore Search and Recall Path Integration

**Verdict: PASS**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| `search()` POSTs to `/v1/lessons/search` with 384-dim vector | PASS | `http.py:315-340`, test `test_search_posts_embedding` |
| Results mapped to `List[RecallResult]` | PASS | `http.py:337-339`, verified in test |
| `Lore.recall()` detects `hasattr(store, 'search')` and delegates | PASS | `lore.py:266-273`, test `test_recall_delegates_to_search_when_available` |
| EmbeddingRouter: prose vector used for search | PASS | `lore.py:258-261`, test `test_recall_uses_prose_vec_for_search` |
| Freshness check still works on remote results | PASS | `lore.py:281-286` -- freshness applied after search regardless of store type |
| Filters passed to server | PASS | `http.py:324-332`, test `test_search_with_filters` |

**Finding (Minor -- F1):** `recall()` does not pass `type` filter to `HttpStore.search()`. At `lore.py:266-273`, the `type` parameter is not forwarded to `store.search()`. The `search()` method on `HttpStore` also doesn't accept a `type` parameter. For local recall, `type` is passed to `_recall_local` and used to filter. For remote recall, type-based filtering is silently dropped. This is documented as a known limitation in the architecture (Section 9.4), but the recall method should ideally document this behavior or at least log it.

---

### Story 4: Lore Wiring -- Remote Store Construction and Atomic Votes

**Verdict: PASS**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| `Lore(store='remote', ...)` constructs `HttpStore` | PASS | `lore.py:116-118`, test `test_lore_remote_store_init` |
| `Lore(store='remote')` works with env vars | PASS | `lore.py:116-118` + `http.py:29-30`, test `test_lore_remote_store_env_fallback` |
| Default behavior unchanged | PASS | `lore.py:119-126`, test `test_default_store_unchanged` |
| `HttpStore` has `upvote(id)` / `downvote(id)` | PASS | `http.py:295-309`, tests `TestHttpStoreVoteMethods` |
| `Lore.upvote()` calls `store.upvote()` when available | PASS | `lore.py:436-438`, test `test_upvote_uses_atomic_when_available` |
| `Lore.downvote()` calls `store.downvote()` when available | PASS | `lore.py:448-450`, test `test_downvote_uses_atomic_when_available` |
| `from lore.store import HttpStore` works (lazy) | PASS | `store/__init__.py:10-14`, test `test_import_httpstore_from_store_package` |
| Importing `lore.store` without httpx doesn't raise | PASS | Lazy `__getattr__` ensures deferred import |

---

### Story 5: MCP Server Remote Store Configuration

**Verdict: PASS**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| `LORE_STORE=remote` + URL + KEY creates remote Lore | PASS | `server.py:41-47`, test `test_remote_store_from_env` |
| `LORE_STORE=local` (or unset) uses default | PASS | `server.py:48-49`, test `test_local_store_default` |
| Invalid `LORE_STORE` raises clear error | PASS | `server.py:50-54`, test `test_invalid_store_type_raises` |
| `LORE_PROJECT` works with remote | PASS | `server.py:38,43`, test `test_project_works_with_remote` |
| MCP tool signatures unchanged | PASS | All `@mcp.tool` definitions unchanged from previous version |

---

### Story 6: Integration Tests Against Live Server

**Verdict: CONDITIONAL PASS (server unavailable)**

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Tests marked with `@pytest.mark.integration` | PASS | `test_http_store_integration.py:43-44` |
| `test_full_crud_cycle` | PRESENT | Lines 99-135 |
| `test_save_and_search` | PRESENT | Lines 138-160 |
| `test_round_trip_fidelity` | PRESENT | Lines 163-192 |
| `test_cross_instance_visibility` | PRESENT | Lines 195-206 |
| `test_upvote_downvote` | PRESENT | Lines 209-233 |
| Test cleanup in teardown | PASS | Fixture at lines 56-69 tracks and deletes created memories |
| `integration` marker registered | PASS | `pyproject.toml` line 77 |

**Could not verify runtime behavior** -- Docker server at localhost:8765 was not running during QA. Tests are correctly skipped via `_server_available()` guard. Test code reviewed manually and appears correct.

---

## Bugs Found

### F1: `type` filter silently dropped for remote recall (Minor)

**File:** `src/lore/lore.py:266-273`

The `recall()` method accepts a `type` parameter but never passes it to `HttpStore.search()`. For local stores, `type` is used in `_recall_local()`. For remote stores, calling `recall(query, type="code")` will return results of all types.

**Impact:** Low. Documented as known limitation in architecture Section 9.4. The server's search endpoint doesn't support `meta.type` filtering anyway.

**Recommendation:** Add a code comment at `lore.py:266` noting this limitation, or add client-side post-filtering of search results by type (same pattern as `list()`).

### F2: `delete()` return value relies on status code != 404, but 204 triggers `raise_for_status()` (Non-issue after analysis)

**File:** `src/lore/store/http.py:272-274`

At first glance: `delete()` checks `resp.status_code != 404`. In `_request()`, after passing 401/403/404/422/500+ checks, line 112 calls `response.raise_for_status()`. A 204 response has `raise_for_status()` as a no-op (it's a 2xx), so this is actually fine. **Not a bug.**

### F3: `update()` doesn't send `upvotes`/`downvotes` (By design)

**File:** `src/lore/store/http.py:259-270`

The `update()` method omits `upvotes` and `downvotes` from the PATCH payload. This is correct because atomic vote methods (`upvote()`/`downvote()`) handle these separately. Confirmed by architecture Section 3.3. **Not a bug.**

### F4: `_lesson_to_memory` returns empty string for `created_at`/`updated_at` when missing (Minor)

**File:** `src/lore/store/http.py:211-212`

If the server response doesn't include `created_at` or `updated_at`, `_to_iso(None)` returns `""`. This matches `Memory.created_at`'s default of `""`, so it's consistent but could cause issues if downstream code calls `datetime.fromisoformat("")`.

**Impact:** Very low. The server always returns these fields. Only matters if the API changes.

---

## Missing Test Coverage

1. **No test for `httpx.ConnectError` message content** -- The `test_health_check_connect_error` test verifies the exception is raised but the `httpx.ConnectError` constructor requires a `message` kwarg in newer httpx versions. Current test passes, so OK for now.

2. **No test for `__del__` behavior** -- `http.py:351-353` has a `__del__` fallback that calls `close()`. Not tested, but `__del__` is inherently hard to test reliably. Acceptable.

3. **No test for the `raise last_exc` fallback at line 133** -- This is the "unreachable" final line in `_request()`. Can't be hit in practice. Acceptable.

4. **No test for JSON parse errors** -- If the server returns malformed JSON, `resp.json()` in `save()`, `get()`, `search()` etc. would raise `json.JSONDecodeError`. Not explicitly handled or tested. Low risk since the server is controlled.

5. **No `reindex()` guard for HttpStore** -- Architecture Section 9.3 notes that `reindex()` won't work with `HttpStore` because fetched memories have `embedding=None`. No guard is implemented. Calling `lore.reindex()` with an `HttpStore` would attempt to re-embed all memories and call `update()` on each -- which would work but waste bandwidth (embedding bytes aren't sent via update, they're in `save()` only). **This is a real gap but out of scope per architecture (deferred).**

---

## Architecture Compliance

| Aspect | Status | Notes |
|---|---|---|
| `HttpStore(Store)` implements all 7 ABC methods | PASS | All abstract methods implemented |
| `search()` as additional method (not ABC) | PASS | Correctly excluded from ABC |
| Synchronous `httpx.Client` | PASS | `http.py:46-51` |
| Connection pooling via session | PASS | Single `httpx.Client` reused |
| Memory <-> Lesson field mapping | PASS | Both directions match architecture spec |
| Embedding pipeline unchanged | PASS | No modifications to embedding code |
| Exception hierarchy matches spec | PASS | Uses existing `LoreConnectionError`, `LoreAuthError`, `MemoryNotFoundError` |
| `base_url` on client | PASS | `http.py:47` |
| Retry policy: 2 retries, exponential backoff | PASS | `http.py:83,107-109,119-121,128-130` |
| Health check on init | PASS | `http.py:53` |
| Lazy import in `__init__.py` | PASS | `store/__init__.py:10-14` |
| Duck-typing for `search()` and `upvote()`/`downvote()` | PASS | `lore.py:266,436,448` |

---

## Test Results Summary

| Test Suite | Result | Count |
|---|---|---|
| `tests/test_http_store.py` | PASS | 71/71 |
| All unit tests (excluding integration) | PASS | 583 passed, 7 skipped |
| `tests/test_http_store_integration.py` | SKIPPED | 7/7 (server unavailable) |

---

## Summary

The HttpStore implementation is solid, well-structured, and closely follows the architecture spec. Code quality is high -- clean separation of concerns, proper error handling, correct field mapping, and comprehensive unit tests. The one functional gap (type filter not forwarded to remote search) is a known/documented limitation, not a defect.

**Blocking issues:** None.
**Conditional on:** Integration tests should be verified against a running server before merge. All 7 integration test cases are present and correctly structured but could not be executed.
