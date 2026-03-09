-- Retrieval analytics: track every retrieve call for measuring effectiveness
CREATE TABLE IF NOT EXISTS retrieval_events (
    id          BIGSERIAL PRIMARY KEY,
    org_id      TEXT NOT NULL,
    query       TEXT NOT NULL,
    results_count INTEGER NOT NULL DEFAULT 0,
    scores      JSONB DEFAULT '[]'::jsonb,
    memory_ids  JSONB DEFAULT '[]'::jsonb,
    avg_score   DOUBLE PRECISION,
    max_score   DOUBLE PRECISION,
    min_score_threshold DOUBLE PRECISION,
    query_time_ms DOUBLE PRECISION,
    project     TEXT,
    format      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_retrieval_events_org_created
    ON retrieval_events (org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_retrieval_events_created
    ON retrieval_events (created_at DESC);
