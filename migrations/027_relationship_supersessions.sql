-- Migration 027: relationship (fact) supersession — bi-temporal facts (#67).
--
-- Lore stores "facts" (subject–predicate–object assertions) as graph
-- relationships: an edge entity[subject] --predicate--> entity[object]. Edges
-- already carry a validity window (valid_from / valid_until, migration 007) and
-- query_relationships(at_time=...) already answers "which edges were valid at
-- T". What was missing for governance/audit is supersede-NOT-delete:
--   * superseded_by — points an expired edge at the newer edge that replaced
--     it (correction lineage), mirroring memory_supersessions.superseded_by.
--   * relationship_supersessions — an append-only audit log of corrections, a
--     direct mirror of the memory_supersessions table (migration 023).
--
-- "Valid at T" is answered by the edge's valid_from/valid_until window (set
-- when an edge is superseded or when re-extraction drops it). The supersession
-- log adds the why / what-replaced-what lineage for compliance reports.
--
-- Mirrors migrations_sqlite/027_relationship_supersessions.sql.

ALTER TABLE relationships ADD COLUMN IF NOT EXISTS superseded_by TEXT;

CREATE INDEX IF NOT EXISTS idx_rel_superseded_by
    ON relationships (superseded_by);

CREATE TABLE IF NOT EXISTS relationship_supersessions (
    id              BIGSERIAL PRIMARY KEY,
    relationship_id TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    superseded_by   TEXT REFERENCES relationships(id) ON DELETE SET NULL,
    reason          TEXT,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent           TEXT NOT NULL DEFAULT 'auto'
);

CREATE INDEX IF NOT EXISTS idx_relationship_supersessions_rel_id_ts
    ON relationship_supersessions (relationship_id, ts DESC);
