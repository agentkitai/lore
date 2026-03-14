# E5: Export / Snapshot — Product Requirements Document

**Epic:** E5 — Safety Net
**Version:** v0.10.0 (Sprint 1 alongside E2)
**Author:** John (Product Manager)
**Date:** March 14, 2026
**Status:** Draft

---

## 1. Overview & Problem Statement

### Problem
Lore stores all agent knowledge in SQLite (local) or Postgres (remote). Users have no way to:
- **Back up** their data before risky operations (consolidation, migrations, upgrades)
- **Migrate** between storage backends (SQLite → Postgres, machine A → machine B)
- **Audit** what their AI agents have stored (export to human-readable format)
- **Interoperate** with other knowledge tools (Obsidian, Notion, git-based notes)
- **Recover** from data corruption or accidental deletion

Trust requires a safety net. Users won't adopt Lore as their primary memory system if they can't get their data out.

### Solution
Deterministic, LLM-free export/import commands that produce portable snapshots of all Lore data — memories, knowledge graph (entities, relationships, mentions), facts, and conflict logs. Supports JSON (machine-readable, lossless round-trip) and Markdown (human-readable, Obsidian-compatible).

### Key Design Principle
**No LLM required. Ever.** Export and import are pure data operations. Round-trip integrity is the north star: `export → wipe → import = identical data` (excluding embeddings, which are regenerated deterministically from content).

---

## 2. User Stories

### US-1: Full JSON Export
**As a** Lore user,
**I want to** export all my memories and knowledge graph to a single JSON file,
**so that** I have a complete, portable backup of my data.

**Acceptance Criteria:**
- `lore export --format json` produces a single `.json` file
- File contains all memories (with metadata), entities, relationships, entity mentions, facts, and conflict log entries
- Embeddings are excluded by default (binary blobs, regeneratable), included with `--include-embeddings`
- File includes export metadata: Lore version, export timestamp, memory count, schema version
- Output file defaults to `./lore-export-YYYY-MM-DDTHHMMSS.json`, overridable with `--output`
- Export completes in <5s for 10,000 memories

### US-2: Filtered Export
**As a** Lore user,
**I want to** export a subset of my memories,
**so that** I can share project-specific knowledge or create targeted backups.

**Acceptance Criteria:**
- `--project <name>` exports only memories for that project (and their linked graph data)
- `--type <type>` exports only memories of that type
- `--since <date>` exports memories created/updated after that date
- `--tier <tier>` exports only memories in that tier
- Filters are combinable: `--project myapp --since 2026-01-01`
- Knowledge graph export includes only entities/relationships connected to exported memories

### US-3: Markdown Export (Obsidian-Compatible)
**As a** user who also uses Obsidian or other Markdown-based tools,
**I want to** export my memories as a folder of Markdown files,
**so that** I can browse them in Obsidian or commit them to git.

**Acceptance Criteria:**
- `lore export --format markdown` produces a directory structure:
  ```
  lore-export/
  ├── memories/
  │   ├── general/
  │   │   ├── <id>-<slug>.md
  │   ├── code/
  │   ├── lesson/
  │   └── ...
  ├── entities/
  │   ├── <name>.md          # One file per entity with backlinks
  ├── graph/
  │   └── relationships.md   # Table of all edges
  └── _export_meta.md        # Export metadata
  ```
- Each memory file has YAML frontmatter (id, type, tier, tags, project, confidence, created_at, updated_at, importance_score) and content body
- Entity files list all memories that mention the entity (backlinks)
- Files use Obsidian-compatible `[[wikilinks]]` for cross-references
- Filenames are filesystem-safe (slugified, no special chars, max 200 chars)

### US-4: Import from JSON Export
**As a** Lore user,
**I want to** import from a JSON export file,
**so that** I can restore a backup, migrate to a new machine, or merge data from another instance.

**Acceptance Criteria:**
- `lore import <file>` loads all data from a JSON export
- Deduplication by memory ID: existing memories are skipped (not overwritten) by default
- `--overwrite` flag: existing memories are replaced with imported versions
- `--dry-run` shows what would be imported without writing anything
- `--project <name>` overrides the project field for all imported memories
- Import validates schema version compatibility before proceeding
- Import regenerates embeddings for all imported memories (unless `--skip-embeddings`)
- Import report: total/imported/skipped/errors printed to stdout

### US-5: Quick Snapshot & Restore
**As a** Lore user,
**I want to** take a quick snapshot before running consolidation or other risky operations,
**so that** I can roll back if something goes wrong.

**Acceptance Criteria:**
- `lore snapshot` creates a timestamped JSON export at `~/.lore/snapshots/<YYYY-MM-DD-HHMMSS>.json`
- `lore snapshot --list` shows available snapshots with date, size, and memory count
- `lore snapshot --restore <name>` imports from a snapshot (with confirmation prompt)
- `lore snapshot --restore --latest` restores the most recent snapshot
- `lore snapshot --delete <name>` removes a snapshot
- `lore snapshot --delete --older-than 30d` cleans up old snapshots
- Maximum of 50 snapshots retained by default (configurable); oldest auto-pruned on new snapshot

### US-6: MCP Tool Access
**As an** AI agent connected via MCP,
**I want to** trigger exports and snapshots,
**so that** I can proactively back up data before performing risky operations.

**Acceptance Criteria:**
- `lore.export` MCP tool: triggers JSON export, returns file path
- `lore.snapshot` MCP tool: creates snapshot, returns snapshot name
- `lore.snapshot_list` MCP tool: lists available snapshots
- Tools respect all the same options as CLI commands
- Tools return structured results (not just stdout text)

### US-7: REST API Access
**As a** remote Lore server operator,
**I want to** export and snapshot via HTTP API,
**so that** automated systems can create backups.

**Acceptance Criteria:**
- `POST /api/v1/export` triggers export, returns JSON body (streamed for large datasets)
- `POST /api/v1/snapshots` creates a server-side snapshot
- `GET /api/v1/snapshots` lists available snapshots
- `POST /api/v1/import` accepts a JSON export body and imports it
- Standard auth (API key) required

---

## 3. Functional Requirements

### 3.1 Export Engine

**FR-1: Data completeness.** Export includes all data types:
| Data Type | JSON | Markdown |
|-----------|------|----------|
| Memories (all fields except embedding) | ✓ | ✓ (frontmatter + body) |
| Embeddings (raw bytes, base64) | opt-in | ✗ |
| Entities | ✓ | ✓ (one file per entity) |
| Relationships | ✓ | ✓ (table in graph/relationships.md) |
| Entity Mentions | ✓ | ✓ (backlinks in entity files) |
| Facts | ✓ | ✓ (table in memory frontmatter) |
| Conflict Log | ✓ | ✗ (JSON-only, too structured for MD) |
| Consolidation Log | ✓ | ✗ (JSON-only) |

**FR-2: Deterministic ordering.** Exported data is sorted by `created_at` (memories), `name` (entities), `source_entity_id + target_entity_id` (relationships). Same data → same output, always.

**FR-3: Embedding handling.** Embeddings are excluded by default. Rationale: they're binary blobs (384-dim float32 = 1.5KB per memory), regeneratable from content via ONNX, and not portable across embedding model versions. `--include-embeddings` serializes them as base64 for users who need exact restoration without re-embedding.

**FR-4: Atomicity.** Export reads a consistent snapshot. For SQLite, this means a single read transaction. For Postgres/HTTP, this means pagination with a fixed timestamp ceiling.

### 3.2 Import Engine

**FR-5: Schema validation.** Import checks `schema_version` in the export file. If the export schema is newer than the running Lore version, import fails with an upgrade message. If older, import applies forward-compatible migration (add missing fields with defaults).

**FR-6: Deduplication.** Default behavior: skip memories with IDs already present in the store. `--overwrite` replaces existing. `--merge` (future, out of scope for v1) would merge fields.

**FR-7: Embedding regeneration.** After import, all memories without embeddings are re-embedded using the current embedding model. This happens automatically unless `--skip-embeddings` is passed (useful for bulk import where you'll trigger a batch re-embed later).

**FR-8: Graph integrity.** Imported relationships reference entity IDs. Import processes entities first, then relationships, then mentions. Orphaned references (entity ID in relationship but entity not in export) are logged as warnings and skipped.

**FR-9: Idempotency.** Running the same import twice produces the same result. No duplicates, no errors (just "skipped" counts).

### 3.3 Snapshot Management

**FR-10: Snapshot storage.** Snapshots are full JSON exports stored at `~/.lore/snapshots/`. Directory is created on first use.

**FR-11: Auto-pruning.** When creating a new snapshot, if count exceeds `max_snapshots` (default 50), delete oldest until under limit.

**FR-12: Snapshot metadata.** Each snapshot file includes header metadata (timestamp, memory count, entity count, Lore version) so `--list` can display info without parsing the full file.

---

## 4. Non-Functional Requirements

### 4.1 Performance

| Scenario | Target |
|----------|--------|
| Export 1,000 memories (JSON, no embeddings) | < 1s |
| Export 10,000 memories (JSON, no embeddings) | < 5s |
| Export 100,000 memories (JSON, no embeddings) | < 30s |
| Export 10,000 memories (Markdown) | < 10s (filesystem I/O bound) |
| Import 10,000 memories (JSON, with re-embedding) | < 60s |
| Import 10,000 memories (JSON, skip embeddings) | < 10s |
| Snapshot creation | Same as JSON export |

**Streaming for large exports:** JSON export streams to file (not built in memory). Use `ijson` or line-by-line writing for the memories array.

### 4.2 Data Integrity

- **Round-trip guarantee:** `export → wipe → import → export` produces byte-identical JSON (given deterministic ordering and embedding exclusion).
- **Content hash verification:** Export includes a SHA-256 hash of the content payload. Import verifies this hash before proceeding.
- **No data loss:** Every field in the Memory, Entity, Relationship, EntityMention, Fact, ConflictEntry, and ConsolidationLogEntry dataclasses must survive round-trip. No silent field dropping.

### 4.3 Compatibility

- Python 3.9+ (match existing requirement)
- No new dependencies for JSON export/import (stdlib `json` module)
- Markdown export: no new dependencies (string formatting only)
- Works with both SqliteStore (local) and HttpStore (remote)

### 4.4 Security

- Exports may contain PII (memories are user content). No encryption in v1, but document the risk.
- Snapshot files inherit filesystem permissions (0600 recommended).
- API key required for REST endpoints (existing auth middleware).
- Import does NOT run content through redaction pipeline (data was already redacted on original save). Flag: `--redact` to optionally re-run redaction on import.

---

## 5. API Design

### 5.1 CLI Commands

```
lore export [OPTIONS]
  --format json|markdown|both    Export format (default: json)
  --output PATH                  Output file/directory path
  --project NAME                 Filter by project
  --type TYPE                    Filter by memory type
  --tier TIER                    Filter by tier
  --since DATE                   Only memories created/updated after DATE
  --include-embeddings           Include raw embedding vectors (base64)
  --pretty                       Pretty-print JSON (default: compact)

lore import FILE [OPTIONS]
  FILE                           Path to JSON export file
  --overwrite                    Replace existing memories on ID conflict
  --dry-run                      Show what would be imported, don't write
  --project NAME                 Override project for all imported memories
  --skip-embeddings              Don't regenerate embeddings after import
  --redact                       Re-run PII redaction on imported content

lore snapshot [OPTIONS]
  (no args)                      Create a new snapshot
  --list                         List available snapshots
  --restore NAME                 Restore from named snapshot
  --restore --latest             Restore from most recent snapshot
  --delete NAME                  Delete a specific snapshot
  --delete --older-than DURATION Delete snapshots older than duration (e.g., 30d, 4w)
```

### 5.2 MCP Tools

```python
@mcp.tool(description="Export all memories and knowledge graph to a JSON file for backup or migration.")
def export(
    format: str = "json",        # "json" or "markdown"
    project: Optional[str] = None,
    type: Optional[str] = None,
    since: Optional[str] = None,
    include_embeddings: bool = False,
) -> dict:
    """Returns: {"path": "/path/to/export.json", "memories": 1234, "entities": 56, "relationships": 78}"""

@mcp.tool(description="Create a quick snapshot backup of all Lore data.")
def snapshot() -> dict:
    """Returns: {"name": "2026-03-14-153045", "path": "~/.lore/snapshots/...", "memories": 1234}"""

@mcp.tool(description="List available snapshots for restore.")
def snapshot_list() -> dict:
    """Returns: {"snapshots": [{"name": "...", "created_at": "...", "memories": N, "size_bytes": N}]}"""
```

### 5.3 REST API Endpoints

```
POST /api/v1/export
  Body: {"format": "json", "project": "...", "include_embeddings": false}
  Response: streamed JSON export file (Content-Type: application/json)
  Headers: X-Lore-Export-Memories: 1234, X-Lore-Export-Entities: 56

POST /api/v1/import
  Body: JSON export file content
  Query: ?overwrite=false&skip_embeddings=false
  Response: {"imported": 100, "skipped": 5, "errors": 0}

POST /api/v1/snapshots
  Response: {"name": "2026-03-14-153045", "memories": 1234}

GET  /api/v1/snapshots
  Response: {"snapshots": [...]}

DELETE /api/v1/snapshots/:name
  Response: 204 No Content
```

### 5.4 SDK Method (Lore class)

```python
class Lore:
    def export_data(
        self,
        format: str = "json",
        output: Optional[str] = None,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
        since: Optional[str] = None,
        include_embeddings: bool = False,
    ) -> ExportResult:
        """Export memories + knowledge graph. Returns ExportResult with path and counts."""

    def import_data(
        self,
        file_path: str,
        overwrite: bool = False,
        skip_embeddings: bool = False,
        project_override: Optional[str] = None,
        dry_run: bool = False,
        redact: bool = False,
    ) -> ImportResult:
        """Import from JSON export file. Returns ImportResult with counts."""
```

---

## 6. Export Format Specification

### 6.1 JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["schema_version", "exported_at", "lore_version", "data"],
  "properties": {
    "schema_version": {
      "type": "integer",
      "const": 1,
      "description": "Export format version. Increment on breaking changes."
    },
    "exported_at": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 UTC timestamp of export."
    },
    "lore_version": {
      "type": "string",
      "description": "Lore SDK version that produced this export."
    },
    "content_hash": {
      "type": "string",
      "description": "SHA-256 of the 'data' object (serialized, sorted keys)."
    },
    "filters": {
      "type": "object",
      "description": "Filters applied during export (empty = full export).",
      "properties": {
        "project": {"type": "string"},
        "type": {"type": "string"},
        "tier": {"type": "string"},
        "since": {"type": "string"}
      }
    },
    "counts": {
      "type": "object",
      "properties": {
        "memories": {"type": "integer"},
        "entities": {"type": "integer"},
        "relationships": {"type": "integer"},
        "entity_mentions": {"type": "integer"},
        "facts": {"type": "integer"},
        "conflicts": {"type": "integer"},
        "consolidation_logs": {"type": "integer"}
      }
    },
    "data": {
      "type": "object",
      "properties": {
        "memories": {
          "type": "array",
          "items": {
            "type": "object",
            "description": "All Memory dataclass fields. embedding is base64 or null.",
            "required": ["id", "content", "type", "created_at"],
            "properties": {
              "id": {"type": "string"},
              "content": {"type": "string"},
              "type": {"type": "string"},
              "tier": {"type": "string"},
              "context": {"type": ["string", "null"]},
              "tags": {"type": "array", "items": {"type": "string"}},
              "metadata": {"type": ["object", "null"]},
              "source": {"type": ["string", "null"]},
              "project": {"type": ["string", "null"]},
              "embedding": {"type": ["string", "null"], "description": "Base64-encoded float32 array, or null"},
              "created_at": {"type": "string"},
              "updated_at": {"type": "string"},
              "ttl": {"type": ["integer", "null"]},
              "expires_at": {"type": ["string", "null"]},
              "confidence": {"type": "number"},
              "upvotes": {"type": "integer"},
              "downvotes": {"type": "integer"},
              "importance_score": {"type": "number"},
              "access_count": {"type": "integer"},
              "last_accessed_at": {"type": ["string", "null"]},
              "archived": {"type": "boolean"},
              "consolidated_into": {"type": ["string", "null"]}
            }
          }
        },
        "entities": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["id", "name", "entity_type"],
            "properties": {
              "id": {"type": "string"},
              "name": {"type": "string"},
              "entity_type": {"type": "string"},
              "aliases": {"type": "array", "items": {"type": "string"}},
              "description": {"type": ["string", "null"]},
              "metadata": {"type": ["object", "null"]},
              "mention_count": {"type": "integer"},
              "first_seen_at": {"type": "string"},
              "last_seen_at": {"type": "string"},
              "created_at": {"type": "string"},
              "updated_at": {"type": "string"}
            }
          }
        },
        "relationships": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["id", "source_entity_id", "target_entity_id", "rel_type"],
            "properties": {
              "id": {"type": "string"},
              "source_entity_id": {"type": "string"},
              "target_entity_id": {"type": "string"},
              "rel_type": {"type": "string"},
              "weight": {"type": "number"},
              "properties": {"type": ["object", "null"]},
              "source_fact_id": {"type": ["string", "null"]},
              "source_memory_id": {"type": ["string", "null"]},
              "valid_from": {"type": "string"},
              "valid_until": {"type": ["string", "null"]},
              "created_at": {"type": "string"},
              "updated_at": {"type": "string"}
            }
          }
        },
        "entity_mentions": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["id", "entity_id", "memory_id"],
            "properties": {
              "id": {"type": "string"},
              "entity_id": {"type": "string"},
              "memory_id": {"type": "string"},
              "mention_type": {"type": "string"},
              "confidence": {"type": "number"},
              "created_at": {"type": "string"}
            }
          }
        },
        "facts": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["id", "memory_id", "subject", "predicate", "object"],
            "properties": {
              "id": {"type": "string"},
              "memory_id": {"type": "string"},
              "subject": {"type": "string"},
              "predicate": {"type": "string"},
              "object": {"type": "string"},
              "confidence": {"type": "number"},
              "extracted_at": {"type": "string"},
              "invalidated_by": {"type": ["string", "null"]},
              "invalidated_at": {"type": ["string", "null"]},
              "metadata": {"type": ["object", "null"]}
            }
          }
        },
        "conflicts": {
          "type": "array",
          "items": {"type": "object"}
        },
        "consolidation_logs": {
          "type": "array",
          "items": {"type": "object"}
        }
      }
    }
  }
}
```

### 6.2 Markdown Structure

**Memory file example** (`memories/code/01HXYZ-fix-sqlite-lock.md`):
```markdown
---
id: 01HXYZ...
type: code
tier: long
project: lore
tags: [sqlite, concurrency, bugfix]
confidence: 0.95
importance_score: 0.82
upvotes: 3
downvotes: 0
created_at: 2026-02-15T10:30:00Z
updated_at: 2026-02-15T10:30:00Z
source: claude-code
---

SQLite WAL mode fixes the "database is locked" error under concurrent MCP connections.
Set `PRAGMA journal_mode=WAL` at connection time.

## Facts
| Subject | Predicate | Object |
|---------|-----------|--------|
| sqlite | uses | WAL mode |
| WAL mode | fixes | database locked error |

## Entities
- [[sqlite]]
- [[WAL mode]]
```

**Entity file example** (`entities/sqlite.md`):
```markdown
---
id: 01HABC...
entity_type: tool
aliases: [sqlite3, SQLite]
mention_count: 12
first_seen_at: 2026-01-05T08:00:00Z
---

# sqlite

## Mentioned In
- [[01HXYZ-fix-sqlite-lock]] — WAL mode for concurrency
- [[01HDEF-sqlite-perf]] — Query optimization tips
- ...

## Relationships
| Direction | Type | Entity |
|-----------|------|--------|
| → | uses | [[WAL mode]] |
| → | part_of | [[lore]] |
| ← | depends_on | [[lore-sdk]] |
```

---

## 7. Integration Patterns by Platform

### 7.1 OpenClaw (Hooks + MCP)
- **Pre-operation hooks:** Add `lore snapshot` as a pre-hook before `lore consolidate` or other mutating operations. Users configure this in their hook chain.
- **MCP tools:** `lore.export`, `lore.snapshot`, `lore.snapshot_list` available. Agent can proactively snapshot before risky operations.
- **Auto-backup hook (optional):** `lore-auto-snapshot` hook fires on session start, creates a daily snapshot if one doesn't exist for today.

### 7.2 Claude Code (MCP + CLAUDE.md)
- **MCP tools:** Same three tools available. Add to CLAUDE.md instructions: "Before running consolidation or bulk operations, call `lore.snapshot` to create a backup."
- **CLI fallback:** Users can run `lore export` or `lore snapshot` directly from terminal within Claude Code.
- **Obsidian bridge:** `lore export --format markdown --output ~/obsidian-vault/lore/` syncs Lore data into an Obsidian vault. Users can set this up as a cron job or manual workflow.

### 7.3 Codex (MCP)
- **MCP tools only.** Codex interacts via MCP. Same three tools.
- **No CLI access** from within Codex sessions — but users can run CLI commands outside.

### 7.4 Cursor (MCP + .cursorrules)
- **MCP tools:** Same three tools. Add to `.cursorrules`: "Use `lore.snapshot` before bulk memory operations."
- **CLI access:** Available via Cursor's terminal.

### 7.5 Cross-Platform Notes
- Export format is identical regardless of which platform triggers it.
- Import works the same whether triggered from CLI, MCP, or REST API.
- Snapshots are stored locally (`~/.lore/snapshots/`) regardless of storage backend (SQLite or Postgres). For remote Postgres users, snapshots are the exported JSON, not a Postgres dump.

---

## 8. Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Round-trip integrity | 100% field preservation | Automated test: export → wipe → import → export → diff |
| Export performance (10K memories) | < 5s | Benchmark test with synthetic dataset |
| Import performance (10K memories, no embeddings) | < 10s | Benchmark test |
| Snapshot adoption | 50% of active users create ≥1 snapshot in first month | Track via `lore stats` (add snapshot_count) |
| Zero data loss incidents | 0 reports of export/import data loss | GitHub issues tracking |
| Obsidian export usage | ≥20% of exports use Markdown format | CLI analytics (opt-in) |

---

## 9. Open Questions

### Decided (Opinionated)

**Q1: Should embeddings be included in exports by default?**
**Decision: No.** Embeddings are 1.5KB per memory (384-dim × 4 bytes), model-version-specific, and regeneratable. Including them by default would bloat exports 10x for no portability gain. Opt-in via `--include-embeddings` for users who need exact restoration.

**Q2: Should Markdown import be supported?**
**Decision: No (v1).** Markdown export is lossy by design (no conflict logs, no consolidation logs, human-edited content may not parse cleanly). JSON is the authoritative format. Markdown is a read-only view. If we add Markdown import later, it would need robust frontmatter parsing and graceful handling of user edits.

**Q3: Should `lore snapshot` run automatically before consolidation?**
**Decision: Yes, recommend but don't enforce.** Document it. Add a `--auto-snapshot` flag to `lore consolidate` that defaults to `true`. Users can opt out with `--no-auto-snapshot`. Don't add implicit side effects to existing commands without a flag.

**Q4: How to handle expired memories in export?**
**Decision: Export everything, mark expiry.** Expired memories are still in the store until `cleanup_expired()` runs. Export includes them with their `expires_at` field. Import preserves the field. Users can filter with `--tier` if they only want long-term memories.

### Open (Needs Discussion)

**Q5: Should export include redacted content or original content?**
Current behavior: PII is masked at write time (original is lost). Export will contain the redacted version since that's what's stored. But if we add reversible redaction in the future, this needs revisiting.

**Q6: Snapshot retention policy — time-based or count-based?**
Current proposal: count-based (max 50). Alternative: time-based (keep last 30 days). Or both? Leaning count-based for simplicity, with `--delete --older-than` for manual cleanup.

**Q7: Should we support incremental/delta export?**
Full export every time is simple but wasteful for large datasets. Incremental export (only changes since last export) is more efficient but adds complexity (tracking export watermarks). **Recommendation: defer to v2.** Full export is fine for v1 target (up to 100K memories).

**Q8: Remote store (HttpStore) export — who does the work?**
For HttpStore users, should `lore export` pull all data client-side and write locally, or should it trigger a server-side export? **Recommendation: client-side pull via existing REST endpoints for v1.** Add server-side `POST /api/v1/export` for efficiency later, but the CLI should work today by paginating through `GET /api/v1/memories`.

---

## 10. Implementation Notes

### Store Interface Additions
The `Store` ABC needs two new methods to support bulk reads efficiently:

```python
class Store(ABC):
    # Existing methods...

    def list_all_facts(self, memory_ids: Optional[List[str]] = None) -> List[Fact]:
        """List all facts, optionally filtered to specific memory IDs."""
        return []

    def list_all_entity_mentions(self, memory_ids: Optional[List[str]] = None) -> List[EntityMention]:
        """List all entity mentions, optionally filtered to specific memory IDs."""
        return []
```

### New Types

```python
@dataclass
class ExportResult:
    path: str
    format: str
    memories: int
    entities: int
    relationships: int
    facts: int
    content_hash: str

@dataclass
class ImportResult:
    total: int
    imported: int
    skipped: int
    errors: int
    warnings: List[str]
    embeddings_regenerated: int
```

### File Organization
```
src/lore/
├── export/
│   ├── __init__.py
│   ├── json_export.py      # JSON export/import logic
│   ├── markdown_export.py  # Markdown export logic
│   └── snapshot.py         # Snapshot management
```

### Test Strategy
- Unit tests for serialization/deserialization of every data type
- Round-trip integration test: create data → export → wipe → import → verify identical
- Performance benchmark with 10K and 100K synthetic memories
- Edge cases: empty database, Unicode content, very long content, null fields, archived memories, expired memories
- Filtered export: verify graph data is correctly scoped to exported memories
