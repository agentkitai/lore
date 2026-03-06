# PRD: F2 — Fact Extraction + Conflict Resolution

**Feature:** F2
**Version:** v0.6.0 ("Open Brain")
**Status:** Draft
**Author:** John (PM)
**Phase:** 2 — Intelligence Layer
**Depends on:** F6 (Metadata Enrichment — LLM abstraction), F9 (Dialog Classification — pipeline integration)
**Dependents:** F1 (Knowledge Graph Layer — facts become graph entities)

---

## 1. Problem Statement

Lore stores memories as opaque text blobs. When a user remembers "We migrated from MySQL to PostgreSQL 16 last week", this is stored as a single string. If the user later remembers "Our database is MySQL 5.7", Lore has no way to detect the contradiction — both memories coexist, and recall may return conflicting information depending on similarity scores.

This creates three problems:

1. **No atomic knowledge.** A single memory may contain multiple independent facts. Recall can only retrieve whole memories, not specific facts within them.
2. **No contradiction detection.** Conflicting memories accumulate silently. Agents receive inconsistent information without warning.
3. **No fact lineage.** When knowledge evolves (e.g., "moved from NYC to Berlin"), there's no audit trail of what changed, when, and why.

Fact extraction transforms unstructured memory content into structured, queryable atomic facts. Conflict resolution ensures the knowledge base stays consistent as facts evolve.

## 2. Goals

1. **Atomic fact extraction** — On `remember()`, extract structured facts from content using an LLM. Each fact is a `{subject, predicate, object}` triple with metadata.
2. **Conflict detection** — Compare new facts against existing facts for the same subject+predicate. Identify contradictions automatically.
3. **Resolution strategies** — Apply configurable resolution: SUPERSEDE (new replaces old), MERGE (complementary facts coexist), CONTRADICT (flag for human review), NOOP (no conflict).
4. **Audit trail** — Maintain a conflict log recording what changed, the old value, the new value, and the resolution strategy applied.
5. **Graceful degradation** — Works without an LLM. When no LLM is configured, memories are stored as today (raw content, no fact extraction). Fact extraction is opt-in.
6. **Graph-ready schema** — Facts are designed so subjects and objects can become knowledge graph entities in F1 (Phase 3).

## 3. Non-Goals

- **Knowledge graph traversal** — That's F1. Facts are stored as flat triples, not a graph with traversal queries.
- **Real-time fact verification** — No external fact-checking or grounding against external sources.
- **Multi-memory reasoning** — Conflict detection is pairwise (new fact vs. existing facts), not multi-hop inference.
- **Automatic resolution for all conflicts** — CONTRADICT resolution defers to humans. The system flags, but doesn't force-resolve ambiguous cases.
- **Fact extraction from recall results** — Extraction happens on `remember()` only, not on read paths.

## 4. Design

### 4.1 Fact Data Model

```python
@dataclass
class Fact:
    """An atomic fact extracted from a memory."""
    id: str                          # UUID
    memory_id: str                   # source memory that produced this fact
    subject: str                     # entity/concept (e.g., "project", "user")
    predicate: str                   # relationship (e.g., "uses", "lives_in", "prefers")
    object: str                      # value (e.g., "PostgreSQL 16", "Berlin", "dark mode")
    confidence: float = 1.0          # extraction confidence (0.0-1.0)
    extracted_at: str = ""           # ISO timestamp
    invalidated_by: Optional[str] = None   # memory_id that superseded this fact
    invalidated_at: Optional[str] = None   # ISO timestamp of invalidation
    metadata: Optional[Dict[str, Any]] = None  # optional extra (e.g., extraction model)
```

**Design rationale — graph-ready:**
- `subject` and `object` are free-text strings now, but will become foreign keys to an `entities` table in F1.
- `predicate` will become a relationship type in the graph.
- The triple structure `(subject, predicate, object)` maps directly to graph edges: `subject --predicate--> object`.

### 4.2 Schema: `facts` Table

**SQLite:**
```sql
CREATE TABLE IF NOT EXISTS facts (
    id              TEXT PRIMARY KEY,
    memory_id       TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    object          TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    extracted_at    TEXT NOT NULL,
    invalidated_by  TEXT,              -- memory_id that caused invalidation
    invalidated_at  TEXT,
    metadata        TEXT,              -- JSON
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);

CREATE INDEX IF NOT EXISTS idx_facts_memory ON facts(memory_id);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate ON facts(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(invalidated_by) WHERE invalidated_by IS NULL;
```

**Postgres (server):**
```sql
CREATE TABLE IF NOT EXISTS facts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id       UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    object          TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    invalidated_by  UUID,
    invalidated_at  TIMESTAMPTZ,
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS idx_facts_memory ON facts(memory_id);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate ON facts(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(invalidated_by) WHERE invalidated_by IS NULL;
```

### 4.3 Schema: `conflict_log` Table

Records every conflict detection and resolution for audit.

**SQLite:**
```sql
CREATE TABLE IF NOT EXISTS conflict_log (
    id              TEXT PRIMARY KEY,
    new_memory_id   TEXT NOT NULL,
    old_fact_id     TEXT NOT NULL,
    new_fact_id     TEXT,              -- NULL if resolution is CONTRADICT (no new fact created)
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    old_value       TEXT NOT NULL,
    new_value       TEXT NOT NULL,
    resolution      TEXT NOT NULL,     -- SUPERSEDE, MERGE, CONTRADICT, NOOP
    resolved_at     TEXT NOT NULL,
    metadata        TEXT               -- JSON: model used, confidence, reasoning
);

CREATE INDEX IF NOT EXISTS idx_conflict_log_memory ON conflict_log(new_memory_id);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolution ON conflict_log(resolution);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolved ON conflict_log(resolved_at);
```

### 4.4 Resolution Strategies

When a new fact `(subject, predicate, new_object)` is extracted and an existing active fact `(subject, predicate, old_object)` is found:

| Strategy | When Applied | Action |
|----------|-------------|--------|
| **SUPERSEDE** | New fact clearly replaces old (temporal update, correction) | Old fact marked `invalidated_by = new_memory_id`, `invalidated_at = now()`. New fact stored as active. |
| **MERGE** | Facts are complementary, not contradictory (e.g., "uses Python" + "uses TypeScript") | Both facts kept active. No invalidation. |
| **CONTRADICT** | Genuine contradiction that can't be auto-resolved | Both facts kept active. Conflict flagged in `conflict_log` with `resolution = 'CONTRADICT'`. Retrievable via `conflicts` tool/CLI. |
| **NOOP** | No existing fact for this subject+predicate, or values are equivalent | New fact stored. No conflict log entry. |

**Resolution is determined by the LLM** during extraction. The extraction prompt asks the LLM to:
1. Extract atomic facts from the new content.
2. For each fact, compare against provided existing facts for the same subject.
3. Classify the relationship as SUPERSEDE, MERGE, CONTRADICT, or NOOP.
4. Provide a brief reasoning string (stored in conflict_log metadata).

### 4.5 Extraction Pipeline

Fact extraction hooks into the enrichment pipeline established by F6/F9.

```
remember(content)
    │
    ▼
┌─────────────────┐
│ Enrichment      │  ← F6: extract topics, entities, sentiment
│ Pipeline        │  ← F9: classify intent, domain, emotion
│                 │  ← F2: extract facts + resolve conflicts (THIS FEATURE)
└────────┬────────┘
         │
         ▼
    Store memory + facts + conflict_log entries
```

**Pipeline position:** Fact extraction runs AFTER metadata enrichment (F6) and classification (F9), because it benefits from the entities and topics already extracted. The enrichment pipeline passes accumulated metadata forward.

### 4.6 LLM Extraction Prompt

The extraction step sends a structured prompt to the configured LLM:

```
Extract atomic facts from the following memory content. Each fact should be a
(subject, predicate, object) triple.

CONTENT:
{memory_content}

EXISTING FACTS for related subjects:
{existing_facts_json}

For each extracted fact:
1. Identify the subject (entity or concept)
2. Identify the predicate (relationship or attribute)
3. Identify the object (value)
4. Assign a confidence score (0.0-1.0)
5. If an existing fact has the same subject+predicate, classify the resolution:
   - SUPERSEDE: the new fact replaces the old (e.g., temporal update, correction)
   - MERGE: both facts are true simultaneously (complementary, not contradictory)
   - CONTRADICT: genuine contradiction that needs human review
   - NOOP: no conflict (new subject+predicate pair, or same value)

Return JSON:
{
  "facts": [
    {
      "subject": "...",
      "predicate": "...",
      "object": "...",
      "confidence": 0.95,
      "resolution": "NOOP",
      "reasoning": "..."
    }
  ]
}
```

**Existing fact lookup:** Before calling the LLM, query the `facts` table for active facts matching any subjects mentioned in the content. Pass these as context so the LLM can detect conflicts. For the initial extraction (no existing facts), the existing facts section is empty.

**Subject normalization:** Subjects should be normalized to lowercase with whitespace trimmed. The LLM prompt should instruct consistent naming. Future F1 will add entity resolution; for now, exact string match on normalized subjects is sufficient.

### 4.7 Configurable LLM

Fact extraction uses the same LLM abstraction established by F6 (Metadata Enrichment):

```python
Lore(
    llm_provider="anthropic",       # or "openai", "ollama", etc.
    llm_model="claude-haiku-4-5-20251001",  # cheap model for extraction
    llm_api_key="...",              # or from env var
    fact_extraction=True,           # enable/disable (default: False)
)
```

When `fact_extraction=False` (default), `remember()` behaves exactly as today — no LLM calls, no facts table interaction. This preserves the zero-dependency, zero-API-key experience.

### 4.8 Lore Facade Changes

**`remember()` method — extended:**
```python
def remember(self, content: str, ...) -> str:
    # ... existing: create Memory, embed, save ...
    memory_id = self._store.save(memory)

    # NEW: fact extraction (if enabled)
    if self._fact_extraction_enabled:
        facts = self._extract_facts(memory)
        for fact in facts:
            self._store.save_fact(fact)
        # Conflict resolution already applied during extraction

    return memory_id
```

**New methods:**

```python
def extract_facts(self, text: str) -> List[Fact]:
    """Extract facts from arbitrary text. Does not store them.
    Useful for testing extraction or processing external content."""
    ...

def get_facts(self, memory_id: str) -> List[Fact]:
    """Get all facts extracted from a specific memory."""
    return self._store.get_facts(memory_id)

def list_conflicts(
    self,
    resolution: Optional[str] = None,
    limit: int = 20,
    project: Optional[str] = None,
) -> List[ConflictEntry]:
    """List recent conflict log entries."""
    return self._store.list_conflicts(resolution=resolution, limit=limit)

def get_active_facts(
    self,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    limit: int = 50,
) -> List[Fact]:
    """Get active (non-invalidated) facts, optionally filtered."""
    return self._store.get_active_facts(subject=subject, predicate=predicate, limit=limit)
```

### 4.9 Store ABC Extensions

```python
class Store(ABC):
    # ... existing methods ...

    # Fact storage
    @abstractmethod
    def save_fact(self, fact: Fact) -> None: ...

    @abstractmethod
    def get_facts(self, memory_id: str) -> List[Fact]: ...

    @abstractmethod
    def get_active_facts(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        limit: int = 50,
    ) -> List[Fact]: ...

    @abstractmethod
    def invalidate_fact(
        self, fact_id: str, invalidated_by: str
    ) -> None: ...

    # Conflict log
    @abstractmethod
    def save_conflict(self, entry: ConflictEntry) -> None: ...

    @abstractmethod
    def list_conflicts(
        self,
        resolution: Optional[str] = None,
        limit: int = 20,
    ) -> List[ConflictEntry]: ...
```

**Implementation note:** These methods have default no-op implementations on the base class (raising `NotImplementedError` or returning empty lists) so that existing store implementations don't break until they're updated. The `MemoryStore` (in-memory) and `SqliteStore` should be updated in this feature. `HttpStore` can be deferred to when the server adds fact endpoints.

### 4.10 Recall Enhancement — Fact-Aware Retrieval

When fact extraction is enabled, `recall()` gains an optional fact-aware mode:

```python
def recall(self, query: str, *, use_facts: bool = False, ...) -> List[RecallResult]:
    results = self._recall_local(query, ...)  # existing vector search

    if use_facts and self._fact_extraction_enabled:
        # Supplement results with fact-based matches
        fact_results = self._recall_by_facts(query)
        results = self._merge_results(results, fact_results)

    return results
```

**`_recall_by_facts()` approach:**
1. Extract a subject+predicate from the query (via LLM or simple keyword match).
2. Look up active facts matching the subject.
3. Return the source memories for matching facts, scored by fact confidence.

This is a lightweight enhancement — full graph-based retrieval comes in F1. For now, fact-aware recall provides more precise answers for direct factual queries like "What database do we use?" or "Where does the user live?".

### 4.11 MCP Tool: `extract_facts`

```python
@mcp.tool(
    description=(
        "Extract structured facts from text without storing them. "
        "Returns atomic (subject, predicate, object) triples with confidence scores. "
        "USE THIS WHEN: you need to understand what facts are contained in a piece of text, "
        "or to preview what facts would be extracted before remembering."
    ),
)
def extract_facts(text: str) -> str:
    """Extract facts from text, return formatted list."""
```

**Output format:**
```
Extracted 3 facts:

1. (project, uses, PostgreSQL 16) [confidence: 0.95]
2. (project, deployed_on, AWS us-east-1) [confidence: 0.88]
3. (team, size, 5 engineers) [confidence: 0.72]
```

### 4.12 MCP Tool: `conflicts`

```python
@mcp.tool(
    description=(
        "List recent fact conflicts detected during memory ingestion. "
        "Shows what facts were superseded, merged, or flagged as contradictions. "
        "USE THIS WHEN: you want to review knowledge changes, audit what facts "
        "were updated, or resolve flagged contradictions."
    ),
)
def conflicts(
    resolution: Optional[str] = None,  # filter: SUPERSEDE, MERGE, CONTRADICT
    limit: int = 10,
) -> str:
    """List recent conflicts."""
```

**Output format:**
```
Recent conflicts (3 total):

1. [SUPERSEDE] user/lives_in: "NYC" -> "Berlin"
   Memory: abc123 (2026-03-06)
   Reason: Temporal update — user explicitly stated they moved.

2. [CONTRADICT] project/database: "MySQL 5.7" vs "PostgreSQL 16"
   Memory: def456 (2026-03-06)
   Reason: Both stated as current — needs human clarification.

3. [MERGE] project/language: "Python" + "TypeScript"
   Memory: ghi789 (2026-03-05)
   Reason: Complementary — project uses both languages.
```

### 4.13 CLI Commands

**`lore facts <memory-id>`** — Show facts extracted from a specific memory:
```
$ lore facts abc123
Facts for memory abc123:

  Subject          Predicate     Object            Confidence  Status
  user             lives_in      Berlin            0.95        active
  user             moved_from    NYC               0.88        active
  move             happened      last week         0.72        active
```

**`lore facts`** (no argument) — List all active facts:
```
$ lore facts --subject user --limit 10
Active facts (filtered by subject: user):

  Subject   Predicate      Object        Confidence  Source Memory
  user      lives_in       Berlin        0.95        abc123
  user      prefers        dark mode     0.90        def456
  user      role           engineer      0.85        ghi789
```

**`lore conflicts`** — List recent conflicts:
```
$ lore conflicts
$ lore conflicts --resolution CONTRADICT
$ lore conflicts --limit 5
```

### 4.14 Cascade Deletion

When a memory is deleted via `forget()`:
- All facts with `memory_id = deleted_id` are also deleted (CASCADE).
- Conflict log entries referencing the memory are preserved (audit trail).
- Facts that were `invalidated_by` the deleted memory are NOT restored — the invalidation stands because the knowledge evolution is still valid even if the source memory is removed.

### 4.15 Batch Extraction (Backfill)

For existing memories that predate fact extraction:

```python
def backfill_facts(self, project: Optional[str] = None, limit: int = 100) -> int:
    """Extract facts from existing memories that have no facts yet."""
    memories = self._store.list(project=project, limit=limit)
    count = 0
    for memory in memories:
        existing_facts = self._store.get_facts(memory.id)
        if not existing_facts:
            facts = self._extract_facts(memory)
            for fact in facts:
                self._store.save_fact(fact)
            count += len(facts)
    return count
```

This is exposed as a CLI command: `lore backfill-facts [--project X] [--limit 100]`

## 5. ConflictEntry Data Model

```python
@dataclass
class ConflictEntry:
    """A record of a fact conflict detection and resolution."""
    id: str                     # UUID
    new_memory_id: str          # memory that triggered the conflict
    old_fact_id: str            # existing fact that conflicted
    new_fact_id: Optional[str]  # new fact (None if CONTRADICT — no new fact stored)
    subject: str
    predicate: str
    old_value: str              # old fact's object
    new_value: str              # new fact's object
    resolution: str             # SUPERSEDE, MERGE, CONTRADICT, NOOP
    resolved_at: str            # ISO timestamp
    metadata: Optional[Dict[str, Any]] = None  # reasoning, model, etc.
```

## 6. File Changes

| File | Change |
|------|--------|
| `src/lore/types.py` | Add `Fact` and `ConflictEntry` dataclasses, `VALID_RESOLUTIONS` constant |
| `src/lore/lore.py` | Add `_extract_facts()`, `extract_facts()`, `get_facts()`, `list_conflicts()`, `get_active_facts()`, `backfill_facts()`. Extend `remember()` with fact extraction step. |
| `src/lore/store/base.py` | Add fact and conflict abstract methods (with default no-op/NotImplementedError) |
| `src/lore/store/sqlite.py` | Create `facts` + `conflict_log` tables. Implement fact/conflict CRUD. |
| `src/lore/store/memory.py` | In-memory implementation of fact/conflict storage (for testing) |
| `src/lore/store/http.py` | Stub methods (raise NotImplementedError until server adds endpoints) |
| `src/lore/extract/` | **NEW** module: `__init__.py`, `facts.py` (extraction logic + LLM prompt), `conflict.py` (resolution logic) |
| `src/lore/mcp/server.py` | Add `extract_facts` and `conflicts` tools |
| `src/lore/cli.py` | Add `facts` and `conflicts` subcommands, `backfill-facts` command |
| `tests/test_fact_extraction.py` | **NEW** — unit tests for fact extraction, conflict resolution |
| `tests/test_conflict_log.py` | **NEW** — unit tests for conflict log CRUD and audit trail |

## 7. Implementation Plan

### 7.1 Task Breakdown

1. **Data model** — Add `Fact`, `ConflictEntry` to `types.py`. Add `VALID_RESOLUTIONS`.
2. **Store layer** — Add abstract methods to `Store` ABC. Implement in `SqliteStore` and `MemoryStore` (schema + CRUD).
3. **Extraction module** — Create `src/lore/extract/facts.py` with LLM prompt construction, response parsing, subject normalization. Uses same LLM abstraction as F6.
4. **Conflict resolution** — Create `src/lore/extract/conflict.py` with resolution application logic (invalidate old facts, create conflict log entries).
5. **Lore facade** — Wire extraction into `remember()`. Add public methods for facts/conflicts access.
6. **MCP tools** — Add `extract_facts` and `conflicts` tools to `server.py`.
7. **CLI** — Add `facts`, `conflicts`, `backfill-facts` subcommands.
8. **Fact-aware recall** — Add `use_facts` parameter to `recall()`, implement `_recall_by_facts()`.
9. **Tests** — Comprehensive test suite covering extraction, conflict resolution, cascade deletion, backfill, and edge cases.

### 7.2 Dependencies on F6/F9

This feature depends on the LLM abstraction layer from F6 (Metadata Enrichment). Specifically:
- LLM provider configuration (model, API key, provider type)
- LLM call wrapper (handles retries, error handling, response parsing)
- Enrichment pipeline hook point (where F2 plugs in)

If F6 is not yet implemented when F2 development begins, F2 can define its own minimal LLM wrapper that F6 later replaces/absorbs.

## 8. Acceptance Criteria

### Must Have (P0)

- [ ] AC-1: `Fact` dataclass exists with all fields: id, memory_id, subject, predicate, object, confidence, extracted_at, invalidated_by, invalidated_at, metadata.
- [ ] AC-2: `facts` table created in SQLite with proper schema and indexes.
- [ ] AC-3: `conflict_log` table created in SQLite with proper schema and indexes.
- [ ] AC-4: `remember()` with `fact_extraction=True` extracts facts via LLM and stores them in `facts` table.
- [ ] AC-5: `remember()` with `fact_extraction=False` (default) behaves identically to v0.5.x — no LLM calls, no facts interaction.
- [ ] AC-6: When a new fact has the same subject+predicate as an existing active fact, conflict is detected.
- [ ] AC-7: SUPERSEDE resolution invalidates old fact (`invalidated_by`, `invalidated_at` set) and stores new fact.
- [ ] AC-8: MERGE resolution keeps both facts active.
- [ ] AC-9: CONTRADICT resolution keeps both facts active and creates a conflict log entry with `resolution='CONTRADICT'`.
- [ ] AC-10: All conflict resolutions create entries in `conflict_log` (except NOOP).
- [ ] AC-11: `get_facts(memory_id)` returns all facts extracted from that memory.
- [ ] AC-12: `get_active_facts()` returns only non-invalidated facts.
- [ ] AC-13: `list_conflicts()` returns conflict log entries, filterable by resolution type.
- [ ] AC-14: `forget(memory_id)` cascades to delete associated facts.
- [ ] AC-15: MCP `extract_facts` tool extracts facts from text and returns formatted output.
- [ ] AC-16: MCP `conflicts` tool lists recent conflicts with resolution details.
- [ ] AC-17: CLI `lore facts <memory-id>` shows facts for a memory.
- [ ] AC-18: CLI `lore conflicts` shows recent conflict log entries.
- [ ] AC-19: All existing tests pass without modification (backward compatibility).
- [ ] AC-20: New tests cover: fact extraction, all 4 resolution strategies, conflict log, cascade deletion, no-LLM mode.

### Should Have (P1)

- [ ] AC-21: CLI `lore facts` (no argument) lists active facts with filtering options.
- [ ] AC-22: CLI `lore backfill-facts` extracts facts from existing memories.
- [ ] AC-23: Fact-aware recall (`use_facts=True`) returns more precise results for factual queries.
- [ ] AC-24: Subject normalization (lowercase, trimmed whitespace) for consistent matching.
- [ ] AC-25: `extract_facts` MCP tool works as a preview (doesn't store facts).

### Could Have (P2)

- [ ] AC-26: Postgres server endpoints for facts and conflicts.
- [ ] AC-27: HttpStore implementation for fact/conflict methods.
- [ ] AC-28: Batch conflict resolution CLI (resolve multiple CONTRADICT entries).

## 9. Success Metrics

| Metric | Target |
|--------|--------|
| All existing tests pass | 100% |
| New test count | >= 40 tests |
| Fact extraction from a 3-sentence memory | Produces 2-5 atomic facts |
| Conflict detection accuracy | Correctly identifies SUPERSEDE vs MERGE vs CONTRADICT in test scenarios |
| No-LLM mode | Zero overhead — `remember()` latency unchanged when `fact_extraction=False` |
| Cascade deletion | `forget()` removes all associated facts |
| Conflict log completeness | Every non-NOOP resolution has a log entry |

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM extraction quality varies | Medium — bad facts degrade knowledge | Confidence scores filter low-quality facts. Users can review via `lore facts`. |
| LLM latency on `remember()` | Medium — slower ingestion | Extraction is optional (off by default). Consider async extraction as future optimization. |
| Subject normalization is brittle | Medium — "PostgreSQL" vs "postgres" vs "pg" | Exact match for now. F1 (Knowledge Graph) will add entity resolution/aliasing. |
| Over-extraction (too many trivial facts) | Low — noise in fact store | LLM prompt instructs to extract only substantive, reusable facts. Confidence threshold filters noise. |
| Conflict resolution is LLM-dependent | Medium — wrong resolutions | CONTRADICT strategy is the safe default for ambiguous cases. Users can review and correct. |
| Schema migration complexity | Low — new tables, no ALTER | `facts` and `conflict_log` are new tables. No migration of existing data needed. |
| F6 LLM abstraction not ready | Medium — blocks implementation | F2 can define a minimal LLM wrapper as a fallback, absorbed by F6 when ready. |

## 11. Interaction with Existing Systems

### Enrichment Pipeline (F6/F9)
Fact extraction is a step in the enrichment pipeline. It runs after metadata enrichment (F6) and classification (F9), receiving their outputs as context. The pipeline is ordered: enrich -> classify -> extract facts.

### Importance Scoring (F5)
Facts inherit importance from their source memory. When a memory's importance decays below threshold and is cleaned up, its facts are cascade-deleted. High-importance memories produce facts that persist longer.

### Memory Tiers (F4)
Working-tier memories may not warrant fact extraction (ephemeral context). The extraction pipeline can skip working-tier memories by default, configurable via `extract_facts_from_tiers=("short", "long")`.

### Recall (existing)
Fact-aware recall is additive — it supplements vector similarity search, doesn't replace it. The `use_facts` parameter defaults to `False` for backward compatibility.

## 12. Future Considerations (Out of Scope)

- **Entity resolution / aliasing** — "PostgreSQL" = "postgres" = "pg". Deferred to F1 (Knowledge Graph).
- **Fact embeddings** — Embedding individual facts for semantic fact search. Potentially useful but adds complexity.
- **Confidence decay** — Facts could lose confidence over time. Currently confidence is static from extraction.
- **Multi-source fact corroboration** — If multiple memories produce the same fact, boost confidence. Future enhancement.
- **Fact editing UI** — Manual fact CRUD via CLI or API. Could be added post-v0.6.0.
- **Temporal facts** — Facts with `valid_from`/`valid_until` timestamps. Deferred to F1 graph layer which has temporal edge support.
