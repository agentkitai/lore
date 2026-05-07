-- Migration 022: Dream runs (Phase 6E memory consolidation, SQLite translation)
--
-- Mirrors migrations/022_dream_runs.sql. Translation notes:
--   * TIMESTAMPTZ → TEXT (ISO-8601 via datetime('now')).
--   * JSONB       → TEXT (JSON string).

CREATE TABLE IF NOT EXISTS dream_runs (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    summary      TEXT,
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_dream_runs_org_id      ON dream_runs(org_id);
CREATE INDEX IF NOT EXISTS idx_dream_runs_started_at  ON dream_runs(started_at DESC);
