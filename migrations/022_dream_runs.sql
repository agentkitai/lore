-- Migration 022: Dream runs (Phase 6E memory consolidation)
--
-- Tracks per-org runs of the LLM-driven consolidation pipeline. One row
-- per ``lore dream`` invocation. Eligibility (24h elapsed AND ≥5 sessions
-- since last completed run) is computed by the service layer against
-- this table; the trigger hook reads ``last_run_at`` from here.
--
-- Status lifecycle: 'running' (set on insert) → 'completed' | 'failed'.
--   * summary: JSONB blob with phase markers + counts emitted by the
--     subagent ({"phase_1": ..., "phase_3_merged": N, ...}).
--   * error:   populated only on 'failed'; null otherwise.

CREATE TABLE IF NOT EXISTS dream_runs (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running',
    summary      JSONB,
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_dream_runs_org_id      ON dream_runs(org_id);
CREATE INDEX IF NOT EXISTS idx_dream_runs_started_at  ON dream_runs(started_at DESC);
