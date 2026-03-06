-- Migration 007: Knowledge Graph tables (F1)
-- Depends on: memories table

CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    aliases         TEXT DEFAULT '[]',
    description     TEXT,
    metadata        TEXT,
    mention_count   INTEGER DEFAULT 1,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
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
    properties          TEXT,
    source_fact_id      TEXT,
    source_memory_id    TEXT,
    valid_from          TEXT NOT NULL,
    valid_until         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
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
    memory_id       TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    mention_type    TEXT DEFAULT 'explicit',
    confidence      REAL DEFAULT 1.0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_em_entity ON entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_em_memory ON entity_mentions(memory_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_em_unique ON entity_mentions(entity_id, memory_id);
