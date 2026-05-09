-- Migration 025 (SQLite): drop importance_score and confidence columns.
-- See migrations/025_drop_quality_score_columns.sql for rationale.
--
-- The memories table got its current name in 009_rename_to_memories.sql
-- (formerly ``lessons``). The migration runner only applies once per file,
-- so we target ``memories`` directly.
--
-- SQLite 3.35.0+ (Mar 2021) supports native ``ALTER TABLE DROP COLUMN``
-- but refuses to drop a column that is referenced by an index. Drop the
-- indexes first.

DROP INDEX IF EXISTS idx_lessons_importance;
DROP INDEX IF EXISTS idx_memories_importance;
DROP INDEX IF EXISTS idx_memories_confidence;

ALTER TABLE memories DROP COLUMN importance_score;
ALTER TABLE memories DROP COLUMN confidence;
