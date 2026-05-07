-- Migration 013: Retrieval profiles (SQLite translation)
--
-- Translation notes:
--   * TEXT[] arrays → TEXT (JSON-encoded array, queried via json_each).
--   * BOOLEAN → INTEGER, TIMESTAMPTZ → TEXT.
--   * The PG migration uses `ON CONFLICT DO NOTHING` with an implicit
--     conflict target. SQLite requires a real UNIQUE/PK target for the
--     ON CONFLICT clause to bind, but a bare `ON CONFLICT DO NOTHING` is
--     also valid and matches any uniqueness violation. The UNIQUE(org_id,
--     name) constraint below provides the matching target either way.

CREATE TABLE IF NOT EXISTS retrieval_profiles (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    name            TEXT NOT NULL,
    semantic_weight REAL NOT NULL DEFAULT 1.0,
    graph_weight    REAL NOT NULL DEFAULT 1.0,
    recency_bias    REAL NOT NULL DEFAULT 30.0,
    tier_filters    TEXT DEFAULT NULL,        -- JSON array of tier names, or NULL
    min_score       REAL NOT NULL DEFAULT 0.3,
    max_results     INTEGER NOT NULL DEFAULT 10,
    is_preset       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(org_id, name)
);

INSERT INTO retrieval_profiles
    (id, org_id, name, semantic_weight, graph_weight, recency_bias,
     tier_filters, min_score, max_results, is_preset)
VALUES
    ('preset-coding',   '__global__', 'coding',            1.0, 0.5,  7.0, '["short","long"]', 0.4, 10, 1),
    ('preset-incident', '__global__', 'incident-response', 0.8, 1.5,  1.0, NULL,                0.2, 20, 1),
    ('preset-research', '__global__', 'research',          1.2, 1.0, 90.0, '["long"]',          0.3, 15, 1)
ON CONFLICT DO NOTHING;

ALTER TABLE api_keys ADD COLUMN default_profile_id TEXT;
