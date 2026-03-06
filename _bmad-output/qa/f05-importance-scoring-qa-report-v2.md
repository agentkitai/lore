# F05 Importance Scoring - QA Re-Verification Report (v2)

**Date:** 2026-03-06
**Tester:** Quinn (QA Engineer)
**Branch:** feature/v0.6.0-open-brain
**Trigger:** F5 dev-fix completed; re-verify 2 blockers from v1 QA report

## Verdict: PASS

Both blockers from the initial QA report are resolved. All tests pass.

---

## Blocker Re-Verification

### Blocker 1: `test_recall_delegates_to_search` (was FAIL)
- **Test:** `tests/test_http_store.py::TestRecallDispatch::test_recall_delegates_to_search_when_available`
- **Result:** PASS
- **Fix:** Server-side multiplicative scoring implemented in commit `63ab62d`

### Blocker 2: `test_recall_uses_prose_vec_for_search` (was FAIL)
- **Test:** `tests/test_http_store.py::TestRecallDispatch::test_recall_uses_prose_vec_for_search`
- **Result:** PASS
- **Fix:** Same commit; recall dispatch now correctly delegates to search

---

## Spot-Checks

### Multiplicative Scoring in `src/lore/server/routes/lessons.py`
- **Location:** Lines 132-225, `search_lessons()` endpoint
- **Confirmed:** Score = `cosine_similarity * importance_score * decay_factor`
- **Decay:** `0.5^(effective_age / half_life)` with `effective_age = LEAST(age_since_created, age_since_last_accessed)`
- **Half-lives:** Type-specific (code=14d, note=21d, lesson=30d, etc.)

### Access Tracking Endpoint
- **Location:** Lines 238-273, `POST /{lesson_id}/access`
- **Confirmed:**
  - Increments `access_count` via `COALESCE(access_count, 0) + 1`
  - Sets `last_accessed_at = now()`
  - Recomputes `importance_score` server-side: `confidence * vote_factor * access_factor`
  - Returns `id`, `access_count`, `last_accessed_at`, `importance_score`

---

## Full Test Suites

### test_importance_scoring.py
- **Result:** 30 passed, 0 failed (3 deprecation warnings for `datetime.utcnow()`)
- **Coverage:** compute_importance, time_adjusted_importance, resolve_half_life, decay_factor, recall access tracking, multiplicative scoring, upvote/downvote importance, cleanup, backward compat, recalculate

### test_http_store.py
- **Result:** 71 passed, 0 failed
- **Coverage:** constructor, health check, request retries, repr/close, memory/lesson mapping, CRUD, search, recall dispatch, vote dispatch, lazy import, MCP get_lore

---

## Summary

| Check | Result |
|---|---|
| Blocker 1: recall delegates to search | PASS |
| Blocker 2: recall uses prose_vec for search | PASS |
| Spot-check: multiplicative scoring | VERIFIED |
| Spot-check: access endpoint | VERIFIED |
| test_importance_scoring.py (30 tests) | ALL PASS |
| test_http_store.py (71 tests) | ALL PASS |

**Overall: PASS** - F5 dev-fix fully resolves both blockers. Feature is ready.
