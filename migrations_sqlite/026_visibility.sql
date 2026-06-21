-- Migration 026: per-user visibility (private/shared) + promote provenance.
-- SQLite translation of migrations/026_visibility.sql.
--
-- Translation notes:
--   * TIMESTAMPTZ (PG) -> TEXT (SQLite stores timestamps as ISO8601 strings,
--     same as created_at/expires_at on this table).
--   * Everything else is identical DDL.
--
-- See the Postgres mirror for the full rationale. Summary: each memory is
-- owned by ``user_id`` and defaults to 'private'; recall returns the
-- requesting user's own private rows UNION the team's shared rows, applying
--   (visibility = 'shared' OR user_id = :requesting_user)
-- only when a requesting user id is known (solo/embedded mode omits it, so
-- single-user behavior is unchanged). ``promote`` flips private->shared and
-- records who/when. Pre-existing rows backfill to 'shared' to stay visible.

ALTER TABLE memories ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'
    CHECK (visibility IN ('private', 'shared'));
ALTER TABLE memories ADD COLUMN promoted_by TEXT;   -- users.id, nullable
ALTER TABLE memories ADD COLUMN promoted_at TEXT;   -- ISO8601, nullable

CREATE INDEX IF NOT EXISTS idx_memories_visibility_user
    ON memories (org_id, visibility, user_id);

-- Pre-existing rows were visible org-wide; keep them so by sharing them.
UPDATE memories SET visibility = 'shared';
