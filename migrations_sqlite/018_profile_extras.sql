-- Migration 018: Profile extras (SQLite translation)
-- Columns the route code references but the original 013 migration omitted.
--
-- Translation notes:
--   * BOOLEAN → INTEGER. SQLite doesn't support multi-column ADD in one ALTER,
--     so we issue four separate ALTERs.

ALTER TABLE retrieval_profiles ADD COLUMN k             INTEGER;
ALTER TABLE retrieval_profiles ADD COLUMN threshold     REAL;
ALTER TABLE retrieval_profiles ADD COLUMN rerank        INTEGER DEFAULT 0;
ALTER TABLE retrieval_profiles ADD COLUMN include_graph INTEGER DEFAULT 1;
