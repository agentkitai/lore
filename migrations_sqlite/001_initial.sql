-- Migration 001: Initial schema for Lore Cloud (SQLite translation)
-- Idempotent — safe to run multiple times.
--
-- Translation notes:
--   * CREATE EXTENSION vector → dropped (sqlite-vec is loaded at connection open).
--   * TIMESTAMPTZ → TEXT (ISO-8601), defaulted via datetime('now').
--   * BOOLEAN → INTEGER (0/1).
--   * JSONB → TEXT.
--   * vector(384) embedding column → DROPPED. Phase 3B will add memory_vectors
--     vec0 virtual table joined by row id.
--   * HNSW index on embedding → DROPPED for the same reason.
--   * DO $$ ... $$ procedural blocks → straight CREATE INDEX IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS orgs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES orgs(id),
    name         TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    project      TEXT,
    is_root      INTEGER DEFAULT 0,
    revoked_at   TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    last_used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_keys_hash ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS lessons (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    problem     TEXT NOT NULL,
    resolution  TEXT NOT NULL,
    context     TEXT,
    tags        TEXT DEFAULT '[]',
    confidence  REAL DEFAULT 0.5,
    source      TEXT,
    project     TEXT,
    -- Phase 3B will add memory_vectors vec0 virtual table for embeddings.
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT,
    upvotes     INTEGER DEFAULT 0,
    downvotes   INTEGER DEFAULT 0,
    meta        TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_lessons_org ON lessons(org_id);
CREATE INDEX IF NOT EXISTS idx_lessons_org_project ON lessons(org_id, project);
-- Phase 3B will add memory_vectors vec0 virtual table (replaces idx_lessons_embedding).
