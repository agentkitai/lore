-- Migration 021: Add ``fts_weight`` to retrieval_profiles (SQLite translation)
-- Mirrors migrations/021_fts_weight.sql. SQLite ALTER TABLE ADD COLUMN
-- has no IF NOT EXISTS, so re-running this migration after applying it
-- once would error. The migration runner tracks applied versions via
-- schema_migrations and never re-runs an applied version, so the missing
-- IF NOT EXISTS is fine here (consistent with 018_profile_extras.sql).

ALTER TABLE retrieval_profiles ADD COLUMN fts_weight REAL NOT NULL DEFAULT 1.0;
