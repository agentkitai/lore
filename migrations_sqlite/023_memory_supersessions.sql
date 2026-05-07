-- Migration 023: memory_supersessions (Phase 6F temporal reasoning, SQLite translation)
--
-- Mirrors migrations/023_memory_supersessions.sql. Translation notes:
--   * BIGSERIAL    → INTEGER PRIMARY KEY AUTOINCREMENT.
--   * TIMESTAMPTZ  → TEXT (ISO-8601 via datetime('now')).
--   * FK ON DELETE CASCADE / ON DELETE SET NULL — same semantics, requires
--     ``PRAGMA foreign_keys = ON`` (set in the SQLite store bootstrap).

CREATE TABLE IF NOT EXISTS memory_supersessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id       TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    superseded_by   TEXT REFERENCES memories(id) ON DELETE SET NULL,
    reason          TEXT,
    ts              TEXT NOT NULL DEFAULT (datetime('now')),
    agent           TEXT NOT NULL DEFAULT 'auto'
);

CREATE INDEX IF NOT EXISTS idx_memory_supersessions_memory_id_ts
    ON memory_supersessions (memory_id, ts DESC);
