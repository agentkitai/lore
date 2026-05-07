-- Migration 016: Workspaces and audit log (F7: Multi-Tenant Workspace Isolation)
-- (SQLite translation)
--
-- Translation notes:
--   * JSONB → TEXT, TIMESTAMPTZ → TEXT, BIGSERIAL → INTEGER PRIMARY KEY AUTOINCREMENT.
--   * INET (Postgres IP-address type) → TEXT. SQLite has no native INET; the
--     route layer formats as a string before insert.

CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    settings    TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now')),
    archived_at TEXT,
    UNIQUE(org_id, slug)
);

CREATE TABLE IF NOT EXISTS workspace_members (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    user_id      TEXT,
    role         TEXT NOT NULL DEFAULT 'writer',
    invited_at   TEXT DEFAULT (datetime('now')),
    accepted_at  TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        TEXT NOT NULL,
    workspace_id  TEXT,
    actor_id      TEXT NOT NULL,
    actor_type    TEXT NOT NULL,
    action        TEXT NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    metadata      TEXT DEFAULT '{}',
    ip_address    TEXT,                  -- Postgres INET; stored as string in SQLite.
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_org_time ON audit_log(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_workspace ON audit_log(workspace_id, created_at DESC);

ALTER TABLE memories ADD COLUMN workspace_id TEXT;
ALTER TABLE api_keys ADD COLUMN workspace_id TEXT;
