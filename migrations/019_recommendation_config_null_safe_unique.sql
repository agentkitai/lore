-- Replace NULL-incompatible UNIQUE constraint on recommendation_config with a
-- COALESCE-based expression index so that (NULL, NULL) counts as one global row.
--
-- The btree UNIQUE constraint treats NULL != NULL (standard SQL), which means
-- ON CONFLICT (workspace_id, agent_id) never fires when both are NULL.
-- The COALESCE sentinel '__null__' is safe because it cannot appear as a real
-- workspace_id or agent_id (those are prefixed IDs like ws_<ULID>).

ALTER TABLE recommendation_config
    DROP CONSTRAINT IF EXISTS recommendation_config_workspace_id_agent_id_key;

CREATE UNIQUE INDEX IF NOT EXISTS recommendation_config_scope_uq
    ON recommendation_config (
        COALESCE(workspace_id, '__null__'),
        COALESCE(agent_id,     '__null__')
    );
