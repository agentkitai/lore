-- Migration 024: scope column on memories (Phase 6G claude-mem parity, SQLite translation)
--
-- Mirrors migrations/024_scope_and_kind.sql. Translation notes:
--   * meta->>'session_id' (PG)        → json_extract(meta, '$.session_id') (SQLite)
--   * meta->>'type' (PG)              → json_extract(meta, '$.type') (SQLite)
--   * Both indexes use the SQLite json_extract() form so they're usable
--     by the temporal/timeline queries from 6G T6 onwards.
--
-- The ``scope`` column is the project-vs-global discriminator that makes
-- universal lessons portable across repos while keeping repo-specific
-- captures from bleeding cross-project. Recall always applies:
--   (scope='global') OR (scope='project' AND project = :current_project)
--
-- Backfill: existing rows whose ``meta.type`` is one of the universal
-- types (lesson/preference/pattern/convention) flip to ``scope='global'``.
-- Everything else inherits the column default (``'project'``).

ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'project'
    CHECK (scope IN ('project', 'global'));

CREATE INDEX IF NOT EXISTS idx_memories_scope_project
    ON memories (scope, project, created_at);

CREATE INDEX IF NOT EXISTS idx_memories_project_session
    ON memories (project, json_extract(meta, '$.session_id'), created_at);

-- Type-based backfill: universal types become global; everything else
-- stays 'project' (the column default).
UPDATE memories
SET scope = 'global'
WHERE json_extract(meta, '$.type') IN ('lesson', 'preference', 'pattern', 'convention');
