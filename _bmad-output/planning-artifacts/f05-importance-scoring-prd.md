# PRD: F5 — Importance Scoring + Adaptive Decay

**Version:** 1.0
**Author:** John (Product Manager)
**Date:** 2026-03-06
**Status:** Draft
**Depends on:** F4 (Multi-Level Memory Tiers)

---

## 1. Problem Statement

Lore's current recall scoring is simplistic: a weighted sum of cosine similarity and a freshness decay factor, with a flat vote adjustment (`1.0 + (upvotes - downvotes) * 0.1`). This has three problems:

1. **No usage signal.** A memory recalled 50 times is scored the same as one never accessed. Frequently useful memories decay just as fast as irrelevant ones.
2. **One-size-fits-all decay.** All memories of the same type decay at the same rate regardless of tier (F4). A working-tier scratch note and a long-term convention both use the same half-life.
3. **No importance threshold.** `cleanup_expired()` only prunes by TTL/expiry. Low-value memories with no expiry accumulate indefinitely, degrading recall quality over time.

This feature replaces the ad-hoc scoring with a unified importance system that accounts for explicit feedback, access patterns, and tier-aware decay — so high-value memories persist and low-value ones fade naturally.

## 2. Goals

1. **Unified importance score** — A single `importance_score` field that synthesizes votes, access frequency, recency, and source confidence into one number.
2. **Adaptive decay** — Exponential decay with half-lives configurable per memory type AND per tier (from F4), replacing the current flat `DECAY_HALF_LIVES` dict.
3. **Access-based reinforcement** — Memories that are frequently recalled get boosted; unused memories sink.
4. **Importance-weighted recall** — Final recall score factors in time-adjusted importance, so stale unused memories rank below fresh or frequently-accessed ones.
5. **Garbage collection** — `cleanup_expired()` gains an importance threshold to prune memories that have decayed below usefulness.

## 3. Non-Goals

- **LLM-based importance estimation** — No LLM calls. Importance is computed from signals already available (votes, access, age, confidence).
- **Real-time importance recomputation** — Importance is updated on access and on vote, not continuously recalculated in the background.
- **Per-user importance** — Importance is global per memory, not personalized per agent/user.
- **Graph-based importance (PageRank)** — That's an F1 concern. This feature uses local signals only.

## 4. Requirements

### 4.1 Must-Have (P0)

| ID | Requirement | Details |
|----|-------------|---------|
| R1 | **New fields on Memory dataclass** | `importance_score: float = 1.0`, `access_count: int = 0`, `last_accessed_at: Optional[str] = None` |
| R2 | **Schema migration (SQLite)** | `ALTER TABLE memories ADD COLUMN importance_score FLOAT DEFAULT 1.0; ALTER TABLE memories ADD COLUMN access_count INT DEFAULT 0; ALTER TABLE memories ADD COLUMN last_accessed_at TIMESTAMP;` |
| R3 | **Schema migration (Postgres)** | Same columns on the server-side `memories`/`lessons` table. HttpStore maps these fields. |
| R4 | **Importance score computation** | `importance_score = base_confidence * vote_factor * access_factor` where: `vote_factor = max(0.1, 1.0 + (upvotes - downvotes) * 0.1)`, `access_factor = 1.0 + log2(1 + access_count) * 0.1`. Initial value = `confidence` (default 1.0). |
| R5 | **Access tracking on recall** | Every `recall()` that returns a memory: increments `access_count`, sets `last_accessed_at = now()`, recomputes `importance_score`, persists via `store.update()`. |
| R6 | **Tier-aware decay half-lives** | Extend `DECAY_HALF_LIVES` from `Dict[str, float]` to a two-level lookup: `DECAY_HALF_LIVES[tier][type]`. Fallback chain: tier+type -> tier default -> type default -> global default (30 days). |
| R7 | **Adaptive decay function** | `time_adjusted_importance = importance_score * 0.5^(age_days / half_life)` where `half_life` is resolved per tier+type. Replaces the current separate `freshness` calculation. |
| R8 | **Unified recall scoring** | Replace current `final_score = sim_weight * similarity + fresh_weight * freshness` with: `final_score = semantic_similarity * time_adjusted_importance`. This is a multiplicative model — irrelevant memories score low regardless of importance, and unimportant memories score low regardless of similarity. |
| R9 | **Upvote/downvote updates importance** | `upvote_memory()` and `downvote_memory()` recompute `importance_score` after modifying vote counts. |
| R10 | **Cleanup by importance threshold** | `cleanup_expired()` also deletes memories where `time_adjusted_importance < threshold`. Threshold configurable via `Lore(importance_threshold=...)`, default `0.05`. |
| R11 | **MCP tool output includes importance** | `recall` results include `importance_score` in formatted output. `list_memories` includes `importance_score`. |
| R12 | **CLI importance display** | `lore memories` shows `importance_score` column. Add `--sort importance` flag to sort by importance (descending). |

### 4.2 Should-Have (P1)

| ID | Requirement | Details |
|----|-------------|---------|
| R13 | **Configurable decay parameters per project** | `Lore(decay_config={...})` accepts a dict with per-project overrides for half-lives and importance threshold. |
| R14 | **Recency boost from last_accessed_at** | When computing time-adjusted importance, use `min(age_since_created, age_since_last_accessed)` as the age input — so a recently-accessed old memory decays from its last access, not its creation date. |
| R15 | **Batch importance recalculation** | `Lore.recalculate_importance(project=None)` method to recompute all importance scores (useful after changing decay config or backfilling access data). |

### 4.3 Nice-to-Have (P2)

| ID | Requirement | Details |
|----|-------------|---------|
| R16 | **Importance history** | Store importance score snapshots in `metadata.importance_history` (last 5 values + timestamps) for debugging/visualization. |
| R17 | **CLI `lore decay-info`** | Show current decay configuration: half-lives per tier/type, importance threshold, decay curve preview. |

## 5. Detailed Design

### 5.1 Importance Score Formula

```python
def compute_importance(memory: Memory) -> float:
    """Compute importance from local signals. Pure function."""
    # Vote factor: net votes shift importance up/down
    vote_factor = max(0.1, 1.0 + (memory.upvotes - memory.downvotes) * 0.1)

    # Access factor: logarithmic boost from usage frequency
    access_factor = 1.0 + math.log2(1 + memory.access_count) * 0.1

    return memory.confidence * vote_factor * access_factor
```

### 5.2 Time-Adjusted Importance (Decay)

```python
def time_adjusted_importance(memory: Memory, half_life_days: float) -> float:
    """Apply exponential decay to importance score."""
    now = datetime.utcnow()
    created = datetime.fromisoformat(memory.created_at)

    # Use last access time if available (P1 — R14)
    if memory.last_accessed_at:
        last_access = datetime.fromisoformat(memory.last_accessed_at)
        age_days = min(
            (now - created).total_seconds() / 86400,
            (now - last_access).total_seconds() / 86400,
        )
    else:
        age_days = (now - created).total_seconds() / 86400

    decay = 0.5 ** (age_days / max(half_life_days, 0.001))
    return memory.importance_score * decay
```

### 5.3 Tier-Aware Half-Life Resolution

```python
# New structure replacing flat DECAY_HALF_LIVES dict
TIER_DECAY_HALF_LIVES: Dict[str, Dict[str, float]] = {
    "working": {
        "default": 1,      # working-tier memories decay fast
        "code": 0.5,
        "note": 1,
    },
    "short": {
        "default": 7,
        "code": 5,
        "note": 7,
        "lesson": 14,
    },
    "long": {
        "default": 30,      # matches current global default
        "code": 14,
        "note": 21,
        "lesson": 30,
        "convention": 60,
        "fact": 90,
        "preference": 90,
    },
}

def resolve_half_life(tier: str, memory_type: str, overrides: dict = None) -> float:
    """Resolve half-life: overrides > tier+type > tier default > global default."""
    if overrides and (tier, memory_type) in overrides:
        return overrides[(tier, memory_type)]
    tier_config = TIER_DECAY_HALF_LIVES.get(tier, {})
    return tier_config.get(memory_type, tier_config.get("default", 30.0))
```

### 5.4 Unified Recall Scoring

Current (to be replaced):
```python
# lore.py:367-380 — current implementation
similarity = cosine_score * memory.confidence * vote_factor
final_score = sim_weight * similarity + fresh_weight * freshness
```

New:
```python
# Multiplicative model — importance modulates similarity
half_life = resolve_half_life(memory.tier, memory.type)
tai = time_adjusted_importance(memory, half_life)
final_score = cosine_score * tai
```

This eliminates the `decay_similarity_weight` and `decay_freshness_weight` parameters. The multiplicative model is simpler and more intuitive: importance acts as a scaling factor on semantic relevance.

**Migration note:** The old `decay_similarity_weight` / `decay_freshness_weight` constructor params should be deprecated (accepted but ignored with a warning) for one version cycle.

### 5.5 Access Tracking in recall()

After computing results but before returning:

```python
for result in results[:limit]:
    memory = result.memory
    memory.access_count += 1
    memory.last_accessed_at = datetime.utcnow().isoformat()
    memory.importance_score = compute_importance(memory)
    self._store.update(memory)
```

**Performance note:** This adds N update calls per recall (where N = result count, typically 5-10). For SQLite this is fine. For HttpStore, batch updates should be considered as a P1 optimization.

### 5.6 Cleanup Enhancement

```python
def cleanup_expired(self, importance_threshold: float = 0.05) -> int:
    """Remove expired memories AND memories below importance threshold."""
    count = 0
    # Existing: remove by TTL/expires_at
    count += self._store.cleanup_expired()
    # New: remove by importance
    all_memories = self._store.list(limit=10000)
    for memory in all_memories:
        half_life = resolve_half_life(memory.tier, memory.type)
        tai = time_adjusted_importance(memory, half_life)
        if tai < importance_threshold:
            self._store.delete(memory.id)
            count += 1
    return count
```

**Note:** The importance-based cleanup should be a separate method on the Store ABC or handled in `Lore` (not pushed into Store implementations), since it requires decay computation logic.

## 6. Data Model Changes

### 6.1 Memory Dataclass (types.py)

Add three fields:

```python
@dataclass
class Memory:
    # ... existing fields ...
    importance_score: float = 1.0
    access_count: int = 0
    last_accessed_at: Optional[str] = None
```

### 6.2 RecallResult (types.py)

No changes needed — `score` already carries the final score. Optionally add `importance_score` for transparency:

```python
@dataclass
class RecallResult:
    memory: Memory       # memory.importance_score is already available
    score: float
    staleness: Any = None
```

### 6.3 DECAY_HALF_LIVES Migration

- Keep `DECAY_HALF_LIVES` as a backward-compatible alias (maps to `TIER_DECAY_HALF_LIVES["long"]`).
- New code uses `TIER_DECAY_HALF_LIVES` and `resolve_half_life()`.

### 6.4 SQLite Schema

```sql
ALTER TABLE memories ADD COLUMN importance_score REAL DEFAULT 1.0;
ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN last_accessed_at TEXT;
```

### 6.5 MemoryStats

Add importance distribution to stats:

```python
@dataclass
class MemoryStats:
    # ... existing ...
    avg_importance: Optional[float] = None
    below_threshold_count: int = 0
```

## 7. API / Interface Changes

### 7.1 Lore Constructor

```python
Lore(
    # Existing (deprecated, still accepted)
    decay_half_life_days=30,       # DEPRECATED — use decay_config
    decay_half_lives={...},        # DEPRECATED — use decay_config
    decay_similarity_weight=0.7,   # DEPRECATED — ignored
    decay_freshness_weight=0.3,    # DEPRECATED — ignored

    # New
    importance_threshold=0.05,     # for cleanup
    decay_config={                 # optional overrides
        ("long", "code"): 14,
        ("short", "lesson"): 10,
    },
)
```

### 7.2 MCP Tool: recall

Output format adds importance:

```
Memory [abc123] (importance: 0.87, score: 0.74)
Type: lesson | Tags: python, testing
Content: Always mock external services in unit tests...
```

### 7.3 MCP Tool: list_memories

Output includes importance_score column.

### 7.4 CLI: `lore memories`

```
$ lore memories --sort importance
ID          Type        Importance  Age    Content
abc123      lesson      0.92        3d     Always mock external...
def456      convention  0.85        14d    Use snake_case for...
ghi789      code        0.23        45d    Quick fix for parse...
```

## 8. File Changes

| File | Change |
|------|--------|
| `src/lore/types.py` | Add fields to `Memory`, add `TIER_DECAY_HALF_LIVES`, add `resolve_half_life()`, update `MemoryStats` |
| `src/lore/lore.py` | Rewrite recall scoring (lines 355-383), add access tracking, add `compute_importance()`, update constructor, update `cleanup_expired` logic |
| `src/lore/store/sqlite.py` | Schema migration for new columns, handle new fields in CRUD |
| `src/lore/store/memory.py` | Handle new fields in in-memory store |
| `src/lore/store/http.py` | Map new fields to/from server API |
| `src/lore/store/base.py` | No changes to ABC (importance cleanup lives in `Lore`, not `Store`) |
| `src/lore/mcp/server.py` | Update recall + list_memories output formatting |
| `src/lore/cli.py` | Add `--sort importance` to `memories` command, display importance column |
| `tests/test_importance_scoring.py` | **NEW** — Unit tests for importance computation, decay, access tracking |
| `tests/test_semantic_decay.py` | Update existing decay tests for new scoring model |
| `tests/test_decay_voting.py` | Update for unified importance (votes now feed importance_score) |

## 9. Backward Compatibility

| Concern | Mitigation |
|---------|-----------|
| `decay_similarity_weight` / `decay_freshness_weight` removed | Accept params with deprecation warning, ignore values. Remove in v0.7.0. |
| `DECAY_HALF_LIVES` dict used externally | Keep as alias for `TIER_DECAY_HALF_LIVES["long"]`. Existing tests pass unchanged. |
| Existing memories have no `importance_score` | Default 1.0 — existing memories start at full importance. |
| Existing memories have no `access_count` | Default 0 — no penalty for pre-existing memories. |
| Recall scores will change | Expected. Document in migration guide. Multiplicative model may produce different absolute scores, but relative ranking should be similar for active memories. |

## 10. Acceptance Criteria

1. **AC1:** A memory with 5 upvotes and 10 accesses has a higher `importance_score` than a memory with 0 upvotes and 0 accesses (same confidence).
2. **AC2:** Calling `recall()` increments `access_count` and updates `last_accessed_at` for all returned memories.
3. **AC3:** A working-tier memory decays faster than a long-tier memory of the same type (verified by comparing `time_adjusted_importance` at the same age).
4. **AC4:** Two memories with identical cosine similarity but different importance scores are ranked by importance in recall results.
5. **AC5:** `cleanup_expired()` removes memories whose `time_adjusted_importance` falls below the configured threshold.
6. **AC6:** MCP `recall` tool output includes `importance` value for each result.
7. **AC7:** `lore memories --sort importance` displays memories sorted by importance descending.
8. **AC8:** Existing `DECAY_HALF_LIVES` dict remains importable and returns long-tier values (backward compat).
9. **AC9:** A memory recalled frequently (high `access_count`) decays more slowly than an identical unaccessed memory, due to `last_accessed_at` recency (R14).
10. **AC10:** All existing tests in `test_semantic_decay.py` and `test_decay_voting.py` pass (updated for new scoring model).

## 11. Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Recall relevance | Frequently-accessed memories rank higher than stale ones at 30+ days | Test: create 2 identical memories, access one 10x, verify ranking after simulated aging |
| Cleanup effectiveness | Memories below threshold are pruned | Test: create memories, simulate aging past threshold, verify deletion |
| Scoring unification | Single code path for decay + importance | No duplicate decay logic — `DECAY_HALF_LIVES` is an alias, not a parallel system |
| Performance | Recall latency increase < 20% from access tracking overhead | Benchmark: 1000 memories, measure recall p95 before/after |
| Test coverage | 95%+ on new code paths | pytest --cov on importance scoring module |

## 12. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Access tracking adds N writes per recall | Recall latency increase | Batch updates; for SQLite use single transaction. Monitor benchmark. |
| Multiplicative scoring changes rankings | Users see different results | This is pre-v1.0 with no locked-in users. Document the change. |
| Importance threshold too aggressive | Useful memories get pruned | Default threshold is very low (0.05). Require explicit opt-in for cleanup. |
| Circular dependency with F4 (tiers) | Can't resolve half-life without tier field | F4 must land first. If tier field is absent, fall back to "long" tier (backward compat). |

## 13. Out of Scope

- **LLM-based importance estimation** — Future enrichment pipeline concern (F6).
- **Collaborative importance** — No per-agent or per-user importance scores.
- **Importance visualization / dashboard** — Beyond CLI display.
- **Server-side importance computation** — Server stores the fields; SDK computes scores.
- **Background decay jobs** — Decay is computed at query time, not via scheduled tasks.
