# Lore Hybrid Retrieval (Phase 6C) — Design

**Status:** Approved (autonomous trust mandate), pending implementation.
**Date:** 2026-05-07

## Goal

Replace today's single-signal vector recall with a **hybrid score** that combines vector similarity + full-text rank + graph proximity + recency + importance, weighted by the active retrieval profile. The profile fields (`semantic_weight`, `graph_weight`, `recency_bias`) already exist in the schema but **don't actually drive scoring** — 6C activates them.

After 6C: with a populated DB, the right memory surfaces consistently. Score noise from the embedder no longer overwhelms a real keyword/title hit. Phase 6D builds on this with progressive disclosure.

## Non-goals

- Re-ranking with an LLM. Out of scope; cheap, stable signals only.
- New retrieval profile fields. Use what's there.
- Changing the public `/v1/retrieve` HTTP shape. Same request/response, better ranking.
- Graph traversal beyond 1-hop. The existing `list_related_memories` is enough.

## Design decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Combination function | **Reciprocal Rank Fusion (RRF)** with profile-driven weights. Each signal produces a sorted candidate list; RRF aggregates. Robust to scale differences (vector cosine 0–1 vs FTS BM25 0.5–10+). |
| 2 | FTS backend | SQLite FTS5 (`USING fts5(content, context, tokenize='porter unicode61')`). PG `GIN(to_tsvector('english', content || ' ' || context))`. New migration on both sides. |
| 3 | Recency signal | Exponential decay `exp(-age_days / recency_bias)` where `recency_bias` comes from the profile. |
| 4 | Importance signal | `memories.importance_score` direct (already maintained by existing pipeline). |
| 5 | Graph signal | For each candidate, count overlapping entities with the query's extracted entities. Already wired via `list_memories_for_topic`/`get_memories_by_entities`. Cheap to evaluate post-vector. |
| 6 | Backwards compat | `/v1/retrieve` keeps the same request/response. Default profile (no profile) uses `semantic_weight=1.0, graph_weight=0.5, recency_bias=30, min_score=0.3` — same shape as the existing presets. |
| 7 | Score normalization for HTTP response | Return both the raw RRF score AND a per-signal breakdown `meta.signals = {vector: 0.84, fts: 0.21, graph: 0.0, recency: 0.92, importance: 0.5}` so consumers can debug. |

## Architecture

```
GET /v1/retrieve?query=...&profile=coding
        │
        ▼
services/retrieve.py:retrieve()
        │
        ▼  (resolve profile via existing PolicyOps)
services.retrieve._hybrid_recall(query, profile)
        │
        ├── vector candidates  ← store.recall_by_embedding(emb, k=4×limit)
        ├── fts candidates     ← store.recall_by_text(query, k=4×limit)        ★ NEW
        ├── graph candidates   ← store.recall_by_entities(entities, k=2×limit) ★ NEW
        ▼
RRF fuse (with profile weights) → top `limit` survivors
        │
        ▼  (annotate)
For each survivor: compute recency + importance signals; multiply into final score.
        │
        ▼
[(StoredMemory, score, signals_dict)] → HTTP response
```

### Invariants

- **Same `/v1/retrieve` HTTP shape.** Phase 6C is internal scoring; consumers see a richer `meta.signals` field but no breaking change.
- **`min_score` is post-RRF.** Drop everything below the profile's threshold.
- **Profile fallback.** If no profile resolves, use the same defaults today's path uses (`semantic_weight=1.0, graph_weight=0.5, recency_bias=30, min_score=0.3`).
- **Each signal is independently optional.** If FTS isn't available (no migration applied yet, or query is empty), skip that branch — don't fail the whole call.

## Components

### Schema additions (new migrations)

| Path | What it adds |
|------|--------------|
| `migrations/020_fts_index.sql` | `CREATE INDEX memories_fts_idx ON memories USING GIN (to_tsvector('english', content || ' ' || COALESCE(context, '')))` (PG). |
| `migrations_sqlite/020_fts_index.sql` | `CREATE VIRTUAL TABLE memories_fts USING fts5(content, context, tokenize='porter unicode61', content='memories', content_rowid='rowid')` + INSERT/DELETE/UPDATE triggers to keep it in sync. |

Migrations parity guard already enforces the sibling rule.

### Store protocol additions

```python
# In persistence/protocol.py — extends MemoryOps slice
async def recall_by_text(
    self,
    org_id: str,
    query: str,
    *,
    limit: int = 20,
    project: Optional[str] = None,
) -> Sequence[Tuple[StoredMemory, float]]:
    """Full-text search with backend-native ranking.

    PG: ts_rank with the GIN index.
    SQLite: bm25() with the FTS5 virtual table.
    Returns list of (memory, fts_rank) ordered by descending rank.
    """

async def recall_by_entities(
    self,
    org_id: str,
    entity_ids: Sequence[str],
    *,
    limit: int = 20,
) -> Sequence[Tuple[StoredMemory, int]]:
    """Memories tied to any of the given entities, ranked by mention count.

    Uses the existing `mentions` table. Returns (memory, n_overlapping_entities).
    Already partially exists as `get_memories_by_entities`; this version returns
    the count for use in scoring.
    """
```

Both methods land on **PostgresStore** and **SqliteStore**.

### Service layer

```python
# src/lore/services/retrieve.py

async def _hybrid_recall(
    store, embed, profile: ResolvedProfile, params: RecallParams
) -> Sequence[Tuple[StoredMemory, float, dict]]:
    # 1. Embed query
    emb = await embed(params.query)

    # 2. Pull 4× limit from each candidate source (concurrent)
    vec, fts, graph = await asyncio.gather(
        store.recall_by_embedding(org_id=..., embedding=emb, limit=4 * params.limit),
        _safe_text_recall(store, params.query, limit=4 * params.limit),       # may return [] if FTS unavailable
        _safe_graph_recall(store, params.query, limit=2 * params.limit),      # entity extraction → entity recall
    )

    # 3. RRF fuse with profile weights
    fused = _rrf_fuse(
        sources=[
            (vec, profile.semantic_weight),
            (fts, profile.fts_weight),       # added field; default 1.0 if not in profile
            (graph, profile.graph_weight),
        ],
        k=60,  # standard RRF dampener
    )

    # 4. For each survivor, multiply by recency × importance
    annotated = []
    for memory, base_score in fused[: params.limit * 2]:
        recency = _recency_signal(memory.created_at, profile.recency_bias)
        importance = memory.importance_score or 0.5
        final = base_score * (1.0 + 0.5 * recency) * (1.0 + 0.5 * (importance - 0.5))
        signals = {"vector": ..., "fts": ..., "graph": ..., "recency": recency, "importance": importance}
        annotated.append((memory, final, signals))

    # 5. Threshold + cap
    annotated = [t for t in annotated if t[1] >= profile.min_score]
    annotated.sort(key=lambda t: t[1], reverse=True)
    return annotated[: params.limit]
```

### Profile schema

`retrieval_profiles` already has `semantic_weight, graph_weight, recency_bias, min_score, max_results`. **Add `fts_weight`** as a nullable column with default 1.0 (one-line migration on both sides). Existing rows back-fill to 1.0 via the `DEFAULT` clause.

### Routes

`src/lore/server/routes/retrieve.py` — no API change; pass-through to `services.retrieve.retrieve()` which now routes through `_hybrid_recall`.

The response payload gains `signals` per memory:

```json
{
  "memories": [
    {
      "id": "mem_...",
      "content": "...",
      "score": 0.84,
      "signals": {"vector": 0.61, "fts": 0.0, "graph": 0.5, "recency": 0.92, "importance": 0.4}
    }
  ]
}
```

Consumers that ignore `signals` work unchanged.

## Tests

| Layer | Coverage |
|-------|----------|
| Migrations | New 020 SQL applies cleanly on both backends; idempotent re-apply OK. |
| Store contract | `recall_by_text` round-trip + ranking order. `recall_by_entities` overlap-count correctness. Tests run on both PG + SQLite. |
| Service | `_hybrid_recall` with synthetic candidate lists; verify RRF math + recency/importance multiplication. |
| Service | Profile selection: when `profile=coding`, FTS gets weight 1.0 + graph gets 0.5 (per preset). |
| Service | Fallback: `recall_by_text` raises → call still succeeds with vector+graph only. |
| HTTP | `GET /v1/retrieve?query=...` returns memories with `signals` dict. |
| Backwards compat | Existing snapshot tests for retrieve still pass. |

## Scope

| Component | LOC |
|---|---|
| 020 migrations (PG + SQLite) | ~80 |
| Store protocol + impls (PG + SQLite) | ~250 |
| Profile fts_weight column + dataclass | ~30 |
| `_hybrid_recall` service | ~200 |
| Tests | ~300 |
| Docs | ~30 |
| **Total** | **~890** |

## Open questions resolved by judgment

- **`fts_weight` default:** 1.0. Equal weight to vector by default; presets can adjust.
- **Tokenizer choice:** SQLite `porter unicode61`, PG `english`. Both stem English; non-English text gets less benefit but doesn't break.
- **Entity extraction for the query:** reuse the existing `services.graph.entities` extractor if present, else punt to "no graph candidates" gracefully.
- **`k=60` in RRF:** standard literature value. Tunable later via `LORE_RRF_K`.

## Out of scope

- Cross-language stemmers / multilingual retrieval. Adds tokenization complexity; revisit if Lore lands non-English content.
- Hybrid query rewrites (multi-vector embedding, query expansion). Phase 6D's progressive disclosure is the right place.
- Phrase / proximity queries. FTS5 + GIN both support them but the API stays simple-string-only for now.
