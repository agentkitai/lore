-- Migration 019: NULL-safe unique on recommendation_config (SQLite translation)
--
-- Translation notes:
--   * SQLite supports expression indexes natively, so the COALESCE expression
--     translates verbatim.
--   * The PG migration drops the original named UNIQUE constraint via
--     ALTER TABLE ... DROP CONSTRAINT. SQLite has no DROP CONSTRAINT for
--     table-level UNIQUEs that were declared inline. To drop the constraint
--     we'd have to rebuild the table — which would violate the "no clever
--     deviation" rule. The expression index below provides the actual
--     NULL-safe semantics the route layer relies on (the original UNIQUE
--     remains as a benign no-op because real (workspace_id, agent_id) pairs
--     are never NULL when both are non-NULL anyway, and the upsert flow
--     uses the expression index as its conflict target).

CREATE UNIQUE INDEX IF NOT EXISTS recommendation_config_scope_uq
    ON recommendation_config (
        COALESCE(workspace_id, '__null__'),
        COALESCE(agent_id,     '__null__')
    );
