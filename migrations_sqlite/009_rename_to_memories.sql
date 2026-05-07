-- Migration 009: Rename lessons → memories, problem → content, resolution → context
-- (SQLite translation)
--
-- Translation notes:
--   * SQLite supports ALTER TABLE … RENAME TO and RENAME COLUMN since 3.25.
--   * The DO $$ guards in the PG version are unnecessary because the SQLite
--     migration runs exactly once (schema_migrations tracker).
--   * Index renames in SQLite require DROP + CREATE; the partial-index
--     equivalents from earlier migrations were named after the lessons-era,
--     so we drop the old idx_lessons_* and re-create as idx_memories_*.
--   * NOTE: SQLite views are read-only — they cannot have INSERT/UPDATE/DELETE
--     RULEs like Postgres. The route layer always targets `memories` directly
--     post-Phase 1H/L, so a read-only view is sufficient for SELECT-style
--     backward compatibility with deprecated /v1/lessons callers. Writers
--     must target memories directly.

-- Step 1: rename the table (lessons → memories). Indexes that reference the
-- old name automatically follow the rename in SQLite.
ALTER TABLE lessons RENAME TO memories;

-- Step 2: column renames. Drop the legacy empty `context` first so we can
-- rename `resolution` → `context` cleanly.
ALTER TABLE memories RENAME COLUMN problem TO content;
ALTER TABLE memories DROP COLUMN context;
ALTER TABLE memories RENAME COLUMN resolution TO context;

-- Step 3: rename the lessons-era indexes to memories-era names. SQLite
-- autocarries indexes through ALTER TABLE RENAME TO, but the index *names*
-- still contain "lessons", so we recreate them under the new naming scheme.
DROP INDEX IF EXISTS idx_lessons_org;
DROP INDEX IF EXISTS idx_lessons_org_project;
DROP INDEX IF EXISTS idx_lessons_importance;
DROP INDEX IF EXISTS idx_lessons_last_accessed;

CREATE INDEX IF NOT EXISTS idx_memories_org           ON memories(org_id);
CREATE INDEX IF NOT EXISTS idx_memories_org_project   ON memories(org_id, project);
CREATE INDEX IF NOT EXISTS idx_memories_importance    ON memories(importance_score);
CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories(last_accessed_at);

-- Step 4: backward-compat view for deprecated /v1/lessons readers.
-- NOTE: SQLite view is read-only; writers must target memories directly.
-- Embedding column intentionally omitted — vector storage moves to the
-- memory_vectors vec0 virtual table in Phase 3B.
DROP VIEW IF EXISTS lessons;
CREATE VIEW IF NOT EXISTS lessons AS
    SELECT id, org_id,
           content    AS problem,
           context    AS resolution,
           NULL       AS context,
           tags, confidence, source, project,
           created_at, updated_at, expires_at,
           upvotes, downvotes, meta,
           importance_score, access_count, last_accessed_at,
           reputation_score, quality_signals
    FROM memories;
