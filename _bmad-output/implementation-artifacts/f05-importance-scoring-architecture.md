# Architecture: F5 — Importance Scoring + Adaptive Decay

**Version:** 1.0
**Author:** Architect Agent
**Date:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f05-importance-scoring-prd.md`
**Depends on:** F4 (Multi-Level Memory Tiers — `memory.tier` field)

---

## 1. Overview

This document specifies how to implement a unified importance scoring system that replaces the current additive decay model (`0.7 * similarity + 0.3 * freshness`) with a multiplicative model (`cosine_similarity * time_adjusted_importance`). The system introduces per-memory importance scores derived from votes, access frequency, and confidence, combined with tier-aware exponential decay.

### Architecture Principles

1. **Compute at query time** — Decay is applied during recall, not stored. `importance_score` is the base (undecayed) value; `time_adjusted_importance` is computed on the fly.
2. **Update on event** — `importance_score` is recomputed only when a signal changes (vote, access), not on a schedule.
3. **Backward compatibility** — Existing `DECAY_HALF_LIVES` remains importable. Deprecated constructor params are accepted with warnings.
4. **Performance budget** — Recall latency increase < 20% for 1000-memory corpus.

---

## 2. Schema Changes

### 2.1 Memory Dataclass (`src/lore/types.py`)

Add three fields to the `Memory` dataclass:

```python
@dataclass
class Memory:
    # ... existing fields ...
    importance_score: float = 1.0       # base importance (undecayed)
    access_count: int = 0               # total recall hits
    last_accessed_at: Optional[str] = None  # ISO 8601 timestamp
```

**Invariants:**
- `importance_score >= 0.0` (enforced in `compute_importance()`)
- `access_count >= 0` (monotonically increasing)
- `last_accessed_at` is `None` until first recall hit, then always set

### 2.2 SQLite Migration (`src/lore/store/sqlite.py`)

Add columns to the `_maybe_migrate()` method, following the existing pattern for schema evolution:

```sql
ALTER TABLE memories ADD COLUMN importance_score REAL DEFAULT 1.0;
ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN last_accessed_at TEXT;
```

Each `ALTER TABLE` wrapped in a try/except to handle idempotent re-runs (column already exists). This matches the existing migration pattern in `_maybe_migrate()`.

**Indexes** (added after column creation):

```sql
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance_score);
CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories(last_accessed_at);
```

The `importance_score` index supports cleanup queries (find memories below threshold). The `last_accessed_at` index supports recency-based queries. An index on `access_count` is **not** needed — it's never queried directly in WHERE/ORDER BY clauses.

### 2.3 PostgreSQL Migration (`migrations/006_importance_scoring.sql`)

```sql
ALTER TABLE lessons ADD COLUMN importance_score REAL DEFAULT 1.0;
ALTER TABLE lessons ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE lessons ADD COLUMN last_accessed_at TIMESTAMPTZ;

CREATE INDEX idx_lessons_importance ON lessons(importance_score);
CREATE INDEX idx_lessons_last_accessed ON lessons(last_accessed_at);
```

### 2.4 In-Memory Store (`src/lore/store/memory.py`)

No schema changes needed — the `MemoryStore` stores `Memory` objects directly. The new fields are carried automatically by the dataclass defaults.

### 2.5 HTTP Store (`src/lore/store/http.py`)

Map the three new fields in `_to_memory()` and `_from_memory()` serialization methods. Fields should be optional in deserialization (server may not yet have them) with the same defaults as the dataclass.

### 2.6 MemoryStats Update (`src/lore/types.py`)

```python
@dataclass
class MemoryStats:
    # ... existing fields ...
    avg_importance: Optional[float] = None
    below_threshold_count: int = 0
```

---

## 3. Importance Score Computation

### 3.1 Location

New module: `src/lore/importance.py`

This module owns all importance-related computation. Keeping it separate from `lore.py` avoids bloating the main class and makes the scoring logic independently testable.

### 3.2 `compute_importance()`

```python
import math
from lore.types import Memory

def compute_importance(memory: Memory) -> float:
    """Compute base importance score from local signals.

    Pure function — no side effects, no I/O.
    """
    vote_factor = max(0.1, 1.0 + (memory.upvotes - memory.downvotes) * 0.1)
    access_factor = 1.0 + math.log2(1 + memory.access_count) * 0.1
    return memory.confidence * vote_factor * access_factor
```

**Design decisions:**

| Component | Formula | Rationale |
|-----------|---------|-----------|
| `vote_factor` | `max(0.1, 1.0 + net_votes * 0.1)` | Each net vote shifts importance by 10%. Floor at 0.1 prevents zero/negative. Matches existing vote_factor in `lore.py:372`. |
| `access_factor` | `1.0 + log2(1 + access_count) * 0.1` | Logarithmic growth prevents runaway boosting. 10 accesses → 1.35x; 100 accesses → 1.67x; 1000 accesses → 2.0x. The `1+` inside log avoids log(0). |
| `confidence` | Multiplicative base | Source confidence (default 1.0) scales the entire score. Low-confidence memories start with lower importance. |

**Score ranges** (with confidence=1.0):

| Scenario | vote_factor | access_factor | importance |
|----------|------------|---------------|------------|
| New memory, no activity | 1.0 | 1.0 | 1.0 |
| 5 upvotes, 10 accesses | 1.5 | 1.35 | 2.02 |
| 3 downvotes, 0 accesses | 0.7 | 1.0 | 0.7 |
| 10 downvotes, 0 accesses | 0.1 (floor) | 1.0 | 0.1 |

### 3.3 Update Triggers

`importance_score` is recomputed and persisted when:

1. **`upvote_memory()`** — After incrementing `upvotes`
2. **`downvote_memory()`** — After incrementing `downvotes`
3. **`recall()` returns a memory** — After incrementing `access_count` and setting `last_accessed_at`

The recomputation is: `memory.importance_score = compute_importance(memory)` followed by `store.update(memory)`.

**No other triggers.** Importance is not recalculated on read-only operations, metadata changes, or tag updates.

---

## 4. Decay Function

### 4.1 Exponential Half-Life Decay

```python
from datetime import datetime

def time_adjusted_importance(
    memory: Memory,
    half_life_days: float,
    now: datetime | None = None,
) -> float:
    """Apply exponential decay to base importance score.

    Returns the effective importance at the given time.
    """
    now = now or datetime.utcnow()
    created = datetime.fromisoformat(memory.created_at)

    # R14: Use last access time if available (recency boost)
    if memory.last_accessed_at:
        last_access = datetime.fromisoformat(memory.last_accessed_at)
        age_days = min(
            (now - created).total_seconds() / 86400,
            (now - last_access).total_seconds() / 86400,
        )
    else:
        age_days = (now - created).total_seconds() / 86400

    decay_factor = 0.5 ** (age_days / max(half_life_days, 0.001))
    return memory.importance_score * decay_factor
```

**Key design:** The `now` parameter is injectable for testing. In production, it defaults to `datetime.utcnow()`.

**Recency boost (R14):** Using `min(age_since_created, age_since_last_accessed)` means a frequently-accessed memory effectively "resets" its decay clock on each access. This is the primary mechanism by which access reinforces memory persistence.

### 4.2 Decay Factor Only (for reuse)

```python
def decay_factor(age_days: float, half_life_days: float) -> float:
    """Pure decay multiplier. Returns value in (0, 1]."""
    return 0.5 ** (age_days / max(half_life_days, 0.001))
```

This is exposed for use in cleanup and stats without requiring a full Memory object.

---

## 5. Unified Decay System — Tier-Aware Half-Lives

### 5.1 `TIER_DECAY_HALF_LIVES` (`src/lore/types.py`)

```python
TIER_DECAY_HALF_LIVES: Dict[str, Dict[str, float]] = {
    "working": {
        "default": 1,
        "code": 0.5,
        "note": 1,
        "lesson": 3,
        "convention": 3,
        "fact": 2,
        "preference": 2,
    },
    "short": {
        "default": 7,
        "code": 5,
        "note": 7,
        "lesson": 14,
        "convention": 14,
        "fact": 10,
        "preference": 10,
    },
    "long": {
        "default": 30,
        "code": 14,
        "note": 21,
        "lesson": 30,
        "convention": 60,
        "fact": 90,
        "preference": 90,
    },
}
```

### 5.2 Backward-Compatible Alias

```python
# Existing DECAY_HALF_LIVES becomes an alias for the long tier
DECAY_HALF_LIVES: Dict[str, float] = TIER_DECAY_HALF_LIVES["long"]
```

This preserves the existing import `from lore.types import DECAY_HALF_LIVES` and ensures all existing tests that reference `DECAY_HALF_LIVES` continue to work unmodified. The alias is a direct reference (not a copy), so mutations to `TIER_DECAY_HALF_LIVES["long"]` are reflected.

### 5.3 `resolve_half_life()` (`src/lore/importance.py`)

```python
def resolve_half_life(
    tier: str | None,
    memory_type: str,
    overrides: Dict[tuple[str, str], float] | None = None,
) -> float:
    """Resolve half-life with fallback chain.

    Resolution order:
    1. overrides[(tier, type)] — per-project config
    2. TIER_DECAY_HALF_LIVES[tier][type] — tier+type specific
    3. TIER_DECAY_HALF_LIVES[tier]["default"] — tier default
    4. DECAY_HALF_LIVES[type] — legacy flat lookup (= long tier)
    5. 30.0 — global default
    """
    # Normalize: absent tier treated as "long" (backward compat with pre-F4 memories)
    effective_tier = tier or "long"

    # 1. Project-level overrides
    if overrides and (effective_tier, memory_type) in overrides:
        return overrides[(effective_tier, memory_type)]

    # 2-3. Tier-specific lookup
    tier_config = TIER_DECAY_HALF_LIVES.get(effective_tier, {})
    if memory_type in tier_config:
        return tier_config[memory_type]
    if "default" in tier_config:
        return tier_config["default"]

    # 4-5. Legacy flat lookup / global default
    return DECAY_HALF_LIVES.get(memory_type, 30.0)
```

**Tier fallback:** Memories created before F4 (no `tier` field, or `tier=None`) default to `"long"` tier, matching the current behavior where all memories use the flat `DECAY_HALF_LIVES` dict.

---

## 6. Recall Integration

### 6.1 Current Scoring (to be replaced)

Location: `src/lore/lore.py` lines 367-379

```python
# CURRENT — additive model
similarity = cosine_score * memory.confidence * vote_factor
final_score = self._similarity_weight * similarity + self._freshness_weight * freshness
```

### 6.2 New Scoring — Multiplicative Model

```python
# NEW — multiplicative model
half_life = resolve_half_life(
    getattr(memory, 'tier', None),
    memory.type,
    overrides=self._decay_config,
)
tai = time_adjusted_importance(memory, half_life, now=now)
final_score = cosine_score * tai
```

**Why multiplicative?**
- A semantically irrelevant memory (low cosine) scores low regardless of importance.
- An unimportant memory scores low regardless of semantic match.
- No tuning weights (`similarity_weight`, `freshness_weight`) — one less knob.
- More intuitive: importance acts as a scaling factor on relevance.

### 6.3 Recall Flow (Updated)

```
recall(query, ...)
  ├─ embed(query) → query_vector
  ├─ store.list(filters) → candidates[]
  ├─ for each candidate:
  │     cosine = dot(query_vector, candidate.embedding)
  │     half_life = resolve_half_life(candidate.tier, candidate.type)
  │     tai = time_adjusted_importance(candidate, half_life)
  │     score = cosine * tai
  ├─ sort by score desc → top_k
  ├─ ACCESS TRACKING (new):
  │     for each result in top_k:
  │       result.memory.access_count += 1
  │       result.memory.last_accessed_at = now().isoformat()
  │       result.memory.importance_score = compute_importance(result.memory)
  │       store.update(result.memory)
  └─ return top_k as RecallResult[]
```

### 6.4 Performance Considerations

**Scoring loop:** The new scoring replaces the old with the same computational complexity — one pass over candidates with per-memory arithmetic. `time_adjusted_importance()` involves one `datetime.fromisoformat()` call and one `**` operation per memory. This is negligible compared to the vectorized cosine computation.

**Access tracking writes:** This adds N `store.update()` calls (N = result count, typically 5-10). For SQLite:

```python
# Wrap access tracking updates in a single transaction
with self._store._get_connection() as conn:
    for result in results[:limit]:
        memory = result.memory
        memory.access_count += 1
        memory.last_accessed_at = datetime.utcnow().isoformat()
        memory.importance_score = compute_importance(memory)
        conn.execute(
            "UPDATE memories SET access_count=?, last_accessed_at=?, importance_score=? WHERE id=?",
            (memory.access_count, memory.last_accessed_at, memory.importance_score, memory.id),
        )
```

This avoids N separate transactions. For HttpStore, the updates should be batched into a single request if the server supports it; otherwise fall back to sequential PATCHes (acceptable for 5-10 memories).

**Vectorized scoring:** The existing numpy vectorized cosine similarity computation (`_recall_local` lines 340-360) remains unchanged. The per-memory scoring loop (which was already O(N)) simply swaps the formula.

### 6.5 `now` Caching

Compute `now = datetime.utcnow()` once at the start of each recall call and pass it through. This ensures consistent decay calculations across all candidates in a single recall and avoids repeated syscalls.

---

## 7. Server-Side Changes

### 7.1 PostgreSQL Search Query Update

The server-side search in `src/lore/server/routes/lessons.py` must be updated to use the multiplicative model. Replace the current SQL scoring:

```sql
-- CURRENT
0.7 * (cosine_sim * confidence * vote_factor) + 0.3 * freshness

-- NEW
cosine_sim * importance_score * power(0.5,
    EXTRACT(EPOCH FROM (
        now() - LEAST(created_at, COALESCE(last_accessed_at, created_at))
    )) / 86400.0
    / resolve_half_life_sql(tier, type)
)
```

The `resolve_half_life_sql` logic can be implemented as a SQL `CASE` expression on `(tier, type)` pairs, matching the `TIER_DECAY_HALF_LIVES` dict. The `importance_score` column is pre-computed and stored, so the SQL only needs to apply the decay factor.

**Note:** `min(age_since_created, age_since_last_accessed)` in SQL is:
```sql
LEAST(
    EXTRACT(EPOCH FROM (now() - created_at)),
    EXTRACT(EPOCH FROM (now() - COALESCE(last_accessed_at, created_at)))
) / 86400.0
```

### 7.2 Server Access Tracking

Add a POST endpoint or extend the search response to batch-update `access_count` and `last_accessed_at` for returned results. Two options:

**Option A (Preferred): Fire-and-forget batch update**
After search returns results, the SDK sends a single POST `/v1/lessons/access` with the list of memory IDs. The server increments counters in one SQL statement:

```sql
UPDATE lessons
SET access_count = access_count + 1,
    last_accessed_at = now(),
    importance_score = confidence
        * GREATEST(0.1, 1.0 + (upvotes - downvotes) * 0.1)
        * (1.0 + log(2, 1 + access_count + 1) * 0.1)
WHERE id = ANY($1);
```

**Option B: Server-side tracking in search**
The search endpoint itself increments access counts for returned results. This couples read and write in a single operation but is simpler.

Recommend **Option A** for separation of concerns and to avoid slowing down search responses.

---

## 8. Deprecated Parameters

### 8.1 Constructor Changes (`src/lore/lore.py`)

```python
class Lore:
    def __init__(
        self,
        # ... existing params ...

        # DEPRECATED (accept, warn, ignore)
        decay_similarity_weight: float = 0.7,   # ignored
        decay_freshness_weight: float = 0.3,     # ignored

        # Existing (still functional, maps to long tier)
        decay_half_life_days: float = 30,
        decay_half_lives: Optional[Dict[str, float]] = None,

        # NEW
        importance_threshold: float = 0.05,
        decay_config: Optional[Dict[tuple[str, str], float]] = None,
    ):
```

**Deprecation warnings:**
```python
if decay_similarity_weight != 0.7 or decay_freshness_weight != 0.3:
    import warnings
    warnings.warn(
        "decay_similarity_weight and decay_freshness_weight are deprecated "
        "and ignored. Scoring now uses multiplicative model: "
        "score = cosine_similarity * time_adjusted_importance. "
        "Remove these parameters. They will be deleted in v0.7.0.",
        DeprecationWarning,
        stacklevel=2,
    )
```

### 8.2 Internal State

- `self._similarity_weight` and `self._freshness_weight` — Remove from instance variables.
- `self._importance_threshold` — New, stored from constructor.
- `self._decay_config` — New, stored from constructor. Passed to `resolve_half_life()`.
- `self._half_lives` — Keep for backward compat. If `decay_half_lives` is provided, merge into `TIER_DECAY_HALF_LIVES["long"]` (or warn and suggest `decay_config`).

---

## 9. Cleanup Strategy

### 9.1 Enhanced `cleanup_expired()` (`src/lore/lore.py`)

```python
def cleanup_expired(self, importance_threshold: float | None = None) -> int:
    """Remove expired memories AND memories below importance threshold."""
    threshold = importance_threshold or self._importance_threshold
    now = datetime.utcnow()
    count = 0

    # Phase 1: Existing TTL/expiry cleanup (delegated to store)
    count += self._store.cleanup_expired()

    # Phase 2: Importance-based cleanup (new)
    all_memories = self._store.list(limit=10000)
    to_delete = []
    for memory in all_memories:
        half_life = resolve_half_life(
            getattr(memory, 'tier', None),
            memory.type,
            overrides=self._decay_config,
        )
        tai = time_adjusted_importance(memory, half_life, now=now)
        if tai < threshold:
            to_delete.append(memory.id)

    for memory_id in to_delete:
        self._store.delete(memory_id)
        count += 1

    return count
```

**Design decisions:**

1. **Threshold default: 0.05** — Very conservative. A memory with importance=1.0 must age ~4.3 half-lives before hitting this threshold (at 30-day half-life, that's ~130 days). Downvoted memories (importance=0.1) cross the threshold after ~1 half-life.

2. **Cleanup lives in `Lore`, not `Store`** — Because it requires decay computation (half-life resolution, tier awareness). The Store ABC stays simple.

3. **Batch deletion** — Collect all IDs first, then delete. This avoids modifying the list while iterating and allows potential future optimization (batch DELETE).

4. **Performance:** Iterating 10,000 memories with per-memory decay computation takes ~10ms (datetime parsing + exponentiation). Acceptable for a cleanup operation that runs at most once per recall session.

### 9.2 Lazy Cleanup Integration

The existing `recall()` method triggers `cleanup_expired()` lazily (first call, then every N minutes). The same trigger point handles importance-based cleanup — no additional integration needed.

---

## 10. Batch Importance Recalculation (P1 — R15)

```python
def recalculate_importance(self, project: str | None = None) -> int:
    """Recompute importance_score for all memories.

    Useful after changing decay config or backfilling access data.
    Returns count of memories updated.
    """
    memories = self._store.list(project=project, limit=100000)
    count = 0
    for memory in memories:
        new_score = compute_importance(memory)
        if memory.importance_score != new_score:
            memory.importance_score = new_score
            self._store.update(memory)
            count += 1
    return count
```

---

## 11. MCP & CLI Integration

### 11.1 MCP Recall Output (`src/lore/mcp/server.py`)

Update the `recall` tool's result formatting to include importance:

```
Memory [abc123] (importance: 0.87, score: 0.74)
Type: lesson | Tier: long | Tags: python, testing
Content: Always mock external services in unit tests...
```

### 11.2 MCP `list_memories` Output

Include `importance_score` in the formatted list output.

### 11.3 CLI `lore memories` (`src/lore/cli.py`)

Add `--sort importance` flag:

```python
@click.option("--sort", type=click.Choice(["created", "importance"]), default="created")
```

Display importance column in table output. When `--sort importance`, sort by `importance_score` descending (base importance, not time-adjusted — time-adjusted would require computing decay for display, which is a different concern).

---

## 12. Module Dependency Graph

```
types.py
  ├── Memory (dataclass with new fields)
  ├── TIER_DECAY_HALF_LIVES (config dict)
  ├── DECAY_HALF_LIVES (backward-compat alias → TIER_DECAY_HALF_LIVES["long"])
  └── MemoryStats (updated)

importance.py (NEW)
  ├── compute_importance(memory) → float
  ├── time_adjusted_importance(memory, half_life, now?) → float
  ├── decay_factor(age_days, half_life) → float
  └── resolve_half_life(tier, type, overrides?) → float
      └── imports: TIER_DECAY_HALF_LIVES, DECAY_HALF_LIVES from types.py

lore.py
  ├── _recall_local() — uses importance.time_adjusted_importance, importance.resolve_half_life
  ├── upvote_memory() — calls importance.compute_importance
  ├── downvote_memory() — calls importance.compute_importance
  ├── cleanup_expired() — uses importance.time_adjusted_importance
  └── recalculate_importance() — calls importance.compute_importance

store/sqlite.py — schema migration, CRUD for new fields
store/memory.py — no changes (dataclass carries fields)
store/http.py — serialization for new fields
store/base.py — no changes to ABC

freshness/detector.py — UNCHANGED (git-based staleness is orthogonal)
freshness/git_ops.py — UNCHANGED
freshness/types.py — UNCHANGED

mcp/server.py — display formatting
cli.py — display formatting, --sort flag

server/routes/lessons.py — SQL scoring update, access tracking endpoint
```

---

## 13. File Change Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `src/lore/types.py` | Modify | Add fields to `Memory`, add `TIER_DECAY_HALF_LIVES`, update `DECAY_HALF_LIVES` to alias, update `MemoryStats` |
| `src/lore/importance.py` | **New** | `compute_importance()`, `time_adjusted_importance()`, `decay_factor()`, `resolve_half_life()` |
| `src/lore/lore.py` | Modify | Rewrite `_recall_local()` scoring (lines 367-379), add access tracking after results, update `upvote_memory()`/`downvote_memory()` to recompute importance, update constructor (new params, deprecation warnings), update `cleanup_expired()`, add `recalculate_importance()` |
| `src/lore/store/sqlite.py` | Modify | Schema migration (3 columns + 2 indexes), handle new fields in `_to_row()`/`_from_row()` |
| `src/lore/store/memory.py` | Modify | Handle new fields in filtering/sorting if applicable |
| `src/lore/store/http.py` | Modify | Map new fields in serialization/deserialization, add batch access tracking call |
| `src/lore/store/base.py` | No change | ABC unchanged |
| `src/lore/mcp/server.py` | Modify | Update recall + list_memories output formatting |
| `src/lore/cli.py` | Modify | Add `--sort importance` flag, display importance column |
| `src/lore/server/routes/lessons.py` | Modify | Update search SQL to multiplicative model, add access tracking endpoint |
| `migrations/006_importance_scoring.sql` | **New** | PostgreSQL schema migration |
| `tests/test_importance_scoring.py` | **New** | Unit tests for importance module |
| `tests/test_semantic_decay.py` | Modify | Update for new scoring model |
| `tests/test_decay_voting.py` | Modify | Update for unified importance (votes now feed importance_score) |

---

## 14. Testing Strategy

### 14.1 Unit Tests (`tests/test_importance_scoring.py`)

| Test | Validates |
|------|-----------|
| `test_compute_importance_default` | New memory → importance = 1.0 |
| `test_compute_importance_upvotes` | 5 upvotes → importance > 1.0 |
| `test_compute_importance_downvotes` | 10 downvotes → importance = 0.1 (floor) |
| `test_compute_importance_access_log` | 10 accesses → logarithmic boost (1.35x) |
| `test_compute_importance_combined` | Votes + accesses combine multiplicatively |
| `test_time_adjusted_importance_fresh` | Age=0 → TAI = importance_score |
| `test_time_adjusted_importance_one_halflife` | Age=half_life → TAI ≈ importance/2 |
| `test_time_adjusted_importance_last_accessed` | Last accessed recently → decays from last access, not creation |
| `test_resolve_half_life_tier_type` | Known tier+type → correct value |
| `test_resolve_half_life_tier_default` | Unknown type in known tier → tier default |
| `test_resolve_half_life_no_tier` | tier=None → falls back to "long" |
| `test_resolve_half_life_overrides` | Override dict takes precedence |
| `test_decay_factor_boundary` | age=0 → 1.0, very large age → ~0.0 |

### 14.2 Integration Tests

| Test | Validates |
|------|-----------|
| `test_recall_updates_access_count` | recall() increments access_count on returned memories |
| `test_recall_sets_last_accessed` | recall() sets last_accessed_at |
| `test_recall_recomputes_importance` | recall() updates importance_score |
| `test_recall_multiplicative_scoring` | Higher importance → higher final score (same cosine) |
| `test_upvote_updates_importance` | upvote_memory() recomputes importance_score |
| `test_cleanup_removes_low_importance` | cleanup_expired() deletes below threshold |
| `test_cleanup_preserves_important` | cleanup_expired() keeps above threshold |
| `test_backward_compat_decay_half_lives` | `DECAY_HALF_LIVES` returns long-tier values |
| `test_deprecated_params_warn` | Old constructor params emit DeprecationWarning |

### 14.3 Existing Test Updates

- `tests/test_semantic_decay.py` — Update expected scores to match multiplicative model. The relative rankings should be preserved; absolute score values will change.
- `tests/test_decay_voting.py` — Update to verify that votes feed through `importance_score` rather than being applied inline in the scoring loop.

---

## 15. Migration & Rollout

### 15.1 Migration Steps

1. **Schema migration** — Run automatically on first access (`_maybe_migrate()`). Existing memories get `importance_score=1.0`, `access_count=0`, `last_accessed_at=NULL`.
2. **No data backfill needed** — Default values are correct. Existing memories start at full importance (1.0) with no access history, which matches their pre-feature behavior.
3. **Optional: `recalculate_importance()`** — If confidence values have been manually adjusted, run this to propagate changes to `importance_score`.

### 15.2 Backward Compatibility Checklist

- [x] `DECAY_HALF_LIVES` remains importable → alias to `TIER_DECAY_HALF_LIVES["long"]`
- [x] `decay_similarity_weight` / `decay_freshness_weight` accepted → warn + ignore
- [x] `decay_half_lives` constructor param works → maps to long tier overrides
- [x] Memories without `tier` field → default to "long" tier
- [x] Memories without `importance_score` → default 1.0
- [x] Existing tests pass after update → relative rankings preserved

---

## 16. Implementation Order

Recommended sequence for implementation stories:

1. **S1: Schema + types** — Add fields to `Memory`, `MemoryStats`, `TIER_DECAY_HALF_LIVES`, SQLite migration
2. **S2: Importance module** — Create `src/lore/importance.py` with all pure functions + unit tests
3. **S3: Recall scoring** — Rewrite `_recall_local()` to use multiplicative model + access tracking
4. **S4: Vote integration** — Update `upvote_memory()` / `downvote_memory()` to recompute importance
5. **S5: Cleanup** — Update `cleanup_expired()` with importance threshold
6. **S6: Constructor** — Deprecation warnings, new params, `decay_config` support
7. **S7: HTTP/Server** — Map new fields, update search SQL, access tracking endpoint
8. **S8: MCP + CLI** — Display formatting, `--sort importance`
9. **S9: Test updates** — Update existing decay/voting tests for new model

S1-S2 have no dependencies. S3-S6 depend on S1-S2. S7-S9 can proceed in parallel after S3.
