-- SLO definitions and alerts (F3: SLO Dashboard)

CREATE TABLE IF NOT EXISTS slo_definitions (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    name            TEXT NOT NULL,
    metric          TEXT NOT NULL,     -- p50_latency, p95_latency, p99_latency, hit_rate
    operator        TEXT NOT NULL,     -- lt, gt
    threshold       DOUBLE PRECISION NOT NULL,
    window_minutes  INTEGER NOT NULL DEFAULT 60,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    alert_channels  JSONB DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS slo_alerts (
    id              BIGSERIAL PRIMARY KEY,
    org_id          TEXT NOT NULL,
    slo_id          TEXT NOT NULL REFERENCES slo_definitions(id) ON DELETE CASCADE,
    metric_value    DOUBLE PRECISION NOT NULL,
    threshold       DOUBLE PRECISION NOT NULL,
    status          TEXT NOT NULL,     -- firing, resolved
    dispatched_to   JSONB DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_slo_alerts_slo_id ON slo_alerts(slo_id);
CREATE INDEX IF NOT EXISTS idx_slo_alerts_created ON slo_alerts(created_at DESC);
