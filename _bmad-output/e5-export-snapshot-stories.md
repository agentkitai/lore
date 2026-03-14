# E5: Export / Snapshot — Sprint Stories

**Epic:** E5 — Safety Net
**Sprint:** v0.10.0
**Scrum Master:** Bob
**Date:** March 14, 2026
**Total Stories:** 13
**Estimated Effort:** ~40-52 hours

---

## Sprint Overview

| Batch | Stories | Theme | Parallelizable |
|-------|---------|-------|----------------|
| 1 | S1, S2, S3 | Core Infrastructure | Yes (all 3) |
| 2 | S4, S5 | JSON Export | Yes (S4+S5 after Batch 1) |
| 3 | S6, S7 | JSON Import | Yes (S6+S7 after Batch 2) |
| 4 | S8 | Markdown Export | Sequential (after Batch 1) |
| 5 | S9, S10 | Snapshot Management | Yes (S9+S10 after Batch 2+3) |
| 6 | S11, S12, S13 | Integration Layer | Yes (all 3 after Batch 5) |

**Note:** Batch 4 (Markdown Export) can run in parallel with Batches 2-3 since it only depends on Batch 1 (serializers).

---

## Batch 1: Core Infrastructure (parallelizable)

### S1: Serializers — Dataclass-to-Dict Conversion

**Size:** M (2-4h)

**Description:**
Create `src/lore/export/serializers.py` with bidirectional conversion functions for all data types (Memory, Entity, Relationship, EntityMention, Fact, ConflictEntry, ConsolidationLogEntry). These are the foundation for both JSON and Markdown export. Include embedding base64 serialization and filesystem-safe filename generation.

**Dependencies:** None

**Acceptance Criteria:**
- `memory_to_dict(memory, include_embedding)` converts all Memory fields to a JSON-serializable dict
- `dict_to_memory(d)` reconstructs a Memory from a dict, applying defaults for missing optional fields
- Equivalent `*_to_dict` / `dict_to_*` pairs for Entity, Relationship, EntityMention, Fact, ConflictEntry, ConsolidationLogEntry
- `serialize_embedding(bytes) -> base64_str` and `deserialize_embedding(base64_str) -> bytes`
- `memory_to_filename(memory) -> str` generates filesystem-safe slugified filenames (max 200 chars)
- Embeddings excluded (set to null) when `include_embedding=False`
- `tags` always serialized as array, never null
- All optional None fields serialize as `null`, not omitted

**Test Scenarios:**
- `test_memory_to_dict_all_fields` — every Memory field present in output
- `test_dict_to_memory_all_fields` — every dict field maps back correctly
- `test_memory_roundtrip` — `dict_to_memory(memory_to_dict(m)) == m` for all fields
- `test_entity_roundtrip`, `test_relationship_roundtrip`, `test_fact_roundtrip`
- `test_conflict_roundtrip`, `test_consolidation_log_roundtrip`, `test_entity_mention_roundtrip`
- `test_embedding_base64_roundtrip` — bytes -> base64 -> bytes identical
- `test_null_fields_preserved` — None fields serialize as null
- `test_unicode_content` — emoji, CJK, RTL text survives roundtrip
- `test_empty_tags_serialized_as_array` — `[]` not `null`
- `test_memory_to_filename_safe` — special chars stripped, long names truncated
- `test_memory_to_filename_empty_content` — falls back to ID-only filename
- `test_deterministic_ordering` — sorted output for same input data

---

### S2: Schema & Hash — Version Validation and Content Integrity

**Size:** S (1-2h)

**Description:**
Create `src/lore/export/schema.py` with export schema version constant (v1), schema validation for imports, and SHA-256 content hash computation/verification. The hash covers the `data` object only (not envelope metadata).

**Dependencies:** None

**Acceptance Criteria:**
- `EXPORT_SCHEMA_VERSION = 1` constant defined
- `validate_schema_version(version)` accepts current or older, rejects newer with upgrade message
- `compute_content_hash(data_dict) -> "sha256:..."` produces deterministic hash via canonical JSON (sorted keys, no whitespace)
- `verify_content_hash(export_dict)` raises `ValueError` on mismatch, passes silently on match
- Legacy exports without `content_hash` field skip verification (return True)
- Hash is deterministic: same data always produces same hash

**Test Scenarios:**
- `test_schema_version_check_current` — version 1 accepted
- `test_schema_version_check_newer` — version 2 rejected with clear upgrade message
- `test_schema_version_check_older` — version 0 accepted
- `test_content_hash_deterministic` — same data, multiple calls, same hash
- `test_content_hash_differs_on_change` — any field change produces different hash
- `test_hash_verification_passes` — valid export dict passes
- `test_hash_verification_fails_on_corruption` — tampered data raises ValueError
- `test_hash_ignores_envelope` — changing `exported_at` doesn't affect hash
- `test_legacy_export_no_hash` — missing `content_hash` key skips verification

---

### S3: New Types & Store ABC Bulk Methods

**Size:** M (2-4h)

**Description:**
Add `ExportResult`, `ImportResult`, and `ExportFilter` dataclasses to `types.py`. Add four new bulk-read methods to Store ABC (`list_all_facts`, `list_all_entity_mentions`, `list_all_conflicts`, `list_all_consolidation_logs`) with default no-op implementations. Implement concrete versions in `SqliteStore`. Create `src/lore/export/__init__.py` with public API surface.

**Dependencies:** None

**Acceptance Criteria:**
- `ExportResult` dataclass: path, format, memories, entities, relationships, entity_mentions, facts, conflicts, consolidation_logs, content_hash, duration_ms
- `ImportResult` dataclass: total, imported, skipped, overwritten, errors, warnings, embeddings_regenerated, duration_ms
- `ExportFilter` dataclass: project, type, tier, since (all Optional)
- Store ABC has 4 new methods with default `return []` implementations
- `SqliteStore.list_all_facts(memory_ids=None)` returns all facts, optionally filtered by memory IDs
- `SqliteStore.list_all_entity_mentions(memory_ids=None)` returns all mentions, optionally filtered
- `SqliteStore.list_all_conflicts(limit=10000)` returns all conflict entries ordered by resolved_at
- `SqliteStore.list_all_consolidation_logs(limit=10000)` returns all log entries ordered by created_at
- MemoryStore inherits no-op defaults (returns empty lists)
- `export/__init__.py` exists and exposes public API

**Test Scenarios:**
- `test_export_result_fields` — all fields accessible with correct types
- `test_import_result_defaults` — warnings defaults to empty list
- `test_export_filter_all_none` — unfiltered state
- `test_sqlite_list_all_facts` — returns facts, filtered by memory_ids
- `test_sqlite_list_all_facts_empty_filter` — empty memory_ids list returns empty
- `test_sqlite_list_all_entity_mentions` — returns mentions, filtered
- `test_sqlite_list_all_conflicts` — returns all conflict entries
- `test_sqlite_list_all_consolidation_logs` — returns all log entries
- `test_base_store_defaults_return_empty` — ABC default methods return `[]`

---

## Batch 2: JSON Export (parallelizable after Batch 1)

### S4: JSON Export Engine

**Size:** L (4-8h)

**Description:**
Create `src/lore/export/exporter.py` with the core `Exporter` class. Fetches all data from Store, applies filters, sorts deterministically, serializes via serializers, computes content hash, and writes streaming JSON to file. Supports `--include-embeddings` and `--pretty` options.

**Dependencies:** S1, S2, S3

**Acceptance Criteria:**
- `Exporter(store).export(format, output, filters, include_embeddings, pretty) -> ExportResult`
- Full export (no filters): exports all memories (including archived), entities, relationships, entity_mentions, facts, conflicts, consolidation_logs
- Filtered export (`--project`, `--type`, `--tier`, `--since`): scopes memories by filter, then scopes graph data to only entities/relationships connected to exported memories via mentions
- Filters are combinable
- Deterministic ordering: memories by `created_at`, entities by `name` (case-insensitive), relationships by `source_entity_id + target_entity_id`, etc.
- Embeddings excluded by default (null in output), included as base64 with `--include-embeddings`
- `--pretty` produces 2-space indented JSON
- Output file defaults to `./lore-export-YYYY-MM-DDTHHMMSS.json`
- Streaming JSON writer for large exports (doesn't build full JSON string in memory)
- Export envelope includes: schema_version, exported_at, lore_version, content_hash, filters, counts
- SQLite exports wrapped in `BEGIN DEFERRED` transaction for read consistency
- Returns `ExportResult` with path, counts, hash, duration

**Test Scenarios:**
- `test_export_full_json` — all data types present in output file
- `test_export_filtered_by_project` — only matching memories and their linked graph data
- `test_export_filtered_by_type` — only matching type
- `test_export_filtered_by_tier` — only matching tier
- `test_export_filtered_by_since` — only memories after date
- `test_export_combined_filters` — project + since
- `test_export_graph_scoping` — filtered export includes only connected entities/rels
- `test_export_deterministic_ordering` — export twice, identical output
- `test_export_embeddings_excluded_default` — embedding fields are null
- `test_export_embeddings_included` — `--include-embeddings` produces base64 values
- `test_export_pretty_print` — indented JSON output
- `test_export_default_filename` — timestamp-based filename
- `test_export_custom_output_path` — `--output` respected
- `test_export_envelope_metadata` — schema_version, lore_version, exported_at present
- `test_export_content_hash_in_envelope` — hash matches data
- `test_export_empty_database` — valid JSON with zero counts
- `test_export_archived_memories_included` — archived=True memories exported
- `test_export_expired_memories_included` — expired but not cleaned up memories exported

---

### S5: Lore.export_data() and CLI `export` Command

**Size:** M (2-4h)

**Description:**
Add `export_data()` method to the `Lore` class in `lore.py`. Add `lore export` CLI subcommand to `cli.py` with all options (--format, --output, --project, --type, --tier, --since, --include-embeddings, --pretty). Wire CLI to Lore facade.

**Dependencies:** S4

**Acceptance Criteria:**
- `Lore.export_data(format, output, project, type, tier, since, include_embeddings, pretty) -> ExportResult`
- CLI `lore export` with all documented options (--format json|markdown|both, --output, --project, --type, --tier, --since, --include-embeddings, --pretty)
- CLI prints summary: file path, counts, hash, duration
- Exit code 0 on success, 1 if no memories match filters, 2 on write error
- CLI uses existing argparse pattern (add_parser to existing subparsers)

**Test Scenarios:**
- `test_lore_export_data_json` — returns ExportResult, file exists
- `test_lore_export_data_with_filters` — filters passed through to exporter
- `test_cli_export_default` — `lore export` produces JSON file
- `test_cli_export_with_format` — `--format markdown` flag accepted
- `test_cli_export_with_project_filter` — `--project myapp` passes filter
- `test_cli_export_exit_code_no_matches` — exit 1 when no memories match

---

## Batch 3: JSON Import (parallelizable after Batch 2)

### S6: JSON Import Engine

**Size:** L (4-8h)

**Description:**
Create `src/lore/export/importer.py` with the core `Importer` class. Reads JSON export file, validates schema version and content hash, then imports data in dependency order (memories → entities → facts → relationships → mentions → conflicts → consolidation logs). Supports deduplication (skip by ID), overwrite mode, dry-run, project override, and embedding regeneration.

**Dependencies:** S1, S2, S3

**Acceptance Criteria:**
- `Importer(store, embedder, redaction_pipeline).import_file(file_path, overwrite, project_override, dry_run) -> ImportResult`
- Schema version validated before import; newer version rejected with upgrade message
- Content hash verified before import; mismatch raises clear error
- Import order: memories first, then entities, facts, relationships, entity_mentions, conflicts, consolidation_logs
- Default deduplication: existing records by ID are skipped
- `--overwrite`: existing records replaced
- `--dry-run`: scan all records, report counts, write nothing
- `--project <name>`: override project field on all imported memories
- Orphaned relationships (referencing missing entity IDs) logged as warnings, skipped
- Orphaned entity mentions (referencing missing entity or memory IDs) logged as warnings, skipped
- Embedding regeneration: after import, batch re-embed all imported memories using current embedder (unless embedder is None / skip-embeddings)
- If export includes non-null embeddings, preserve them as-is (no re-embed)
- Idempotent: importing same file twice produces same store state (second run = all skipped)
- Returns `ImportResult` with total, imported, skipped, overwritten, errors, warnings, embeddings_regenerated, duration_ms
- Handles malformed JSON with clear error message
- Handles missing required fields (id, content) by skipping record + warning

**Test Scenarios:**
- `test_import_full_json` — all data types imported
- `test_import_idempotent` — second import skips all, no duplicates
- `test_import_overwrite` — existing memories replaced
- `test_import_dry_run` — no data written, correct counts returned
- `test_import_project_override` — project field overridden on all memories
- `test_import_empty_database` — import into fresh DB works
- `test_import_orphaned_relationship_warning` — missing entity → warning, not error
- `test_import_orphaned_mention_warning` — missing entity/memory → warning
- `test_import_regenerates_embeddings` — null embeddings → regenerated after import
- `test_import_preserves_embeddings_when_included` — non-null embeddings kept as-is
- `test_import_schema_version_mismatch` — newer schema → abort with message
- `test_import_content_hash_mismatch` — tampered file → abort with message
- `test_import_corrupted_json` — malformed JSON → clear error
- `test_import_missing_required_fields` — missing id → skip + warning
- `test_import_extra_unknown_fields` — unknown fields ignored (forward-compatible)
- `test_import_order_enforced` — entities before relationships before mentions

---

### S7: Lore.import_data() and CLI `import` Command

**Size:** M (2-4h)

**Description:**
Add `import_data()` method to the `Lore` class. Add `lore import FILE` CLI subcommand with all options (--overwrite, --dry-run, --project, --skip-embeddings, --redact). Wire CLI to Lore facade.

**Dependencies:** S6

**Acceptance Criteria:**
- `Lore.import_data(file_path, overwrite, skip_embeddings, project_override, dry_run, redact) -> ImportResult`
- Passes `embedder=None` when `skip_embeddings=True`
- Passes `redaction_pipeline` when `redact=True`
- CLI `lore import FILE` with all documented options
- CLI prints import report: total, imported, skipped, errors, warnings, embeddings regenerated
- Dry-run output clearly labeled "Dry run — no changes written"
- Exit code 0 on success, 1 on file not found/invalid JSON, 2 on schema incompatible, 3 on hash mismatch

**Test Scenarios:**
- `test_lore_import_data` — returns ImportResult, data present in store
- `test_lore_import_data_skip_embeddings` — no embedder passed to importer
- `test_lore_import_data_dry_run` — no changes to store
- `test_cli_import_basic` — `lore import export.json` works
- `test_cli_import_overwrite_flag` — `--overwrite` passed through
- `test_cli_import_dry_run_output` — dry run output formatted correctly
- `test_cli_import_file_not_found` — exit 1 with error message

---

## Batch 4: Markdown Export (parallelizable with Batches 2-3, after Batch 1)

### S8: Markdown/Obsidian Export Renderer

**Size:** L (4-8h)

**Description:**
Create `src/lore/export/markdown.py` with Obsidian-compatible Markdown export. Generates directory structure with memory files (YAML frontmatter + content body + facts + entity wikilinks), entity files (with backlinks and relationship tables), graph relationships table, and export metadata file. Wire into Exporter for `--format markdown` and `--format both`.

**Dependencies:** S1, S3

**Acceptance Criteria:**
- `MarkdownRenderer.render(data, output_dir) -> ExportResult`
- Directory structure: `memories/<type>/<id-slug>.md`, `entities/<name>.md`, `graph/relationships.md`, `_export_meta.md`
- Memory files have YAML frontmatter (id, type, tier, project, tags, confidence, importance_score, upvotes, downvotes, created_at, updated_at, source)
- Memory files have content body, Facts table (if any), Entities section with `[[wikilinks]]`
- Entity files have frontmatter (id, entity_type, aliases, mention_count, first_seen_at)
- Entity files have "Mentioned In" section with backlinks (`[[id-slug]]` — content preview)
- Entity files have Relationships table (Direction, Type, Entity with wikilinks)
- `graph/relationships.md` has a table of all relationship edges
- `_export_meta.md` has export timestamp, Lore version, counts, filters
- Filenames are filesystem-safe: special chars stripped, slugified, max 200 chars
- Subdirectories created per memory type
- Works with `--format both` (JSON + Markdown in one call)
- Filtered exports produce correctly scoped Markdown

**Test Scenarios:**
- `test_markdown_directory_structure` — correct subdirs created for all memory types
- `test_markdown_memory_frontmatter` — YAML frontmatter has all expected fields
- `test_markdown_memory_content_body` — content appears after frontmatter
- `test_markdown_memory_facts_section` — facts table present when facts exist
- `test_markdown_memory_wikilinks` — entity cross-references use `[[...]]`
- `test_markdown_entity_file_backlinks` — entity files list mentioning memories with previews
- `test_markdown_entity_relationships_table` — entity file has relationship table
- `test_markdown_relationships_file` — `graph/relationships.md` has all edges
- `test_markdown_export_meta` — `_export_meta.md` has correct metadata
- `test_markdown_filename_safety` — special chars sanitized, long names truncated
- `test_markdown_filtered_export` — filtered export produces scoped Markdown
- `test_markdown_format_both` — JSON and Markdown produced together
- `test_markdown_unicode_filenames` — CJK/emoji in content handled
- `test_markdown_empty_database` — produces valid directory with meta file only

---

## Batch 5: Snapshot Management (after Batches 2+3)

### S9: Snapshot Manager — Create, List, Delete, Prune

**Size:** M (2-4h)

**Description:**
Create `src/lore/export/snapshot.py` with `SnapshotManager` class. Manages snapshot lifecycle: create (full JSON export to `~/.lore/snapshots/`), list (with metadata parsing), delete (by name), cleanup (by age), and auto-prune (count-based, default max 50).

**Dependencies:** S4 (uses Exporter for create)

**Acceptance Criteria:**
- `SnapshotManager(lore, snapshots_dir, max_snapshots)` constructor
- `create() -> dict` — exports JSON to `~/.lore/snapshots/YYYY-MM-DD-HHMMSS.json`, returns {name, path, memories, size_human}
- `list() -> List[dict]` — lists snapshots sorted newest first, with name, created_at, memories count, size_bytes, size_human (parsed from first ~500 bytes of each file)
- `delete(name) -> bool` — deletes specific snapshot file, returns success
- `cleanup(older_than: str) -> int` — parses duration string (30d, 4w, 6m), deletes matching, returns count
- Auto-prune on create: if snapshot count exceeds `max_snapshots`, delete oldest until under limit
- Snapshots directory created on first use if it doesn't exist
- File permissions set to 0600 on created snapshot files

**Test Scenarios:**
- `test_snapshot_create` — creates file in snapshots dir, returns correct metadata
- `test_snapshot_create_directory_auto_created` — snapshots dir created if missing
- `test_snapshot_list` — lists snapshots with correct metadata, newest first
- `test_snapshot_list_empty` — returns empty list when no snapshots
- `test_snapshot_delete` — removes specific snapshot file
- `test_snapshot_delete_nonexistent` — returns False, no error
- `test_snapshot_cleanup_older_than_30d` — deletes old snapshots, keeps recent
- `test_snapshot_auto_prune` — creating snapshot when at max_snapshots deletes oldest
- `test_snapshot_auto_prune_count` — exactly max_snapshots retained after prune

---

### S10: Snapshot Restore and CLI `snapshot` Command

**Size:** M (2-4h)

**Description:**
Add restore functionality to `SnapshotManager`. Add `lore snapshot` CLI subcommand with all options (create, --list, --restore, --latest, --delete, --older-than, --yes, --max-snapshots). Restore uses `Lore.import_data()` with `overwrite=True`.

**Dependencies:** S7, S9

**Acceptance Criteria:**
- `SnapshotManager.restore(name, confirm_callback) -> ImportResult` — imports from named snapshot with overwrite=True
- `restore(name="__latest__")` resolves to most recent snapshot
- Restore prompts for confirmation unless `--yes` flag or confirm_callback returns True
- CLI `lore snapshot` (no args) creates a snapshot
- CLI `lore snapshot --list` shows table: NAME, MEMORIES, SIZE, DATE
- CLI `lore snapshot --restore <name>` restores with confirmation prompt
- CLI `lore snapshot --restore --latest` restores most recent
- CLI `lore snapshot --delete <name>` deletes specific snapshot
- CLI `lore snapshot --delete --older-than 30d` cleans up old snapshots
- CLI `--yes` skips confirmation on restore
- CLI `--max-snapshots N` overrides default 50

**Test Scenarios:**
- `test_snapshot_restore` — restores data from snapshot correctly
- `test_snapshot_restore_latest` — `--latest` picks most recent snapshot
- `test_snapshot_restore_confirmation_denied` — aborts when user declines
- `test_cli_snapshot_create` — `lore snapshot` creates and prints summary
- `test_cli_snapshot_list` — `--list` prints formatted table
- `test_cli_snapshot_restore` — `--restore <name>` prompts and imports
- `test_cli_snapshot_restore_latest` — `--restore --latest` works
- `test_cli_snapshot_delete` — `--delete <name>` removes file
- `test_cli_snapshot_cleanup` — `--delete --older-than 30d` removes old snapshots
- `test_cli_snapshot_yes_flag` — `--yes` skips confirmation

---

## Batch 6: Integration Layer (parallelizable, after Batch 5)

### S11: MCP Tools — export, snapshot, snapshot_list

**Size:** M (2-4h)

**Description:**
Add three MCP tools to `mcp/server.py`: `export` (triggers JSON export, returns path and counts), `snapshot` (creates snapshot, returns name and path), `snapshot_list` (lists available snapshots). Tools wrap `Lore.export_data()` and `SnapshotManager` with structured string responses.

**Dependencies:** S5, S9

**Acceptance Criteria:**
- `export` MCP tool with params: format, project, type, tier, since, include_embeddings, output
- Returns formatted string with path, memory/entity/relationship/fact counts, hash
- `snapshot` MCP tool (no params) creates snapshot, returns name, path, memories, size
- `snapshot_list` MCP tool lists snapshots in formatted table
- All tools handle errors gracefully (return error message string, don't crash server)
- Tools follow existing MCP tool patterns in server.py

**Test Scenarios:**
- `test_mcp_export_tool` — returns path and counts
- `test_mcp_export_tool_with_filters` — project/type filters applied
- `test_mcp_snapshot_tool` — creates snapshot, returns metadata
- `test_mcp_snapshot_list_tool` — lists snapshots
- `test_mcp_snapshot_list_empty` — "No snapshots available." message
- `test_mcp_export_tool_error_handling` — returns error string on failure

---

### S12: REST API Endpoints — Export, Import, Snapshots

**Size:** L (4-8h)

**Description:**
Create `src/lore/server/routes/export.py` with REST API endpoints: `POST /api/v1/export` (triggers export, returns JSON body), `POST /api/v1/import` (accepts JSON body, imports), `POST /api/v1/snapshots` (creates snapshot), `GET /api/v1/snapshots` (lists), `DELETE /api/v1/snapshots/:name` (deletes). All endpoints require API key auth (existing middleware).

**Dependencies:** S5, S7, S9

**Acceptance Criteria:**
- `POST /api/v1/export` — body: {format, project, include_embeddings}; response: streamed JSON export; headers: X-Lore-Export-Memories, X-Lore-Export-Entities
- `POST /api/v1/import` — body: JSON export content; query: ?overwrite=false&skip_embeddings=false; response: {imported, skipped, errors, warnings}
- `POST /api/v1/snapshots` — creates server-side snapshot; response: {name, memories}
- `GET /api/v1/snapshots` — response: {snapshots: [...]}
- `DELETE /api/v1/snapshots/:name` — response: 204 No Content
- All endpoints require API key auth (existing middleware)
- Error responses follow existing API error format

**Test Scenarios:**
- `test_api_export_json` — POST /api/v1/export returns valid JSON export
- `test_api_export_with_filters` — project filter applied
- `test_api_import` — POST /api/v1/import imports data, returns counts
- `test_api_import_overwrite` — ?overwrite=true replaces existing
- `test_api_import_dry_run` — reports but doesn't write
- `test_api_snapshot_create` — POST /api/v1/snapshots returns name
- `test_api_snapshot_list` — GET /api/v1/snapshots returns array
- `test_api_snapshot_delete` — DELETE returns 204
- `test_api_auth_required` — all endpoints reject unauthenticated requests

---

### S13: Round-Trip Integration Tests and Performance Benchmarks

**Size:** L (4-8h)

**Description:**
Comprehensive integration tests proving end-to-end correctness: create diverse data → export → wipe → import → export → diff = zero. Performance benchmarks for 10K and 100K memory datasets. Edge case coverage for unicode, null fields, archived/expired memories.

**Dependencies:** S5, S7, S8 (all export/import features complete)

**Acceptance Criteria:**
- Full round-trip test: export JSON → wipe DB → import → export again → byte-identical output (given deterministic ordering, embeddings excluded)
- Round-trip with embeddings: include-embeddings export → import → identical
- Round-trip with filtered export: filtered data only present after import
- Round-trip with project override: project field changed on all imported memories
- Round-trip graph integrity: entities, relationships, mentions all preserved
- Round-trip facts and conflicts: facts + conflict log + consolidation log preserved
- Edge cases: empty DB, unicode/emoji content, 100KB content, all-null optional fields, archived memories, expired memories
- Performance: export 10K memories JSON < 5s
- Performance: export 100K memories JSON < 30s
- Performance: import 10K memories (skip embeddings) < 10s
- Performance: export 10K memories Markdown < 10s

**Test Scenarios:**
- `test_full_roundtrip` — export → wipe → import → export → diff = zero
- `test_roundtrip_with_embeddings` — byte-identical with embeddings
- `test_roundtrip_filtered_export` — only filtered data present
- `test_roundtrip_with_project_override` — project field changed
- `test_roundtrip_graph_integrity` — entities, rels, mentions preserved
- `test_roundtrip_facts_and_conflicts` — facts + conflict log preserved
- `test_export_empty_database` — valid JSON with zero counts
- `test_export_unicode_and_emoji` — 🎉, 日本語, عربي
- `test_export_very_long_content` — 100KB content field
- `test_export_null_everywhere` — all optional fields null
- `test_export_archived_memories` — archived included
- `test_export_expired_memories` — expired included
- `test_import_corrupted_json` — malformed JSON → clear error
- `test_import_missing_required_fields` — skip + warning
- `test_import_extra_unknown_fields` — forward-compatible
- `test_export_10k_memories_under_5s` — performance benchmark
- `test_export_100k_memories_under_30s` — performance benchmark
- `test_import_10k_skip_embeddings_under_10s` — performance benchmark
- `test_markdown_export_10k_under_10s` — performance benchmark

---

## Dependency Graph

```
S1 (Serializers) ──┬──► S4 (JSON Export) ──► S5 (CLI export) ──┬──► S11 (MCP Tools)
                   │                                            ├──► S12 (REST API)
S2 (Schema/Hash) ──┤                                            │
                   │                                            │
S3 (Types/Store) ──┼──► S6 (JSON Import) ──► S7 (CLI import) ──┤
                   │                                            │
                   └──► S8 (Markdown) ──────────────────────────┤
                                                                │
                        S4 ──► S9 (Snapshot Mgr) ──┐            │
                               S7 ──► S10 (Snap CLI)──► S11,S12│
                                                                │
                                            S5+S7+S8 ──► S13 (Integration Tests)
```

## Effort Summary

| Story | Size | Estimate | Batch |
|-------|------|----------|-------|
| S1: Serializers | M | 2-4h | 1 |
| S2: Schema & Hash | S | 1-2h | 1 |
| S3: Types & Store Methods | M | 2-4h | 1 |
| S4: JSON Export Engine | L | 4-8h | 2 |
| S5: CLI export + Lore.export_data | M | 2-4h | 2 |
| S6: JSON Import Engine | L | 4-8h | 3 |
| S7: CLI import + Lore.import_data | M | 2-4h | 3 |
| S8: Markdown Export | L | 4-8h | 4 |
| S9: Snapshot Manager | M | 2-4h | 5 |
| S10: Snapshot CLI + Restore | M | 2-4h | 5 |
| S11: MCP Tools | M | 2-4h | 6 |
| S12: REST API Endpoints | L | 4-8h | 6 |
| S13: Integration + Perf Tests | L | 4-8h | 6 |
| **Total** | | **~33-66h** | |

## Parallelization Strategy

**Maximum parallelism (3 agents):**

```
Time ──────────────────────────────────────────────────────────►

Agent 1:  [S1 Serializers]──[S4 JSON Export]──[S5 CLI export]──[S9 Snapshot]──[S11 MCP]
Agent 2:  [S2 Schema/Hash]──[S6 JSON Import]──[S7 CLI import]──[S10 Snap CLI]──[S12 REST]
Agent 3:  [S3 Types/Store]──[S8 Markdown Export]──────────────────────────────[S13 Tests]
```

**Critical path:** S3 → S4 → S5 → S9 → S10 → S12 (~16-32h elapsed)
