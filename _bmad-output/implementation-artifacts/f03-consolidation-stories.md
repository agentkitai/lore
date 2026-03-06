# F3 -- Memory Consolidation / Auto-Summarization: User Stories

**Feature:** F3 -- Memory Consolidation / Auto-Summarization
**Version:** v0.6.0 ("Open Brain")
**Sprint Planning Date:** 2026-03-06
**Architecture Doc:** `_bmad-output/implementation-artifacts/f03-consolidation-architecture.md`
**PRD:** `_bmad-output/planning-artifacts/f03-consolidation-prd.md`
**Key Design:** Six-stage batch pipeline (identify, group, summarize, archive, relink, log) with dry-run default and graceful LLM degradation

---

## Sprint Overview

| Sprint | Theme | Stories | Focus |
|--------|-------|---------|-------|
| **Sprint 1** | Data Foundation | S1-S3 | Schema changes, types, store filtering |
| **Sprint 2** | Core Pipeline | S4-S7 | Candidate identification, dedup grouping, entity grouping, LLM summarization |
| **Sprint 3** | Execution & Audit | S8-S10 | Archive + relink + log, dry-run mode, full pipeline orchestration |
| **Sprint 4** | Surface & Scheduling | S11-S12 | MCP tool + CLI, scheduled consolidation + stats |

**Critical path:** S1 -> S2 -> S3 -> S4 -> S5 -> S8 -> S10

**Parallelism notes:**
- Sprint 1: S1 first (types), then S2 + S3 in parallel (both depend on S1 types only)
- Sprint 2: S4 depends on S2; S5 + S6 can run in parallel after S4; S7 independent of S5/S6
- Sprint 3: S8 depends on S5/S6/S7; S9 can run with S8; S10 depends on S8+S9
- Sprint 4: S11 depends on S10; S12 depends on S10

---

## Sprint 1: Data Foundation

### S1: Types, Dataclasses & Configuration Constants

**As a** developer,
**I want** `ConsolidationLogEntry`, `ConsolidationResult`, `DEFAULT_RETENTION_POLICIES`, and `DEFAULT_CONSOLIDATION_CONFIG` defined as dataclasses/constants, and `archived`/`consolidated_into` fields added to the `Memory` dataclass,
**so that** all subsequent consolidation stories have stable type definitions.

**Estimate:** S

**Dependencies:** None (F4 tier field and F5 importance_score already exist)

**Scope:**
- Add `archived: bool = False` and `consolidated_into: Optional[str] = None` fields to `Memory` dataclass in `src/lore/types.py`
- Add `ConsolidationLogEntry` dataclass per architecture doc section 2.2
- Add `ConsolidationResult` dataclass per architecture doc section 2.2
- Add `DEFAULT_RETENTION_POLICIES` and `DEFAULT_CONSOLIDATION_CONFIG` dicts per architecture doc section 2.2
- Add `archived_count: int = 0`, `consolidation_count: int = 0`, `last_consolidation_at: Optional[str] = None` to `MemoryStats` per architecture doc section 2.3

**Acceptance Criteria:**

```
GIVEN the updated Memory dataclass
WHEN a Memory is created with default values
THEN archived is False and consolidated_into is None

GIVEN the ConsolidationLogEntry dataclass
WHEN importing from src/lore/types.py
THEN all fields match architecture doc section 2.2 (id, consolidated_memory_id, original_memory_ids, strategy, model_used, original_count, created_at, metadata)

GIVEN the ConsolidationResult dataclass
WHEN importing from src/lore/types.py
THEN all fields match architecture doc section 2.2 (groups_found, memories_consolidated, memories_created, duplicates_merged, groups, dry_run)

GIVEN DEFAULT_RETENTION_POLICIES
WHEN accessed
THEN working=3600, short=604800, long=2592000

GIVEN DEFAULT_CONSOLIDATION_CONFIG
WHEN accessed
THEN contains retention_policies, dedup_threshold=0.95, min_group_size=3, batch_size=50, max_groups_per_run=100, llm_model=None

GIVEN the updated MemoryStats
WHEN importing from src/lore/types.py
THEN archived_count, consolidation_count, and last_consolidation_at fields are available with proper defaults
```

---

### S2: Schema Migration & Store Persistence for Consolidation Fields

**As a** developer,
**I want** the `archived` and `consolidated_into` columns added to the memories table in all store implementations, and the `consolidation_log` table created,
**so that** consolidation data can be persisted.

**Estimate:** M

**Dependencies:** S1

**Scope:**
- Add `_maybe_add_consolidation_columns()` to `SqliteStore` per architecture doc section 2.4 -- adds `archived INTEGER DEFAULT 0` and `consolidated_into TEXT` columns with `idx_memories_archived` index
- Add `_maybe_create_consolidation_log_table()` to `SqliteStore` per architecture doc section 2.5 -- creates `consolidation_log` table with `idx_clog_memory` and `idx_clog_created` indexes
- Call both from `SqliteStore.__init__()` after existing migration methods
- Update `SqliteStore._row_to_memory()` to read `archived` and `consolidated_into` per architecture doc section 5.2
- Update `SqliteStore.save()` and `SqliteStore.update()` to write `archived` and `consolidated_into` columns per architecture doc section 5.3
- Add `save_consolidation_log()` and `get_consolidation_log()` to `SqliteStore` per architecture doc section 11.1
- Add `_row_to_consolidation_log()` static method per architecture doc section 11.1
- Add `include_archived` filter to `MemoryStore.list()` per architecture doc section 11.2
- Add `_consolidation_log` list and CRUD to `MemoryStore` per architecture doc section 11.2
- Map `archived` and `consolidated_into` in `HttpStore` JSON serialization/deserialization

**Acceptance Criteria:**

```
GIVEN an existing SQLite database without consolidation columns
WHEN SqliteStore initializes
THEN archived and consolidated_into columns are added to memories table and idx_memories_archived index is created

GIVEN SqliteStore initialization
WHEN consolidation_log table does not exist
THEN it is created with id, consolidated_memory_id, original_memory_ids, strategy, model_used, original_count, created_at, metadata columns and proper indexes

GIVEN a Memory with archived=True saved via SqliteStore
WHEN loaded back via _row_to_memory()
THEN archived is True and consolidated_into contains the referenced ID

GIVEN a ConsolidationLogEntry
WHEN saved via save_consolidation_log() and retrieved via get_consolidation_log()
THEN all fields round-trip correctly including JSON-serialized original_memory_ids and metadata

GIVEN a MemoryStore with some archived memories
WHEN save_consolidation_log() is called
THEN the entry is stored in the in-memory list and retrievable via get_consolidation_log()
```

---

### S3: Store list() Archived Filtering & Recall Exclusion

**As a** developer,
**I want** `store.list()` to exclude archived memories by default and `recall()` to never return archived memories,
**so that** consolidated-away memories don't pollute normal operations.

**Estimate:** S

**Dependencies:** S2

**Scope:**
- Add `include_archived: bool = False` parameter to `Store.list()` base class signature in `src/lore/store/base.py` per architecture doc section 3.1
- Update `SqliteStore.list()` to add `WHERE archived = 0` when `include_archived=False` per architecture doc section 5.1
- Update `MemoryStore.list()` to filter `not m.archived` when `include_archived=False`
- Add `save_consolidation_log()` and `get_consolidation_log()` no-op default methods to `Store` base class per architecture doc section 3.2
- Verify that `Lore.recall()` naturally excludes archived memories (it calls `store.list()` which now filters by default)

**Acceptance Criteria:**

```
GIVEN a store with 5 active and 3 archived memories
WHEN list() is called with default parameters
THEN only 5 active memories are returned

GIVEN a store with 5 active and 3 archived memories
WHEN list(include_archived=True) is called
THEN all 8 memories are returned

GIVEN a store with archived memories matching a recall query
WHEN recall() is called with that query
THEN no archived memories appear in results

GIVEN a custom Store subclass that does not override consolidation log methods
WHEN save_consolidation_log() is called
THEN it succeeds silently (no-op)

GIVEN a custom Store subclass that does not override consolidation log methods
WHEN get_consolidation_log() is called
THEN it returns an empty list
```

---

## Sprint 2: Core Pipeline

### S4: ConsolidationEngine Skeleton & Candidate Identification (Stage 1)

**As a** developer,
**I want** a `ConsolidationEngine` class in `src/lore/consolidation.py` that identifies consolidation candidates based on tier-specific retention policies,
**so that** only age-eligible, non-archived memories enter the pipeline.

**Estimate:** M

**Dependencies:** S3

**Scope:**
- Create `src/lore/consolidation.py` with `ConsolidationEngine` class per architecture doc section 4.2
- Constructor takes `store`, `embedder`, `llm_provider` (optional), `config` (optional, merged with `DEFAULT_CONSOLIDATION_CONFIG`)
- Implement `_identify_candidates(project, tier)` per architecture doc section 4.3 Stage 1 -- filters by age vs. tier retention threshold, excludes archived
- Implement batch processing: when candidates exceed `batch_size`, process in chunks per architecture doc section 4.3

**Acceptance Criteria:**

```
GIVEN a store with memories of various ages and tiers
WHEN _identify_candidates() is called with default retention policies
THEN only memories older than their tier's threshold are returned (working > 1h, short > 7d, long > 30d)

GIVEN a store with some archived memories older than retention threshold
WHEN _identify_candidates() is called
THEN archived memories are not included in candidates

GIVEN custom retention_policies in config (e.g., working=60s)
WHEN _identify_candidates() is called
THEN the custom thresholds are used instead of defaults

GIVEN a project filter
WHEN _identify_candidates(project="myapp") is called
THEN only memories from that project are returned as candidates

GIVEN a tier filter
WHEN _identify_candidates(tier="short") is called
THEN only short-tier memories are returned as candidates

GIVEN 120 candidates and batch_size=50
WHEN the pipeline runs
THEN candidates are processed in 3 batches (50, 50, 20)
```

---

### S5: Deduplication Grouping (Stage 2a)

**As a** developer,
**I want** near-duplicate memories (cosine similarity > configurable threshold) grouped together,
**so that** redundant memories can be merged.

**Estimate:** M

**Dependencies:** S4

**Scope:**
- Implement `_find_duplicates(candidates)` per architecture doc section 4.3 Stage 2a
- Deserialize embeddings from `struct.pack` float32 format (same as `Lore.recall()`)
- Compute cosine similarity via `np.dot(a, b) / (||a|| * ||b||)`
- Use `dedup_threshold` from config (default 0.95)
- Group transitively: if A~B and B~C, all three form one group
- Skip memories without embeddings
- Return `List[List[Memory]]` -- each inner list is a dedup group with 2+ members

**Acceptance Criteria:**

```
GIVEN two memories with cosine similarity 0.97 (above default 0.95 threshold)
WHEN _find_duplicates() is called
THEN they are grouped together in one dedup group

GIVEN two memories with cosine similarity 0.90 (below default 0.95 threshold)
WHEN _find_duplicates() is called
THEN they are NOT grouped together

GIVEN two memories with identical embeddings (similarity 1.0)
WHEN _find_duplicates() is called
THEN they are grouped together

GIVEN a custom dedup_threshold of 0.90 in config
WHEN _find_duplicates() is called with memories at 0.92 similarity
THEN they are grouped together

GIVEN memories A~B (0.96) and B~C (0.96) but A~C (0.80)
WHEN _find_duplicates() is called
THEN A, B, and C are in the same group (transitive grouping)

GIVEN a memory without an embedding
WHEN _find_duplicates() is called
THEN that memory is skipped (not included in any group)
```

---

### S6: Entity/Topic Grouping (Stage 2b)

**As a** developer,
**I want** memories that share knowledge graph entities to be grouped for summarization,
**so that** related episodic memories can be compressed into semantic knowledge.

**Estimate:** M

**Dependencies:** S4, F1 (Knowledge Graph -- entity_mentions table)

**Scope:**
- Implement `_group_by_entity(candidates, already_grouped)` per architecture doc section 4.3 Stage 2b
- Query `store.get_entity_mentions_for_memory()` for each candidate not in `already_grouped`
- Build `entity_to_memories` mapping; group memories sharing the same entity where group size >= `min_group_size` (default 3)
- Sort entities by mention count descending for deterministic grouping
- Exclude memories already placed in dedup groups via `already_grouped` set
- Applied after dedup grouping (Stage 2a) to prevent overlap

**Acceptance Criteria:**

```
GIVEN 4 memories all sharing entity "auth-service" via entity_mentions
WHEN _group_by_entity() is called with min_group_size=3
THEN all 4 memories are grouped together

GIVEN 2 memories sharing entity "redis" via entity_mentions
WHEN _group_by_entity() is called with min_group_size=3
THEN they are NOT grouped (below minimum group size)

GIVEN memory IDs {A, B} in already_grouped set
WHEN _group_by_entity() is called
THEN memories A and B are excluded from entity grouping

GIVEN memories sharing multiple entities
WHEN _group_by_entity() is called
THEN entities are processed in descending order by mention count and each memory appears in at most one group

GIVEN no entity_mentions exist for any candidate
WHEN _group_by_entity() is called
THEN an empty list of groups is returned
```

---

### S7: LLM Summarization with Fallback (Stage 3)

**As a** developer,
**I want** memory groups summarized via LLM into concise consolidated content, with fallback to highest-importance content when no LLM is configured or LLM fails,
**so that** consolidation produces meaningful summaries or degrades gracefully.

**Estimate:** M

**Dependencies:** S4

**Scope:**
- Implement `_summarize_group(memories, strategy)` per architecture doc section 4.3 Stage 3
- Define `CONSOLIDATION_PROMPT` template per architecture doc
- When `strategy == "deduplicate"` or `self._llm is None`: return content from highest-importance memory
- When LLM is available and strategy is "summarize": format memories into prompt and call `self._llm.complete(prompt, max_tokens=500)`
- On LLM exception: log warning, fall back to highest-importance content
- Implement `_create_consolidated_memory(originals, content, strategy)` per architecture doc section 4.4 -- resolves type (most common), merges tags (union), computes new embedding, sets tier="long", source="consolidation", metadata with consolidated_from/strategy/count, inherits max importance/confidence, sums access_count/upvotes/downvotes

**Acceptance Criteria:**

```
GIVEN a dedup group and strategy="deduplicate"
WHEN _summarize_group() is called
THEN the content of the highest-importance memory is returned (no LLM call)

GIVEN an entity group, strategy="summarize", and a configured LLM
WHEN _summarize_group() is called
THEN the LLM is invoked with CONSOLIDATION_PROMPT containing all memory contents
AND the LLM response is returned as consolidated content

GIVEN an entity group but no LLM configured (llm_provider=None)
WHEN _summarize_group() is called
THEN the highest-importance memory content is returned without error

GIVEN an entity group and LLM that raises an exception
WHEN _summarize_group() is called
THEN a warning is logged and the highest-importance memory content is returned as fallback

GIVEN originals with types ["fact", "fact", "lesson"]
WHEN _create_consolidated_memory() is called
THEN the consolidated memory type is "fact" (most common)

GIVEN originals with importance scores [0.3, 0.8, 0.5]
WHEN _create_consolidated_memory() is called
THEN the consolidated memory has importance_score=0.8, tier="long", source="consolidation"

GIVEN originals with access_counts [5, 3, 2] and upvotes [1, 0, 2]
WHEN _create_consolidated_memory() is called
THEN the consolidated memory has access_count=10 and upvotes=3

GIVEN originals with tags [["python"], ["testing", "python"], ["ci"]]
WHEN _create_consolidated_memory() is called
THEN the consolidated memory has tags containing exactly {"python", "testing", "ci"}
```

---

## Sprint 3: Execution & Audit

### S8: Archive Originals, Relink Graph Edges & Consolidation Log (Stages 4-6)

**As a** developer,
**I want** original memories soft-deleted with references to consolidated memories, graph edges relinked, and all actions logged,
**so that** consolidation is fully auditable and the knowledge graph stays consistent.

**Estimate:** L

**Dependencies:** S5, S6, S7

**Scope:**
- Implement `_archive_originals(originals, consolidated_memory_id)` per architecture doc section 4.3 Stage 4 -- sets `archived=True`, `consolidated_into=<id>`, updates `updated_at`, calls `store.update()`
- Implement `_relink_graph_edges(original_ids, consolidated_memory_id)` per architecture doc section 4.3 Stage 5 -- creates new entity_mentions linking entities to consolidated memory (INSERT OR IGNORE), updates relationships where `source_memory_id` matches original
- Implement `_log_consolidation(consolidated_memory_id, original_ids, strategy, model_used, metadata)` per architecture doc section 4.3 Stage 6 -- creates `ConsolidationLogEntry` with ULID, saves via `store.save_consolidation_log()`

**Acceptance Criteria:**

```
GIVEN a list of original memories and a consolidated memory ID
WHEN _archive_originals() is called
THEN each original has archived=True, consolidated_into set to the consolidated memory ID, and updated_at refreshed

GIVEN archived originals with entity_mentions
WHEN _relink_graph_edges() is called
THEN new entity_mentions are created linking those entities to the consolidated memory ID

GIVEN archived originals referenced by relationships (source_memory_id)
WHEN _relink_graph_edges() is called
THEN those relationships have source_memory_id updated to the consolidated memory ID

GIVEN a completed consolidation group
WHEN _log_consolidation() is called
THEN a ConsolidationLogEntry is saved with correct consolidated_memory_id, original_memory_ids, strategy, model_used, and timestamp

GIVEN the consolidation log
WHEN get_consolidation_log() is called
THEN entries are returned ordered by created_at descending
```

---

### S9: Dry-Run Mode

**As a** developer,
**I want** the consolidation pipeline to support a dry-run mode that identifies and groups candidates without modifying any data,
**so that** users can preview consolidation results before committing.

**Estimate:** S

**Dependencies:** S5, S6

**Scope:**
- When `dry_run=True` in `consolidate()`: execute Stages 1-2 (identify + group) but skip Stages 3-6 (no LLM calls, no archiving, no relinking, no logging)
- Build per-group preview info: strategy, memory_count, memory_ids, first 200 chars of content
- For dedup groups: include max pairwise similarity score via `_max_pairwise_similarity()`
- For entity groups: include shared entity names via `_get_shared_entities()`
- Return `ConsolidationResult` with `dry_run=True` and populated `groups` list

**Acceptance Criteria:**

```
GIVEN a store with consolidation candidates
WHEN consolidate(dry_run=True) is called
THEN groups are identified and ConsolidationResult.groups is populated with preview info

GIVEN dry_run=True
WHEN consolidation completes
THEN no memories are archived, no new memories are created, no graph edges are modified, no log entries are written

GIVEN a dedup group in dry-run
WHEN the result is inspected
THEN the group info includes strategy="deduplicate", memory_count, memory_ids, content preview, and similarity score

GIVEN an entity group in dry-run
WHEN the result is inspected
THEN the group info includes strategy="summarize", memory_count, memory_ids, content preview, and entity names

GIVEN dry_run=True
WHEN consolidation completes
THEN ConsolidationResult.dry_run is True and memories_consolidated/memories_created reflect what WOULD happen
```

---

### S10: Full Pipeline Orchestration

**As a** developer,
**I want** the `consolidate()` method to orchestrate all six stages end-to-end with per-group error isolation, strategy filtering, and safety limits,
**so that** the complete consolidation pipeline works reliably.

**Estimate:** L

**Dependencies:** S8, S9

**Scope:**
- Implement `consolidate(project, tier, strategy, dry_run)` orchestration per architecture doc section 4.5
- Strategy filtering: `"deduplicate"` runs only dedup groups; `"summarize"` runs only entity groups (requires LLM); `"all"` runs both
- Apply `max_groups_per_run` safety limit (default 100)
- Implement `_process_group(group, strategy, result)` per architecture doc section 4.5 -- runs Stages 3-6 for a single group
- Per-group error isolation: if one group fails, log error and continue to next group
- Empty store returns empty `ConsolidationResult`
- Wire into `Lore` facade: add `consolidation_config` param to `Lore.__init__()`, instantiate `ConsolidationEngine`, add `consolidate()` and `get_consolidation_log()` facade methods per architecture doc section 6

**Acceptance Criteria:**

```
GIVEN an empty store
WHEN consolidate() is called
THEN an empty ConsolidationResult is returned with all counters at 0

GIVEN strategy="deduplicate"
WHEN consolidate() is called
THEN only dedup groups are processed, entity groups are skipped

GIVEN strategy="summarize" and an LLM configured
WHEN consolidate() is called
THEN only entity groups are processed, dedup groups are skipped

GIVEN strategy="all"
WHEN consolidate() is called
THEN both dedup and entity groups are processed, dedup first to prevent overlap

GIVEN 150 groups identified and max_groups_per_run=100
WHEN consolidate() is called
THEN only 100 groups are processed

GIVEN one group that throws an exception during _process_group()
WHEN consolidate() is called with multiple groups
THEN the failed group is logged and skipped, remaining groups are processed normally

GIVEN consolidate(dry_run=False) completes successfully
WHEN the store is inspected
THEN original memories are archived, new consolidated memories exist, graph edges are updated, and consolidation log entries are present

GIVEN the Lore facade
WHEN Lore(consolidation_config={"dedup_threshold": 0.90}) is constructed
THEN the ConsolidationEngine uses dedup_threshold=0.90
```

---

## Sprint 4: Surface & Scheduling

### S11: MCP Tool & CLI Subcommand

**As a** user,
**I want** an MCP `consolidate` tool and a `lore consolidate` CLI command,
**so that** I can trigger and preview consolidation from any interface.

**Estimate:** M

**Dependencies:** S10

**Scope:**
- Add `consolidate` MCP tool to `src/lore/mcp/server.py` per architecture doc section 7 -- parameters: `project`, `dry_run` (default True), `strategy`
- Add `_format_consolidation_result()` helper to format dry-run and execute output per architecture doc section 7
- Add `consolidate` subcommand to `src/lore/cli.py` per architecture doc section 8 -- flags: `--dry-run` (default), `--execute`, `--project`, `--tier`, `--strategy`, `--log`, `--limit`, `--db`
- Add `cmd_consolidate()` handler per architecture doc section 8.1 -- supports both consolidation and log viewing modes

**Acceptance Criteria:**

```
GIVEN the MCP consolidate tool
WHEN called with dry_run=true (default)
THEN a formatted preview is returned showing groups found, memories affected, and per-group details

GIVEN the MCP consolidate tool
WHEN called with dry_run=false
THEN consolidation executes and a formatted summary is returned showing archives, creates, and merges

GIVEN the CLI command `lore consolidate --dry-run`
WHEN executed
THEN a preview of consolidation is printed to stdout

GIVEN the CLI command `lore consolidate --execute --strategy deduplicate`
WHEN executed
THEN only deduplication runs and results are printed

GIVEN the CLI command `lore consolidate --log`
WHEN executed
THEN recent consolidation log entries are displayed

GIVEN the CLI command `lore consolidate --log --limit 20`
WHEN executed
THEN up to 20 consolidation log entries are displayed
```

---

### S12: Scheduled Consolidation & Stats Integration

**As a** user,
**I want** consolidation to run automatically on a configurable schedule and consolidation metrics included in stats(),
**so that** memory bloat is managed without manual intervention and I can monitor consolidation health.

**Estimate:** M

**Dependencies:** S10

**Scope:**
- Implement `ConsolidationScheduler` class in `src/lore/consolidation.py` per architecture doc section 9 -- uses `asyncio` periodic task, supports "hourly", "daily", "weekly" intervals
- Add `consolidation_schedule` param to `Lore.__init__()` -- when set, creates and stores `ConsolidationScheduler` instance
- Scheduler runs `consolidate(dry_run=False)` at configured interval, logs results, catches and logs exceptions
- Add `start()` and `stop()` methods to scheduler
- Update `stats()` to include `archived_count`, `consolidation_count`, `last_consolidation_at` per architecture doc section 2.3
- Query consolidation_log table for count and latest timestamp; query memories table for archived count

**Acceptance Criteria:**

```
GIVEN Lore(consolidation_schedule="daily")
WHEN the scheduler is started
THEN a background asyncio task is created that runs consolidation every 86400 seconds

GIVEN Lore(consolidation_schedule="weekly")
WHEN the scheduler is started
THEN a background asyncio task runs consolidation every 604800 seconds

GIVEN a running scheduler
WHEN stop() is called
THEN the background task is cancelled

GIVEN a scheduled consolidation run that raises an exception
WHEN the scheduler fires
THEN the error is logged and the scheduler continues (does not crash)

GIVEN a store with 3 archived memories and 2 consolidation log entries
WHEN stats() is called
THEN the result includes archived_count=3, consolidation_count=2, and last_consolidation_at matching the most recent log entry

GIVEN no consolidation has ever run
WHEN stats() is called
THEN archived_count=0, consolidation_count=0, last_consolidation_at=None
```

---

## Dependency Graph

```
S1 (Types & Config)
├── S2 (Schema & Store Persistence)
│   └── S3 (Archived Filtering & Recall Exclusion)
│       └── S4 (Engine Skeleton & Candidate ID)
│           ├── S5 (Dedup Grouping)
│           ├── S6 (Entity Grouping) [also depends on F1]
│           └── S7 (LLM Summarization)
│               └── S8 (Archive + Relink + Log) [also depends on S5, S6]
│                   └── S10 (Full Pipeline Orchestration) [also depends on S9]
│                       ├── S11 (MCP + CLI)
│                       └── S12 (Scheduling + Stats)
S5 ──┐
S6 ──┴── S9 (Dry-Run Mode)
```

## PRD Requirement Traceability

| PRD Req | Story | Coverage |
|---------|-------|----------|
| R1 (Six-stage pipeline) | S4, S5, S6, S7, S8, S10 | Full pipeline across stories |
| R2 (Dedup detection) | S5 | Cosine similarity grouping |
| R3 (Consolidation log table) | S2, S8 | Schema in S2, write logic in S8 |
| R4 (Soft delete originals) | S1, S2, S3, S8 | Fields in S1, schema in S2, filtering in S3, logic in S8 |
| R5 (Importance preservation) | S7 | _create_consolidated_memory() |
| R6 (Graph integration) | S6, S8 | Entity grouping in S6, relinking in S8 |
| R7 (Configurable retention) | S1, S4 | Config in S1, identification in S4 |
| R8 (MCP consolidate tool) | S11 | Full MCP tool |
| R9 (CLI consolidate) | S11 | Full CLI subcommand |
| R10 (LLM summarization) | S7 | With fallback |
| R11 (Schema changes) | S1, S2 | Types in S1, migration in S2 |
| R12 (Scheduled consolidation) | S12 | ConsolidationScheduler |
| R13 (Stats) | S12 | Stats integration |
| R14 (Batch processing) | S4 | Batch chunking |
| R15 (Dry-run detail) | S9 | Full dry-run with previews |
