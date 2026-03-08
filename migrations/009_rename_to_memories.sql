-- Migration 009: Rename lessons → memories, problem → content, resolution → context
-- Idempotent — safe to run multiple times

-- Step 1: Rename the table
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lessons') THEN
        ALTER TABLE lessons RENAME TO memories;
    END IF;
END $$;

-- Step 2: Rename columns
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'memories' AND column_name = 'problem'
    ) THEN
        ALTER TABLE memories RENAME COLUMN problem TO content;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'memories' AND column_name = 'resolution'
    ) THEN
        ALTER TABLE memories RENAME COLUMN resolution TO context;
    END IF;
END $$;

-- Step 3: Rename indexes to reflect new table name
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_lessons_org') THEN
        ALTER INDEX idx_lessons_org RENAME TO idx_memories_org;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_lessons_org_project') THEN
        ALTER INDEX idx_lessons_org_project RENAME TO idx_memories_org_project;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_lessons_embedding') THEN
        ALTER INDEX idx_lessons_embedding RENAME TO idx_memories_embedding;
    END IF;
END $$;

-- Step 4: Create a view for backward compatibility (deprecated /v1/lessons)
CREATE OR REPLACE VIEW lessons AS
    SELECT id, org_id,
           content AS problem,
           context AS resolution,
           tags, confidence, source, project, embedding,
           created_at, updated_at, expires_at,
           upvotes, downvotes, meta,
           importance_score, access_count, last_accessed_at,
           reputation_score, quality_signals
    FROM memories;

-- Step 5: Create rules to make the view writable (for legacy INSERT/UPDATE/DELETE)
CREATE OR REPLACE RULE lessons_insert AS ON INSERT TO lessons
DO INSTEAD
    INSERT INTO memories (id, org_id, content, context, tags, confidence, source, project,
                          embedding, created_at, updated_at, expires_at, upvotes, downvotes, meta)
    VALUES (NEW.id, NEW.org_id, NEW.problem, NEW.resolution, NEW.tags, NEW.confidence,
            NEW.source, NEW.project, NEW.embedding, NEW.created_at, NEW.updated_at,
            NEW.expires_at, NEW.upvotes, NEW.downvotes, NEW.meta);

CREATE OR REPLACE RULE lessons_update AS ON UPDATE TO lessons
DO INSTEAD
    UPDATE memories SET
        content = NEW.problem,
        context = NEW.resolution,
        tags = NEW.tags,
        confidence = NEW.confidence,
        source = NEW.source,
        project = NEW.project,
        embedding = NEW.embedding,
        updated_at = NEW.updated_at,
        expires_at = NEW.expires_at,
        upvotes = NEW.upvotes,
        downvotes = NEW.downvotes,
        meta = NEW.meta
    WHERE id = OLD.id;

CREATE OR REPLACE RULE lessons_delete AS ON DELETE TO lessons
DO INSTEAD
    DELETE FROM memories WHERE id = OLD.id;
