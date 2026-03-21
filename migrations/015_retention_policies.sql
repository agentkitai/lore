-- Retention policies and restore drills (F6: Policy-Based Retention)

CREATE TABLE IF NOT EXISTS retention_policies (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,
    name                TEXT NOT NULL,
    retention_window    JSONB NOT NULL DEFAULT '{"working": 3600, "short": 604800, "long": null}',
    snapshot_schedule   TEXT,
    encryption_required BOOLEAN DEFAULT FALSE,
    max_snapshots       INT DEFAULT 50,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE(org_id, name)
);

CREATE TABLE IF NOT EXISTS snapshot_metadata (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    policy_id       TEXT REFERENCES retention_policies(id),
    name            TEXT NOT NULL,
    path            TEXT NOT NULL,
    size_bytes      BIGINT,
    memory_count    INT,
    encrypted       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS restore_drill_results (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL,
    snapshot_id       TEXT REFERENCES snapshot_metadata(id),
    snapshot_name     TEXT NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL,
    completed_at      TIMESTAMPTZ,
    recovery_time_ms  BIGINT,
    memories_restored INT,
    status            TEXT DEFAULT 'running',
    error             TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);
