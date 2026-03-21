-- Workspaces and audit log (F7: Multi-Tenant Workspace Isolation)

CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    settings    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now(),
    archived_at TIMESTAMPTZ,
    UNIQUE(org_id, slug)
);

CREATE TABLE IF NOT EXISTS workspace_members (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    user_id      TEXT,
    role         TEXT NOT NULL DEFAULT 'writer',
    invited_at   TIMESTAMPTZ DEFAULT now(),
    accepted_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           BIGSERIAL PRIMARY KEY,
    org_id       TEXT NOT NULL,
    workspace_id TEXT,
    actor_id     TEXT NOT NULL,
    actor_type   TEXT NOT NULL,
    action       TEXT NOT NULL,
    resource_type TEXT,
    resource_id  TEXT,
    metadata     JSONB DEFAULT '{}',
    ip_address   INET,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_org_time ON audit_log(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_workspace ON audit_log(workspace_id, created_at DESC);

ALTER TABLE memories ADD COLUMN IF NOT EXISTS workspace_id TEXT;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS workspace_id TEXT;
