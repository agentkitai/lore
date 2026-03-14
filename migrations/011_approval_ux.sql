-- E6: Approval UX for Discovered Connections (Trust Layer)
-- Adds status column to relationships and rejected_patterns table.

-- Add status column to relationships (default 'approved' for backward compat)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'relationships' AND column_name = 'status'
    ) THEN
        ALTER TABLE relationships ADD COLUMN status TEXT DEFAULT 'approved';
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_rel_status ON relationships(status);
CREATE INDEX IF NOT EXISTS idx_rel_pending ON relationships(status) WHERE status = 'pending';

-- Rejected patterns table — tracks what not to re-suggest
CREATE TABLE IF NOT EXISTS rejected_patterns (
    id              TEXT PRIMARY KEY,
    source_name     TEXT NOT NULL,
    target_name     TEXT NOT NULL,
    rel_type        TEXT NOT NULL,
    rejected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_memory_id TEXT,
    reason          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rp_unique
    ON rejected_patterns(source_name, target_name, rel_type);
