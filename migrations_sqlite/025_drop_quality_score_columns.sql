-- Migration 025 (SQLite): drop importance_score and confidence columns.
-- See migrations/025_drop_quality_score_columns.sql for rationale.
--
-- The memories table got its current name in 009_rename_to_memories.sql
-- (formerly ``lessons``). The migration runner only applies once per file,
-- so we target ``memories`` directly.
--
-- SQLite 3.35.0+ (Mar 2021) supports native ``ALTER TABLE DROP COLUMN``
-- but refuses to drop a column referenced by indexes or views. Drop the
-- ``lessons`` compatibility view (recreated below without the dropped
-- columns) and the per-column indexes before the ALTERs.

DROP VIEW IF EXISTS lessons;
DROP INDEX IF EXISTS idx_lessons_importance;
DROP INDEX IF EXISTS idx_memories_importance;
DROP INDEX IF EXISTS idx_memories_confidence;

ALTER TABLE memories DROP COLUMN importance_score;
ALTER TABLE memories DROP COLUMN confidence;

-- Recreate the read-only lessons view minus the dropped columns. Keeps the
-- legacy ``problem``/``resolution``/``context`` shape for any old callers
-- that still hit the view.
CREATE VIEW IF NOT EXISTS lessons AS
    SELECT id, org_id,
           content    AS problem,
           context    AS resolution,
           NULL       AS context,
           tags, source, project,
           created_at, updated_at, expires_at,
           upvotes, downvotes, meta,
           access_count, last_accessed_at,
           reputation_score, quality_signals
    FROM memories;
