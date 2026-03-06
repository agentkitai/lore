# Migration Guide: v0.5 to v0.6

This guide covers upgrading from Lore v0.5.x to v0.6.0 ("Open Brain").

## Automatic Migration

Lore v0.6.0 runs schema migrations automatically on startup. If you use the default SQLite store, no manual action is required -- just upgrade the package:

```bash
pip install --upgrade lore-sdk
```

The first time Lore opens your database after the upgrade, it will:

1. Add new columns to the `memories` table (`tier`, `importance_score`, `access_count`, `last_accessed_at`, `archived`, `consolidated_into`)
2. Create the `facts` table
3. Create the `conflict_log` table
4. Create the `entities` table
5. Create the `relationships` table
6. Create the `entity_mentions` table
7. Create the `consolidation_log` table

Existing memories are preserved. New columns use sensible defaults (`tier='long'`, `importance_score=1.0`, etc.).

## Schema Changes

### memories table -- new columns

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `tier` | TEXT | `'long'` | Memory tier: `working`, `short`, or `long` |
| `importance_score` | REAL | `1.0` | Computed importance (0.0-1.0+), decays over time |
| `access_count` | INTEGER | `0` | Number of times recalled |
| `last_accessed_at` | TEXT | NULL | ISO-8601 timestamp of last recall |
| `archived` | INTEGER | `0` | Whether memory has been archived by consolidation |
| `consolidated_into` | TEXT | NULL | ID of the consolidated memory that replaced this one |

### New tables

| Table | Purpose |
|-------|---------|
| `facts` | Structured (subject, predicate, object) triples extracted from memories |
| `conflict_log` | Records of fact conflicts and their resolutions (SUPERSEDE, MERGE, CONTRADICT) |
| `entities` | Knowledge graph nodes (people, tools, projects, concepts, etc.) |
| `relationships` | Directed edges between entities (depends_on, uses, implements, etc.) |
| `entity_mentions` | Links entities to the memories that mention them |
| `consolidation_log` | Records of consolidation actions (which memories were merged/summarized) |

## Manual SQL (for users managing their own schemas)

If you manage your database schema outside of Lore (e.g., with Alembic or custom migrations), apply these changes:

```sql
-- Add new columns to memories
ALTER TABLE memories ADD COLUMN tier TEXT DEFAULT 'long';
ALTER TABLE memories ADD COLUMN importance_score REAL DEFAULT 1.0;
ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN last_accessed_at TEXT;
ALTER TABLE memories ADD COLUMN archived INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN consolidated_into TEXT;

CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_memories_project_tier ON memories(project, tier);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance_score);
CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories(last_accessed_at);
CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived);

-- Facts table
CREATE TABLE IF NOT EXISTS facts (
    id              TEXT PRIMARY KEY,
    memory_id       TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    object          TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    extracted_at    TEXT NOT NULL,
    invalidated_by  TEXT,
    invalidated_at  TEXT,
    metadata        TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_memory ON facts(memory_id);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate ON facts(subject, predicate);

-- Conflict log
CREATE TABLE IF NOT EXISTS conflict_log (
    id              TEXT PRIMARY KEY,
    new_memory_id   TEXT NOT NULL,
    old_fact_id     TEXT NOT NULL,
    new_fact_id     TEXT,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    old_value       TEXT NOT NULL,
    new_value       TEXT NOT NULL,
    resolution      TEXT NOT NULL,
    resolved_at     TEXT NOT NULL,
    metadata        TEXT
);
CREATE INDEX IF NOT EXISTS idx_conflict_log_memory ON conflict_log(new_memory_id);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolution ON conflict_log(resolution);

-- Knowledge graph tables
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

-- Consolidation log
CREATE TABLE IF NOT EXISTS consolidation_log (
    id                      TEXT PRIMARY KEY,
    consolidated_memory_id  TEXT NOT NULL,
    original_memory_ids     TEXT NOT NULL,
    strategy                TEXT NOT NULL,
    model_used              TEXT,
    original_count          INTEGER NOT NULL,
    created_at              TEXT NOT NULL,
    metadata                TEXT
);
CREATE INDEX IF NOT EXISTS idx_clog_memory ON consolidation_log(consolidated_memory_id);
CREATE INDEX IF NOT EXISTS idx_clog_created ON consolidation_log(created_at);
```

## New Tools (7 in v0.5 -> 20 in v0.6)

### Existing tools (updated)

| Tool | Changes in v0.6 |
|------|-----------------|
| `remember` | New `tier` parameter (working/short/long). Auto-extracts facts and updates knowledge graph when enabled. |
| `recall` | New filter parameters: `tier`, `intent`, `domain`, `emotion`, `topic`, `sentiment`, `entity`, `category`. Returns importance scores. |
| `forget` | No API changes. |
| `list_memories` | New `tier` filter. |
| `stats` | Now includes `by_tier`, `avg_importance`, `archived_count`, `consolidation_count`. |
| `upvote_memory` | No API changes. |
| `downvote_memory` | No API changes. |

### New tools in v0.6

| Tool | Description |
|------|-------------|
| `extract_facts` | Extract structured (subject, predicate, object) facts from text. Requires LLM. |
| `list_facts` | List active facts, optionally filtered by subject. |
| `conflicts` | Show detected fact conflicts and their resolutions. |
| `graph_query` | Traverse the knowledge graph from a given entity with configurable depth. |
| `entity_map` | List entities in the knowledge graph, optionally filtered by type. |
| `related` | Find memories and entities related to a given memory or entity. |
| `classify` | Classify text by intent, domain, and emotion. |
| `enrich` | Add LLM-extracted metadata (topics, sentiment, entities) to memories. |
| `consolidate` | Merge near-duplicate memories and summarize related clusters. |
| `ingest` | Import content from external sources with provenance tracking. |
| `as_prompt` | Export memories formatted for LLM context injection (XML, ChatML, markdown). |
| `check_freshness` | Check if code-related memories are still fresh against git history. |
| `github_sync` | Sync GitHub PRs, issues, commits, and releases into Lore. |

## Breaking Changes

### recall() response

`RecallResult` now includes additional fields:

- `memory.tier` -- the memory tier (always present, defaults to `"long"` for migrated memories)
- `memory.importance_score` -- computed importance score
- `memory.access_count` -- how many times this memory has been recalled
- `memory.last_accessed_at` -- when it was last recalled

Code that parses recall results should handle these new fields gracefully.

### Scoring model

The recall scoring model has changed from an additive to a multiplicative model:

- **v0.5:** `score = similarity * weight + freshness * weight`
- **v0.6:** `score = cosine_similarity * time_adjusted_importance * tier_weight`

This produces more accurate rankings but means absolute score values are not comparable between versions.

### Deprecated parameters

| Parameter | Status | Migration |
|-----------|--------|-----------|
| `decay_similarity_weight` | Deprecated, ignored. Will be removed in v0.7.0. | Remove from constructor calls. |
| `decay_freshness_weight` | Deprecated, ignored. Will be removed in v0.7.0. | Remove from constructor calls. |

Using these parameters will emit a `DeprecationWarning`.

### Renamed concepts

- "Lessons" terminology is replaced by "Memories" throughout the API and docs. The v0.5 `save_lesson`/`recall_lessons` MCP tools are now `remember`/`recall`. The HTTP API endpoints under `/v1/lessons` remain available for backward compatibility.

## New Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_CLASSIFY` | `false` | Enable dialog classification on remember |
| `LORE_KNOWLEDGE_GRAPH` | `false` | Enable knowledge graph extraction |
| `LORE_FACT_EXTRACTION` | `false` | Enable structured fact extraction |
| `LORE_ENRICHMENT_ENABLED` | `false` | Enable LLM enrichment pipeline |
| `LORE_ENRICHMENT_MODEL` | `gpt-4o-mini` | Model for enrichment |
| `LORE_LLM_PROVIDER` | none | LLM provider (anthropic, openai, etc.) |
| `LORE_LLM_MODEL` | `gpt-4o-mini` | LLM model for classification/extraction |
| `LORE_LLM_API_KEY` | none | API key for the LLM provider |

## Troubleshooting

### "table already exists" errors

This should not happen with the default SQLite store, as all CREATE statements use `IF NOT EXISTS`. If you see this with a custom migration setup, ensure you are not running the migration SQL twice.

### Old memories have importance_score=1.0

This is expected. Existing memories start at 1.0 and will naturally decay over time based on the importance model. You can trigger a recalculation by recalling or upvoting them.

### Knowledge graph is empty after upgrade

The knowledge graph is not retroactively populated. To backfill entities and relationships from existing memories:

```bash
lore graph-backfill
```

### Facts are empty after upgrade

Fact extraction only runs on new memories when enabled. To extract facts from existing memories:

```bash
lore backfill-facts
```

Both commands require LLM configuration (`LORE_LLM_PROVIDER`, `LORE_LLM_API_KEY`).
