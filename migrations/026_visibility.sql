-- Migration 026: per-user visibility (private/shared) + promote provenance.
--
-- Lets a single org/team safely share ONE memory pool. Each memory is owned
-- by the user who wrote it (the existing nullable ``user_id`` column, added in
-- 005 but never populated until now) and is ``private`` by default. Recall
-- returns the requesting user's own private rows UNION the team's shared rows:
--   (visibility = 'shared' OR user_id = :requesting_user)
-- That predicate is only applied when a requesting user id is known; with no
-- identity configured (solo/embedded mode, org 'solo') recall omits it
-- entirely, so single-user behavior is byte-identical to before this change.
--
-- ``promote`` flips a row private→shared and records who/when. Demote is the
-- reverse (clears the provenance).
--
-- Backfill: every pre-existing row predates per-user scoping and is currently
-- visible to everyone in the org. Flipping them to 'shared' preserves that —
-- nothing that's visible today disappears. New captures default 'private'.
--
-- Mirrors migrations_sqlite/026_visibility.sql.

ALTER TABLE memories ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'
    CHECK (visibility IN ('private', 'shared'));
ALTER TABLE memories ADD COLUMN promoted_by TEXT;          -- users.id, nullable
ALTER TABLE memories ADD COLUMN promoted_at TIMESTAMPTZ;   -- nullable

-- Speeds the shared-pool scan and the owner-equality branch of the recall
-- predicate (the vector/FTS scans are still the dominant cost).
CREATE INDEX IF NOT EXISTS idx_memories_visibility_user
    ON memories (org_id, visibility, user_id);

-- Pre-existing rows were visible org-wide; keep them so by sharing them.
UPDATE memories SET visibility = 'shared';
