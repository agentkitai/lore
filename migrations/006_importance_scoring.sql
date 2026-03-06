-- F5: Importance Scoring + Adaptive Decay
-- Adds importance scoring fields and indexes to lessons table.

ALTER TABLE lessons ADD COLUMN importance_score REAL DEFAULT 1.0;
ALTER TABLE lessons ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE lessons ADD COLUMN last_accessed_at TIMESTAMPTZ;

CREATE INDEX idx_lessons_importance ON lessons(importance_score);
CREATE INDEX idx_lessons_last_accessed ON lessons(last_accessed_at);
