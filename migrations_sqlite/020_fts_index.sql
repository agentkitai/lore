-- Migration 020: Full-text search index (Phase 6C hybrid retrieval, SQLite)
--
-- Uses FTS5 with porter+unicode61 tokenization. ``external content`` mode
-- (content='memories') means the FTS5 table doesn't duplicate the text — it
-- references rows in ``memories`` by rowid and reads content/context on
-- demand. Triggers below keep the FTS index aligned with INSERT/UPDATE/DELETE
-- against ``memories``.
--
-- Translation notes:
--   * PG ``GIN(to_tsvector('english', ...))`` → SQLite FTS5 virtual table.
--   * ``tokenize='porter unicode61'`` matches PG's ``english`` dictionary
--     (Porter stemmer + Unicode-aware token splitting + diacritic folding).
--   * The backfill at the bottom seeds rows that already exist before this
--     migration ran. ``INSERT OR IGNORE`` keeps it safe to re-run.

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    context,
    tokenize='porter unicode61',
    content='memories',
    content_rowid='rowid'
);

-- Sync triggers — keep memories_fts in lockstep with memories.
-- AFTER INSERT: index the new row.
CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, context)
    VALUES (new.rowid, new.content, COALESCE(new.context, ''));
END;

-- AFTER DELETE: remove the row from the FTS index. The 'delete' command
-- form is the FTS5 way to delete external-content rows.
CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, context)
    VALUES ('delete', old.rowid, old.content, COALESCE(old.context, ''));
END;

-- AFTER UPDATE: delete-then-insert. Required because FTS5 external content
-- doesn't auto-detect content changes.
CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, context)
    VALUES ('delete', old.rowid, old.content, COALESCE(old.context, ''));
    INSERT INTO memories_fts(rowid, content, context)
    VALUES (new.rowid, new.content, COALESCE(new.context, ''));
END;

-- Backfill any pre-existing rows. INSERT OR IGNORE is safe on re-run because
-- FTS5 external-content tables tolerate duplicate rowid inserts (the trigger
-- path won't fire again because the row already exists post-migration).
INSERT INTO memories_fts(rowid, content, context)
    SELECT rowid, content, COALESCE(context, '') FROM memories
    WHERE rowid NOT IN (SELECT rowid FROM memories_fts);
