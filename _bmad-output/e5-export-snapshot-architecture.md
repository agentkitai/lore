# E5: Export / Snapshot — Technical Architecture

**Epic:** E5 — Safety Net
**Version:** v0.10.0
**Author:** Winston (Solutions Architect)
**Date:** March 14, 2026
**Status:** Draft

---

## 1. Component Architecture

### 1.1 New Modules

```
src/lore/
├── export/
│   ├── __init__.py           # Public API: export_json, export_markdown, import_json
│   ├── exporter.py           # Core export engine (JSON + streaming)
│   ├── markdown.py           # Markdown/Obsidian export renderer
│   ├── importer.py           # JSON import engine with deduplication
│   ├── snapshot.py           # Snapshot lifecycle management
│   ├── schema.py             # Export schema version, validation, migration
│   └── serializers.py        # Dataclass ↔ dict serialization (shared by export/import)
```

### 1.2 Component Interaction Diagram

```
┌─────────────┐   ┌─────────────┐   ┌──────────────┐
│   CLI        │   │  MCP Server │   │  HTTP Server  │
│  (cli.py)    │   │ (server.py) │   │  (routes/)    │
└──────┬───────┘   └──────┬──────┘   └──────┬────────┘
       │                  │                  │
       └────────┬─────────┴─────────┬────────┘
                │                   │
                ▼                   ▼
         ┌──────────────┐   ┌──────────────┐
         │   Lore       │   │  Snapshot     │
         │ .export_data │   │  Manager      │
         │ .import_data │   │ (snapshot.py) │
         └──────┬───────┘   └──────┬────────┘
                │                  │
                ▼                  │
         ┌──────────────┐         │
         │   Exporter    │◄────────┘
         │ (exporter.py) │
         └──────┬───────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌──────────┐
│ Store  │ │Markdown│ │Serializer│
│ (ABC)  │ │Renderer│ │  s.py    │
└────────┘ └────────┘ └──────────┘
```

### 1.3 Design Principles

1. **Exporter reads from Store ABC** — no direct SQLite/Postgres access. Works with any backend.
2. **Serializers are shared** — same `memory_to_dict` / `dict_to_memory` used by both export and import. Round-trip fidelity is enforced at this layer.
3. **Snapshot Manager composes Exporter + filesystem** — snapshots are JSON exports with lifecycle management layered on top.
4. **No LLM dependency** — pure data operations. Embedding regeneration on import uses the existing `Embedder` pipeline.

---

## 2. Export Format Specification

### 2.1 JSON Export Schema (v1)

The top-level envelope:

```json
{
  "schema_version": 1,
  "exported_at": "2026-03-14T15:30:45.123456+00:00",
  "lore_version": "0.10.0",
  "content_hash": "sha256:abcdef1234567890...",
  "filters": {
    "project": null,
    "type": null,
    "tier": null,
    "since": null
  },
  "counts": {
    "memories": 1234,
    "entities": 56,
    "relationships": 78,
    "entity_mentions": 340,
    "facts": 200,
    "conflicts": 15,
    "consolidation_logs": 8
  },
  "data": {
    "memories": [...],
    "entities": [...],
    "relationships": [...],
    "entity_mentions": [...],
    "facts": [...],
    "conflicts": [...],
    "consolidation_logs": [...]
  }
}
```

### 2.2 Memory Serialization Rules

Each memory is serialized as a flat dict with these field-level rules:

| Field | Serialization | Notes |
|-------|---------------|-------|
| `id` | string | ULID, preserved exactly |
| `content` | string | UTF-8, no transformation |
| `type` | string | One of `VALID_MEMORY_TYPES` |
| `tier` | string | "working", "short", "long" |
| `context` | string or null | |
| `tags` | array of strings | Always an array, never null |
| `metadata` | object or null | Arbitrary JSON, preserved as-is |
| `source` | string or null | |
| `project` | string or null | |
| `embedding` | string or null | Base64-encoded float32 bytes if `--include-embeddings`, else null |
| `created_at` | string | ISO 8601 UTC |
| `updated_at` | string | ISO 8601 UTC |
| `ttl` | integer or null | Seconds |
| `expires_at` | string or null | ISO 8601 UTC |
| `confidence` | number | float, default 1.0 |
| `upvotes` | integer | default 0 |
| `downvotes` | integer | default 0 |
| `importance_score` | number | float, default 1.0 |
| `access_count` | integer | default 0 |
| `last_accessed_at` | string or null | ISO 8601 UTC |
| `archived` | boolean | |
| `consolidated_into` | string or null | Memory ID |

### 2.3 Knowledge Graph Serialization

**Entities** — all fields from `Entity` dataclass:
```json
{
  "id": "01HABC...",
  "name": "sqlite",
  "entity_type": "tool",
  "aliases": ["sqlite3", "SQLite"],
  "description": "Embedded SQL database",
  "metadata": null,
  "mention_count": 12,
  "first_seen_at": "2026-01-05T08:00:00+00:00",
  "last_seen_at": "2026-03-10T14:00:00+00:00",
  "created_at": "2026-01-05T08:00:00+00:00",
  "updated_at": "2026-03-10T14:00:00+00:00"
}
```

**Relationships** — all fields from `Relationship` dataclass:
```json
{
  "id": "01HDEF...",
  "source_entity_id": "01HABC...",
  "target_entity_id": "01HGHI...",
  "rel_type": "uses",
  "weight": 1.5,
  "properties": null,
  "source_fact_id": "01HJKL...",
  "source_memory_id": "01HXYZ...",
  "valid_from": "2026-02-15T10:30:00+00:00",
  "valid_until": null,
  "created_at": "2026-02-15T10:30:00+00:00",
  "updated_at": "2026-02-15T10:30:00+00:00"
}
```

**Entity Mentions** — all fields from `EntityMention`:
```json
{
  "id": "01HMNO...",
  "entity_id": "01HABC...",
  "memory_id": "01HXYZ...",
  "mention_type": "explicit",
  "confidence": 1.0,
  "created_at": "2026-02-15T10:30:00+00:00"
}
```

**Facts** — all fields from `Fact`:
```json
{
  "id": "01HPQR...",
  "memory_id": "01HXYZ...",
  "subject": "sqlite",
  "predicate": "uses",
  "object": "WAL mode",
  "confidence": 0.95,
  "extracted_at": "2026-02-15T10:30:00+00:00",
  "invalidated_by": null,
  "invalidated_at": null,
  "metadata": null
}
```

**Conflicts** — all fields from `ConflictEntry`:
```json
{
  "id": "01HSTU...",
  "new_memory_id": "01HXYZ...",
  "old_fact_id": "01HOLD...",
  "new_fact_id": "01HNEW...",
  "subject": "python",
  "predicate": "default_version",
  "old_value": "3.9",
  "new_value": "3.12",
  "resolution": "SUPERSEDE",
  "resolved_at": "2026-03-01T12:00:00+00:00",
  "metadata": {"reasoning": "newer version mentioned"}
}
```

**Consolidation Logs** — all fields from `ConsolidationLogEntry`:
```json
{
  "id": "01HVWX...",
  "consolidated_memory_id": "01HNEW...",
  "original_memory_ids": ["01H001...", "01H002...", "01H003..."],
  "strategy": "dedup",
  "model_used": null,
  "original_count": 3,
  "created_at": "2026-03-01T12:00:00+00:00",
  "metadata": null
}
```

### 2.4 Deterministic Ordering

All arrays in the `data` object are sorted for deterministic output:

| Data Type | Sort Key | Order |
|-----------|----------|-------|
| memories | `created_at` | ascending |
| entities | `name` | ascending (case-insensitive) |
| relationships | `source_entity_id`, then `target_entity_id` | ascending |
| entity_mentions | `entity_id`, then `memory_id` | ascending |
| facts | `memory_id`, then `extracted_at` | ascending |
| conflicts | `resolved_at` | ascending |
| consolidation_logs | `created_at` | ascending |

### 2.5 Markdown Directory Structure

```
lore-export/
├── memories/
│   ├── general/
│   │   └── 01HXYZ-descriptive-slug.md
│   ├── code/
│   │   └── 01HABC-fix-sqlite-lock.md
│   ├── lesson/
│   ├── convention/
│   ├── fact/
│   ├── preference/
│   ├── debug/
│   ├── pattern/
│   └── note/
├── entities/
│   └── sqlite.md
├── graph/
│   └── relationships.md
└── _export_meta.md
```

**Filename generation** (`serializers.py`):

```python
def memory_to_filename(memory: Memory) -> str:
    """Generate filesystem-safe filename: <id_prefix>-<slug>.md"""
    slug = re.sub(r'[^\w\s-]', '', memory.content[:60]).strip()
    slug = re.sub(r'[\s_]+', '-', slug).lower()
    slug = slug[:140]  # Keep total path under 200 chars
    id_prefix = memory.id[:8]
    return f"{id_prefix}-{slug}.md" if slug else f"{id_prefix}.md"
```

**Memory markdown file** — YAML frontmatter + content body + facts section + entity links:

```markdown
---
id: 01HXYZ...
type: code
tier: long
project: lore
tags: [sqlite, concurrency]
confidence: 0.95
importance_score: 0.82
upvotes: 3
downvotes: 0
created_at: 2026-02-15T10:30:00+00:00
updated_at: 2026-02-15T10:30:00+00:00
source: claude-code
---

SQLite WAL mode fixes the "database is locked" error.

## Facts
| Subject | Predicate | Object |
|---------|-----------|--------|
| sqlite | uses | WAL mode |

## Entities
- [[sqlite]]
- [[WAL mode]]
```

**Entity markdown file**:

```markdown
---
id: 01HABC...
entity_type: tool
aliases: [sqlite3, SQLite]
mention_count: 12
first_seen_at: 2026-01-05T08:00:00+00:00
---

# sqlite

## Mentioned In
- [[01HXYZ-fix-sqlite-lock]] — SQLite WAL mode fixes the "datab...
- [[01HDEF-sqlite-perf]] — Query optimization tips for...

## Relationships
| Direction | Type | Entity |
|-----------|------|--------|
| → | uses | [[WAL mode]] |
| ← | depends_on | [[lore-sdk]] |
```

---

## 3. Store ABC Changes

### 3.1 New Methods on `Store` Base Class

Two new methods are needed for bulk export. These are added as **default no-op implementations** on the ABC (same pattern as existing graph methods), with concrete implementations in `SqliteStore`. `HttpStore` will compose from existing REST endpoints.

```python
# In store/base.py — add to Store class:

def list_all_facts(
    self,
    memory_ids: Optional[List[str]] = None,
) -> List[Fact]:
    """List all facts, optionally filtered to specific memory IDs.

    Used by export engine for bulk fact retrieval instead of
    N+1 get_facts() calls per memory.
    """
    return []

def list_all_entity_mentions(
    self,
    memory_ids: Optional[List[str]] = None,
) -> List[EntityMention]:
    """List all entity mentions, optionally filtered to specific memory IDs.

    Used by export engine for bulk mention retrieval instead of
    N+1 get_entity_mentions_for_memory() calls.
    """
    return []

def list_all_conflicts(
    self,
    limit: int = 10000,
) -> List[ConflictEntry]:
    """List all conflict log entries (no resolution filter).

    Used by export engine. Existing list_conflicts() has a resolution
    filter and low default limit (20) — unsuitable for full export.
    """
    return []

def list_all_consolidation_logs(
    self,
    limit: int = 10000,
) -> List[ConsolidationLogEntry]:
    """List all consolidation log entries.

    Used by export engine. Existing get_consolidation_log() has
    a low default limit (50).
    """
    return []
```

### 3.2 SqliteStore Implementations

```python
# In store/sqlite.py — add to SqliteStore:

def list_all_facts(self, memory_ids: Optional[List[str]] = None) -> List[Fact]:
    if memory_ids is not None:
        if not memory_ids:
            return []
        placeholders = ",".join("?" * len(memory_ids))
        query = f"SELECT * FROM facts WHERE memory_id IN ({placeholders}) ORDER BY memory_id, extracted_at"
        rows = self._conn.execute(query, memory_ids).fetchall()
    else:
        rows = self._conn.execute(
            "SELECT * FROM facts ORDER BY memory_id, extracted_at"
        ).fetchall()
    return [self._row_to_fact(r) for r in rows]

def list_all_entity_mentions(self, memory_ids: Optional[List[str]] = None) -> List[EntityMention]:
    if memory_ids is not None:
        if not memory_ids:
            return []
        placeholders = ",".join("?" * len(memory_ids))
        query = f"SELECT * FROM entity_mentions WHERE memory_id IN ({placeholders}) ORDER BY entity_id, memory_id"
        rows = self._conn.execute(query, memory_ids).fetchall()
    else:
        rows = self._conn.execute(
            "SELECT * FROM entity_mentions ORDER BY entity_id, memory_id"
        ).fetchall()
    return [self._row_to_entity_mention(r) for r in rows]

def list_all_conflicts(self, limit: int = 10000) -> List[ConflictEntry]:
    rows = self._conn.execute(
        "SELECT * FROM conflict_log ORDER BY resolved_at LIMIT ?", (limit,)
    ).fetchall()
    return [self._row_to_conflict(r) for r in rows]

def list_all_consolidation_logs(self, limit: int = 10000) -> List[ConsolidationLogEntry]:
    rows = self._conn.execute(
        "SELECT * FROM consolidation_log ORDER BY created_at LIMIT ?", (limit,)
    ).fetchall()
    return [self._row_to_consolidation_log(r) for r in rows]
```

### 3.3 HttpStore — Compose from Existing Endpoints

`HttpStore` does not implement the new bulk methods directly. Instead, the exporter handles HttpStore by paginating through existing REST endpoints:

- Memories: `GET /v1/lessons?limit=500&offset=N` (paginated)
- Entities: `GET /api/v1/entities` (new endpoint needed on server)
- Relationships: `GET /api/v1/relationships` (new endpoint needed on server)

For v1, HttpStore export works client-side by pulling all data through existing methods. The `list` method already supports pagination. Facts, entity mentions, conflicts, and consolidation logs require new server-side endpoints (see Section 11).

### 3.4 No Changes to Abstract Methods

All new methods have default no-op implementations. **No existing Store implementations break.** The MemoryStore (in-memory test store) inherits the defaults and returns empty lists — acceptable since export tests will use SqliteStore.

---

## 4. Data Flow

### 4.1 Export Pipeline

```
User invokes: lore export --format json --project myapp
                │
                ▼
         ┌──────────────┐
         │ Lore.export_  │
         │ data()        │
         └──────┬────────┘
                │
                ▼
         ┌──────────────┐
         │ Build filter  │  FilterSpec(project="myapp", type=None,
         │ spec          │  tier=None, since=None)
         └──────┬────────┘
                │
                ▼
         ┌──────────────┐
    ┌────│ Exporter.     │────┐
    │    │ export()      │    │
    │    └───────────────┘    │
    │                         │
    ▼                         ▼
┌────────────┐         ┌────────────────┐
│ 1. Fetch   │         │ 2. Fetch graph │
│ memories   │         │ data           │
│ store.list │         │                │
│ (filters)  │         │ a. entities    │
│            │         │ b. rels        │
│ include_   │         │ c. mentions    │
│ archived=  │         │ d. facts       │
│ True       │         │ e. conflicts   │
└─────┬──────┘         │ f. consol logs │
      │                └───────┬────────┘
      │                        │
      ▼                        ▼
┌──────────────────────────────────┐
│ 3. Filter graph to exported      │
│    memories (for filtered        │
│    exports only)                 │
│                                  │
│    memory_ids = {m.id for m in   │
│                   memories}      │
│    mentions = [m for m in all_   │
│      mentions if m.memory_id     │
│      in memory_ids]              │
│    entity_ids = {m.entity_id     │
│      for m in mentions}          │
│    entities = [e for e in all_   │
│      entities if e.id in         │
│      entity_ids]                 │
│    relationships = [r for r in   │
│      all_rels if r.source_       │
│      entity_id in entity_ids     │
│      or r.target_entity_id in    │
│      entity_ids]                 │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│ 4. Serialize                     │
│    a. Sort all arrays            │
│    b. Strip embeddings (default) │
│    c. Convert dataclasses→dicts  │
│    d. Build data payload         │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│ 5. Compute content hash          │
│    SHA-256 of canonical JSON     │
│    of data object (sorted keys,  │
│    no whitespace)                │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│ 6. Write to file                 │
│    a. Build envelope (schema_    │
│       version, counts, hash)     │
│    b. Stream write to file       │
│       (for large exports)        │
└──────────────┬───────────────────┘
               │
               ▼
         ExportResult(path, counts, hash)
```

### 4.2 Import Pipeline

```
User invokes: lore import export.json --overwrite
                │
                ▼
         ┌──────────────┐
         │ Lore.import_  │
         │ data()        │
         └──────┬────────┘
                │
                ▼
         ┌──────────────────────┐
         │ 1. Read + validate   │
         │    a. Parse JSON     │
         │    b. Check schema_  │
         │       version        │
         │    c. Verify content │
         │       hash           │
         └──────────┬───────────┘
                    │ fail → abort with error
                    ▼
         ┌──────────────────────┐
         │ 2. Dry run check     │
         │    If --dry-run:     │
         │    scan all records, │
         │    report counts,    │
         │    return early      │
         └──────────┬───────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │ 3. Import memories   │  ← ORDER MATTERS
         │    For each memory:  │
         │    a. Check if exists│
         │    b. Skip (default) │
         │       or overwrite   │
         │    c. Apply project  │
         │       override       │
         │    d. Strip embedding│
         │       (regenerate    │
         │        later)        │
         │    e. store.save()   │
         └──────────┬───────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │ 4. Import entities   │
         │    store.save_entity │
         │    for each entity   │
         └──────────┬───────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │ 5. Import facts      │
         │    store.save_fact   │
         │    for each fact     │
         └──────────┬───────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │ 6. Import            │
         │    relationships     │
         │    Validate entity   │
         │    IDs exist. Skip + │
         │    warn if orphaned  │
         └──────────┬───────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │ 7. Import entity     │
         │    mentions          │
         │    Validate entity + │
         │    memory IDs exist  │
         └──────────┬───────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │ 8. Import conflicts  │
         │    + consolidation   │
         │    logs              │
         └──────────┬───────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │ 9. Regenerate        │
         │    embeddings        │
         │    (unless --skip-   │
         │     embeddings)      │
         │    Batch embed all   │
         │    imported memories │
         └──────────┬───────────┘
                    │
                    ▼
         ImportResult(total, imported,
                      skipped, errors,
                      warnings,
                      embeddings_regenerated)
```

### 4.3 Snapshot Lifecycle

```
lore snapshot
    │
    ├─► SnapshotManager.create()
    │     1. Export JSON to ~/.lore/snapshots/YYYY-MM-DD-HHMMSS.json
    │     2. Auto-prune if count > max_snapshots (default 50)
    │     3. Return snapshot name + path
    │
lore snapshot --list
    │
    ├─► SnapshotManager.list()
    │     1. Glob ~/.lore/snapshots/*.json
    │     2. Read first ~500 bytes of each for header metadata
    │     3. Return sorted list (newest first)
    │
lore snapshot --restore <name>
    │
    ├─► SnapshotManager.restore(name)
    │     1. Resolve name to file path
    │     2. Confirm with user (CLI prompt)
    │     3. Call Lore.import_data(path, overwrite=True)
    │     4. Return ImportResult
    │
lore snapshot --delete --older-than 30d
    │
    ├─► SnapshotManager.cleanup(older_than="30d")
          1. Parse duration string
          2. Delete matching snapshot files
          3. Return count deleted
```

---

## 5. Embedding Handling

### 5.1 Export: Exclude by Default

Embeddings are 384-dim float32 vectors = 1,536 bytes per memory. For 10K memories, that's ~15MB of binary data that:
- Is model-version-specific (not portable across embedding model upgrades)
- Is regeneratable deterministically from content
- Bloats exports 10x for no portability gain

**Default behavior:** Set `embedding: null` in exported JSON.

**Opt-in:** `--include-embeddings` serializes as base64:

```python
import base64

def serialize_embedding(raw: Optional[bytes]) -> Optional[str]:
    if raw is None:
        return None
    return base64.b64encode(raw).decode("ascii")

def deserialize_embedding(b64: Optional[str]) -> Optional[bytes]:
    if b64 is None:
        return None
    return base64.b64decode(b64)
```

### 5.2 Import: Regenerate by Default

After all memories are imported, the importer batch-regenerates embeddings using the current embedding model:

```python
def _regenerate_embeddings(
    self,
    memories: List[Memory],
    embedder: Embedder,
    store: Store,
    batch_size: int = 100,
) -> int:
    """Batch-regenerate embeddings for imported memories."""
    count = 0
    for i in range(0, len(memories), batch_size):
        batch = memories[i:i + batch_size]
        texts = [_embed_text(m) for m in batch]
        vectors = embedder.embed_batch(texts)
        for mem, vec in zip(batch, vectors):
            mem.embedding = _serialize_embedding(vec)
            store.update(mem)
            count += 1
    return count
```

**`--skip-embeddings`** skips this step. Useful for bulk imports where embeddings will be regenerated later via `lore reindex`.

**`--include-embeddings` on export + import** restores exact embeddings from the export without re-embedding. The importer detects non-null `embedding` fields and preserves them as-is.

---

## 6. Knowledge Graph Export

### 6.1 Full Export

All graph data is exported: entities, relationships, entity mentions. No filtering applied.

### 6.2 Filtered Export

When filters are applied (e.g., `--project myapp`), graph data must be scoped to exported memories:

```python
def _filter_graph_data(
    self,
    memory_ids: Set[str],
    entities: List[Entity],
    relationships: List[Relationship],
    mentions: List[EntityMention],
) -> Tuple[List[Entity], List[Relationship], List[EntityMention]]:
    """Scope graph data to only entities/rels connected to exported memories."""

    # 1. Filter mentions to exported memories
    filtered_mentions = [m for m in mentions if m.memory_id in memory_ids]

    # 2. Collect referenced entity IDs
    entity_ids = {m.entity_id for m in filtered_mentions}

    # 3. Filter entities
    filtered_entities = [e for e in entities if e.id in entity_ids]

    # 4. Filter relationships — keep if BOTH endpoints are in entity_ids
    filtered_rels = [
        r for r in relationships
        if r.source_entity_id in entity_ids and r.target_entity_id in entity_ids
    ]

    return filtered_entities, filtered_rels, filtered_mentions
```

### 6.3 Graph Integrity on Import

Import order is critical for referential integrity:

1. **Memories** first (entities and facts reference them)
2. **Entities** second (relationships and mentions reference them)
3. **Facts** third (relationships may reference them via `source_fact_id`)
4. **Relationships** fourth (reference entities + facts)
5. **Entity mentions** fifth (reference entities + memories)
6. **Conflicts** and **consolidation logs** last (reference memories + facts)

Orphan handling: if a relationship references an entity ID not present in the import, log a warning and skip that relationship. Do not fail the entire import.

---

## 7. Large Dataset Handling

### 7.1 Memory Pressure Mitigation

For exports up to 100K memories (target), the full dataset fits in memory (~100MB for 100K memories without embeddings). No streaming needed for the data collection phase.

For the **write phase**, use streaming JSON output to avoid building the entire JSON string in memory:

```python
def _stream_write_json(
    self,
    envelope: Dict[str, Any],
    data: Dict[str, List],
    output_path: str,
    pretty: bool = False,
) -> None:
    """Write JSON export with streaming for the data arrays."""
    indent = 2 if pretty else None
    with open(output_path, "w", encoding="utf-8") as f:
        # Write envelope fields
        f.write("{\n")
        for key in ("schema_version", "exported_at", "lore_version",
                     "content_hash", "filters", "counts"):
            f.write(f'  "{key}": {json.dumps(envelope[key], indent=indent)}')
            f.write(",\n")

        # Write data object with streaming arrays
        f.write('  "data": {\n')
        data_keys = list(data.keys())
        for i, key in enumerate(data_keys):
            f.write(f'    "{key}": [\n')
            items = data[key]
            for j, item in enumerate(items):
                line = json.dumps(item, ensure_ascii=False, sort_keys=True)
                comma = "," if j < len(items) - 1 else ""
                f.write(f"      {line}{comma}\n")
            comma = "," if i < len(data_keys) - 1 else ""
            f.write(f"    ]{comma}\n")
        f.write("  }\n")
        f.write("}\n")
```

### 7.2 SQLite Read Transaction

For `SqliteStore`, the entire export reads within a single connection (SQLite default isolation level provides a consistent snapshot within a single `execute` sequence). Since we issue multiple queries (memories, entities, facts, etc.), we wrap them in an explicit transaction:

```python
# In SqliteStore or at the exporter level:
self._conn.execute("BEGIN DEFERRED")
try:
    memories = self.list(include_archived=True, ...)
    entities = self.list_entities(limit=100000)
    # ... all reads ...
finally:
    self._conn.execute("ROLLBACK")  # read-only, no writes to commit
```

### 7.3 HttpStore Pagination

For `HttpStore` exports, paginate through the REST API with a fixed `created_before` timestamp to ensure consistency:

```python
def _fetch_all_memories_http(store: HttpStore, filters) -> List[Memory]:
    ceiling = datetime.now(timezone.utc).isoformat()
    all_memories = []
    offset = 0
    page_size = 500
    while True:
        page = store.list(limit=page_size, ...)  # needs offset support
        all_memories.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return all_memories
```

### 7.4 Import Batching

For large imports, batch `store.save()` calls to reduce SQLite transaction overhead:

```python
def _batch_save_memories(self, memories: List[Memory], store: Store, batch_size: int = 500):
    """Save memories in batches for SQLite performance."""
    if isinstance(store, SqliteStore):
        for i in range(0, len(memories), batch_size):
            batch = memories[i:i + batch_size]
            for mem in batch:
                store.save(mem)
            # SqliteStore.save() commits per call — could optimize
            # by deferring commits, but that's a Store-level change.
            # Acceptable for v1 at <10s for 10K memories.
    else:
        for mem in memories:
            store.save(mem)
```

---

## 8. Content Hash Verification

### 8.1 Hash Computation

The content hash covers the entire `data` object to detect corruption or tampering:

```python
import hashlib
import json

def compute_content_hash(data: Dict[str, Any]) -> str:
    """SHA-256 hash of the canonical JSON representation of the data object."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
```

### 8.2 Hash Verification on Import

```python
def verify_content_hash(export: Dict[str, Any]) -> bool:
    """Verify the content hash of an export file."""
    expected = export.get("content_hash")
    if not expected:
        return True  # No hash = legacy export, skip verification

    actual = compute_content_hash(export["data"])
    if actual != expected:
        raise ValueError(
            f"Content hash mismatch. Expected {expected}, got {actual}. "
            "The export file may be corrupted or tampered with."
        )
    return True
```

### 8.3 Hash Scope

The hash covers `data` only (not the envelope). This means:
- Changing `exported_at` or `lore_version` doesn't invalidate the hash
- The hash is deterministic given the same data (canonical JSON, sorted keys)
- Round-trip test: `export → import → export` produces the same `content_hash`

---

## 9. Idempotent Import

### 9.1 Deduplication Strategy

**Default mode (no flags):** Skip existing records by primary key.

```python
def _should_import_memory(self, memory_id: str, store: Store, overwrite: bool) -> str:
    """Returns 'import', 'skip', or 'overwrite'."""
    existing = store.get(memory_id)
    if existing is None:
        return "import"
    if overwrite:
        return "overwrite"
    return "skip"
```

**Same logic for all data types:**

| Data Type | Key | Skip if exists? | Overwrite behavior |
|-----------|-----|-----------------|-------------------|
| Memory | `id` | Yes | Replace via `store.save()` (INSERT OR REPLACE) |
| Entity | `id` | Yes | Replace via `store.save_entity()` |
| Fact | `id` | Yes | Replace via `store.save_fact()` |
| Relationship | `id` | Yes | Replace via `store.save_relationship()` |
| Entity Mention | `id` | Yes | Replace via `store.save_entity_mention()` |
| Conflict | `id` | Yes | Replace via `store.save_conflict()` |
| Consolidation Log | `id` | Yes | Replace via `store.save_consolidation_log()` |

### 9.2 Idempotency Guarantee

Running `lore import export.json` twice produces identical results:
- First run: N records imported, 0 skipped
- Second run: 0 records imported, N skipped
- Store state after both runs: identical

### 9.3 Project Override

`--project <name>` sets the `project` field on all imported memories. This enables re-importing the same data under a different project without ID conflicts (since the IDs remain the same, the memory will be skipped/overwritten).

---

## 10. CLI Command Design

### 10.1 `lore export`

```
lore export [OPTIONS]

Options:
  --format {json,markdown,both}   Export format (default: json)
  --output PATH                   Output file or directory path
                                  Default (json): ./lore-export-YYYY-MM-DDTHHMMSS.json
                                  Default (markdown): ./lore-export-YYYY-MM-DDTHHMMSS/
  --project NAME                  Filter: only memories for this project
  --type TYPE                     Filter: only memories of this type
  --tier {working,short,long}     Filter: only memories in this tier
  --since DATE                    Filter: memories created/updated after DATE (ISO 8601)
  --include-embeddings            Include raw embedding vectors as base64
  --pretty                        Pretty-print JSON (2-space indent)
  --db PATH                       Database path (default: ~/.lore/memories.db)

Output:
  Exports N memories, E entities, R relationships to <path>.
  Content hash: sha256:abc123...
  Export completed in 1.23s.

Exit codes:
  0  Success
  1  No memories match filters
  2  Write error (permissions, disk space)
```

### 10.2 `lore import`

```
lore import FILE [OPTIONS]

Arguments:
  FILE                            Path to JSON export file

Options:
  --overwrite                     Replace existing memories on ID conflict
  --dry-run                       Show what would be imported without writing
  --project NAME                  Override project field for all imported memories
  --skip-embeddings               Don't regenerate embeddings after import
  --redact                        Re-run PII redaction on imported content
  --db PATH                       Database path (default: ~/.lore/memories.db)

Output (normal):
  Import complete:
    Total records:  1234
    Imported:       1200
    Skipped:        30
    Errors:         4
    Warnings:       2
    Embeddings regenerated: 1200

Output (dry-run):
  Dry run — no changes written:
    Would import:   1200
    Would skip:     30
    Would overwrite: 0

Exit codes:
  0  Success
  1  File not found or invalid JSON
  2  Schema version incompatible
  3  Content hash mismatch
```

### 10.3 `lore snapshot`

```
lore snapshot [OPTIONS]

Actions (mutually exclusive):
  (no args)                       Create a new snapshot
  --list                          List available snapshots
  --restore NAME                  Restore from named snapshot
  --latest                        With --restore: use most recent snapshot
  --delete NAME                   Delete a specific snapshot
  --older-than DURATION           With --delete: delete snapshots older than
                                  duration (e.g., 30d, 4w, 6m)

Options:
  --yes                           Skip confirmation prompt on restore
  --max-snapshots N               Override default max (50)
  --db PATH                       Database path (default: ~/.lore/memories.db)

Output (create):
  Snapshot created: 2026-03-14-153045
    Path: ~/.lore/snapshots/2026-03-14-153045.json
    Memories: 1234
    Size: 2.3 MB
    Snapshots retained: 12/50

Output (list):
  Available snapshots:
    NAME                  MEMORIES  SIZE     DATE
    2026-03-14-153045     1234      2.3 MB   2026-03-14 15:30:45
    2026-03-13-091200     1230      2.2 MB   2026-03-13 09:12:00
    ...

Output (restore):
  Restore snapshot 2026-03-14-153045? This will overwrite existing data. [y/N]
  > y
  Restoring...
  Import complete: 1234 imported, 0 skipped.
```

### 10.4 CLI Integration in `cli.py`

Add three new subcommand parsers following the existing pattern (argparse):

```python
# In build_parser() — add alongside existing subparsers:

# --- export ---
export_parser = subparsers.add_parser("export", help="Export memories and knowledge graph")
export_parser.add_argument("--format", choices=["json", "markdown", "both"], default="json")
export_parser.add_argument("--output", type=str, default=None)
export_parser.add_argument("--project", type=str, default=None)
export_parser.add_argument("--type", type=str, default=None)
export_parser.add_argument("--tier", choices=["working", "short", "long"], default=None)
export_parser.add_argument("--since", type=str, default=None)
export_parser.add_argument("--include-embeddings", action="store_true")
export_parser.add_argument("--pretty", action="store_true")
export_parser.set_defaults(func=cmd_export)

# --- import ---
import_parser = subparsers.add_parser("import", help="Import from JSON export file")
import_parser.add_argument("file", type=str)
import_parser.add_argument("--overwrite", action="store_true")
import_parser.add_argument("--dry-run", action="store_true")
import_parser.add_argument("--project", type=str, default=None)
import_parser.add_argument("--skip-embeddings", action="store_true")
import_parser.add_argument("--redact", action="store_true")
import_parser.set_defaults(func=cmd_import)

# --- snapshot ---
snap_parser = subparsers.add_parser("snapshot", help="Snapshot management")
snap_parser.add_argument("--list", action="store_true")
snap_parser.add_argument("--restore", type=str, nargs="?", const="__prompt__")
snap_parser.add_argument("--latest", action="store_true")
snap_parser.add_argument("--delete", type=str, nargs="?", const="__prompt__")
snap_parser.add_argument("--older-than", type=str, default=None)
snap_parser.add_argument("--yes", action="store_true")
snap_parser.add_argument("--max-snapshots", type=int, default=50)
snap_parser.set_defaults(func=cmd_snapshot)
```

---

## 11. MCP Tool Schema

### 11.1 `lore.export`

```python
@mcp.tool(
    description=(
        "Export all memories and knowledge graph to a JSON file for backup or migration. "
        "USE THIS WHEN: you want to back up data before a risky operation, migrate to "
        "a new machine, or create a portable copy of the knowledge base. "
        "Returns the file path and summary counts."
    ),
)
def export(
    format: str = "json",
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    since: Optional[str] = None,
    include_embeddings: bool = False,
    output: Optional[str] = None,
) -> str:
    """Export memories and knowledge graph."""
    try:
        lore = _get_lore()
        result = lore.export_data(
            format=format,
            project=project,
            type=type,
            tier=tier,
            since=since,
            include_embeddings=include_embeddings,
            output=output,
        )
        return (
            f"Export complete: {result.path}\n"
            f"  Memories: {result.memories}\n"
            f"  Entities: {result.entities}\n"
            f"  Relationships: {result.relationships}\n"
            f"  Facts: {result.facts}\n"
            f"  Hash: {result.content_hash}"
        )
    except Exception as e:
        return f"Export failed: {e}"
```

### 11.2 `lore.snapshot`

```python
@mcp.tool(
    description=(
        "Create a quick snapshot backup of all Lore data. "
        "USE THIS WHEN: you're about to run consolidation, bulk operations, "
        "or any mutation that could go wrong. Snapshots are stored locally "
        "at ~/.lore/snapshots/ and can be restored with snapshot_restore."
    ),
)
def snapshot() -> str:
    """Create a snapshot backup."""
    try:
        lore = _get_lore()
        from lore.export.snapshot import SnapshotManager
        mgr = SnapshotManager(lore)
        result = mgr.create()
        return (
            f"Snapshot created: {result['name']}\n"
            f"  Path: {result['path']}\n"
            f"  Memories: {result['memories']}\n"
            f"  Size: {result['size_human']}"
        )
    except Exception as e:
        return f"Snapshot failed: {e}"
```

### 11.3 `lore.snapshot_list`

```python
@mcp.tool(
    description=(
        "List available snapshots for restore. "
        "USE THIS WHEN: you want to see what backups are available "
        "before restoring or cleaning up old snapshots."
    ),
)
def snapshot_list() -> str:
    """List available snapshots."""
    try:
        lore = _get_lore()
        from lore.export.snapshot import SnapshotManager
        mgr = SnapshotManager(lore)
        snapshots = mgr.list()
        if not snapshots:
            return "No snapshots available."

        lines = ["Available snapshots:\n"]
        lines.append(f"{'NAME':<25} {'MEMORIES':>10} {'SIZE':>10} {'DATE'}")
        lines.append("-" * 70)
        for s in snapshots:
            lines.append(
                f"{s['name']:<25} {s['memories']:>10} {s['size_human']:>10} "
                f"{s['created_at'][:19]}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list snapshots: {e}"
```

---

## 12. REST API Endpoints

### 12.1 New Server Routes

Add `src/lore/server/routes/export.py`:

```python
# POST /api/v1/export
# Triggers export, returns JSON body (streamed for large datasets)
# Body: {"format": "json", "project": "...", "include_embeddings": false}
# Response: Full JSON export (Content-Type: application/json)
# Headers: X-Lore-Export-Memories, X-Lore-Export-Entities

# POST /api/v1/import
# Accepts JSON export body and imports it
# Query: ?overwrite=false&skip_embeddings=false
# Response: {"imported": 100, "skipped": 5, "errors": 0, "warnings": [...]}

# POST /api/v1/snapshots
# Creates server-side snapshot
# Response: {"name": "2026-03-14-153045", "memories": 1234}

# GET /api/v1/snapshots
# Lists available snapshots
# Response: {"snapshots": [...]}

# DELETE /api/v1/snapshots/:name
# Deletes a specific snapshot
# Response: 204 No Content
```

### 12.2 Bulk Data Endpoints (for HttpStore export)

New endpoints needed for HttpStore clients to pull graph data:

```
GET /api/v1/entities?entity_type=...&limit=1000
GET /api/v1/relationships?entity_id=...&limit=1000
GET /api/v1/entity-mentions?memory_id=...&limit=10000
GET /api/v1/facts?memory_id=...&limit=10000
GET /api/v1/conflicts?limit=10000
GET /api/v1/consolidation-logs?limit=10000
```

---

## 13. New Types

Add to `src/lore/types.py`:

```python
@dataclass
class ExportResult:
    """Result of an export operation."""

    path: str
    format: str
    memories: int
    entities: int
    relationships: int
    entity_mentions: int
    facts: int
    conflicts: int
    consolidation_logs: int
    content_hash: str
    duration_ms: int = 0


@dataclass
class ImportResult:
    """Result of an import operation."""

    total: int
    imported: int
    skipped: int
    overwritten: int
    errors: int
    warnings: List[str] = field(default_factory=list)
    embeddings_regenerated: int = 0
    duration_ms: int = 0


@dataclass
class ExportFilter:
    """Filters applied during export."""

    project: Optional[str] = None
    type: Optional[str] = None
    tier: Optional[str] = None
    since: Optional[str] = None
```

---

## 14. Lore Class Integration

### 14.1 New Methods on `Lore`

```python
# In lore.py — add to Lore class:

def export_data(
    self,
    format: str = "json",
    output: Optional[str] = None,
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    since: Optional[str] = None,
    include_embeddings: bool = False,
    pretty: bool = False,
) -> ExportResult:
    """Export memories and knowledge graph to file."""
    from lore.export.exporter import Exporter
    from lore.types import ExportFilter

    filters = ExportFilter(project=project, type=type, tier=tier, since=since)
    exporter = Exporter(store=self._store)
    return exporter.export(
        format=format,
        output=output,
        filters=filters,
        include_embeddings=include_embeddings,
        pretty=pretty,
    )

def import_data(
    self,
    file_path: str,
    overwrite: bool = False,
    skip_embeddings: bool = False,
    project_override: Optional[str] = None,
    dry_run: bool = False,
    redact: bool = False,
) -> ImportResult:
    """Import from JSON export file."""
    from lore.export.importer import Importer

    importer = Importer(
        store=self._store,
        embedder=self._embedder if not skip_embeddings else None,
        redaction_pipeline=self._redact if redact else None,
    )
    return importer.import_file(
        file_path=file_path,
        overwrite=overwrite,
        project_override=project_override,
        dry_run=dry_run,
    )
```

---

## 15. Testing Strategy

### 15.1 Unit Tests (`tests/test_export/`)

**Serializer tests** (`test_serializers.py`):
- `test_memory_to_dict_all_fields` — every Memory field survives serialization
- `test_dict_to_memory_all_fields` — every dict field maps back correctly
- `test_memory_roundtrip` — `dict_to_memory(memory_to_dict(m)) == m`
- `test_entity_roundtrip`, `test_relationship_roundtrip`, `test_fact_roundtrip`
- `test_conflict_roundtrip`, `test_consolidation_log_roundtrip`
- `test_entity_mention_roundtrip`
- `test_embedding_base64_roundtrip` — bytes → base64 → bytes
- `test_null_fields_preserved` — None fields serialize as null, not omitted
- `test_unicode_content` — emoji, CJK, RTL text survives roundtrip
- `test_empty_tags_serialized_as_array` — `[]` not `null`
- `test_memory_to_filename_safe` — special chars, long names, empty content
- `test_deterministic_ordering` — same data → same sort order

**Hash tests** (`test_hash.py`):
- `test_content_hash_deterministic` — same data → same hash
- `test_content_hash_differs_on_change` — any change → different hash
- `test_hash_verification_passes` — valid file → True
- `test_hash_verification_fails_on_corruption` — tampered file → ValueError
- `test_hash_ignores_envelope` — changing `exported_at` doesn't change hash

**Schema tests** (`test_schema.py`):
- `test_schema_version_check_current` — version 1 → accepted
- `test_schema_version_check_newer` — version 2 → rejected with upgrade message
- `test_schema_version_check_older` — version 0 → accepted with defaults applied

### 15.2 Integration Tests (`tests/test_export/`)

**Round-trip test** (`test_roundtrip.py`):
```python
def test_full_roundtrip():
    """Export → wipe → import → export → diff = zero."""
    # 1. Create diverse test data (memories, entities, rels, facts, conflicts)
    # 2. Export to JSON
    # 3. Wipe database
    # 4. Import from JSON
    # 5. Export again
    # 6. Compare: second export must be byte-identical to first
    #    (given deterministic ordering and embedding exclusion)
```

- `test_roundtrip_with_embeddings` — includes embeddings, byte-identical
- `test_roundtrip_filtered_export` — filtered export → import → verify only filtered data present
- `test_roundtrip_with_project_override` — import with `--project` changes project field
- `test_roundtrip_graph_integrity` — entities, relationships, mentions all preserved
- `test_roundtrip_facts_and_conflicts` — facts + conflict log preserved

**Import tests** (`test_import.py`):
- `test_import_idempotent` — import twice → second run all skipped, no duplicates
- `test_import_overwrite` — `--overwrite` replaces existing memories
- `test_import_dry_run` — no data written, correct counts returned
- `test_import_empty_database` — import into fresh DB works
- `test_import_orphaned_relationship_warning` — missing entity → warning, not error
- `test_import_regenerates_embeddings` — embeddings null in export → regenerated
- `test_import_preserves_embeddings_when_included` — `--include-embeddings` → no re-embed
- `test_import_schema_version_mismatch` — newer schema → abort

**Markdown export tests** (`test_markdown.py`):
- `test_markdown_directory_structure` — correct subdirs created
- `test_markdown_frontmatter` — YAML frontmatter has all fields
- `test_markdown_wikilinks` — entity cross-references use `[[...]]`
- `test_markdown_entity_backlinks` — entity files list mentioning memories
- `test_markdown_relationships_table` — `graph/relationships.md` has all edges
- `test_markdown_filename_safety` — special chars sanitized

**Snapshot tests** (`test_snapshot.py`):
- `test_snapshot_create` — creates file in `~/.lore/snapshots/`
- `test_snapshot_list` — lists snapshots with metadata
- `test_snapshot_restore` — restore imports data correctly
- `test_snapshot_restore_latest` — `--latest` picks most recent
- `test_snapshot_delete` — removes specific snapshot
- `test_snapshot_auto_prune` — >50 snapshots → oldest deleted
- `test_snapshot_cleanup_older_than` — `--older-than 30d` deletes old ones

### 15.3 Performance Tests (`tests/test_export/test_performance.py`)

```python
@pytest.mark.slow
def test_export_10k_memories_under_5s():
    """Export 10,000 memories (JSON, no embeddings) in under 5 seconds."""
    # Generate 10K synthetic memories
    # Time the export
    # Assert < 5.0 seconds

@pytest.mark.slow
def test_export_100k_memories_under_30s():
    """Export 100,000 memories (JSON, no embeddings) in under 30 seconds."""

@pytest.mark.slow
def test_import_10k_memories_skip_embeddings_under_10s():
    """Import 10,000 memories (skip embeddings) in under 10 seconds."""

@pytest.mark.slow
def test_markdown_export_10k_memories_under_10s():
    """Export 10,000 memories (Markdown) in under 10 seconds."""
```

### 15.4 Edge Case Tests

- `test_export_empty_database` — produces valid JSON with zero counts
- `test_export_unicode_and_emoji` — content with 🎉, 日本語, عربي
- `test_export_very_long_content` — 100KB content field
- `test_export_null_everywhere` — all optional fields null
- `test_export_archived_memories` — archived memories included
- `test_export_expired_memories` — expired but not cleaned up memories included
- `test_import_corrupted_json` — malformed JSON → clear error
- `test_import_missing_required_fields` — missing `id` or `content` → skip + warning
- `test_import_extra_unknown_fields` — forward-compatible, ignore unknown fields

### 15.5 Store Method Tests

- `test_sqlite_list_all_facts` — returns all facts, filtered by memory_ids
- `test_sqlite_list_all_entity_mentions` — returns all mentions, filtered
- `test_sqlite_list_all_conflicts` — returns all conflict log entries
- `test_sqlite_list_all_consolidation_logs` — returns all log entries
- `test_sqlite_list_all_facts_empty` — empty table → empty list
- `test_base_store_defaults_return_empty` — ABC no-op methods return []

---

## 16. Implementation Sequence

### Phase 1: Core Infrastructure (Stories 1-3)
1. `export/serializers.py` — dataclass ↔ dict conversion, filename generation
2. `export/schema.py` — version constant, validation, hash computation
3. New types in `types.py` — `ExportResult`, `ImportResult`, `ExportFilter`
4. Store ABC additions — 4 new bulk list methods
5. SqliteStore implementations — 4 concrete methods

### Phase 2: JSON Export (Stories 4-5)
6. `export/exporter.py` — core export engine
7. `Lore.export_data()` method
8. CLI `export` command
9. Round-trip tests + serializer tests

### Phase 3: JSON Import (Stories 6-8)
10. `export/importer.py` — core import engine with dedup
11. `Lore.import_data()` method
12. CLI `import` command
13. Import tests + idempotency tests

### Phase 4: Markdown Export (Story 9)
14. `export/markdown.py` — Obsidian-compatible renderer
15. Markdown tests

### Phase 5: Snapshot Management (Stories 10-11)
16. `export/snapshot.py` — snapshot lifecycle
17. CLI `snapshot` command
18. Snapshot tests

### Phase 6: MCP + REST Integration (Stories 12-13)
19. MCP tools — `export`, `snapshot`, `snapshot_list`
20. REST endpoints — export, import, snapshots
21. Integration tests

---

## 17. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Memory pressure on 100K+ exports | OOM crash | Streaming JSON writer; defer full in-memory to v2 |
| HttpStore missing graph endpoints | Incomplete export | v1 exports memories only for HttpStore; graph endpoints added in parallel |
| Schema evolution breaks old exports | Import failure | `schema_version` field + forward-compatible defaults |
| SQLite read consistency | Torn reads across queries | Explicit `BEGIN DEFERRED` transaction |
| Embedding model version mismatch | Poor recall after import | Document: re-embed after import is default behavior |
| Large snapshot directory | Disk exhaustion | Auto-prune at 50; `--older-than` cleanup |
| PII in export files | Data leak | Document risk; recommend 0600 permissions; defer encryption to v2 |

---

## 18. Open Decisions for Implementation

1. **Snapshot restore semantics:** Should `--restore` wipe the database first, or import over existing data with `--overwrite`? **Recommendation:** Import with `--overwrite` (additive, not destructive). Users who want a clean restore can `lore forget --all` first (needs implementation).

2. **HttpStore graph export:** The current HttpStore has no endpoints for entities, relationships, or facts. Options: (a) add server endpoints first, (b) skip graph data for HttpStore exports in v1, (c) make the exporter pull via individual per-memory calls (N+1, slow). **Recommendation:** (b) for v1 — document the limitation, add server endpoints in a follow-up.

3. **`import` as a subcommand name:** Python's `import` is a reserved keyword. The CLI uses it as a string argument to argparse which is fine, but the `cmd_import` function name and any module named `import.py` would conflict. **Resolution:** File is `importer.py`, function is `cmd_import`, no conflicts.
