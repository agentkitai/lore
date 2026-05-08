-- Migration 024: scope column on memories (Phase 6G claude-mem parity)
--
-- The ``scope`` column is the project-vs-global discriminator that makes
-- universal lessons portable across repos while keeping repo-specific
-- captures from bleeding cross-project. Recall always applies:
--   (scope='global') OR (scope='project' AND project = :current_project)
--
-- Backfill: existing rows whose ``meta.type`` is one of the universal
-- types (lesson/preference/pattern/convention) flip to ``scope='global'``.
-- Everything else inherits the column default (``'project'``).
--
-- Mirrors migrations_sqlite/024_scope_and_kind.sql. Differences:
--   * SQLite uses json_extract(meta, '$.session_id') / '$.type' instead of
--     PG's meta->>'session_id' / meta->>'type'. Both indexes function the
--     same logical role per backend.

ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'project'
    CHECK (scope IN ('project', 'global'));

CREATE INDEX IF NOT EXISTS idx_memories_scope_project
    ON memories (scope, project, created_at);

CREATE INDEX IF NOT EXISTS idx_memories_project_session
    ON memories (project, (meta->>'session_id'), created_at);

-- Type-based backfill: universal types become global; everything else
-- stays 'project' (the column default).
UPDATE memories
SET scope = 'global'
WHERE meta->>'type' IN ('lesson', 'preference', 'pattern', 'convention');
