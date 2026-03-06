# F2 Fact Extraction + Conflict Resolution - User Stories

**Feature:** F2 — Fact Extraction + Conflict Resolution
**Version:** v0.6.0 ("Open Brain")
**Sprint Planning:** SM breakdown into INVEST-compliant stories

---

## Story Map

```
S1 Dataclasses/Schemas ──┐
                          ├── S3 Store ABC ──┬── S4 SQLite Store ──┐
S2 Database Tables ───────┘                  │                     │
                                             └── S5 MemoryStore ───┤
                                                                   ├── S6 FactExtractor ──┐
                                                                   │                      ├── S8 Pipeline Integration
                                                                   └── S7 ConflictResolver┘       │
                                                                                                   ├── S9 Fact-Aware Recall
                                                                                                   ├── S10 MCP Tools
                                                                                                   ├── S11 CLI Commands
                                                                                                   └── S12 Tests
```

---

## S1: Fact and ConflictEntry Dataclasses + Schemas

**Size:** S
**Dependencies:** None
**Priority:** P0 (foundation)

### Description

Add `Fact` and `ConflictEntry` dataclasses to `types.py`, along with the `VALID_RESOLUTIONS` constant and the `ExtractedFact` / `ResolutionResult` intermediate types used by the extraction pipeline. These are pure data definitions with no behavior.

### Acceptance Criteria

**AC1 — Fact dataclass exists with all fields**
- **Given** `Fact` is imported from `lore.types`
- **When** a `Fact` is instantiated with `id`, `memory_id`, `subject`, `predicate`, `object`
- **Then** the instance has all fields: `id`, `memory_id`, `subject`, `predicate`, `object`, `confidence` (default 1.0), `extracted_at` (default ""), `invalidated_by` (default None), `invalidated_at` (default None), `metadata` (default None)

**AC2 — ConflictEntry dataclass exists with all fields**
- **Given** `ConflictEntry` is imported from `lore.types`
- **When** a `ConflictEntry` is instantiated with required fields
- **Then** the instance has: `id`, `new_memory_id`, `old_fact_id`, `new_fact_id` (Optional), `subject`, `predicate`, `old_value`, `new_value`, `resolution`, `resolved_at`, `metadata` (default None)

**AC3 — VALID_RESOLUTIONS constant**
- **Given** `VALID_RESOLUTIONS` is imported from `lore.types`
- **Then** it equals `("SUPERSEDE", "MERGE", "CONTRADICT", "NOOP")`

**AC4 — ConflictEntry enforces valid resolution**
- **Given** a `ConflictEntry` is created with `resolution="SUPERSEDE"`
- **Then** it is accepted
- **And** `resolution` must be one of `VALID_RESOLUTIONS` (validated at usage sites)

**AC5 — Fact defaults are backward-compatible**
- **Given** existing code that does not use facts
- **When** `Fact` is imported
- **Then** no existing imports or code paths break

### Implementation Notes

- Add to `src/lore/types.py`
- `Fact` uses `@dataclass` with optional fields having defaults
- `subject` and `predicate` are stored normalized (lowercase, trimmed) — normalization happens at extraction time, not in the dataclass
- `ExtractedFact` and `ResolutionResult` go in `src/lore/extract/extractor.py` (S6) and `src/lore/extract/resolver.py` (S7) respectively

---

## S2: Database Tables (facts + conflict_log) with Schema Creation

**Size:** S
**Dependencies:** None (can parallelize with S1)
**Priority:** P0 (foundation)

### Description

Add `facts` and `conflict_log` table creation DDL to `SqliteStore`. Tables are created via `CREATE TABLE IF NOT EXISTS` in a new `_maybe_create_fact_tables()` method called during `__init__()`. No migration needed — these are new tables.

### Acceptance Criteria

**AC1 — facts table created on fresh database**
- **Given** a fresh SQLite database
- **When** `SqliteStore.__init__()` runs
- **Then** the `facts` table exists with columns: `id` (TEXT PK), `memory_id` (TEXT NOT NULL FK), `subject` (TEXT NOT NULL), `predicate` (TEXT NOT NULL), `object` (TEXT NOT NULL), `confidence` (REAL DEFAULT 1.0), `extracted_at` (TEXT NOT NULL), `invalidated_by` (TEXT), `invalidated_at` (TEXT), `metadata` (TEXT)

**AC2 — conflict_log table created on fresh database**
- **Given** a fresh SQLite database
- **When** `SqliteStore.__init__()` runs
- **Then** the `conflict_log` table exists with columns: `id` (TEXT PK), `new_memory_id` (TEXT NOT NULL), `old_fact_id` (TEXT NOT NULL), `new_fact_id` (TEXT), `subject`, `predicate`, `old_value`, `new_value`, `resolution` (TEXT NOT NULL), `resolved_at` (TEXT NOT NULL), `metadata` (TEXT)

**AC3 — Indexes created**
- **Given** the tables exist
- **Then** indexes exist: `idx_facts_memory`, `idx_facts_subject`, `idx_facts_subject_predicate`, `idx_facts_active` (partial), `idx_conflict_log_memory`, `idx_conflict_log_resolution`, `idx_conflict_log_resolved`

**AC4 — CASCADE deletion via FK**
- **Given** the `facts` table has `FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE`
- **When** a memory is deleted
- **Then** all facts with that `memory_id` are automatically deleted

**AC5 — Schema creation is idempotent**
- **Given** the tables already exist
- **When** `_maybe_create_fact_tables()` runs again
- **Then** no error occurs and no data is lost

**AC6 — conflict_log has no FK to facts**
- **Given** a conflict_log entry references a fact_id
- **When** that fact is deleted (via cascade)
- **Then** the conflict_log entry is preserved (audit trail intact)

### Implementation Notes

- Add `_FACT_SCHEMA` module-level constant with CREATE TABLE + CREATE INDEX statements
- Add `_maybe_create_fact_tables()` to `SqliteStore`, called in `__init__()` after existing migrations
- `PRAGMA foreign_keys = ON` is already enabled in `SqliteStore.__init__`

---

## S3: Store ABC Additions for Facts and Conflicts

**Size:** S
**Dependencies:** S1
**Priority:** P0 (foundation)

### Description

Extend the `Store` base class with default (no-op) implementations for fact and conflict methods. This ensures `HttpStore` and custom stores don't break. Only `SqliteStore` and `MemoryStore` provide real implementations.

### Acceptance Criteria

**AC1 — save_fact method exists with no-op default**
- **Given** the `Store` base class
- **When** `save_fact(fact)` is called on a store that hasn't overridden it
- **Then** no error occurs (no-op)

**AC2 — get_facts returns empty list by default**
- **Given** the `Store` base class
- **When** `get_facts(memory_id)` is called
- **Then** an empty list is returned

**AC3 — get_active_facts with filters**
- **Given** the `Store` base class
- **When** `get_active_facts(subject=None, predicate=None, limit=50)` is called
- **Then** an empty list is returned

**AC4 — invalidate_fact method exists with no-op default**
- **Given** the `Store` base class
- **When** `invalidate_fact(fact_id, invalidated_by)` is called
- **Then** no error occurs (no-op)

**AC5 — save_conflict method exists with no-op default**
- **Given** the `Store` base class
- **When** `save_conflict(entry)` is called
- **Then** no error occurs (no-op)

**AC6 — list_conflicts returns empty list by default**
- **Given** the `Store` base class
- **When** `list_conflicts(resolution=None, limit=20)` is called
- **Then** an empty list is returned

**AC7 — HttpStore is unaffected**
- **Given** the `HttpStore` class does not override fact/conflict methods
- **When** fact methods are called on `HttpStore`
- **Then** the base class no-op defaults are used without error

### Implementation Notes

- Add 6 methods to `Store` in `src/lore/store/base.py`
- All have concrete default implementations (not `@abstractmethod`) — `pass` for void, `return []` for list returns
- Import `Fact` and `ConflictEntry` types at the top of `base.py`

---

## S4: SQLite Store Implementation for Facts and Conflicts

**Size:** M
**Dependencies:** S1, S2, S3
**Priority:** P0

### Description

Implement the 6 fact/conflict store methods in `SqliteStore`: `save_fact`, `get_facts`, `get_active_facts`, `invalidate_fact`, `save_conflict`, `list_conflicts`. Add helper methods `_row_to_fact()` and `_row_to_conflict()` for row mapping.

### Acceptance Criteria

**AC1 — save_fact persists a Fact**
- **Given** a valid `Fact` instance
- **When** `store.save_fact(fact)` is called
- **Then** the fact is persisted in the `facts` table
- **And** `fact.metadata` is serialized as JSON text

**AC2 — get_facts returns facts for a memory**
- **Given** 3 facts exist for `memory_id="m1"` and 2 for `memory_id="m2"`
- **When** `store.get_facts("m1")` is called
- **Then** exactly 3 facts are returned, ordered by `extracted_at`

**AC3 — get_active_facts filters invalidated facts**
- **Given** 5 facts exist, 2 are invalidated (`invalidated_by` is set)
- **When** `store.get_active_facts()` is called
- **Then** exactly 3 facts are returned (only active)

**AC4 — get_active_facts filters by subject**
- **Given** active facts with subjects "user" and "project"
- **When** `store.get_active_facts(subject="user")` is called
- **Then** only facts with `subject="user"` are returned

**AC5 — get_active_facts filters by subject + predicate**
- **Given** active facts with various subject+predicate pairs
- **When** `store.get_active_facts(subject="project", predicate="uses_database")` is called
- **Then** only matching facts are returned

**AC6 — get_active_facts normalizes input**
- **Given** a fact with `subject="user"`
- **When** `store.get_active_facts(subject="  User  ")` is called
- **Then** the fact is returned (input is normalized to lowercase/trimmed)

**AC7 — invalidate_fact marks fact as invalidated**
- **Given** an active fact with `id="f1"`
- **When** `store.invalidate_fact("f1", invalidated_by="m2")` is called
- **Then** the fact has `invalidated_by="m2"` and `invalidated_at` is set to current timestamp

**AC8 — invalidate_fact is idempotent for already-invalidated facts**
- **Given** a fact already invalidated (has `invalidated_by` set)
- **When** `store.invalidate_fact()` is called again
- **Then** the original `invalidated_by` is preserved (WHERE clause checks `invalidated_by IS NULL`)

**AC9 — save_conflict persists a ConflictEntry**
- **Given** a valid `ConflictEntry`
- **When** `store.save_conflict(entry)` is called
- **Then** the entry is persisted in `conflict_log`

**AC10 — list_conflicts returns entries ordered by resolved_at DESC**
- **Given** 5 conflict log entries
- **When** `store.list_conflicts(limit=3)` is called
- **Then** the 3 most recent entries are returned

**AC11 — list_conflicts filters by resolution**
- **Given** entries with resolutions SUPERSEDE, MERGE, CONTRADICT
- **When** `store.list_conflicts(resolution="CONTRADICT")` is called
- **Then** only CONTRADICT entries are returned

**AC12 — cascade deletion works**
- **Given** a memory with associated facts
- **When** the memory is deleted from `memories` table
- **Then** all facts with that `memory_id` are automatically deleted
- **And** conflict_log entries referencing those facts are preserved

### Implementation Notes

- Add methods to `src/lore/store/sqlite.py`
- Use `INSERT OR REPLACE` for `save_fact` (upsert semantics)
- Add `_row_to_fact()` and `_row_to_conflict()` static helper methods
- JSON serialize/deserialize `metadata` fields

---

## S5: MemoryStore Implementation for Facts and Conflicts

**Size:** S
**Dependencies:** S1, S3
**Priority:** P0

### Description

Implement in-memory fact and conflict storage in `MemoryStore` for testing. Uses dict-based storage (`_facts: Dict[str, Fact]`) and list-based conflict log (`_conflict_log: List[ConflictEntry]`).

### Acceptance Criteria

**AC1 — save_fact and get_facts round-trip**
- **Given** a `MemoryStore` instance
- **When** `save_fact(fact)` then `get_facts(memory_id)` is called
- **Then** the saved fact is returned

**AC2 — get_active_facts excludes invalidated**
- **Given** facts where some have `invalidated_by` set
- **When** `get_active_facts()` is called
- **Then** only active facts (invalidated_by is None) are returned

**AC3 — get_active_facts filters by subject and predicate**
- **Given** active facts with different subjects and predicates
- **When** `get_active_facts(subject="user", predicate="lives_in")` is called
- **Then** only matching facts are returned

**AC4 — invalidate_fact sets invalidated_by and invalidated_at**
- **Given** an active fact
- **When** `invalidate_fact(fact_id, invalidated_by="m2")` is called
- **Then** the fact's `invalidated_by` is `"m2"` and `invalidated_at` is set

**AC5 — save_conflict and list_conflicts round-trip**
- **Given** conflict entries are saved
- **When** `list_conflicts()` is called
- **Then** entries are returned ordered by `resolved_at` DESC

**AC6 — list_conflicts filters by resolution**
- **Given** entries with different resolutions
- **When** `list_conflicts(resolution="MERGE")` is called
- **Then** only MERGE entries are returned

**AC7 — Memory deletion cascades to facts**
- **Given** a `MemoryStore` with a memory and associated facts
- **When** the memory is deleted via `delete(memory_id)`
- **Then** facts with that `memory_id` are also removed

### Implementation Notes

- Add `_facts: Dict[str, Fact] = {}` and `_conflict_log: List[ConflictEntry] = []` to `MemoryStore.__init__()`
- Override `delete()` to also clean up facts for the deleted memory_id
- Normalize subject/predicate in `get_active_facts()` filter

---

## S6: FactExtractor Class (LLM-Powered Extraction)

**Size:** L
**Dependencies:** S1, S3 (store interface), F6 (LLM abstraction)
**Priority:** P0

### Description

Create `src/lore/extract/` module with `FactExtractor` class in `extractor.py` and prompt templates in `prompts.py`. The extractor takes memory content, queries existing facts for conflict context, calls the LLM with a structured prompt, and returns `ExtractedFact` objects with resolution metadata. Includes `extract_preview()` for stateless extraction (MCP tool use).

### Acceptance Criteria

**AC1 — Module structure created**
- **Given** the codebase
- **When** `from lore.extract import FactExtractor` is executed
- **Then** the import succeeds
- **And** `src/lore/extract/__init__.py`, `extractor.py`, `prompts.py` exist

**AC2 — extract() produces ExtractedFact list**
- **Given** a `FactExtractor` with a mock LLM client and store
- **When** `extract(memory, enrichment_context)` is called
- **Then** a list of `ExtractedFact` objects is returned
- **And** each has a `Fact` with `id` (ULID), `memory_id`, normalized `subject`, normalized `predicate`, `object`, `confidence`, `extracted_at`

**AC3 — Subject normalization**
- **Given** the LLM returns `subject="  PostgreSQL  "`
- **When** the response is parsed
- **Then** `fact.subject == "postgresql"` (lowercase, trimmed)

**AC4 — Predicate normalization**
- **Given** the LLM returns `predicate="lives in"`
- **When** the response is parsed
- **Then** `fact.predicate == "lives_in"` (lowercase, trimmed, spaces to underscores)

**AC5 — Confidence clamping**
- **Given** the LLM returns `confidence=1.5` or `confidence=-0.1`
- **When** the response is parsed
- **Then** confidence is clamped to `[0.0, 1.0]`

**AC6 — Confidence threshold filtering**
- **Given** `confidence_threshold=0.3` (default)
- **When** the LLM returns a fact with `confidence=0.2`
- **Then** that fact is excluded from the result

**AC7 — Existing fact lookup for conflict context**
- **Given** the store has active facts for subject "project"
- **When** `extract()` is called for content mentioning "project"
- **Then** existing facts are included in the LLM prompt as JSON context

**AC8 — Resolution is passed through from LLM**
- **Given** the LLM returns `resolution="SUPERSEDE"` for a fact
- **When** parsed
- **Then** `extracted_fact.resolution == "SUPERSEDE"` and `extracted_fact.conflicting_fact` references the old fact

**AC9 — Invalid resolution defaults to NOOP**
- **Given** the LLM returns `resolution="UNKNOWN"`
- **When** parsed
- **Then** `extracted_fact.resolution == "NOOP"`

**AC10 — Malformed JSON returns empty list**
- **Given** the LLM returns non-JSON garbage
- **When** parsed
- **Then** an empty list is returned and a warning is logged

**AC11 — extract_preview() works without store context**
- **Given** a `FactExtractor`
- **When** `extract_preview(text)` is called
- **Then** facts are extracted without querying existing facts or checking conflicts
- **And** the result is a list of `Fact` objects (no resolution metadata)

**AC12 — Enrichment context is included in prompt**
- **Given** enrichment context with entities and topics from F6/F9
- **When** the prompt is built
- **Then** the enrichment context appears in the prompt to aid extraction

**AC13 — JSON extraction handles markdown code blocks**
- **Given** the LLM wraps its response in ` ```json ... ``` `
- **When** parsed
- **Then** the JSON is correctly extracted from the code block

### Implementation Notes

- Create `src/lore/extract/__init__.py` with `from .extractor import FactExtractor` and `from .resolver import ConflictResolver`
- `FactExtractor.__init__` takes `llm_client`, `store`, `confidence_threshold=0.3`
- `ExtractedFact` dataclass: `fact: Fact`, `resolution: str`, `reasoning: str`, `conflicting_fact: Optional[Fact]`
- Prompt template in `prompts.py` with `build_extraction_prompt()` function
- Subject hints from: enrichment entities, topics, memory tags, project name

---

## S7: ConflictResolver Class (4 Resolution Strategies)

**Size:** M
**Dependencies:** S1, S3 (store interface)
**Priority:** P0

### Description

Create `ConflictResolver` in `src/lore/extract/resolver.py`. Takes `ExtractedFact` list from `FactExtractor` and applies the appropriate resolution strategy for each: NOOP (save, no log), SUPERSEDE (invalidate old, save new, log), MERGE (save new, keep old active, log), CONTRADICT (don't save new, log with proposed fact in metadata).

### Acceptance Criteria

**AC1 — NOOP: fact saved, no conflict logged**
- **Given** an `ExtractedFact` with `resolution="NOOP"`
- **When** `resolve_all()` is called
- **Then** the fact is saved via `store.save_fact()`
- **And** no `ConflictEntry` is created

**AC2 — SUPERSEDE: old invalidated, new saved, conflict logged**
- **Given** an `ExtractedFact` with `resolution="SUPERSEDE"` and a `conflicting_fact`
- **When** `resolve_all()` is called
- **Then** `store.invalidate_fact()` is called on the old fact
- **And** the new fact is saved via `store.save_fact()`
- **And** a `ConflictEntry` with `resolution="SUPERSEDE"` is saved with `old_value` and `new_value`

**AC3 — MERGE: both active, conflict logged**
- **Given** an `ExtractedFact` with `resolution="MERGE"` and a `conflicting_fact`
- **When** `resolve_all()` is called
- **Then** the new fact is saved (old fact remains active, not invalidated)
- **And** a `ConflictEntry` with `resolution="MERGE"` is saved

**AC4 — CONTRADICT: new fact NOT saved, conflict logged**
- **Given** an `ExtractedFact` with `resolution="CONTRADICT"` and a `conflicting_fact`
- **When** `resolve_all()` is called
- **Then** no new fact is saved (store.save_fact is NOT called for this fact)
- **And** a `ConflictEntry` with `resolution="CONTRADICT"` is saved with `new_fact_id=None`
- **And** the proposed fact is stored in `conflict.metadata["proposed_fact"]`

**AC5 — ResolutionResult has stats**
- **Given** `resolve_all()` processes 5 facts: 2 NOOP, 1 SUPERSEDE, 1 MERGE, 1 CONTRADICT
- **When** the result is returned
- **Then** `result.stats == {"noop": 2, "supersede": 1, "merge": 1, "contradict": 1}`
- **And** `result.saved_facts` has 4 facts (CONTRADICT excluded)
- **And** `result.conflicts` has 3 entries (NOOP excluded)

**AC6 — Unknown resolution defaults to NOOP**
- **Given** an `ExtractedFact` with `resolution="INVALID"`
- **When** `resolve_all()` is called
- **Then** it is treated as NOOP (saved, no conflict log)
- **And** a warning is logged

**AC7 — SUPERSEDE without conflicting_fact still saves**
- **Given** an `ExtractedFact` with `resolution="SUPERSEDE"` but `conflicting_fact=None`
- **When** `resolve_all()` is called
- **Then** the new fact is saved
- **And** a conflict entry is created with empty `old_fact_id` and `old_value`

### Implementation Notes

- Create `src/lore/extract/resolver.py`
- `ConflictResolver.__init__` takes `store: Store`
- `resolve_all(extracted_facts, memory_id)` returns `ResolutionResult`
- Private methods: `_apply_supersede()`, `_apply_merge()`, `_apply_contradict()`
- `ResolutionResult` dataclass: `saved_facts`, `conflicts`, `stats`

---

## S8: Pipeline Integration (remember flow)

**Size:** M
**Dependencies:** S6, S7, F6 (enrichment pipeline), F9 (classification)
**Priority:** P0

### Description

Wire `FactExtractor` and `ConflictResolver` into the `remember()` flow in `lore.py`. Fact extraction runs as the third step in the enrichment pipeline (after F6 enrich and F9 classify). Add `fact_extraction` config parameter to `Lore.__init__()`. When disabled (default), `remember()` has zero overhead. Add `extract_facts()`, `get_facts()`, `list_conflicts()`, `get_active_facts()`, and `backfill_facts()` public methods to the facade.

### Acceptance Criteria

**AC1 — fact_extraction=False (default) has zero overhead**
- **Given** `Lore(fact_extraction=False)` (the default)
- **When** `remember("some content")` is called
- **Then** no LLM calls are made for fact extraction
- **And** no queries to the `facts` table
- **And** behavior is identical to pre-F2

**AC2 — fact_extraction=True extracts and resolves facts**
- **Given** `Lore(fact_extraction=True, llm_provider=..., llm_model=...)`
- **When** `remember("We use PostgreSQL 16")` is called
- **Then** the memory is saved first
- **And** `FactExtractor.extract()` is called with the memory and enrichment context
- **And** `ConflictResolver.resolve_all()` is called with the extracted facts
- **And** facts are persisted in the store

**AC3 — Extraction failure does not block remember**
- **Given** fact extraction is enabled but the LLM call fails
- **When** `remember()` is called
- **Then** the memory is saved successfully (without facts)
- **And** a warning is logged

**AC4 — Pipeline ordering: enrich -> classify -> extract facts**
- **Given** F6 enrichment and F9 classification are also enabled
- **When** `remember()` is called
- **Then** fact extraction receives the enrichment context from F6/F9 as input

**AC5 — get_facts() facade method**
- **Given** facts exist for a memory
- **When** `lore.get_facts(memory_id)` is called
- **Then** all facts for that memory are returned

**AC6 — get_active_facts() facade method**
- **Given** active facts exist
- **When** `lore.get_active_facts(subject="user")` is called
- **Then** active facts matching the subject are returned

**AC7 — list_conflicts() facade method**
- **Given** conflict log entries exist
- **When** `lore.list_conflicts(resolution="CONTRADICT")` is called
- **Then** matching conflict entries are returned

**AC8 — extract_facts() preview method**
- **Given** fact extraction is enabled
- **When** `lore.extract_facts("Some text to analyze")` is called
- **Then** facts are extracted from the text and returned WITHOUT storing them

**AC9 — backfill_facts() processes existing memories**
- **Given** memories exist without facts
- **When** `lore.backfill_facts(project="myproject", limit=50)` is called
- **Then** facts are extracted for each memory that has no existing facts
- **And** the count of extracted facts is returned

### Implementation Notes

- Add `fact_extraction: bool = False` parameter to `Lore.__init__()`
- Create `FactExtractor` and `ConflictResolver` instances when `fact_extraction=True`
- Add `_extract_and_resolve_facts(memory, enrichment_context)` private method
- Wrap fact extraction in try/except with warning log on failure
- `backfill_facts()` iterates memories via `store.list()`, skips those with existing facts

---

## S9: Fact-Aware Recall (use_facts parameter)

**Size:** M
**Dependencies:** S4 or S5 (store with facts), S8 (pipeline must exist)
**Priority:** P1 (Should Have)

### Description

Add `use_facts=True` optional parameter to `recall()`. When enabled and fact extraction is configured, supplement vector similarity results with fact-based matches. Extract subject+predicate from the query, look up matching active facts, and merge their source memories into the result set.

### Acceptance Criteria

**AC1 — use_facts=False (default) is unchanged**
- **Given** `recall(query, use_facts=False)`
- **When** called
- **Then** behavior is identical to pre-F2 recall (vector search only)

**AC2 — use_facts=True adds fact-based results**
- **Given** `recall("What database does the project use?", use_facts=True)`
- **And** an active fact `(project, uses_database, PostgreSQL 16)` exists
- **When** called
- **Then** the source memory for that fact is included in results
- **And** it is scored by fact confidence

**AC3 — Fact results are merged with vector results**
- **Given** vector search returns memories A, B, C
- **And** fact lookup returns memory D (source of a matching fact)
- **When** results are merged
- **Then** the result set contains A, B, C, D (deduplicated by memory_id)

**AC4 — use_facts=True without fact_extraction enabled is no-op**
- **Given** `Lore(fact_extraction=False)`
- **When** `recall(query, use_facts=True)` is called
- **Then** only vector search results are returned (no error, graceful degradation)

**AC5 — Subject extraction from query**
- **Given** a query "Where does Alice live?"
- **When** fact-aware recall processes it
- **Then** it looks up active facts for subject "alice" (normalized)

### Implementation Notes

- Add `use_facts: bool = False` parameter to `recall()` in `lore.py`
- Add `_recall_by_facts(query)` private method
- Simple subject extraction: use LLM or keyword matching to extract subject+predicate from query
- Merge strategy: deduplicate by memory_id, use max score when duplicates exist
- This is a lightweight enhancement — full graph-based retrieval deferred to F1

---

## S10: MCP Tools (extract_facts, list_facts, conflicts)

**Size:** M
**Dependencies:** S8 (pipeline integration)
**Priority:** P0

### Description

Add three MCP tools to `src/lore/mcp/server.py`: `extract_facts` (preview extraction from text), `list_facts` (list active facts with optional subject filter), and `conflicts` (list recent conflict log entries with optional resolution filter). Each returns human-readable formatted output.

### Acceptance Criteria

**AC1 — extract_facts tool extracts from text**
- **Given** the MCP server is running with fact extraction enabled
- **When** `extract_facts(text="We use PostgreSQL 16 deployed on AWS")` is called
- **Then** extracted facts are returned in formatted output:
  ```
  Extracted 2 facts:

  1. (project, uses, PostgreSQL 16) [confidence: 0.95]
  2. (project, deployed_on, AWS) [confidence: 0.88]
  ```
- **And** no facts are stored (preview only)

**AC2 — extract_facts graceful degradation**
- **Given** fact extraction is not enabled (no LLM configured)
- **When** `extract_facts(text=...)` is called
- **Then** a clear message is returned: "Fact extraction requires an LLM provider. Configure llm_provider and set fact_extraction=True."

**AC3 — list_facts tool lists active facts**
- **Given** active facts exist in the store
- **When** `list_facts(subject="project", limit=10)` is called
- **Then** matching facts are returned in table format with Subject, Predicate, Object, Confidence, Source Memory columns

**AC4 — list_facts with no filter lists all**
- **Given** active facts exist
- **When** `list_facts()` is called with no arguments
- **Then** all active facts are returned (up to default limit)

**AC5 — conflicts tool lists recent conflicts**
- **Given** conflict log entries exist
- **When** `conflicts(resolution="CONTRADICT", limit=5)` is called
- **Then** matching entries are returned with formatted output showing resolution, subject/predicate, old/new values, reason

**AC6 — conflicts tool with no filter**
- **Given** conflict log entries exist
- **When** `conflicts()` is called with no arguments
- **Then** the 10 most recent conflicts are returned (default limit)

### Implementation Notes

- Add tools to `src/lore/mcp/server.py`
- `extract_facts` calls `lore.extract_facts(text)` (preview, no store)
- `list_facts` calls `lore.get_active_facts(subject, limit)`
- `conflicts` calls `lore.list_conflicts(resolution, limit)`
- Format output for human readability (agents consume this)

---

## S11: CLI Commands (facts, conflicts, backfill-facts)

**Size:** M
**Dependencies:** S8 (pipeline integration)
**Priority:** P0

### Description

Add three CLI subcommands to `src/lore/cli.py`: `facts` (show facts for a memory or list active facts), `conflicts` (show conflict log), and `backfill-facts` (extract facts from existing memories). Uses click/typer consistent with existing CLI patterns.

### Acceptance Criteria

**AC1 — `lore facts <memory-id>` shows facts for a memory**
- **Given** facts exist for memory `abc123`
- **When** `lore facts abc123` is run
- **Then** output shows a table with Subject, Predicate, Object, Confidence, Status columns
- **And** invalidated facts show "invalidated" status

**AC2 — `lore facts` (no argument) lists active facts**
- **Given** active facts exist
- **When** `lore facts` is run
- **Then** all active facts are listed

**AC3 — `lore facts --subject user` filters by subject**
- **Given** active facts with various subjects
- **When** `lore facts --subject user --limit 10` is run
- **Then** only facts with subject "user" are shown (up to 10)

**AC4 — `lore conflicts` lists recent conflicts**
- **Given** conflict log entries exist
- **When** `lore conflicts` is run
- **Then** recent conflicts are displayed with resolution, subject/predicate, old/new values

**AC5 — `lore conflicts --resolution CONTRADICT` filters**
- **Given** entries with various resolutions
- **When** `lore conflicts --resolution CONTRADICT` is run
- **Then** only CONTRADICT entries are shown

**AC6 — `lore conflicts --limit 5`**
- **Given** many conflict entries
- **When** `lore conflicts --limit 5` is run
- **Then** at most 5 entries are shown

**AC7 — `lore backfill-facts` extracts from existing memories**
- **Given** memories exist without facts
- **When** `lore backfill-facts --project myproject --limit 50` is run
- **Then** fact extraction runs on each memory without existing facts
- **And** output shows count of facts extracted

**AC8 — `lore backfill-facts` requires fact_extraction enabled**
- **Given** fact extraction is not configured
- **When** `lore backfill-facts` is run
- **Then** a clear error message is shown

### Implementation Notes

- Add to `src/lore/cli.py` using existing CLI framework patterns
- `facts` command: optional positional `memory_id`, `--subject`, `--limit` options
- `conflicts` command: `--resolution`, `--limit` options
- `backfill-facts` command: `--project`, `--limit` options
- Format output as aligned tables for readability

---

## S12: Comprehensive Test Suite

**Size:** L
**Dependencies:** S1-S11 (all prior stories)
**Priority:** P0

### Description

Write comprehensive tests covering fact extraction, all 4 resolution strategies, store CRUD operations, cascade deletion, edge cases, and backward compatibility. Target >= 40 tests across multiple test files. Use mock LLM client for extraction tests.

### Acceptance Criteria

**AC1 — Fact dataclass tests**
- **Given** test file `tests/test_fact_extraction.py`
- **Then** tests verify `Fact` creation with defaults, all field assignments, and `VALID_RESOLUTIONS` constant

**AC2 — ConflictEntry dataclass tests**
- **Given** test file
- **Then** tests verify `ConflictEntry` creation, optional `new_fact_id=None` for CONTRADICT, metadata dict

**AC3 — Store CRUD tests (SQLite)**
- **Given** test file `tests/test_fact_store.py`
- **Then** tests cover: `save_fact`, `get_facts`, `get_active_facts` (with all filter combinations), `invalidate_fact`, `save_conflict`, `list_conflicts` (with resolution filter)

**AC4 — Store CRUD tests (MemoryStore)**
- **Given** same test patterns as AC3
- **Then** MemoryStore passes all the same CRUD tests

**AC5 — FactExtractor tests with mock LLM**
- **Given** a mock LLM client returning known JSON
- **Then** tests verify: correct fact parsing, subject normalization, predicate normalization, confidence clamping, threshold filtering, enrichment context passed to prompt, markdown code block handling

**AC6 — NOOP resolution test**
- **Given** an extracted fact with no existing conflict
- **When** `ConflictResolver.resolve_all()` is called
- **Then** fact is saved, no conflict entry created, stats show `noop: 1`

**AC7 — SUPERSEDE resolution test**
- **Given** an extracted fact with `resolution="SUPERSEDE"` and a conflicting existing fact
- **When** `resolve_all()` is called
- **Then** old fact is invalidated, new fact is saved, conflict entry has `resolution="SUPERSEDE"` with old/new values

**AC8 — MERGE resolution test**
- **Given** an extracted fact with `resolution="MERGE"` and a conflicting existing fact
- **When** `resolve_all()` is called
- **Then** both facts are active, new fact is saved, conflict entry has `resolution="MERGE"`

**AC9 — CONTRADICT resolution test**
- **Given** an extracted fact with `resolution="CONTRADICT"` and a conflicting existing fact
- **When** `resolve_all()` is called
- **Then** new fact is NOT saved, conflict entry has `resolution="CONTRADICT"` and `new_fact_id=None`, proposed fact is in metadata

**AC10 — Cascade deletion test**
- **Given** a memory with 3 associated facts
- **When** the memory is deleted via `forget(memory_id)`
- **Then** all 3 facts are deleted
- **And** conflict log entries referencing those facts are preserved

**AC11 — Pipeline integration test**
- **Given** `Lore(fact_extraction=True)` with mock LLM
- **When** `remember("We use PostgreSQL 16")` is called
- **Then** the memory is saved AND facts are extracted and persisted

**AC12 — Backward compatibility test**
- **Given** `Lore(fact_extraction=False)`
- **When** `remember("some content")` is called
- **Then** behavior is identical to pre-F2 (no fact-related calls)
- **And** all existing tests pass without modification

**AC13 — Edge case: empty content**
- **Given** content = ""
- **When** fact extraction runs
- **Then** an empty list of facts is returned (no error)

**AC14 — Edge case: LLM returns empty facts array**
- **Given** the LLM returns `{"facts": []}`
- **When** parsed
- **Then** an empty list is returned (no error)

**AC15 — Edge case: multi-step supersede chain**
- **Given** fact A exists, then fact B supersedes A, then fact C supersedes B
- **Then** only fact C is active
- **And** conflict log has 2 entries: A->B and B->C

**AC16 — Backfill test**
- **Given** 3 memories without facts
- **When** `backfill_facts()` is called
- **Then** facts are extracted for all 3 memories
- **And** the return value is the total count of new facts

**AC17 — Test count target**
- **Given** all test files
- **Then** at least 40 tests exist across fact extraction, conflict resolution, store CRUD, and integration

### Implementation Notes

- Create `tests/test_fact_extraction.py` — extraction + resolution tests
- Create `tests/test_fact_store.py` — store CRUD tests for both SQLite and MemoryStore
- Create `tests/test_conflict_log.py` — conflict log audit trail tests
- Use mock LLM client that returns canned JSON responses
- Parametrize store tests to run against both SQLite and MemoryStore
- Verify existing test suite still passes (backward compat)
