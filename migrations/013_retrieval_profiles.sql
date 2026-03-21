-- Retrieval profiles (F4: Adaptive Retrieval Profiles)

CREATE TABLE IF NOT EXISTS retrieval_profiles (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    name            TEXT NOT NULL,
    semantic_weight REAL NOT NULL DEFAULT 1.0,
    graph_weight    REAL NOT NULL DEFAULT 1.0,
    recency_bias    REAL NOT NULL DEFAULT 30.0,
    tier_filters    TEXT[] DEFAULT NULL,
    min_score       REAL NOT NULL DEFAULT 0.3,
    max_results     INT NOT NULL DEFAULT 10,
    is_preset       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(org_id, name)
);

INSERT INTO retrieval_profiles (id, org_id, name, semantic_weight, graph_weight, recency_bias, tier_filters, min_score, max_results, is_preset)
VALUES
  ('preset-coding', '__global__', 'coding', 1.0, 0.5, 7.0, ARRAY['short','long'], 0.4, 10, TRUE),
  ('preset-incident', '__global__', 'incident-response', 0.8, 1.5, 1.0, NULL, 0.2, 20, TRUE),
  ('preset-research', '__global__', 'research', 1.2, 1.0, 90.0, ARRAY['long'], 0.3, 15, TRUE)
ON CONFLICT DO NOTHING;

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS default_profile_id TEXT;
