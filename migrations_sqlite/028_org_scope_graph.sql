-- Migration 028: org-scope the knowledge graph (close cross-tenant leak #83).
-- SQLite translation of migrations/028_org_scope_graph.sql.
--
-- Translation notes:
--   * SQLite ADD COLUMN cannot add a NOT NULL column without a non-null DEFAULT
--     (same rule that forced DEFAULT 'private' in migration 026), and cannot
--     combine NOT NULL with a REFERENCES clause. So org_id is NOT NULL DEFAULT ''
--     with no inline FK; the app ALWAYS supplies a real org_id and a test asserts
--     no '' rows exist.
--   * The SqliteStore migration runner version-tracks each file, so plain
--     ADD COLUMN / DROP INDEX run exactly once.

-- 1. entities -------------------------------------------------------------
ALTER TABLE entities ADD COLUMN org_id TEXT NOT NULL DEFAULT '';
DROP INDEX IF EXISTS idx_entities_name;
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_org_name ON entities(org_id, name);
CREATE INDEX IF NOT EXISTS idx_entities_org_type ON entities(org_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_org_mentions ON entities(org_id, mention_count DESC);

-- 2. relationships --------------------------------------------------------
ALTER TABLE relationships ADD COLUMN org_id TEXT NOT NULL DEFAULT '';
DROP INDEX IF EXISTS idx_rel_unique_edge;
CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_org_unique_edge ON relationships(org_id, source_entity_id, target_entity_id, rel_type) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_rel_org_source ON relationships(org_id, source_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_org_target ON relationships(org_id, target_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_org_active ON relationships(org_id, source_entity_id) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_rel_org_type ON relationships(org_id, rel_type);

-- 3. entity_mentions ------------------------------------------------------
ALTER TABLE entity_mentions ADD COLUMN org_id TEXT NOT NULL DEFAULT '';
DROP INDEX IF EXISTS idx_em_unique;
CREATE UNIQUE INDEX IF NOT EXISTS idx_em_org_unique ON entity_mentions(org_id, entity_id, memory_id);
CREATE INDEX IF NOT EXISTS idx_em_org_entity ON entity_mentions(org_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_em_org_memory ON entity_mentions(org_id, memory_id);
