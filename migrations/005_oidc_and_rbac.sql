-- Migration 005: OIDC user tracking + RBAC
-- Additive only — no drops, no breaking changes
-- Idempotent — safe to run multiple times

-- OIDC users get a record for audit + org mapping
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    oidc_sub        TEXT NOT NULL UNIQUE,
    email           TEXT,
    display_name    TEXT,
    role            TEXT NOT NULL DEFAULT 'viewer',
    org_id          TEXT NOT NULL REFERENCES orgs(id),
    created_at      TIMESTAMPTZ DEFAULT now(),
    last_seen_at    TIMESTAMPTZ,
    disabled_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_users_sub ON users(oidc_sub);
CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id);

-- Add tenant_id column to lessons/memories (for explicit multi-tenant querying)
DO $$
DECLARE
    _tbl text;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'memories' AND table_type = 'BASE TABLE') THEN
        _tbl := 'memories';
    ELSIF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lessons' AND table_type = 'BASE TABLE') THEN
        _tbl := 'lessons';
    ELSE
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = _tbl AND column_name = 'tenant_id') THEN
        EXECUTE format('ALTER TABLE %I ADD COLUMN tenant_id TEXT', _tbl);
    END IF;
END $$;

-- Add tenant_id column to api_keys
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'api_keys' AND column_name = 'tenant_id') THEN
        ALTER TABLE api_keys ADD COLUMN tenant_id TEXT;
    END IF;
END $$;

-- Add user_id column to lessons/memories (track which user created/modified)
DO $$
DECLARE
    _tbl text;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'memories' AND table_type = 'BASE TABLE') THEN
        _tbl := 'memories';
    ELSIF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lessons' AND table_type = 'BASE TABLE') THEN
        _tbl := 'lessons';
    ELSE
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = _tbl AND column_name = 'user_id') THEN
        EXECUTE format('ALTER TABLE %I ADD COLUMN user_id TEXT', _tbl);
    END IF;
END $$;

-- Add user_id column to api_keys
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'api_keys' AND column_name = 'user_id') THEN
        ALTER TABLE api_keys ADD COLUMN user_id TEXT;
    END IF;
END $$;

-- Add role column to api_keys for RBAC (default 'admin' preserves existing behavior)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'api_keys' AND column_name = 'role') THEN
        ALTER TABLE api_keys ADD COLUMN role TEXT DEFAULT 'admin';
    END IF;
END $$;

-- ── ROLLBACK SQL (do NOT run automatically) ──
-- DROP INDEX IF EXISTS idx_users_sub;
-- DROP INDEX IF EXISTS idx_users_org;
-- DROP TABLE IF EXISTS users;
-- ALTER TABLE lessons DROP COLUMN IF EXISTS tenant_id;
-- ALTER TABLE lessons DROP COLUMN IF EXISTS user_id;
-- ALTER TABLE api_keys DROP COLUMN IF EXISTS tenant_id;
-- ALTER TABLE api_keys DROP COLUMN IF EXISTS user_id;
-- ALTER TABLE api_keys DROP COLUMN IF EXISTS role;
