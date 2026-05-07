-- Migration 015: Retention policies and restore drills (SQLite translation)
--
-- Translation notes:
--   * JSONB → TEXT, BOOLEAN → INTEGER, BIGINT stays INTEGER (SQLite ints are
--     dynamically sized up to 64-bit), TIMESTAMPTZ → TEXT.

CREATE TABLE IF NOT EXISTS retention_policies (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,
    name                TEXT NOT NULL,
    retention_window    TEXT NOT NULL DEFAULT '{"working": 3600, "short": 604800, "long": null}',
    snapshot_schedule   TEXT,
    encryption_required INTEGER DEFAULT 0,
    max_snapshots       INTEGER DEFAULT 50,
    is_active           INTEGER DEFAULT 1,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now')),
    UNIQUE(org_id, name)
);

CREATE TABLE IF NOT EXISTS snapshot_metadata (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    policy_id    TEXT REFERENCES retention_policies(id),
    name         TEXT NOT NULL,
    path         TEXT NOT NULL,
    size_bytes   INTEGER,
    memory_count INTEGER,
    encrypted    INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS restore_drill_results (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL,
    snapshot_id       TEXT REFERENCES snapshot_metadata(id),
    snapshot_name     TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    completed_at      TEXT,
    recovery_time_ms  INTEGER,
    memories_restored INTEGER,
    status            TEXT DEFAULT 'running',
    error             TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);
