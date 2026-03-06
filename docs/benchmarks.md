# Benchmarks

Performance benchmarks for Lore v0.6.0 operations.

## Methodology

Benchmarks are run on a single machine with the following procedure:

1. **Setup:** Fresh SQLite database, pre-populated with the specified number of memories
2. **Warmup:** 10 operations discarded before measurement
3. **Measurement:** 100 iterations per operation, collecting median and P95 latency
4. **Environment:** Python 3.11, local ONNX embeddings, LLM features disabled unless noted

All times include embedding computation where applicable. Network latency is excluded (local SQLite store only).

## Results

### Core Operations (1,000 memories)

| Operation | Median | P95 | Target | Status |
|-----------|--------|-----|--------|--------|
| `remember` (with embedding) | -- ms | -- ms | < 100 ms | -- |
| `recall` (top-5) | -- ms | -- ms | < 200 ms | -- |
| `forget` | -- ms | -- ms | < 10 ms | -- |
| `list_memories` (100 results) | -- ms | -- ms | < 50 ms | -- |
| `stats` | -- ms | -- ms | < 20 ms | -- |

### Core Operations (10,000 memories)

| Operation | Median | P95 | Target | Status |
|-----------|--------|-----|--------|--------|
| `remember` (with embedding) | -- ms | -- ms | < 100 ms | -- |
| `recall` (top-5) | -- ms | -- ms | < 500 ms | -- |
| `forget` | -- ms | -- ms | < 10 ms | -- |
| `list_memories` (100 results) | -- ms | -- ms | < 50 ms | -- |
| `stats` | -- ms | -- ms | < 20 ms | -- |

### Embedding

| Operation | Median | P95 | Target | Status |
|-----------|--------|-----|--------|--------|
| Single embed (prose) | -- ms | -- ms | < 50 ms | -- |
| Single embed (code) | -- ms | -- ms | < 50 ms | -- |
| Batch embed (10 texts) | -- ms | -- ms | < 200 ms | -- |

### Knowledge Graph (1,000 entities, 5,000 relationships)

| Operation | Median | P95 | Target | Status |
|-----------|--------|-----|--------|--------|
| `graph_query` (depth=1) | -- ms | -- ms | < 100 ms | -- |
| `graph_query` (depth=2) | -- ms | -- ms | < 300 ms | -- |
| `graph_query` (depth=3) | -- ms | -- ms | < 1000 ms | -- |
| `entity_map` (50 entities) | -- ms | -- ms | < 50 ms | -- |
| `related` (depth=1) | -- ms | -- ms | < 100 ms | -- |

### Fact Extraction (requires LLM)

| Operation | Median | P95 | Target | Status |
|-----------|--------|-----|--------|--------|
| `extract_facts` (short text) | -- ms | -- ms | < 3000 ms | -- |
| Conflict resolution (per fact) | -- ms | -- ms | < 100 ms | -- |

### Consolidation (100 memories)

| Operation | Median | P95 | Target | Status |
|-----------|--------|-----|--------|--------|
| Dedup scan (dry run) | -- ms | -- ms | < 2000 ms | -- |
| Summarization (5-memory group) | -- ms | -- ms | < 5000 ms | -- |

### Ingestion

| Operation | Median | P95 | Target | Status |
|-----------|--------|-----|--------|--------|
| Single ingest | -- ms | -- ms | < 200 ms | -- |
| Batch ingest (10 items) | -- ms | -- ms | < 1000 ms | -- |
| Deduplication check | -- ms | -- ms | < 100 ms | -- |

## Scalability Notes

- **SQLite** is single-writer. Concurrent writes from multiple MCP sessions may queue. For multi-user deployments, use the self-hosted PostgreSQL server.
- **Recall** performance is O(n) with the number of memories because cosine similarity is computed in-process. For databases over 50,000 memories, consider using the PostgreSQL + pgvector backend.
- **Graph traversal** at depth=3 can return large result sets. The traverser caps results to prevent runaway queries.
- **LLM operations** (enrichment, fact extraction, consolidation summaries) are bounded by LLM API latency, not Lore internals.

## Running Benchmarks

```bash
# Install dev dependencies
pip install lore-sdk[dev]

# Run the benchmark suite (when available)
python -m pytest tests/benchmarks/ -v --benchmark-only
```

Benchmark results will vary based on hardware, Python version, and database size. The targets above represent acceptable performance for interactive use via MCP.
