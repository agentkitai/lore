-- Migration 001: Initial schema for Lore Cloud
-- Idempotent — safe to run multiple times

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS orgs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES orgs(id),
    name         TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    project      TEXT,
    is_root      BOOLEAN DEFAULT FALSE,
    revoked_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_keys_hash ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS lessons (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    problem     TEXT NOT NULL,
    resolution  TEXT NOT NULL,
    context     TEXT,
    tags        JSONB DEFAULT '[]',
    confidence  REAL DEFAULT 0.5,
    source      TEXT,
    project     TEXT,
    embedding   vector(384),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ,
    upvotes     INTEGER DEFAULT 0,
    downvotes   INTEGER DEFAULT 0,
    meta        JSONB DEFAULT '{}'
);

-- Only create indexes if lessons is a table (not a view — after migration 009)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lessons' AND table_type = 'BASE TABLE') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_lessons_org ON lessons(org_id)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_lessons_org_project ON lessons(org_id, project)';
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_lessons_embedding') THEN
            EXECUTE 'CREATE INDEX idx_lessons_embedding ON lessons USING hnsw (embedding vector_cosine_ops)';
        END IF;
    END IF;
END $$;
