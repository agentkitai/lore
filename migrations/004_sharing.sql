-- Migration 004: Sharing & community tables
-- Idempotent — safe to run multiple times

CREATE TABLE IF NOT EXISTS sharing_config (
    id                      TEXT PRIMARY KEY,
    org_id                  TEXT NOT NULL REFERENCES orgs(id),
    enabled                 BOOLEAN DEFAULT FALSE,
    human_review_enabled    BOOLEAN DEFAULT FALSE,
    rate_limit_per_hour     INTEGER DEFAULT 100,
    volume_alert_threshold  INTEGER DEFAULT 1000,
    updated_at              TIMESTAMPTZ DEFAULT now(),
    UNIQUE (org_id)
);

CREATE TABLE IF NOT EXISTS agent_sharing_config (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    agent_id    TEXT NOT NULL,
    enabled     BOOLEAN DEFAULT FALSE,
    categories  JSONB DEFAULT '[]',
    updated_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (org_id, agent_id)
);

CREATE TABLE IF NOT EXISTS deny_list_rules (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    pattern     TEXT NOT NULL,
    is_regex    BOOLEAN DEFAULT FALSE,
    reason      TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deny_list_org ON deny_list_rules(org_id);

CREATE TABLE IF NOT EXISTS sharing_audit (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES orgs(id),
    event_type      TEXT NOT NULL,
    lesson_id       TEXT,
    query_text      TEXT,
    initiated_by    TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sharing_audit_org ON sharing_audit(org_id);
CREATE INDEX IF NOT EXISTS idx_sharing_audit_org_type ON sharing_audit(org_id, event_type);
CREATE INDEX IF NOT EXISTS idx_sharing_audit_org_created ON sharing_audit(org_id, created_at);

-- Add columns to lessons/memories table (idempotent, handles post-rename)
DO $$
DECLARE
    _tbl text;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'memories' AND table_type = 'BASE TABLE') THEN
        _tbl := 'memories';
    ELSIF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lessons' AND table_type = 'BASE TABLE') THEN
        _tbl := 'lessons';
    ELSE
        RETURN;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = _tbl AND column_name = 'reputation_score') THEN
        EXECUTE format('ALTER TABLE %I ADD COLUMN reputation_score INTEGER DEFAULT 0', _tbl);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = _tbl AND column_name = 'quality_signals') THEN
        EXECUTE format('ALTER TABLE %I ADD COLUMN quality_signals JSONB DEFAULT ''{}''', _tbl);
    END IF;
END $$;
