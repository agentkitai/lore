-- Migration 008: Conversation extraction jobs (SQLite translation)
--
-- Translation notes:
--   * TIMESTAMPTZ → TEXT.

CREATE TABLE IF NOT EXISTS conversation_jobs (
    id                 TEXT PRIMARY KEY,
    org_id             TEXT NOT NULL REFERENCES orgs(id),
    status             TEXT NOT NULL DEFAULT 'accepted',
    message_count      INTEGER NOT NULL DEFAULT 0,
    messages_json      TEXT,
    user_id            TEXT,
    session_id         TEXT,
    project            TEXT,
    memory_ids         TEXT DEFAULT '[]',
    memories_extracted INTEGER DEFAULT 0,
    duplicates_skipped INTEGER DEFAULT 0,
    error              TEXT,
    processing_time_ms INTEGER DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversation_jobs_org_id ON conversation_jobs(org_id);
CREATE INDEX IF NOT EXISTS idx_conversation_jobs_status ON conversation_jobs(status);
