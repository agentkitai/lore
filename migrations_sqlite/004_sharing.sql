-- Migration 004: Sharing & community tables (SQLite translation)
-- Idempotent — safe to run multiple times.
--
-- Translation notes:
--   * BOOLEAN → INTEGER, JSONB → TEXT, TIMESTAMPTZ → TEXT.
--   * The DO $$ block that picked between 'lessons' / 'memories' for the
--     ALTER TABLE additions becomes an unconditional ALTER. At Phase 3A
--     migration time the table is still named 'lessons' (rename happens
--     in 009), so columns are added there and travel with the rename.

CREATE TABLE IF NOT EXISTS sharing_config (
    id                      TEXT PRIMARY KEY,
    org_id                  TEXT NOT NULL REFERENCES orgs(id),
    enabled                 INTEGER DEFAULT 0,
    human_review_enabled    INTEGER DEFAULT 0,
    rate_limit_per_hour     INTEGER DEFAULT 100,
    volume_alert_threshold  INTEGER DEFAULT 1000,
    updated_at              TEXT DEFAULT (datetime('now')),
    UNIQUE (org_id)
);

CREATE TABLE IF NOT EXISTS agent_sharing_config (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    agent_id    TEXT NOT NULL,
    enabled     INTEGER DEFAULT 0,
    categories  TEXT DEFAULT '[]',
    updated_at  TEXT DEFAULT (datetime('now')),
    UNIQUE (org_id, agent_id)
);

CREATE TABLE IF NOT EXISTS deny_list_rules (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    pattern     TEXT NOT NULL,
    is_regex    INTEGER DEFAULT 0,
    reason      TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_deny_list_org ON deny_list_rules(org_id);

CREATE TABLE IF NOT EXISTS sharing_audit (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES orgs(id),
    event_type      TEXT NOT NULL,
    lesson_id       TEXT,
    query_text      TEXT,
    initiated_by    TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sharing_audit_org ON sharing_audit(org_id);
CREATE INDEX IF NOT EXISTS idx_sharing_audit_org_type ON sharing_audit(org_id, event_type);
CREATE INDEX IF NOT EXISTS idx_sharing_audit_org_created ON sharing_audit(org_id, created_at);

-- Add columns to lessons (renamed to memories in 009; columns travel with the rename).
ALTER TABLE lessons ADD COLUMN reputation_score INTEGER DEFAULT 0;
ALTER TABLE lessons ADD COLUMN quality_signals TEXT DEFAULT '{}';
