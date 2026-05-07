-- Migration 010: Retrieval analytics (SQLite translation)
--
-- Translation notes:
--   * BIGSERIAL → INTEGER PRIMARY KEY AUTOINCREMENT (SQLite ROWID alias).
--   * JSONB → TEXT, TIMESTAMPTZ → TEXT.
--   * DOUBLE PRECISION → REAL.

CREATE TABLE IF NOT EXISTS retrieval_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id              TEXT NOT NULL,
    query               TEXT NOT NULL,
    results_count       INTEGER NOT NULL DEFAULT 0,
    scores              TEXT DEFAULT '[]',
    memory_ids          TEXT DEFAULT '[]',
    avg_score           REAL,
    max_score           REAL,
    min_score_threshold REAL,
    query_time_ms       REAL,
    project             TEXT,
    format              TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_retrieval_events_org_created
    ON retrieval_events (org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_retrieval_events_created
    ON retrieval_events (created_at DESC);
