-- Migration 012: SLO definitions and alerts (F3: SLO Dashboard) (SQLite translation)
--
-- Translation notes:
--   * BIGSERIAL → INTEGER PRIMARY KEY AUTOINCREMENT.
--   * BOOLEAN → INTEGER (1 = true, 0 = false).
--   * JSONB → TEXT, TIMESTAMPTZ → TEXT, DOUBLE PRECISION → REAL.

CREATE TABLE IF NOT EXISTS slo_definitions (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    name            TEXT NOT NULL,
    metric          TEXT NOT NULL,     -- p50_latency, p95_latency, p99_latency, hit_rate
    operator        TEXT NOT NULL,     -- lt, gt
    threshold       REAL NOT NULL,
    window_minutes  INTEGER NOT NULL DEFAULT 60,
    enabled         INTEGER NOT NULL DEFAULT 1,
    alert_channels  TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS slo_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL,
    slo_id          TEXT NOT NULL REFERENCES slo_definitions(id) ON DELETE CASCADE,
    metric_value    REAL NOT NULL,
    threshold       REAL NOT NULL,
    status          TEXT NOT NULL,     -- firing, resolved
    dispatched_to   TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_slo_alerts_slo_id ON slo_alerts(slo_id);
CREATE INDEX IF NOT EXISTS idx_slo_alerts_created ON slo_alerts(created_at DESC);
