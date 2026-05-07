-- Migration 006: Importance Scoring + Adaptive Decay (SQLite translation)
-- Adds importance scoring fields and indexes to the lessons table (renamed to
-- memories in 009; columns and indexes travel with the rename).
--
-- Translation notes:
--   * DO $$ block + format() loop → straight ALTER + CREATE INDEX. The SQLite
--     migration runs exactly once thanks to the schema_migrations tracker, so
--     no introspection is needed.
--   * TIMESTAMPTZ → TEXT.

ALTER TABLE lessons ADD COLUMN importance_score REAL DEFAULT 1.0;
ALTER TABLE lessons ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE lessons ADD COLUMN last_accessed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_lessons_importance ON lessons(importance_score);
CREATE INDEX IF NOT EXISTS idx_lessons_last_accessed ON lessons(last_accessed_at);
