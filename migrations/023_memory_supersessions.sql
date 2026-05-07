-- Migration 023: memory_supersessions (Phase 6F temporal reasoning)
--
-- Append-only audit log linking a memory to (optionally) the memory that
-- supersedes it. A memory is considered "superseded" iff the LATEST row
-- for that ``memory_id`` (by ``ts``) has ``superseded_by IS NOT NULL``.
-- A row with ``superseded_by IS NULL`` explicitly un-supersedes (keeps
-- the audit trail; flips the current state).
--
-- Reads always go through "latest row per memory" — see
-- ``persistence.protocol.is_superseded`` / ``are_superseded`` for the
-- canonical query shape and the ``(memory_id, ts DESC)`` index that
-- makes it cheap.

CREATE TABLE IF NOT EXISTS memory_supersessions (
    id              BIGSERIAL PRIMARY KEY,
    memory_id       TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    superseded_by   TEXT REFERENCES memories(id) ON DELETE SET NULL,
    reason          TEXT,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent           TEXT NOT NULL DEFAULT 'auto'
);

CREATE INDEX IF NOT EXISTS idx_memory_supersessions_memory_id_ts
    ON memory_supersessions (memory_id, ts DESC);
