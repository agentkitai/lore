-- Review decisions audit trail (F5: Graph Approval Inbox)

CREATE TABLE IF NOT EXISTS review_decisions (
    id              TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('approve', 'reject')),
    reviewer_id     TEXT,
    notes           TEXT,
    decided_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_review_decisions_rel ON review_decisions(relationship_id);
CREATE INDEX IF NOT EXISTS idx_review_decisions_time ON review_decisions(decided_at DESC);
