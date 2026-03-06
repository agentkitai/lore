# PRD: F4 — Multi-Level Memory Tiers

**Feature:** Multi-Level Memory Tiers
**Version:** v0.6.0 ("Open Brain")
**Status:** Draft
**Author:** John (PM)
**Dependencies:** None (foundation feature)
**Dependents:** F5 (Importance Scoring + Adaptive Decay), F3 (Memory Consolidation)

---

## 1. Problem Statement

All memories in Lore are currently treated equally — a scratch note from a debugging session has the same persistence and recall weight as a hard-won architectural lesson. This forces users to manually manage memory lifecycle (set TTL, forget old memories) or accept noise in recall results.

Cognitive science distinguishes between working memory (seconds-minutes), short-term memory (hours-days), and long-term memory (persistent). Lore should mirror this model, enabling automatic lifecycle management and tier-aware retrieval.

## 2. Goals

1. **Automatic lifecycle management** — working-tier memories expire after 1 hour, short-tier after 7 days, long-tier persists indefinitely (unless explicit TTL overrides).
2. **Tier-aware recall** — long-term memories are weighted higher than short-term, which are weighted higher than working, reducing noise from ephemeral context.
3. **Backward compatibility** — existing memories seamlessly migrate to `long` tier. All APIs remain functional without the tier parameter.
4. **Foundation for F5** — tier-specific decay half-lives will be consumed by the Importance Scoring feature.

## 3. Non-Goals

- Automatic promotion/demotion between tiers (future consideration for F3/F5).
- UI/dashboard for tier management.
- Tier-based storage separation (all tiers use the same store backend).

## 4. Design

### 4.1 Tier Definition

| Tier | Key | Default TTL | Decay Half-Life | Recall Weight Boost | Use Case |
|------|-----|-------------|-----------------|---------------------|----------|
| Working | `working` | 1 hour (3600s) | 1 day | 1.0x (baseline) | Scratch context, current-task state, ephemeral notes |
| Short-term | `short` | 7 days (604800s) | 7 days | 1.1x | Session learnings, recent discoveries, WIP patterns |
| Long-term | `long` | None (no expiry) | 30 days (existing default) | 1.2x | Proven lessons, stable conventions, user preferences |

### 4.2 Tier-TTL Interaction Rules

Tiers ADD to the existing TTL mechanism — they do not replace it.

1. **No explicit TTL + tier specified:** Memory gets the tier's default TTL.
   - `remember("x", tier="working")` → TTL = 3600s, `expires_at` computed automatically.
   - `remember("x", tier="long")` → TTL = None, no expiry.
2. **Explicit TTL + tier specified:** Explicit TTL wins. The tier is still recorded for recall weighting, but the user's TTL overrides the default.
   - `remember("x", tier="working", ttl=7200)` → TTL = 7200s (2h, not the default 1h).
3. **No explicit TTL + no tier specified:** Tier defaults to `long`, TTL = None. This preserves exact backward compatibility.
4. **Explicit TTL + no tier specified:** Tier defaults to `long`, explicit TTL is used. Backward compatible.

### 4.3 Data Model Changes

```python
# In types.py

VALID_TIERS = ("working", "short", "long")

TIER_DEFAULT_TTL: Dict[str, Optional[int]] = {
    "working": 3600,      # 1 hour
    "short": 604800,      # 7 days
    "long": None,         # no expiry
}

TIER_RECALL_WEIGHT: Dict[str, float] = {
    "working": 1.0,
    "short": 1.1,
    "long": 1.2,
}

@dataclass
class Memory:
    id: str
    content: str
    type: str = "general"
    tier: str = "long"            # NEW FIELD
    context: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    project: Optional[str] = None
    embedding: Optional[bytes] = None
    created_at: str = ""
    updated_at: str = ""
    ttl: Optional[int] = None
    expires_at: Optional[str] = None
    confidence: float = 1.0
    upvotes: int = 0
    downvotes: int = 0
```

### 4.4 Schema Migration

**SQLite:**
```sql
ALTER TABLE memories ADD COLUMN tier TEXT DEFAULT 'long';
CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
```

**Postgres (server):**
```sql
ALTER TABLE memories ADD COLUMN tier VARCHAR(10) DEFAULT 'long';
CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
```

All existing memories receive `tier = 'long'` via the DEFAULT clause. No data backfill needed.

### 4.5 Store ABC Changes

```python
class Store(ABC):
    # Existing methods unchanged.

    @abstractmethod
    def list(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,       # NEW PARAMETER
        limit: Optional[int] = None,
    ) -> List[Memory]: ...

    @abstractmethod
    def count(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,       # NEW PARAMETER
    ) -> int: ...
```

All store implementations (SqliteStore, MemoryStore, HttpStore) must be updated:
- `save()` / `update()` — persist the `tier` field.
- `list()` / `count()` — support optional `tier` filter.
- `_row_to_memory()` (SQLite) — read `tier` column.

### 4.6 Lore Facade Changes

**`remember()` method:**
- Add `tier: str = "long"` parameter.
- Validate tier against `VALID_TIERS`.
- If no explicit `ttl` provided, apply `TIER_DEFAULT_TTL[tier]`.
- Compute `expires_at` from effective TTL.

```python
def remember(
    self,
    content: str,
    *,
    type: str = "general",
    tier: str = "long",               # NEW
    # ... existing params ...
    ttl: Optional[int] = None,
) -> str:
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid tier {tier!r}, must be one of: {VALID_TIERS}")

    # Apply tier default TTL if no explicit TTL
    effective_ttl = ttl if ttl is not None else TIER_DEFAULT_TTL[tier]
    # ... rest of method uses effective_ttl instead of ttl ...
```

**`recall()` method:**
- Add `tier: Optional[str] = None` filter parameter.
- Apply tier-based recall weight boost in scoring.

**`_recall_local()` scoring adjustment:**
```python
tier_weight = TIER_RECALL_WEIGHT.get(memory.tier, 1.0)
similarity = cosine_score * memory.confidence * vote_factor * tier_weight
```

**`list_memories()` method:**
- Add `tier: Optional[str] = None` parameter, pass through to store.

**`stats()` method:**
- Add `by_tier: Dict[str, int]` to `MemoryStats` dataclass.
- Populate tier counts alongside type counts.

### 4.7 MCP Tool Updates

**`remember` tool:**
```python
def remember(
    content: str,
    type: str = "general",
    tier: str = "long",               # NEW
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    project: Optional[str] = None,
    ttl: Optional[int] = None,
) -> str:
```

Update tool description to explain tiers:
> "Optionally set tier: 'working' (auto-expires in 1h, for scratch context), 'short' (auto-expires in 7d, for session learnings), or 'long' (default, no expiry, for lasting knowledge)."

**`list_memories` tool:**
```python
def list_memories(
    type: Optional[str] = None,
    tier: Optional[str] = None,       # NEW
    project: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
```

**`recall` tool:**
```python
def recall(
    query: str,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,       # NEW
    limit: int = 5,
    repo_path: Optional[str] = None,
) -> str:
```

**`stats` tool:** Include tier breakdown in output.

**Recall output:** Add tier to memory display:
```
Memory 1  (score: 0.85, id: abc123, type: lesson, tier: long)
```

### 4.8 CLI Updates

**`remember` subcommand:**
```
lore remember "Always use exponential backoff" --tier long
lore remember "Current PR is #42" --tier working
```
Add `--tier` argument (choices: working, short, long; default: long).

**`memories` (list) subcommand:**
```
lore memories --tier working
```
Add `--tier` filter argument.

**`memories` output:** Add Tier column to table display.

**`recall` subcommand:**
```
lore recall "rate limiting" --tier long
```
Add `--tier` filter argument.

### 4.9 Auto-Tier Heuristics (Optional, Configurable)

This is a stretch goal. If implemented, it provides automatic tier assignment when no tier is explicitly specified.

**Heuristic rules (configurable, off by default):**
| Signal | Assigned Tier |
|--------|---------------|
| Content length < 50 chars | `working` |
| Content contains "TODO", "WIP", "temp", "scratch" | `working` |
| Source is "clipboard" or "stdin" | `short` |
| Explicit `remember()` call with no tier | `long` (default, unchanged) |

**Configuration:**
```python
Lore(auto_tier=True, auto_tier_rules={...})
```

This is opt-in. When `auto_tier=False` (default), tier always falls back to the explicit value or `"long"`.

### 4.10 Server API Updates (Postgres Backend)

**POST /api/v1/memories** — accept `tier` field in request body.
**GET /api/v1/memories** — accept `tier` query parameter for filtering.
**POST /api/v1/memories/search** — accept `tier` in search request for filtering; apply tier weight in scoring.
**GET /api/v1/stats** — include `by_tier` in response.

## 5. Implementation Plan

### 5.1 Task Breakdown

1. **types.py** — Add `tier` field to `Memory`, add `VALID_TIERS`, `TIER_DEFAULT_TTL`, `TIER_RECALL_WEIGHT` constants. Add `by_tier` to `MemoryStats`.
2. **store/base.py** — Add `tier` parameter to `list()` and `count()` signatures.
3. **store/sqlite.py** — Migration (add column + index), update `save`, `update`, `list`, `count`, `_row_to_memory`.
4. **store/memory.py** — Update in-memory store for tier filtering.
5. **store/http.py** — Pass tier parameter in API calls.
6. **lore.py** — Update `remember()` (tier param, default TTL logic), `recall()` (tier filter + weight), `list_memories()` (tier filter), `stats()` (by_tier).
7. **mcp/server.py** — Add `tier` param to `remember`, `list_memories`, `recall`, update `stats` output.
8. **cli.py** — Add `--tier` to `remember`, `memories`, `recall` subcommands. Update table output.
9. **server/** — Update HTTP endpoints for tier support.
10. **Tests** — Unit tests for all changes, migration test.

### 5.2 Migration Strategy

- SQLite: `_maybe_add_tier_column()` method (pattern matches existing `_maybe_add_context_column()`).
- Postgres: SQL migration script in `server/migrations/`.
- Both use `DEFAULT 'long'` so existing data is automatically assigned.

## 6. Acceptance Criteria

### Must Have
- [ ] `Memory` dataclass has `tier` field with default `"long"`.
- [ ] `remember()` accepts `tier` parameter; invalid tiers raise `ValueError`.
- [ ] Working-tier memories without explicit TTL auto-expire after 1 hour.
- [ ] Short-tier memories without explicit TTL auto-expire after 7 days.
- [ ] Long-tier memories without explicit TTL have no expiry.
- [ ] Explicit TTL overrides tier default TTL.
- [ ] `list_memories()` and `recall()` support `tier` filter.
- [ ] Recall scoring applies tier-based weight boost (long > short > working).
- [ ] SQLite migration adds `tier` column with `DEFAULT 'long'`.
- [ ] Existing memories are accessible with `tier = 'long'` after migration.
- [ ] MCP `remember` tool accepts optional `tier` parameter.
- [ ] MCP `list_memories` tool accepts optional `tier` filter.
- [ ] CLI `--tier` flag works on `remember`, `memories`, and `recall`.
- [ ] All existing tests pass without modification (backward compat).
- [ ] New tests cover: tier validation, default TTL application, TTL override, tier filtering, recall weighting, migration.

### Should Have
- [ ] `stats` output includes `by_tier` breakdown.
- [ ] Recall output displays tier in memory details.
- [ ] Store ABC `list()` and `count()` accept `tier` parameter.
- [ ] HttpStore passes tier in API calls.

### Could Have
- [ ] Auto-tier heuristics (configurable, off by default).
- [ ] Postgres server migration script.

## 7. Success Metrics

| Metric | Target |
|--------|--------|
| All existing tests pass | 590/590 |
| New test coverage for tier feature | >= 30 new tests |
| Working-tier memory expiry verified | Within 60s of TTL (cleanup interval) |
| Recall with tier filter returns only matching tier | 100% accuracy |
| Tier weight impact on recall ordering | Verified: same-score long-tier memory ranks above working-tier |
| Migration: existing DB opens without error | Verified on SQLite |
| Backward compat: `remember("x")` works identically to v0.5.1 | tier=long, no TTL, no behavior change |

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Tier weight distorts recall for low-relevance long-term memories | Medium | Weight boost is small (1.0-1.2x) — similarity still dominates |
| Working-tier memories expire before user retrieves them | Low | 1 hour is generous for scratch context; explicit TTL override available |
| Auto-tier heuristics misclassify | Low | Off by default; explicit tier always wins |
| Store ABC signature change breaks custom stores | Low | `tier` parameter has default `None`; existing implementations work without it |

## 9. Interaction with Existing Systems

### TTL System
Tiers provide **default TTLs** — they do not replace the TTL mechanism. The existing `ttl`, `expires_at`, and `cleanup_expired()` infrastructure is unchanged. Tier defaults are applied at `remember()` time, converting to the same `expires_at` field.

### Decay System
The existing type-based decay half-lives (`DECAY_HALF_LIVES`) continue to work. Tier adds a separate **recall weight multiplier** that stacks with the decay freshness score. F5 (Importance Scoring) will later add tier-specific decay curves, but F4 does not modify the decay calculation beyond the weight multiplier.

### Upvote/Downvote
Unchanged. Vote factor and tier weight are independent multipliers in the recall score formula.

## 10. Future Considerations (Out of Scope)

- **Tier promotion/demotion** — F3 (Consolidation) or F5 (Importance) may auto-promote frequently-accessed working memories to short/long.
- **Tier-specific storage** — e.g., working memories in Redis for speed. Not needed at current scale.
- **Tier quotas** — limit number of working/short memories per project.
