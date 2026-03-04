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

CREATE TABLE IF NOT EXISTS memories (
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

CREATE INDEX IF NOT EXISTS idx_memories_org ON memories(org_id);
CREATE INDEX IF NOT EXISTS idx_memories_org_project ON memories(org_id, project);

-- ivfflat index requires data to exist for training; create only if not exists
-- Note: ivfflat index creation will be deferred in production until sufficient data exists
-- For now we use exact search (no index) which is fine for < 100k memories
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_embedding') THEN
        -- Use HNSW instead of ivfflat — works on empty tables
        CREATE INDEX idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);
    END IF;
END $$;
