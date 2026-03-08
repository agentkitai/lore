-- F5: Importance Scoring + Adaptive Decay
-- Adds importance scoring fields and indexes to lessons/memories table.

DO $$
DECLARE
    _tbl text;
BEGIN
    -- Use 'memories' if it exists (post-migration 009), otherwise 'lessons'
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'memories' AND table_type = 'BASE TABLE') THEN
        _tbl := 'memories';
    ELSIF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lessons' AND table_type = 'BASE TABLE') THEN
        _tbl := 'lessons';
    ELSE
        RETURN;
    END IF;

    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS importance_score REAL DEFAULT 1.0', _tbl);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS access_count INTEGER DEFAULT 0', _tbl);
    EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ', _tbl);

    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_lessons_importance') THEN
        EXECUTE format('CREATE INDEX idx_lessons_importance ON %I(importance_score)', _tbl);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_lessons_last_accessed') THEN
        EXECUTE format('CREATE INDEX idx_lessons_last_accessed ON %I(last_accessed_at)', _tbl);
    END IF;
END $$;
