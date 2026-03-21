-- Proactive recommendations (F9: Proactive Memory Recommendations)

CREATE TABLE IF NOT EXISTS recommendation_feedback (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    workspace_id TEXT,
    memory_id    TEXT NOT NULL,
    actor_id     TEXT NOT NULL,
    signal       TEXT DEFAULT 'manual',
    feedback     TEXT NOT NULL,
    context_hash TEXT,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rec_feedback_memory ON recommendation_feedback(memory_id);
CREATE INDEX IF NOT EXISTS idx_rec_feedback_actor ON recommendation_feedback(actor_id, created_at DESC);

CREATE TABLE IF NOT EXISTS recommendation_config (
    id               TEXT PRIMARY KEY,
    workspace_id     TEXT,
    agent_id         TEXT,
    aggressiveness   REAL DEFAULT 0.5,
    enabled          BOOLEAN DEFAULT true,
    max_suggestions  INTEGER DEFAULT 3,
    cooldown_minutes INTEGER DEFAULT 15,
    updated_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE(workspace_id, agent_id)
);
