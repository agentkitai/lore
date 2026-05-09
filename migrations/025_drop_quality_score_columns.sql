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

-- Drop the on-access trigger that recomputed importance on read. Names
-- vary — try the variants we've used historically.
DROP TRIGGER IF EXISTS on_update_importance ON memories;
DROP TRIGGER IF EXISTS memories_recompute_importance_on_access ON memories;
DROP FUNCTION IF EXISTS update_importance_score() CASCADE;
DROP FUNCTION IF EXISTS memories_recompute_importance() CASCADE;

ALTER TABLE memories DROP COLUMN IF EXISTS importance_score;
ALTER TABLE memories DROP COLUMN IF EXISTS confidence;
