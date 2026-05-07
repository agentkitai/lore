-- Migration 020: Full-text search index (Phase 6C hybrid retrieval)
-- Adds a GIN index over the English-stemmed tsvector of memory content + context.
-- Used by ``recall_by_text`` (Store protocol) for BM25-style ranking via ts_rank.
-- Idempotent — safe to run multiple times.

CREATE INDEX IF NOT EXISTS memories_fts_idx
    ON memories
    USING GIN (to_tsvector('english', content || ' ' || COALESCE(context, '')));
