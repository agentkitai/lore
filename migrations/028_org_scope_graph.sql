-- Migration 028: org-scope the knowledge graph (close cross-tenant leak #83).
-- entities, relationships and entity_mentions were GLOBAL (migration 007) — no
-- org_id, so org A's graph queries returned org B's entities and same-named
-- entities were merged across orgs. No running deployments → org_id NOT NULL
-- with NO backfill. org_id is denormalized onto all three tables (stamped from
-- the source memory at write) so every graph read filters on a single indexed
-- column. Mirrors migrations_sqlite/028_org_scope_graph.sql.

-- 1. entities -------------------------------------------------------------
ALTER TABLE entities ADD COLUMN org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE;
-- Entity NAME is unique PER ORG now, not globally (two orgs may both have 'Acme').
DROP INDEX IF EXISTS idx_entities_name;
CREATE UNIQUE INDEX idx_entities_org_name ON entities(org_id, name);
CREATE INDEX idx_entities_org_type ON entities(org_id, entity_type);
CREATE INDEX idx_entities_org_mentions ON entities(org_id, mention_count DESC);

-- 2. relationships --------------------------------------------------------
ALTER TABLE relationships ADD COLUMN org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE;
-- Active-edge uniqueness is now per-org.
DROP INDEX IF EXISTS idx_rel_unique_edge;
CREATE UNIQUE INDEX idx_rel_org_unique_edge ON relationships(org_id, source_entity_id, target_entity_id, rel_type) WHERE valid_until IS NULL;
CREATE INDEX idx_rel_org_source ON relationships(org_id, source_entity_id);
CREATE INDEX idx_rel_org_target ON relationships(org_id, target_entity_id);
CREATE INDEX idx_rel_org_active ON relationships(org_id, source_entity_id) WHERE valid_until IS NULL;
CREATE INDEX idx_rel_org_type ON relationships(org_id, rel_type);

-- 3. entity_mentions ------------------------------------------------------
ALTER TABLE entity_mentions ADD COLUMN org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE;
DROP INDEX IF EXISTS idx_em_unique;
CREATE UNIQUE INDEX idx_em_org_unique ON entity_mentions(org_id, entity_id, memory_id);
CREATE INDEX idx_em_org_entity ON entity_mentions(org_id, entity_id);
CREATE INDEX idx_em_org_memory ON entity_mentions(org_id, memory_id);

-- relationship_supersessions (migration 027) gets NO org_id column; it is keyed
-- by relationship_id and scoped at read time via
--   JOIN relationships r ON r.id = rs.relationship_id WHERE r.org_id = $1.
