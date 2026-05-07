-- Migration 011: Approval UX for Discovered Connections (SQLite translation)
-- Adds status column to relationships and the rejected_patterns table.
--
-- Translation notes:
--   * DO $$ block → straight ALTER TABLE (migration runs once; no need to
--     introspect schema).
--   * TIMESTAMPTZ → TEXT.

ALTER TABLE relationships ADD COLUMN status TEXT DEFAULT 'approved';

CREATE INDEX IF NOT EXISTS idx_rel_status ON relationships(status);
CREATE INDEX IF NOT EXISTS idx_rel_pending ON relationships(status) WHERE status = 'pending';

-- Rejected patterns: tracks what not to re-suggest.
CREATE TABLE IF NOT EXISTS rejected_patterns (
    id               TEXT PRIMARY KEY,
    source_name      TEXT NOT NULL,
    target_name      TEXT NOT NULL,
    rel_type         TEXT NOT NULL,
    rejected_at      TEXT NOT NULL DEFAULT (datetime('now')),
    source_memory_id TEXT,
    reason           TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rp_unique
    ON rejected_patterns(source_name, target_name, rel_type);
