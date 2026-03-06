-- F1: Knowledge Graph Layer
-- Adds entities, relationships, and entity_mentions tables for the knowledge graph.

CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    aliases         JSONB DEFAULT '[]',
    description     TEXT,
    metadata        JSONB,
    mention_count   INTEGER DEFAULT 1,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_mention_count ON entities(mention_count DESC);

CREATE TABLE IF NOT EXISTS relationships (
    id                  TEXT PRIMARY KEY,
    source_entity_id    TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id    TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel_type            TEXT NOT NULL,
    weight              REAL DEFAULT 1.0,
    properties          JSONB,
    source_fact_id      TEXT,
    source_memory_id    TEXT,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_until         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_rel_active ON relationships(source_entity_id) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(rel_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_unique_edge ON relationships(source_entity_id, target_entity_id, rel_type) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_rel_temporal ON relationships(valid_from, valid_until);

CREATE TABLE IF NOT EXISTS entity_mentions (
    id              TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    memory_id       TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    mention_type    TEXT DEFAULT 'explicit',
    confidence      REAL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_em_entity ON entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_em_memory ON entity_mentions(memory_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_em_unique ON entity_mentions(entity_id, memory_id);
