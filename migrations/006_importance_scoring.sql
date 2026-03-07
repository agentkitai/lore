-- F5: Importance Scoring + Adaptive Decay
-- Adds importance scoring fields and indexes to lessons table.

ALTER TABLE lessons ADD COLUMN IF NOT EXISTS importance_score REAL DEFAULT 1.0;
ALTER TABLE lessons ADD COLUMN IF NOT EXISTS access_count INTEGER DEFAULT 0;
ALTER TABLE lessons ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_lessons_importance ON lessons(importance_score);
CREATE INDEX IF NOT EXISTS idx_lessons_last_accessed ON lessons(last_accessed_at);
