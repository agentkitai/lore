-- Migration 027: relationship (fact) supersession — bi-temporal facts (#67).
-- SQLite translation of migrations/027_relationship_supersessions.sql.
--
-- Translation notes:
--   * BIGSERIAL    -> INTEGER PRIMARY KEY AUTOINCREMENT.
--   * TIMESTAMPTZ  -> TEXT (ISO-8601 via datetime('now')).
--   * SQLite ALTER TABLE has no ADD COLUMN IF NOT EXISTS, but the SqliteStore
--     migration runner version-tracks (schema_migrations), so each file runs
--     exactly once — plain ADD COLUMN is safe (same as migrations 011 / 026).
--   * FK ON DELETE CASCADE / ON DELETE SET NULL — same semantics, requires
--     ``PRAGMA foreign_keys = ON`` (set in the SQLite store bootstrap).
--
-- See the Postgres mirror for the full rationale.

ALTER TABLE relationships ADD COLUMN superseded_by TEXT;

CREATE INDEX IF NOT EXISTS idx_rel_superseded_by
    ON relationships (superseded_by);

CREATE TABLE IF NOT EXISTS relationship_supersessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship_id TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    superseded_by   TEXT REFERENCES relationships(id) ON DELETE SET NULL,
    reason          TEXT,
    ts              TEXT NOT NULL DEFAULT (datetime('now')),
    agent           TEXT NOT NULL DEFAULT 'auto'
);

CREATE INDEX IF NOT EXISTS idx_relationship_supersessions_rel_id_ts
    ON relationship_supersessions (relationship_id, ts DESC);
