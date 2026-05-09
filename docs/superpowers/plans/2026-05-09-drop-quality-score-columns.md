# Drop importance_score and confidence columns

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to resume this plan from the checkpoint. Check the "Status as of last commit" section first to see what's already done.

**Goal:** Remove the two broken-by-default `memory` columns (`importance_score` always 1.0; `confidence` always 0.5 for observations) and every code path that reads, writes, or ranks by them. Lore's recall stack already does cosine-similarity + FTS hybrid scoring; importance/confidence multipliers are no-ops on the current data and only confuse the UI.

**Architecture:** Schema migration drops columns; persistence layer stops carrying the fields; service / MCP / HTTP / CLI / export layers stop accepting and emitting them; `importance.py` was deleted and replaced by `decay.py` with just the still-needed pure functions. `min_confidence` parameters that are actually min-score thresholds get renamed to `min_score`. Recall scoring is now `cosine × time_decay × tier_weight × graph_boost` (no importance multiplier; was always a no-op since every row had identical 1.0).

**Out of scope (separate "confidence" concepts — DO NOT TOUCH):**
- `recommend/engine.py` / `recommend/types.py` — RecommendationConfidence (`0.6 * magnitude + 0.4 * agreement`)
- `freshness/detector.py` — staleness confidence per commit-count threshold
- `services/graph/`, `graph_extraction.py`, graph relationships — these have their own per-relationship `confidence` and `weight` fields on a *different* table (entities/mentions/relationships)
- `classify/` — classification confidence per axis

---

## Status as of last commit (2af9ead)

**Done:**
- ✅ Phase 1: schema migrations (`migrations/025_drop_quality_score_columns.sql` + SQLite version)
- ✅ Phase 2: dataclass field removals (NewMemory, StoredMemory, MemoryPatch, ExportedMemory in `persistence/types.py`)
- ✅ Phase 3a: SQLite persistence (INSERT, SELECT, recall scoring, record_memory_access, bump_access_counts, recommendation candidate ordering, search_memories_text, _row_to_memory, _row_to_exported, _MEMORY_COLS, GraphStats.avg_importance)
- ✅ Phase 3b: PostgreSQL persistence (matching shape changes — RETURNING clauses, recall scoring, FTS branch, recall_by_entities, list_memories_without_mentions, list_temporal_buckets, search_memories_text, record_memory_access, list_candidate_memories_for_recommendation, AVG aggregates)
- ✅ Phase 3c: protocol.py (bump_access_counts docstring; import_extracted_memory and upsert_memory_with_embedding signatures lose the confidence parameter)
- ✅ Phase 4: `src/lore/importance.py` deleted; new `src/lore/decay.py` with `decay_factor` + `resolve_half_life`; `lore.py` updated to use `_memory_decay` helper instead of `time_adjusted_importance`; upvote/downvote no longer recompute importance; `cleanup_expired` uses decay-only thresholding; `recalculate_importance` method removed; `tests/test_importance_scoring.py` deleted
- ✅ Phase 5a: 3 services (`services/observations.py`, `services/memories.py`, `services/lessons.py`) — drop `confidence` parameter, drop `min_confidence` (renamed to `min_score`), strip from response dicts and bulk-upsert path

**Remaining (Phase 5b onward):**

### Phase 5b — services + adjacent
- `src/lore/services/conversations.py` — drop confidence pass-through to memory creation
- `src/lore/services/snapshots.py` — drop importance_score from response/dataclass mappings
- `src/lore/services/retrieve.py` — line ~524 has `float(memory.importance_score) if ... else 0.5` to drop
- `src/lore/services/graph/graph.py`, `services/graph/review.py` — sweep for memory.importance_score / memory.confidence references; LEAVE per-relationship graph confidence/weight alone
- `src/lore/services/graph_extraction.py` — sweep for memory.importance_score / memory.confidence (NOT the graph extraction's own LLM-emitted confidence per fact, that's a different concept)
- `src/lore/consolidation.py` — drop importance_score from consolidated NewMemory; replace `max(memories, key=lambda m: m.importance_score)` with `max(..., key=lambda m: m.created_at)`
- `src/lore/conversation/extractor.py` — drop `confidence=candidate.get("confidence", 0.8)` from create-memory call (the LLM still emits per-fact confidence, we just stop persisting it on the memory column)
- `src/lore/extract/extractor.py`, `extract/prompts.py`, `extract/resolver.py` — drop confidence from any NewMemory constructors
- `src/lore/ingest/dedup.py` — line 68 `min_confidence=0.0` → rename to `min_score=0.0`

### Phase 6 — MCP server
- `src/lore/mcp/server.py::remember()` — drop `confidence: float = 1.0` parameter from signature; drop from the `lore.remember(...)` call
- Sweep response dicts for confidence / importance_score

### Phase 7 — HTTP routes & response models
- `src/lore/server/models.py` — drop confidence/importance_score from any pydantic MemoryResponse / MemoryRow
- `src/lore/server/routes/recent.py` — `RecentMemoryItem.importance_score` field gone; drop from `_to_item()` and the markdown formatter
- `src/lore/server/routes/retention.py` — drop `min_importance_score` query parameter; drop importance_score from response model. Replace with min_age_days/tier or just remove.
- `src/lore/server/routes/lessons.py` — drop confidence from create body; rename `min_confidence` → `min_score`; drop confidence + importance_score from response model
- `src/lore/server/routes/memories.py` — drop both fields from MemoryResponse; replace any min_confidence/min_importance_score with min_score
- `src/lore/server/routes/observations.py` — drop fields from response (they're in `meta`, but verify)
- `src/lore/server/routes/temporal.py` — drop importance_score from temporal response models
- `src/lore/server/routes/review.py` — sweep for the dropped columns (leave graph confidence alone)
- `src/lore/server/routes/recommendations.py` — sweep; leave RecommendationConfidence alone, only touch references to memory column
- `src/lore/server/routes/graph/memories.py`, `graph/models.py`, `graph/stats.py` — drop avg_importance from GraphStats response; leave per-relationship graph confidence

### Phase 8 — top-level + CLI + export + UI source
- `src/lore/lore.py::remember()` — drop `confidence` parameter from public API; drop `0 <= confidence <= 1.0` guard
- `src/lore/async_lore.py` — same surgery
- `src/lore/types.py` — drop `confidence: float = 1.0` and `importance_score: float = 1.0` from any top-level Memory / ScoredMemory dataclasses; drop avg_importance / avg_confidence from MemoryStats
- `src/lore/temporal.py:181` — drop the `importance: {mem.importance_score:.2f}` formatter line
- `src/lore/recent.py`, `src/lore/retention.py` — drop importance_score from data structures; drop importance threshold parameters
- `src/lore/store/http.py` — drop importance_score / confidence from the request payloads and response parsers
- `src/lore/cli/__init__.py:575` — drop `--min-importance` argument
- `src/lore/cli/commands/manage.py` — drop importance-recompute commands
- `src/lore/cli/commands/misc.py` — sweep references
- `src/lore/cli/commands/remember.py` — drop `--confidence` flag
- `src/lore/export/markdown.py:137` — drop `"confidence": mem.confidence,`
- `src/lore/export/serializers.py` — drop both fields from JSON serializer
- `src/lore/ui/src/panels/detail.js` — drop the rows that render "Importance: 100%" and "Confidence: 50%"
- `src/lore/ui/src/state.js` — sweep
- `src/lore/ui/dist/app.js` — leave (build artifact); note in PR that UI build needs re-run

### Phase 9 — tests update + add regression
~30 test files reference these fields. Sweep:
```bash
grep -rln "importance_score\|memory\.confidence\|min_confidence\|min_importance" tests/ | grep -v __pycache__
```
For each: remove `importance_score=`/`confidence=` from fixture constructors, drop assertions on those fields, rename `min_confidence` → `min_score` parameters.

Add `tests/persistence/test_quality_columns_dropped.py` regression test:
```python
import pytest
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")
from lore.persistence.sqlite import SqliteStore

@pytest.mark.asyncio
async def test_memories_table_has_no_quality_score_columns(tmp_path):
    db = tmp_path / "lore.db"
    store = await SqliteStore.create(f"sqlite:///{db}", run_migrations=True)
    try:
        async with store._pool.acquire() as conn:
            cur = await conn.execute("PRAGMA table_info(memories)")
            cols = [row[1] async for row in cur]
        assert "importance_score" not in cols
        assert "confidence" not in cols
    finally:
        await store.close()


def test_importance_module_does_not_exist():
    with pytest.raises(ImportError):
        from lore import importance  # noqa: F401
```

### Phase 10 — verify, lint, push, PR

```bash
PYTHONPATH=src python3 -m pytest tests/ -x -q --ignore=tests/integration
ruff check src/ tests/
git push
gh pr ready <PR#>  # if originally opened as draft
```

---

## Helpful invariants for the next executor

- **`m.confidence` is OK to keep** in queries against `entity_mentions` table (graph relationship confidence — different column, different concept). Verify by checking the FROM clause: if it's `FROM entity_mentions` or has a `JOIN entity_mentions m ON ...` with `m.` prefix, that's graph.
- The `_row_to_mention` function in both sqlite.py and postgres.py keeps its `confidence=row["confidence"]` line — that's the graph mention.
- `recommend/engine.py` `_compute_confidence` is a separate computation (signal magnitude × agreement). Don't touch.
- `freshness/detector.py` `confidence` is a per-staleness-status threshold. Don't touch.
- `classify/` axis confidence is unrelated. Don't touch.

## Done-state self-check

- `grep -rn "importance_score\|memory\.confidence" src/ | grep -v "graph\|recommend\|freshness\|classify"` returns zero hits
- `python3 -c "from lore import importance"` raises ImportError
- `pytest tests/ --ignore=tests/integration` is fully green
- `ruff check src/ tests/` is clean
- migration runs cleanly on a fresh DB
