-- ============================================================
-- Migration 006: Open Brain pivot — lessons → memories
-- Idempotent — safe to run multiple times
-- NON-DESTRUCTIVE: lessons table is preserved; memories table is created alongside
-- ============================================================

-- 1. Create the memories table (if not exists)
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(id),
    content     TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'note',
    source      TEXT,
    project     TEXT,
    tags        JSONB NOT NULL DEFAULT '[]',
    metadata    JSONB NOT NULL DEFAULT '{}',
    embedding   vector(384),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ
);

-- 2. Create indexes
CREATE INDEX IF NOT EXISTS idx_memories_org
    ON memories(org_id);

CREATE INDEX IF NOT EXISTS idx_memories_org_project
    ON memories(org_id, project);

CREATE INDEX IF NOT EXISTS idx_memories_org_type
    ON memories(org_id, type);

CREATE INDEX IF NOT EXISTS idx_memories_created
    ON memories(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memories_tags
    ON memories USING gin(tags);

-- HNSW index for vector search (requires DO block for IF NOT EXISTS check)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_memories_embedding') THEN
        CREATE INDEX idx_memories_embedding ON memories
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
    END IF;
END $$;

-- 3. Migrate data from lessons → memories (skip if already done)
-- Uses INSERT ... ON CONFLICT to be idempotent
-- Only runs if the lessons table exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lessons') THEN
        INSERT INTO memories (id, org_id, content, type, source, project, tags, metadata, embedding, created_at, updated_at, expires_at)
        SELECT
            id,
            org_id,
            -- Combine problem + resolution into content
            CASE
                WHEN resolution IS NOT NULL AND resolution != ''
                THEN problem || E'\n\n' || resolution
                ELSE problem
            END AS content,
            'lesson' AS type,
            source,
            project,
            COALESCE(tags, '[]'::jsonb) AS tags,
            -- Merge meta with context, confidence, upvotes, downvotes
            jsonb_strip_nulls(
                COALESCE(meta, '{}'::jsonb) || jsonb_build_object(
                    'context', context,
                    'confidence', confidence,
                    'upvotes', upvotes,
                    'downvotes', downvotes,
                    'migrated_from', 'lore_lessons'
                )
            ) AS metadata,
            embedding,
            created_at,
            updated_at,
            expires_at
        FROM lessons
        ON CONFLICT (id) DO NOTHING;
    END IF;
END $$;

-- 4. DO NOT DROP lessons table — keep it for rollback safety
-- The lessons table can be dropped in a future migration (007+) after validation
