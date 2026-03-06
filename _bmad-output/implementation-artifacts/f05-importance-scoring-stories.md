# F5 — Importance Scoring + Adaptive Decay: User Stories

**Feature:** F5 — Importance Scoring + Adaptive Decay
**PRD:** `_bmad-output/planning-artifacts/f05-importance-scoring-prd.md`
**Architecture:** `_bmad-output/implementation-artifacts/f05-importance-scoring-architecture.md`
**Date:** 2026-03-06
**Depends on:** F4 (Multi-Level Memory Tiers)

---

## Priority Legend

| Priority | Meaning |
|----------|---------|
| P0 | Foundation — must land first |
| P1 | Core logic — depends on P0 |
| P2 | Integration — depends on P1 |
| P3 | Polish — depends on P2 |

---

## S1: Schema Migration — Add Importance Fields to Memory Dataclass and SQLite

**Priority:** P0 | **Estimate:** M | **Dependencies:** F4 landed

### Description

Add `importance_score`, `access_count`, and `last_accessed_at` fields to the `Memory` dataclass and apply the corresponding SQLite schema migration. Add `TIER_DECAY_HALF_LIVES` config dict to `types.py` with backward-compatible `DECAY_HALF_LIVES` alias. Update `MemoryStats` with `avg_importance` and `below_threshold_count`.

### Files Changed

- `src/lore/types.py` — Add 3 fields to `Memory`, add `TIER_DECAY_HALF_LIVES` dict, update `DECAY_HALF_LIVES` to alias `TIER_DECAY_HALF_LIVES["long"]`, update `MemoryStats`
- `src/lore/store/sqlite.py` — Add 3 `ALTER TABLE` statements in `_maybe_migrate()`, add indexes on `importance_score` and `last_accessed_at`, handle new fields in `_to_row()`/`_from_row()`
- `src/lore/store/memory.py` — Verify new fields are carried through (dataclass defaults handle this)
- `src/lore/store/http.py` — Map new fields in `_to_memory()`/`_from_memory()` with optional deserialization defaults

### Acceptance Criteria

```
Given an existing SQLite database without importance columns
When the application starts and _maybe_migrate() runs
Then columns importance_score (DEFAULT 1.0), access_count (DEFAULT 0), and last_accessed_at (NULL) are added
And indexes idx_memories_importance and idx_memories_last_accessed are created

Given a Memory created with no explicit importance fields
When inspected
Then importance_score == 1.0, access_count == 0, last_accessed_at is None

Given code that imports DECAY_HALF_LIVES from lore.types
When accessed
Then it returns the same values as TIER_DECAY_HALF_LIVES["long"]

Given TIER_DECAY_HALF_LIVES is defined
When resolve lookup for tier="working", type="code"
Then the half-life value is 0.5

Given HttpStore receives a memory payload without importance fields
When deserialized via _to_memory()
Then defaults are applied (importance_score=1.0, access_count=0, last_accessed_at=None)
```

---

## S2: Importance Module — Pure Computation Functions

**Priority:** P0 | **Estimate:** M | **Dependencies:** S1

### Description

Create `src/lore/importance.py` containing all importance-related pure functions: `compute_importance()`, `time_adjusted_importance()`, `decay_factor()`, and `resolve_half_life()`. These are pure functions with no I/O or side effects (except `time_adjusted_importance` which defaults `now` to `datetime.utcnow()` but accepts injection).

### Files Changed

- `src/lore/importance.py` — **New file** with 4 functions

### Acceptance Criteria

```
Given a Memory with confidence=1.0, upvotes=0, downvotes=0, access_count=0
When compute_importance() is called
Then it returns 1.0

Given a Memory with upvotes=5, downvotes=0, access_count=10, confidence=1.0
When compute_importance() is called
Then vote_factor = 1.5, access_factor = 1.0 + log2(11) * 0.1 ≈ 1.346
And result ≈ 2.02

Given a Memory with downvotes=10, upvotes=0
When compute_importance() is called
Then vote_factor is floored at 0.1 (not negative)

Given a Memory with importance_score=1.0 and age exactly equal to half_life_days
When time_adjusted_importance() is called
Then result ≈ 0.5

Given a Memory with last_accessed_at set to 1 day ago, created_at set to 30 days ago, half_life=30
When time_adjusted_importance() is called
Then decay uses age=1 day (min of created age and last-access age), result ≈ 0.977

Given decay_factor(age_days=0, half_life_days=30)
When called
Then returns 1.0

Given decay_factor(age_days=300, half_life_days=30)
When called
Then returns a value very close to 0.0
```

---

## S3: Tier-Aware Decay Lookup with Fallback Chain

**Priority:** P0 | **Estimate:** S | **Dependencies:** S1

### Description

Implement `resolve_half_life()` in `src/lore/importance.py` with the full fallback chain: project overrides > tier+type > tier default > legacy flat lookup > global default (30 days). Memories without a `tier` field default to `"long"` for backward compatibility.

### Files Changed

- `src/lore/importance.py` — `resolve_half_life()` function (may already be stubbed in S2; this story ensures the full fallback chain and override support)

### Acceptance Criteria

```
Given tier="long", type="convention", no overrides
When resolve_half_life() is called
Then returns 60.0 (from TIER_DECAY_HALF_LIVES["long"]["convention"])

Given tier="working", type="unknown_type", no overrides
When resolve_half_life() is called
Then returns 1 (from TIER_DECAY_HALF_LIVES["working"]["default"])

Given tier=None, type="lesson"
When resolve_half_life() is called
Then returns 30.0 (falls back to "long" tier, lesson=30)

Given overrides={("short", "code"): 3}, tier="short", type="code"
When resolve_half_life() is called
Then returns 3 (override takes precedence over TIER_DECAY_HALF_LIVES)

Given tier="nonexistent_tier", type="note"
When resolve_half_life() is called
Then returns 30.0 (global default, since tier not in TIER_DECAY_HALF_LIVES)
```

### Notes

- S2 and S3 are logically part of the same module but split for clarity. S3 focuses on the fallback chain specifically. If implemented together with S2, mark both complete.

---

## S4: Score Computation Logic — Votes and Access Combined

**Priority:** P1 | **Estimate:** M | **Dependencies:** S1, S2

### Description

Wire `compute_importance()` into `upvote_memory()` and `downvote_memory()` so that importance_score is recomputed and persisted after each vote change. Update the Lore constructor to accept `importance_threshold` and `decay_config` parameters, and emit deprecation warnings for `decay_similarity_weight` and `decay_freshness_weight`.

### Files Changed

- `src/lore/lore.py` — Update `upvote_memory()`, `downvote_memory()`, constructor params, deprecation warnings

### Acceptance Criteria

```
Given a Memory with importance_score=1.0
When upvote_memory() is called
Then importance_score is recomputed via compute_importance() and persisted via store.update()
And the new importance_score reflects the updated upvotes count

Given a Memory with upvotes=0, downvotes=0
When downvote_memory() is called 3 times
Then importance_score == 1.0 * max(0.1, 1.0 + (0 - 3) * 0.1) * 1.0 == 0.7

Given Lore(decay_similarity_weight=0.5)
When constructed
Then a DeprecationWarning is emitted mentioning multiplicative model
And the parameter value is ignored

Given Lore(importance_threshold=0.1, decay_config={("short", "code"): 3})
When constructed
Then self._importance_threshold == 0.1
And self._decay_config contains the override
```

---

## S5: Access Reinforcement — Recall Updates Access Tracking

**Priority:** P1 | **Estimate:** M | **Dependencies:** S1, S2

### Description

After `recall()` computes results and selects the top-K memories, increment `access_count`, set `last_accessed_at = now()`, recompute `importance_score`, and persist each returned memory. For SQLite, wrap updates in a single transaction. Cache `now` once per recall call.

### Files Changed

- `src/lore/lore.py` — Add access tracking block in `_recall_local()` (after scoring, before return). Wrap in transaction for SQLite.

### Acceptance Criteria

```
Given a Memory with access_count=0 and last_accessed_at=None
When recall() returns this memory in results
Then access_count becomes 1
And last_accessed_at is set to the current timestamp
And importance_score is recomputed (access_factor increases)

Given a Memory with access_count=5
When recall() returns it again
Then access_count becomes 6
And last_accessed_at is updated to the new timestamp

Given recall() returns 5 memories
When using SQLite store
Then all 5 updates occur within a single transaction (not 5 separate transactions)

Given recall() returns a memory
When time_adjusted_importance is computed on next recall
Then the decay age uses min(age_since_created, age_since_last_accessed)
So a recently-accessed old memory decays slower than an unaccessed old memory
```

---

## S6: Recall Integration — Multiplicative Scoring Model

**Priority:** P1 | **Estimate:** L | **Dependencies:** S1, S2, S3

### Description

Replace the current additive scoring model (`sim_weight * similarity + fresh_weight * freshness`) in `_recall_local()` with the multiplicative model (`cosine_score * time_adjusted_importance`). Remove `self._similarity_weight` and `self._freshness_weight` internal state. Compute `now` once per recall call and pass through.

### Files Changed

- `src/lore/lore.py` — Rewrite scoring in `_recall_local()` (lines ~367-379), remove weight instance variables, use `resolve_half_life()` + `time_adjusted_importance()` per candidate

### Acceptance Criteria

```
Given two memories with identical cosine_similarity=0.8
When memory A has importance_score=2.0 (half_life=30, age=0 days)
And memory B has importance_score=0.5 (half_life=30, age=0 days)
Then memory A's final_score = 0.8 * 2.0 = 1.6
And memory B's final_score = 0.8 * 0.5 = 0.4
And A ranks above B

Given a memory with cosine_similarity=0.9, importance_score=1.0, age=30 days, half_life=30
When recall scoring is computed
Then final_score = 0.9 * (1.0 * 0.5) = 0.45

Given a working-tier memory and a long-tier memory of the same type and age
When both have the same cosine_similarity and importance_score
Then the working-tier memory scores lower (faster decay from shorter half-life)

Given recall() is called
When scoring candidates
Then datetime.utcnow() is called once and reused for all candidates
```

---

## S7: Cleanup Strategy — Prune Below Importance Threshold

**Priority:** P2 | **Estimate:** M | **Dependencies:** S2, S3, S4

### Description

Enhance `cleanup_expired()` to add a Phase 2: after TTL-based cleanup, iterate all memories, compute `time_adjusted_importance`, and delete those below `importance_threshold` (default 0.05). Add `recalculate_importance()` method for batch recomputation.

### Files Changed

- `src/lore/lore.py` — Update `cleanup_expired()`, add `recalculate_importance()`

### Acceptance Criteria

```
Given a memory with importance_score=1.0, age=150 days, half_life=30 days
When cleanup_expired(importance_threshold=0.05) runs
Then time_adjusted_importance ≈ 1.0 * 0.5^(150/30) = 0.031
And the memory is deleted (0.031 < 0.05)

Given a memory with importance_score=2.0, age=150 days, half_life=30 days
When cleanup_expired(importance_threshold=0.05) runs
Then time_adjusted_importance ≈ 2.0 * 0.031 = 0.063
And the memory is NOT deleted (0.063 > 0.05)

Given cleanup_expired() is called with no explicit threshold
When self._importance_threshold is 0.05 (default)
Then the default threshold is used

Given recalculate_importance(project="my-project") is called
When 3 memories have stale importance_scores
Then all 3 are recomputed and persisted
And the method returns 3
```

---

## S8: CLI and MCP Output Updates

**Priority:** P2 | **Estimate:** M | **Dependencies:** S4, S6

### Description

Update MCP tool output formatting to include `importance_score` in `recall` and `list_memories` results. Update CLI `lore memories` to display importance column and add `--sort importance` flag.

### Files Changed

- `src/lore/mcp/server.py` — Update recall result formatting to show `(importance: X.XX, score: Y.YY)`, update list_memories to include importance column
- `src/lore/cli.py` — Add `--sort` option with `importance` choice, display importance column in table output

### Acceptance Criteria

```
Given a recall result with importance_score=0.87 and final_score=0.74
When formatted for MCP output
Then output includes "importance: 0.87" and "score: 0.74"

Given list_memories MCP tool is called
When results are formatted
Then each memory line includes importance_score value

Given `lore memories --sort importance` is run
When there are 3 memories with importance_scores [0.5, 0.9, 0.2]
Then they are displayed in order: 0.9, 0.5, 0.2 (descending)

Given `lore memories` is run without --sort flag
When results are displayed
Then importance column is visible but sort order is by creation date (default)
```

---

## S9: PostgreSQL Migration and Server-Side Scoring

**Priority:** P2 | **Estimate:** L | **Dependencies:** S2, S3, S6

### Description

Create PostgreSQL migration `migrations/006_importance_scoring.sql` adding the 3 new columns and indexes. Update server-side search SQL in `src/lore/server/routes/lessons.py` to use the multiplicative scoring model. Add batch access tracking endpoint `POST /v1/lessons/access`.

### Files Changed

- `migrations/006_importance_scoring.sql` — **New file** with ALTER TABLE + CREATE INDEX
- `src/lore/server/routes/lessons.py` — Update search SQL to multiplicative model, add `/v1/lessons/access` endpoint
- `src/lore/store/http.py` — Call batch access tracking endpoint after recall

### Acceptance Criteria

```
Given a fresh PostgreSQL database
When migration 006 runs
Then importance_score, access_count, last_accessed_at columns exist on lessons table
And idx_lessons_importance and idx_lessons_last_accessed indexes exist

Given a server-side search query
When scoring is computed
Then it uses: cosine_sim * importance_score * power(0.5, age_days / half_life)
And age_days uses LEAST(age_since_created, age_since_last_accessed)

Given POST /v1/lessons/access with body {"ids": ["abc", "def"]}
When called
Then access_count is incremented and last_accessed_at updated for both memories
And importance_score is recomputed server-side
```

---

## S10: Test Suite — Unit, Integration, and Existing Test Updates

**Priority:** P3 | **Estimate:** L | **Dependencies:** S1-S8

### Description

Create `tests/test_importance_scoring.py` with comprehensive unit tests for the importance module. Update `tests/test_semantic_decay.py` and `tests/test_decay_voting.py` for the new multiplicative scoring model. Ensure all existing tests pass.

### Files Changed

- `tests/test_importance_scoring.py` — **New file** with unit tests for `compute_importance`, `time_adjusted_importance`, `decay_factor`, `resolve_half_life`, and integration tests for access tracking, cleanup, and backward compat
- `tests/test_semantic_decay.py` — Update expected scores for multiplicative model
- `tests/test_decay_voting.py` — Update to verify votes feed through `importance_score`

### Acceptance Criteria

```
Given the importance module
When unit tests run
Then all 13+ test cases from architecture doc section 14.1 pass:
  - compute_importance: default, upvotes, downvotes floor, access log, combined
  - time_adjusted: fresh, one half-life, last_accessed recency
  - resolve_half_life: tier+type, tier default, no tier, overrides
  - decay_factor: boundary conditions

Given the integration tests
When run against SQLite store
Then access tracking (count increment, last_accessed update, importance recompute) works
And multiplicative scoring produces correct rankings
And cleanup removes only below-threshold memories
And DECAY_HALF_LIVES backward compat alias works
And deprecated constructor params emit warnings

Given existing tests in test_semantic_decay.py and test_decay_voting.py
When updated for multiplicative model
Then all pass with correct relative rankings preserved

Given pytest --cov on src/lore/importance.py
When measured
Then coverage is >= 95%
```

---

## Story Dependency Graph

```
S1 (Schema + Types) ─────┬──> S2 (Importance Module) ──┬──> S4 (Vote Integration)
          │               │                              │
          │               └──> S3 (Tier Decay Lookup) ──┤──> S6 (Recall Scoring) ──┐
          │                                              │                          │
          │                                              ├──> S5 (Access Tracking)  │
          │                                              │                          │
          │                                              └──> S7 (Cleanup) ────────>│
          │                                                                         │
          └──> S9 (PostgreSQL + Server) ──────────────────────────────────────────>│
                                                                                    │
                                                            S8 (CLI/MCP) <──────────┤
                                                                                    │
                                                            S10 (Tests) <───────────┘
```

## Sprint Planning Summary

| Story | Title | Size | Priority | Dependencies |
|-------|-------|------|----------|-------------|
| S1 | Schema Migration + Types | M | P0 | F4 |
| S2 | Importance Module (pure functions) | M | P0 | S1 |
| S3 | Tier-Aware Decay Lookup | S | P0 | S1 |
| S4 | Score Computation — Vote Integration | M | P1 | S1, S2 |
| S5 | Access Reinforcement in Recall | M | P1 | S1, S2 |
| S6 | Recall Integration — Multiplicative Model | L | P1 | S1, S2, S3 |
| S7 | Cleanup Strategy | M | P2 | S2, S3, S4 |
| S8 | CLI/MCP Output Updates | M | P2 | S4, S6 |
| S9 | PostgreSQL Migration + Server Scoring | L | P2 | S2, S3, S6 |
| S10 | Test Suite | L | P3 | S1-S8 |

**Total estimated effort:** 2S + 5M + 3L = ~34 story points (S=2, M=4, L=6)

### Recommended Sprint Allocation

- **Sprint 1:** S1, S2, S3 (foundation — can parallelize S2 and S3 after S1)
- **Sprint 2:** S4, S5, S6 (core logic — S4 and S5 can parallelize, S6 after S3)
- **Sprint 3:** S7, S8, S9 (integration — all can parallelize)
- **Sprint 4:** S10 (tests — after all implementation is stable)
