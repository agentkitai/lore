-- Migration 025: drop importance_score and confidence columns from memories.
--
-- Both columns carried mechanical defaults that no caller ever overrode:
--   * importance_score = 1.0 (schema default; INSERT statements never
--     populated it before PR #295038e; the recall-side multiplier was a
--     no-op since every row had identical 1.0).
--   * confidence = 0.5 hardcoded for observations (services/observations.py),
--     uncalibrated user-supplied number for lessons; never used in ranking,
--     only echoed back in API responses.
--
-- Lore's recall stack already does cosine-similarity + ts_rank/FTS hybrid
-- scoring with optional fts_weight per profile. The dropped columns added
-- noise (UI rendered a flat "Importance 100% / Confidence 50%" on every
-- memory) without changing any actual ranking on existing data.
--
-- Industry consensus across mem0 / Letta / LangMem / Zep-graphiti / Cognee
-- is to skip per-memory quality scores entirely.
--
-- Out of scope (DO NOT confuse with these): graph-table per-relationship
-- ``confidence`` and ``weight`` columns live in entities/mentions/
-- relationships and are unaffected.

-- Indexes first — keeping them after a column drop would leave dangling
-- references in some DB engines.
DROP INDEX IF EXISTS idx_lessons_importance;
DROP INDEX IF EXISTS idx_memories_importance;
DROP INDEX IF EXISTS idx_memories_confidence;

-- The legacy ``lessons`` view (created in 009_rename_to_memories.sql)
-- references both columns; CASCADE drops it together with its rewrite
-- rules so the ALTER TABLE below succeeds. We recreate the view at the
-- end without the dropped columns.
DROP VIEW IF EXISTS lessons CASCADE;

-- Drop the on-access trigger that recomputed importance on read. Names
-- vary — try the variants we've used historically.
DROP TRIGGER IF EXISTS on_update_importance ON memories;
DROP TRIGGER IF EXISTS memories_recompute_importance_on_access ON memories;
DROP FUNCTION IF EXISTS update_importance_score() CASCADE;
DROP FUNCTION IF EXISTS memories_recompute_importance() CASCADE;

ALTER TABLE memories DROP COLUMN IF EXISTS importance_score;
ALTER TABLE memories DROP COLUMN IF EXISTS confidence;

-- Recreate the read/write lessons compatibility view (minus the dropped
-- columns) so any legacy /v1/lessons callers that still query it keep
-- working. Mirrors the original CREATE VIEW + rules in migration 009.
CREATE OR REPLACE VIEW lessons AS
    SELECT id, org_id,
           content AS problem,
           context AS resolution,
           NULL::text AS context,
           tags, source, project, embedding,
           created_at, updated_at, expires_at,
           upvotes, downvotes, meta,
           access_count, last_accessed_at,
           reputation_score, quality_signals
    FROM memories;

CREATE OR REPLACE RULE lessons_insert AS ON INSERT TO lessons
DO INSTEAD
    INSERT INTO memories (id, org_id, content, context, tags, source, project,
                          embedding, created_at, updated_at, expires_at, upvotes, downvotes, meta)
    VALUES (NEW.id, NEW.org_id, NEW.problem, NEW.resolution, NEW.tags,
            NEW.source, NEW.project, NEW.embedding, NEW.created_at, NEW.updated_at,
            NEW.expires_at, NEW.upvotes, NEW.downvotes, NEW.meta);

CREATE OR REPLACE RULE lessons_update AS ON UPDATE TO lessons
DO INSTEAD
    UPDATE memories SET
        content = NEW.problem,
        context = NEW.resolution,
        tags = NEW.tags,
        source = NEW.source,
        project = NEW.project,
        embedding = NEW.embedding,
        updated_at = NEW.updated_at,
        expires_at = NEW.expires_at,
        upvotes = NEW.upvotes,
        downvotes = NEW.downvotes,
        meta = NEW.meta
    WHERE id = OLD.id;

CREATE OR REPLACE RULE lessons_delete AS ON DELETE TO lessons
DO INSTEAD
    DELETE FROM memories WHERE id = OLD.id;
