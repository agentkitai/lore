-- Migration 005: OIDC user tracking + RBAC (SQLite translation)
-- Additive only — no drops, no breaking changes.
-- Idempotent — safe to run multiple times.
--
-- Translation notes:
--   * BOOLEAN/TIMESTAMPTZ → INTEGER/TEXT.
--   * DO $$ blocks → straight ALTER TABLE. SQLite migrations are unconditional;
--     each migration runs once thanks to the schema_migrations tracker.
--   * The role default for api_keys stays 'admin' to preserve existing behavior.

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    oidc_sub        TEXT NOT NULL UNIQUE,
    email           TEXT,
    display_name    TEXT,
    role            TEXT NOT NULL DEFAULT 'viewer',
    org_id          TEXT NOT NULL REFERENCES orgs(id),
    created_at      TEXT DEFAULT (datetime('now')),
    last_seen_at    TEXT,
    disabled_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_sub ON users(oidc_sub);
CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id);

-- Add tenant_id / user_id to lessons (renamed to memories in 009).
ALTER TABLE lessons ADD COLUMN tenant_id TEXT;
ALTER TABLE lessons ADD COLUMN user_id TEXT;

-- Add tenant_id / user_id / role to api_keys.
ALTER TABLE api_keys ADD COLUMN tenant_id TEXT;
ALTER TABLE api_keys ADD COLUMN user_id TEXT;
ALTER TABLE api_keys ADD COLUMN role TEXT DEFAULT 'admin';
