# Architecture: F2 — Fact Extraction + Conflict Resolution

**Version:** 1.0
**Author:** Architect Agent
**Date:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f02-fact-extraction-prd.md`
**Phase:** 2 — Intelligence Layer
**Depends on:** F6 (Metadata Enrichment — LLM abstraction), F9 (Dialog Classification — pipeline integration)
**Dependents:** F1 (Knowledge Graph Layer — facts become graph entities/edges)

---

## 1. Overview

F2 transforms Lore from an opaque memory store into a structured knowledge base. When a user calls `remember()`, the system extracts atomic `(subject, predicate, object)` triples from the content, detects conflicts against existing facts, and applies resolution strategies (SUPERSEDE, MERGE, CONTRADICT, NOOP). An audit trail records every conflict detection and resolution.

### Architecture Principles

1. **Opt-in extraction** — Fact extraction is disabled by default (`fact_extraction=False`). When off, `remember()` has zero overhead — no LLM calls, no fact queries, no extra DB writes.
2. **Graceful degradation** — LLM extraction failures never block `remember()`. If extraction fails, the memory is saved without facts and a warning is logged.
3. **Graph-ready schema** — `subject` and `object` are free-text strings now, but the schema is designed so they become FK references to an `entities` table in F1 (Phase 3). `predicate` becomes a relationship type.
4. **Pipeline integration** — Fact extraction is the third step in the enrichment pipeline: F6 (enrich) → F9 (classify) → F2 (extract facts). It consumes upstream metadata (entities, topics) for better extraction context.
5. **Audit everything** — Every non-NOOP conflict creates a `conflict_log` entry. The audit trail persists even when source memories are deleted.

---

## 2. Fact Data Model

### 2.1 Zod-style Schema (TypeScript reference)

```typescript
const FactSchema = z.object({
  id:              z.string().ulid(),
  memory_id:       z.string().ulid(),
  subject:         z.string().min(1).transform(s => s.toLowerCase().trim()),
  predicate:       z.string().min(1).transform(s => s.toLowerCase().trim()),
  object:          z.string().min(1),
  confidence:      z.number().min(0).max(1).default(1.0),
  extracted_at:    z.string().datetime(),
  invalidated_by:  z.string().ulid().nullable().default(null),
  invalidated_at:  z.string().datetime().nullable().default(null),
  metadata:        z.record(z.unknown()).nullable().default(null),
});

const ConflictEntrySchema = z.object({
  id:              z.string().ulid(),
  new_memory_id:   z.string().ulid(),
  old_fact_id:     z.string().ulid(),
  new_fact_id:     z.string().ulid().nullable(),
  subject:         z.string().min(1),
  predicate:       z.string().min(1),
  old_value:       z.string().min(1),
  new_value:       z.string().min(1),
  resolution:      z.enum(["SUPERSEDE", "MERGE", "CONTRADICT", "NOOP"]),
  resolved_at:     z.string().datetime(),
  metadata:        z.record(z.unknown()).nullable().default(null),
});

const ResolutionStrategy = z.enum(["SUPERSEDE", "MERGE", "CONTRADICT", "NOOP"]);

const LLMExtractionResult = z.object({
  facts: z.array(z.object({
    subject:    z.string().min(1),
    predicate:  z.string().min(1),
    object:     z.string().min(1),
    confidence: z.number().min(0).max(1),
    resolution: ResolutionStrategy,
    reasoning:  z.string().optional(),
    conflicts_with: z.string().ulid().optional(), // old_fact_id if resolution != NOOP
  })),
});
```

### 2.2 Python Dataclasses (`src/lore/types.py`)

```python
from typing import Any, Dict, List, Optional, Tuple

VALID_RESOLUTIONS: Tuple[str, ...] = ("SUPERSEDE", "MERGE", "CONTRADICT", "NOOP")


@dataclass
class Fact:
    """An atomic fact extracted from a memory.

    Represents a (subject, predicate, object) triple — a single piece
    of structured knowledge derived from unstructured memory content.

    Graph-ready: subject/object will become FK references to an entities
    table in F1 (Knowledge Graph, Phase 3). predicate becomes a
    relationship type.
    """

    id: str                                        # ULID
    memory_id: str                                 # source memory that produced this fact
    subject: str                                   # entity/concept, normalized lowercase
    predicate: str                                 # relationship/attribute, normalized lowercase
    object: str                                    # value (free-text, case preserved)
    confidence: float = 1.0                        # extraction confidence [0.0, 1.0]
    extracted_at: str = ""                         # ISO 8601 timestamp
    invalidated_by: Optional[str] = None           # memory_id that superseded this fact
    invalidated_at: Optional[str] = None           # ISO 8601 timestamp of invalidation
    metadata: Optional[Dict[str, Any]] = None      # extraction model, reasoning, etc.


@dataclass
class ConflictEntry:
    """A record of a fact conflict detection and resolution.

    Created whenever a new fact has the same (subject, predicate) as an
    existing active fact and the resolution is not NOOP.
    """

    id: str                                        # ULID
    new_memory_id: str                             # memory that triggered the conflict
    old_fact_id: str                               # existing fact that conflicted
    new_fact_id: Optional[str]                     # new fact (None if CONTRADICT)
    subject: str
    predicate: str
    old_value: str                                 # old fact's object
    new_value: str                                 # new fact's object
    resolution: str                                # one of VALID_RESOLUTIONS
    resolved_at: str                               # ISO 8601 timestamp
    metadata: Optional[Dict[str, Any]] = None      # reasoning, model, confidence
```

**Invariants:**

| Field | Constraint | Enforcement |
|-------|-----------|-------------|
| `subject` | Lowercase, trimmed | Normalized in `FactExtractor._normalize_subject()` before storage |
| `predicate` | Lowercase, trimmed, snake_case preferred | Normalized in `FactExtractor._normalize_predicate()` |
| `confidence` | `0.0 <= c <= 1.0` | Clamped in extraction parsing |
| `resolution` | One of `VALID_RESOLUTIONS` | Validated before `ConflictEntry` creation |
| `invalidated_by` | Must reference a valid `memory_id` | Checked during `invalidate_fact()` |
| `new_fact_id` | `None` only when `resolution == "CONTRADICT"` | Enforced in `ConflictResolver.apply()` |

---

## 3. Database Schema

### 3.1 `facts` Table — SQLite

```sql
CREATE TABLE IF NOT EXISTS facts (
    id              TEXT PRIMARY KEY,
    memory_id       TEXT NOT NULL,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    object          TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    extracted_at    TEXT NOT NULL,
    invalidated_by  TEXT,
    invalidated_at  TEXT,
    metadata        TEXT,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_facts_memory
    ON facts(memory_id);
CREATE INDEX IF NOT EXISTS idx_facts_subject
    ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
    ON facts(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_active
    ON facts(id) WHERE invalidated_by IS NULL;
```

**Schema notes:**
- `FOREIGN KEY ... ON DELETE CASCADE` ensures `forget(memory_id)` cascades to delete associated facts. Requires `PRAGMA foreign_keys = ON` (already enabled in `SqliteStore.__init__`).
- `idx_facts_subject_predicate` is the critical index for conflict detection — queries like `SELECT * FROM facts WHERE subject = ? AND predicate = ? AND invalidated_by IS NULL`.
- `idx_facts_active` is a partial index on active (non-invalidated) facts for fast active-fact queries.
- `metadata` is stored as JSON text (same pattern as `Memory.metadata` in existing schema).

### 3.2 `conflict_log` Table — SQLite

```sql
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

CREATE INDEX IF NOT EXISTS idx_conflict_log_memory
    ON conflict_log(new_memory_id);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolution
    ON conflict_log(resolution);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolved
    ON conflict_log(resolved_at);
```

**Design decision — no FK from `conflict_log` to `facts`:** Conflict log entries are audit records that persist even when source memories/facts are deleted. Using FKs would cause cascade deletion, losing the audit trail. Instead, `old_fact_id` and `new_fact_id` are stored as plain text references.

### 3.3 `facts` Table — PostgreSQL (Server)

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
CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(id) WHERE invalidated_by IS NULL;
```

```sql
CREATE TABLE IF NOT EXISTS conflict_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    new_memory_id   UUID NOT NULL,
    old_fact_id     UUID NOT NULL,
    new_fact_id     UUID,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    old_value       TEXT NOT NULL,
    new_value       TEXT NOT NULL,
    resolution      TEXT NOT NULL,
    resolved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS idx_conflict_log_memory ON conflict_log(new_memory_id);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolution ON conflict_log(resolution);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolved ON conflict_log(resolved_at);
```

### 3.4 Schema Creation in SqliteStore

Follow the existing pattern — add table creation to `__init__()` after migrations:

```python
class SqliteStore(Store):
    def __init__(self, db_path: str):
        # ... existing init ...
        self._maybe_create_fact_tables()

    def _maybe_create_fact_tables(self) -> None:
        """Create facts and conflict_log tables if they don't exist."""
        with self._connect() as conn:
            conn.executescript(_FACT_SCHEMA)
```

Where `_FACT_SCHEMA` is a module-level constant containing the `CREATE TABLE IF NOT EXISTS` statements and indexes above. This is idempotent — safe to run on every init.

### 3.5 Graph Precursor Design

The schema is explicitly designed to support the future F1 Knowledge Graph:

```
Current (F2):                          Future (F1):
┌──────────┐                          ┌──────────┐
│  facts   │                          │  facts   │
├──────────┤                          ├──────────┤
│ subject  │ TEXT ──────────────────→  │ subject_entity_id │ UUID FK → entities
│ predicate│ TEXT ──────────────────→  │ predicate_type    │ TEXT → relationship_types
│ object   │ TEXT ──────────────────→  │ object_entity_id  │ UUID FK → entities
└──────────┘                          └──────────┘

                                      ┌──────────┐
                                      │ entities │
                                      ├──────────┤
                                      │ id       │ UUID PK
                                      │ name     │ TEXT (canonical)
                                      │ aliases  │ TEXT[] (entity resolution)
                                      │ type     │ TEXT (person, tool, project...)
                                      └──────────┘
```

**Migration path from F2 → F1:**
1. Create `entities` table with `(id, name, aliases, type)`.
2. Run entity resolution: group facts by normalized `subject`/`object`, create entity records.
3. Add `subject_entity_id` and `object_entity_id` columns to `facts`.
4. Backfill entity FKs from `subject`/`object` text fields.
5. Keep `subject`/`object` text columns for backward compatibility (read-only after migration).

**Design decisions for graph readiness:**
- `subject` and `object` are free-text but normalized (lowercase, trimmed). This makes entity resolution grouping deterministic.
- `predicate` uses snake_case convention (`lives_in`, `uses`, `deployed_on`). This maps cleanly to relationship types.
- The triple structure `(subject, predicate, object)` maps directly to graph edges: `subject --predicate--> object`.
- `metadata` on facts can carry entity type hints from F6 enrichment (e.g., `{"subject_type": "person", "object_type": "tool"}`), which F1 will consume during entity resolution.

---

## 4. Fact Extraction Module

### 4.1 Module Structure

```
src/lore/extract/
    __init__.py           # Public API: FactExtractor, ConflictResolver
    extractor.py          # LLM-powered fact extraction
    resolver.py           # Conflict detection + resolution logic
    prompts.py            # LLM prompt templates
```

### 4.2 FactExtractor (`src/lore/extract/extractor.py`)

```python
from lore.types import Fact, Memory
from lore.store.base import Store
from typing import List, Optional, Dict, Any
import json
import ulid

class FactExtractor:
    """Extracts atomic (subject, predicate, object) facts from memory content.

    Uses an LLM to parse unstructured text into structured triples.
    Queries the store for existing facts to detect conflicts.
    """

    def __init__(
        self,
        llm_client,            # LLMClient from F6/shared abstraction
        store: Store,
        confidence_threshold: float = 0.3,
    ):
        self._llm = llm_client
        self._store = store
        self._confidence_threshold = confidence_threshold

    def extract(
        self,
        memory: Memory,
        enrichment_context: Optional[Dict[str, Any]] = None,
    ) -> List[ExtractedFact]:
        """Extract facts from a memory's content.

        Args:
            memory: The source memory.
            enrichment_context: Upstream enrichment data from F6/F9
                (entities, topics, classification). Passed to the LLM
                prompt for better extraction.

        Returns:
            List of ExtractedFact (fact + resolution + reasoning).
            Does NOT save to store — caller handles persistence.
        """
        # 1. Build subject candidates from content + enrichment
        subject_hints = self._get_subject_hints(memory, enrichment_context)

        # 2. Query existing active facts for those subjects
        existing_facts = self._lookup_existing_facts(subject_hints)

        # 3. Build LLM prompt with content + existing facts
        prompt = self._build_prompt(memory.content, existing_facts, enrichment_context)

        # 4. Call LLM
        response = self._llm.complete(prompt)

        # 5. Parse + validate response
        extracted = self._parse_response(response, memory.id, existing_facts)

        # 6. Filter by confidence threshold
        return [f for f in extracted if f.fact.confidence >= self._confidence_threshold]

    def extract_preview(self, text: str) -> List[Fact]:
        """Extract facts from arbitrary text without storing.

        Used by MCP extract_facts tool and CLI. Does NOT check for
        conflicts (no existing facts context).
        """
        prompt = self._build_prompt(text, existing_facts=[], enrichment_context=None)
        response = self._llm.complete(prompt)
        return self._parse_preview_response(response, text)

    def _get_subject_hints(
        self,
        memory: Memory,
        enrichment_context: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Extract likely subject strings from memory + enrichment.

        Sources (in priority order):
        1. F6 enrichment entities (if available)
        2. F9 classification context
        3. Memory tags
        4. Project name
        """
        hints = []
        if enrichment_context:
            entities = enrichment_context.get("enrichment", {}).get("entities", [])
            hints.extend(e["name"].lower().strip() for e in entities)
            topics = enrichment_context.get("enrichment", {}).get("topics", [])
            hints.extend(t.lower().strip() for t in topics)
        if memory.tags:
            hints.extend(t.lower().strip() for t in memory.tags)
        if memory.project:
            hints.append(memory.project.lower().strip())
        return list(set(hints))  # deduplicate

    def _lookup_existing_facts(self, subject_hints: List[str]) -> List[Fact]:
        """Query store for active facts matching any subject hint."""
        existing = []
        for subject in subject_hints:
            facts = self._store.get_active_facts(subject=subject)
            existing.extend(facts)
        return existing

    @staticmethod
    def _normalize_subject(s: str) -> str:
        """Normalize subject: lowercase, strip whitespace."""
        return s.lower().strip()

    @staticmethod
    def _normalize_predicate(p: str) -> str:
        """Normalize predicate: lowercase, strip, replace spaces with underscores."""
        return p.lower().strip().replace(" ", "_")

    def _build_prompt(
        self,
        content: str,
        existing_facts: List[Fact],
        enrichment_context: Optional[Dict[str, Any]],
    ) -> str:
        """Build the extraction prompt. See Section 4.3."""
        return build_extraction_prompt(content, existing_facts, enrichment_context)

    def _parse_response(
        self,
        response: str,
        memory_id: str,
        existing_facts: List[Fact],
    ) -> List[ExtractedFact]:
        """Parse LLM JSON response into ExtractedFact objects.

        Handles malformed JSON gracefully — logs warning, returns
        partial results or empty list.
        """
        try:
            data = json.loads(self._extract_json(response))
        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON for fact extraction")
            return []

        facts_data = data.get("facts", [])
        results = []
        now = datetime.utcnow().isoformat() + "Z"

        existing_by_id = {f.id: f for f in existing_facts}

        for item in facts_data:
            subject = self._normalize_subject(item.get("subject", ""))
            predicate = self._normalize_predicate(item.get("predicate", ""))
            obj = item.get("object", "").strip()
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
            resolution = item.get("resolution", "NOOP").upper()
            reasoning = item.get("reasoning", "")

            if not subject or not predicate or not obj:
                continue
            if resolution not in VALID_RESOLUTIONS:
                resolution = "NOOP"

            fact = Fact(
                id=str(ulid.new()),
                memory_id=memory_id,
                subject=subject,
                predicate=predicate,
                object=obj,
                confidence=confidence,
                extracted_at=now,
                metadata={"extraction_model": self._llm.model, "reasoning": reasoning},
            )

            # Find the existing fact this conflicts with (if any)
            conflicts_with_id = item.get("conflicts_with")
            conflicting_fact = existing_by_id.get(conflicts_with_id) if conflicts_with_id else None

            # If LLM didn't specify conflicts_with, try subject+predicate match
            if not conflicting_fact and resolution != "NOOP":
                for ef in existing_facts:
                    if ef.subject == subject and ef.predicate == predicate and ef.invalidated_by is None:
                        conflicting_fact = ef
                        break

            results.append(ExtractedFact(
                fact=fact,
                resolution=resolution,
                reasoning=reasoning,
                conflicting_fact=conflicting_fact,
            ))

        return results

    @staticmethod
    def _extract_json(response: str) -> str:
        """Extract JSON from LLM response, handling markdown code blocks."""
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            # Remove first and last lines (```json and ```)
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                elif line.strip() == "```" and in_block:
                    break
                elif in_block:
                    json_lines.append(line)
            return "\n".join(json_lines)
        return response


@dataclass
class ExtractedFact:
    """A fact with its resolution context, before persistence."""
    fact: Fact
    resolution: str                     # SUPERSEDE, MERGE, CONTRADICT, NOOP
    reasoning: str                      # LLM reasoning for the resolution
    conflicting_fact: Optional[Fact]    # existing fact this conflicts with (if any)
```

### 4.3 LLM Extraction Prompt (`src/lore/extract/prompts.py`)

```python
def build_extraction_prompt(
    content: str,
    existing_facts: List[Fact],
    enrichment_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the fact extraction prompt for the LLM."""

    existing_section = ""
    if existing_facts:
        facts_json = [
            {
                "id": f.id,
                "subject": f.subject,
                "predicate": f.predicate,
                "object": f.object,
                "confidence": f.confidence,
            }
            for f in existing_facts
        ]
        existing_section = f"""
EXISTING FACTS for related subjects (compare against these for conflicts):
{json.dumps(facts_json, indent=2)}

For each extracted fact, if an existing fact has the same subject+predicate:
- Set "conflicts_with" to the existing fact's "id"
- Classify "resolution" as one of:
  - SUPERSEDE: the new fact replaces the old (temporal update, correction, migration)
  - MERGE: both facts are true simultaneously (complementary, not contradictory)
  - CONTRADICT: genuine contradiction that needs human review
"""
    else:
        existing_section = """
No existing facts found. Set resolution to "NOOP" for all facts.
"""

    enrichment_section = ""
    if enrichment_context:
        enrichment = enrichment_context.get("enrichment", {})
        if enrichment:
            enrichment_section = f"""
ENRICHMENT CONTEXT (from upstream pipeline):
- Topics: {', '.join(enrichment.get('topics', []))}
- Entities: {json.dumps(enrichment.get('entities', []))}
- Categories: {', '.join(enrichment.get('categories', []))}
Use this context to improve entity/subject identification.
"""

    return f"""Extract atomic facts from the following memory content. Each fact should be a
(subject, predicate, object) triple representing a single piece of reusable knowledge.

RULES:
- Extract only substantive, reusable facts. Skip trivial or context-dependent statements.
- Subject: the entity or concept the fact is about. Use a short, specific noun phrase.
- Predicate: the relationship or attribute. Use a verb phrase or attribute name.
- Object: the value, target, or description. Can be a noun, value, or short phrase.
- Confidence: your confidence that this fact is correctly extracted (0.0-1.0).
  Higher confidence for explicit, unambiguous statements.
  Lower confidence for implied or uncertain information.
- Normalize subjects to lowercase. Use consistent naming across facts.

CONTENT:
\"\"\"{content}\"\"\"
{existing_section}{enrichment_section}
Return ONLY valid JSON (no markdown, no explanation):
{{
  "facts": [
    {{
      "subject": "...",
      "predicate": "...",
      "object": "...",
      "confidence": 0.95,
      "resolution": "NOOP",
      "reasoning": "brief explanation",
      "conflicts_with": null
    }}
  ]
}}"""
```

### 4.4 Confidence Scoring

Confidence scores are assigned by the LLM during extraction. Guidelines provided in the prompt:

| Content Pattern | Expected Confidence |
|----------------|-------------------|
| Explicit, unambiguous statement ("We use PostgreSQL 16") | 0.85 - 1.0 |
| Strong implicit fact ("I migrated from MySQL to Postgres") | 0.70 - 0.85 |
| Implied or contextual ("The deploy was slow, maybe the DB") | 0.40 - 0.70 |
| Speculative or uncertain ("I think we might use Redis") | 0.20 - 0.40 |

**Confidence threshold:** Facts below `confidence_threshold` (default: 0.3) are discarded. This filters noise from speculative extractions.

**Confidence is static:** Once extracted, a fact's confidence never changes. Future enhancement (out of scope): multi-source corroboration could boost confidence.

---

## 5. Conflict Resolution Engine

### 5.1 ConflictResolver (`src/lore/extract/resolver.py`)

```python
from lore.types import Fact, ConflictEntry, VALID_RESOLUTIONS
from lore.store.base import Store
import ulid
from datetime import datetime
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


class ConflictResolver:
    """Applies conflict resolution strategies to extracted facts.

    Takes a list of ExtractedFact (from FactExtractor) and persists
    them according to their resolution strategy:
    - NOOP: save new fact, no conflict log
    - SUPERSEDE: invalidate old fact, save new fact, log conflict
    - MERGE: save new fact (old stays active), log conflict
    - CONTRADICT: do NOT save new fact, log conflict
    """

    def __init__(self, store: Store):
        self._store = store

    def resolve_all(
        self,
        extracted_facts: List[ExtractedFact],
        memory_id: str,
    ) -> ResolutionResult:
        """Apply resolution to all extracted facts.

        Returns:
            ResolutionResult with saved facts, conflict entries, and stats.
        """
        saved_facts: List[Fact] = []
        conflicts: List[ConflictEntry] = []
        stats = {"noop": 0, "supersede": 0, "merge": 0, "contradict": 0}

        for ef in extracted_facts:
            resolution = ef.resolution.upper()
            if resolution not in VALID_RESOLUTIONS:
                logger.warning(f"Unknown resolution '{resolution}', treating as NOOP")
                resolution = "NOOP"

            if resolution == "NOOP":
                self._store.save_fact(ef.fact)
                saved_facts.append(ef.fact)
                stats["noop"] += 1

            elif resolution == "SUPERSEDE":
                self._apply_supersede(ef, memory_id, saved_facts, conflicts)
                stats["supersede"] += 1

            elif resolution == "MERGE":
                self._apply_merge(ef, memory_id, saved_facts, conflicts)
                stats["merge"] += 1

            elif resolution == "CONTRADICT":
                self._apply_contradict(ef, memory_id, conflicts)
                stats["contradict"] += 1

        return ResolutionResult(
            saved_facts=saved_facts,
            conflicts=conflicts,
            stats=stats,
        )

    def _apply_supersede(
        self,
        ef: ExtractedFact,
        memory_id: str,
        saved_facts: List[Fact],
        conflicts: List[ConflictEntry],
    ) -> None:
        """SUPERSEDE: old fact invalidated, new fact saved."""
        now = datetime.utcnow().isoformat() + "Z"

        if ef.conflicting_fact:
            # Invalidate old fact
            self._store.invalidate_fact(
                fact_id=ef.conflicting_fact.id,
                invalidated_by=memory_id,
            )

        # Save new fact
        self._store.save_fact(ef.fact)
        saved_facts.append(ef.fact)

        # Log conflict
        conflict = ConflictEntry(
            id=str(ulid.new()),
            new_memory_id=memory_id,
            old_fact_id=ef.conflicting_fact.id if ef.conflicting_fact else "",
            new_fact_id=ef.fact.id,
            subject=ef.fact.subject,
            predicate=ef.fact.predicate,
            old_value=ef.conflicting_fact.object if ef.conflicting_fact else "",
            new_value=ef.fact.object,
            resolution="SUPERSEDE",
            resolved_at=now,
            metadata={"reasoning": ef.reasoning},
        )
        self._store.save_conflict(conflict)
        conflicts.append(conflict)

    def _apply_merge(
        self,
        ef: ExtractedFact,
        memory_id: str,
        saved_facts: List[Fact],
        conflicts: List[ConflictEntry],
    ) -> None:
        """MERGE: both facts stay active, new fact saved."""
        now = datetime.utcnow().isoformat() + "Z"

        # Save new fact (old stays active)
        self._store.save_fact(ef.fact)
        saved_facts.append(ef.fact)

        # Log the merge
        conflict = ConflictEntry(
            id=str(ulid.new()),
            new_memory_id=memory_id,
            old_fact_id=ef.conflicting_fact.id if ef.conflicting_fact else "",
            new_fact_id=ef.fact.id,
            subject=ef.fact.subject,
            predicate=ef.fact.predicate,
            old_value=ef.conflicting_fact.object if ef.conflicting_fact else "",
            new_value=ef.fact.object,
            resolution="MERGE",
            resolved_at=now,
            metadata={"reasoning": ef.reasoning},
        )
        self._store.save_conflict(conflict)
        conflicts.append(conflict)

    def _apply_contradict(
        self,
        ef: ExtractedFact,
        memory_id: str,
        conflicts: List[ConflictEntry],
    ) -> None:
        """CONTRADICT: both facts stay active, NO new fact saved, conflict logged."""
        now = datetime.utcnow().isoformat() + "Z"

        # Do NOT save the new fact — contradiction is unresolved
        # Both old and new facts are flagged for human review

        conflict = ConflictEntry(
            id=str(ulid.new()),
            new_memory_id=memory_id,
            old_fact_id=ef.conflicting_fact.id if ef.conflicting_fact else "",
            new_fact_id=None,  # No new fact stored
            subject=ef.fact.subject,
            predicate=ef.fact.predicate,
            old_value=ef.conflicting_fact.object if ef.conflicting_fact else "",
            new_value=ef.fact.object,
            resolution="CONTRADICT",
            resolved_at=now,
            metadata={
                "reasoning": ef.reasoning,
                "proposed_fact": {
                    "subject": ef.fact.subject,
                    "predicate": ef.fact.predicate,
                    "object": ef.fact.object,
                    "confidence": ef.fact.confidence,
                },
            },
        )
        self._store.save_conflict(conflict)
        conflicts.append(conflict)


@dataclass
class ResolutionResult:
    """Result of applying conflict resolution to extracted facts."""
    saved_facts: List[Fact]
    conflicts: List[ConflictEntry]
    stats: Dict[str, int]       # {"noop": N, "supersede": N, "merge": N, "contradict": N}
```

### 5.2 Resolution Decision Logic

The LLM makes the resolution decision, but the system validates it:

```
LLM returns: resolution = "SUPERSEDE", conflicts_with = "fact_abc123"
                │
                ▼
┌────────────────────────────────┐
│ Validate:                      │
│ 1. Is resolution in VALID?     │ No → default to NOOP
│ 2. Does conflicts_with exist?  │ No → fallback to subject+predicate match
│ 3. Is conflicting fact active? │ No → treat as NOOP (already invalidated)
└────────────────────────────────┘
                │
                ▼
        ConflictResolver.apply()
```

### 5.3 Multi-Step Resolution Chains

When a fact is superseded multiple times:

```
Memory 1: "We use MySQL 5.7"
  → Fact A: (project, uses_database, MySQL 5.7) [active]

Memory 2: "We migrated to PostgreSQL 16"
  → Fact B: (project, uses_database, PostgreSQL 16) [active]
  → Fact A: invalidated_by = memory_2, invalidated_at = now
  → Conflict log: A → B, SUPERSEDE

Memory 3: "Actually we're on PostgreSQL 17 now"
  → Fact C: (project, uses_database, PostgreSQL 17) [active]
  → Fact B: invalidated_by = memory_3, invalidated_at = now
  → Conflict log: B → C, SUPERSEDE
```

Query: `get_active_facts(subject="project", predicate="uses_database")`
Result: `[Fact C]` — only the latest active fact.

Query: `get_facts(memory_id="memory_1")` → `[Fact A (invalidated)]`
Full history visible through conflict log.

### 5.4 Conflict Scoring Heuristics

When the LLM's resolution is ambiguous, these heuristics help:

| Signal | Interpretation |
|--------|---------------|
| Temporal language ("now", "moved to", "switched") | SUPERSEDE |
| Additive language ("also", "in addition", "and") | MERGE |
| Contradictory values with no temporal signal | CONTRADICT |
| Same value or semantically equivalent | NOOP |
| Confidence of new fact < 0.5 | Lean toward CONTRADICT (uncertain extraction) |

These heuristics are embedded in the LLM prompt, not in code. The LLM applies them during extraction.

---

## 6. Storage & Persistence

### 6.1 Store ABC Extensions (`src/lore/store/base.py`)

```python
class Store(ABC):
    # ... existing methods ...

    # === Fact Storage ===

    def save_fact(self, fact: Fact) -> None:
        """Save a fact to the store. Default: no-op (for backward compat)."""
        pass

    def get_facts(self, memory_id: str) -> List[Fact]:
        """Get all facts extracted from a specific memory (active + invalidated)."""
        return []

    def get_active_facts(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        limit: int = 50,
    ) -> List[Fact]:
        """Get active (non-invalidated) facts, optionally filtered."""
        return []

    def invalidate_fact(self, fact_id: str, invalidated_by: str) -> None:
        """Mark a fact as invalidated by a memory_id."""
        pass

    # === Conflict Log ===

    def save_conflict(self, entry: ConflictEntry) -> None:
        """Save a conflict log entry."""
        pass

    def list_conflicts(
        self,
        resolution: Optional[str] = None,
        limit: int = 20,
    ) -> List[ConflictEntry]:
        """List conflict log entries, optionally filtered by resolution type."""
        return []
```

**Design decision — default no-op implementations:** The base `Store` provides default implementations (no-op or empty returns) rather than `@abstractmethod`. This ensures existing `HttpStore` and custom store implementations don't break. Only `SqliteStore` and `MemoryStore` are updated in this feature.

### 6.2 SqliteStore Implementation

```python
# In SqliteStore:

def save_fact(self, fact: Fact) -> None:
    with self._connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO facts
               (id, memory_id, subject, predicate, object, confidence,
                extracted_at, invalidated_by, invalidated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fact.id,
                fact.memory_id,
                fact.subject,
                fact.predicate,
                fact.object,
                fact.confidence,
                fact.extracted_at,
                fact.invalidated_by,
                fact.invalidated_at,
                json.dumps(fact.metadata) if fact.metadata else None,
            ),
        )

def get_facts(self, memory_id: str) -> List[Fact]:
    with self._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM facts WHERE memory_id = ? ORDER BY extracted_at",
            (memory_id,),
        ).fetchall()
        return [self._row_to_fact(r) for r in rows]

def get_active_facts(
    self,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    limit: int = 50,
) -> List[Fact]:
    query = "SELECT * FROM facts WHERE invalidated_by IS NULL"
    params: List[Any] = []
    if subject:
        query += " AND subject = ?"
        params.append(subject.lower().strip())
    if predicate:
        query += " AND predicate = ?"
        params.append(predicate.lower().strip())
    query += " ORDER BY extracted_at DESC LIMIT ?"
    params.append(limit)

    with self._connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_fact(r) for r in rows]

def invalidate_fact(self, fact_id: str, invalidated_by: str) -> None:
    now = datetime.utcnow().isoformat() + "Z"
    with self._connect() as conn:
        conn.execute(
            """UPDATE facts
               SET invalidated_by = ?, invalidated_at = ?
               WHERE id = ? AND invalidated_by IS NULL""",
            (invalidated_by, now, fact_id),
        )

def save_conflict(self, entry: ConflictEntry) -> None:
    with self._connect() as conn:
        conn.execute(
            """INSERT INTO conflict_log
               (id, new_memory_id, old_fact_id, new_fact_id, subject,
                predicate, old_value, new_value, resolution, resolved_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.new_memory_id,
                entry.old_fact_id,
                entry.new_fact_id,
                entry.subject,
                entry.predicate,
                entry.old_value,
                entry.new_value,
                entry.resolution,
                entry.resolved_at,
                json.dumps(entry.metadata) if entry.metadata else None,
            ),
        )

def list_conflicts(
    self,
    resolution: Optional[str] = None,
    limit: int = 20,
) -> List[ConflictEntry]:
    query = "SELECT * FROM conflict_log"
    params: List[Any] = []
    if resolution:
        query += " WHERE resolution = ?"
        params.append(resolution.upper())
    query += " ORDER BY resolved_at DESC LIMIT ?"
    params.append(limit)

    with self._connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_conflict(r) for r in rows]

@staticmethod
def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        memory_id=row["memory_id"],
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        confidence=row["confidence"],
        extracted_at=row["extracted_at"],
        invalidated_by=row["invalidated_by"],
        invalidated_at=row["invalidated_at"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else None,
    )

@staticmethod
def _row_to_conflict(row: sqlite3.Row) -> ConflictEntry:
    return ConflictEntry(
        id=row["id"],
        new_memory_id=row["new_memory_id"],
        old_fact_id=row["old_fact_id"],
        new_fact_id=row["new_fact_id"],
        subject=row["subject"],
        predicate=row["predicate"],
        old_value=row["old_value"],
        new_value=row["new_value"],
        resolution=row["resolution"],
        resolved_at=row["resolved_at"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else None,
    )
```

### 6.3 MemoryStore Implementation (In-Memory)

```python
class MemoryStore(Store):
    def __init__(self):
        # ... existing ...
        self._facts: Dict[str, Fact] = {}               # fact_id → Fact
        self._conflict_log: List[ConflictEntry] = []

    def save_fact(self, fact: Fact) -> None:
        self._facts[fact.id] = fact

    def get_facts(self, memory_id: str) -> List[Fact]:
        return [f for f in self._facts.values() if f.memory_id == memory_id]

    def get_active_facts(self, subject=None, predicate=None, limit=50):
        results = [f for f in self._facts.values() if f.invalidated_by is None]
        if subject:
            results = [f for f in results if f.subject == subject.lower().strip()]
        if predicate:
            results = [f for f in results if f.predicate == predicate.lower().strip()]
        return sorted(results, key=lambda f: f.extracted_at, reverse=True)[:limit]

    def invalidate_fact(self, fact_id: str, invalidated_by: str) -> None:
        if fact_id in self._facts:
            fact = self._facts[fact_id]
            fact.invalidated_by = invalidated_by
            fact.invalidated_at = datetime.utcnow().isoformat() + "Z"

    def save_conflict(self, entry: ConflictEntry) -> None:
        self._conflict_log.append(entry)

    def list_conflicts(self, resolution=None, limit=20):
        results = self._conflict_log
        if resolution:
            results = [c for c in results if c.resolution == resolution.upper()]
        return sorted(results, key=lambda c: c.resolved_at, reverse=True)[:limit]

    def delete(self, memory_id: str) -> bool:
        # ... existing delete logic ...
        # CASCADE: delete associated facts
        fact_ids_to_delete = [
            fid for fid, f in self._facts.items() if f.memory_id == memory_id
        ]
        for fid in fact_ids_to_delete:
            del self._facts[fid]
        return existed
```

### 6.4 HttpStore Stubs

```python
class HttpStore(Store):
    # Fact methods raise NotImplementedError until server adds endpoints
    def save_fact(self, fact: Fact) -> None:
        raise NotImplementedError("Fact storage not yet supported via HTTP")

    # ... etc for all fact/conflict methods ...
```

### 6.5 Fact Invalidation Flow

```
┌─────────────────────────────────────────────────────────┐
│                  FACT LIFECYCLE                          │
│                                                         │
│  Created  ──────────────────────────────────→  Active   │
│                                                  │      │
│                                    SUPERSEDE by  │      │
│                                    new memory     │      │
│                                                  ▼      │
│                                           Invalidated   │
│                                                  │      │
│                                    Source memory  │      │
│                                    deleted        │      │
│                                    (CASCADE)      │      │
│                                                  ▼      │
│                                             Deleted     │
│                                                         │
│  Note: Invalidated facts are NOT restored when the      │
│  superseding memory is deleted. The knowledge evolution  │
│  stands — the invalidation represents that the fact was  │
│  once superseded, regardless of the superseding memory's │
│  continued existence.                                    │
└─────────────────────────────────────────────────────────┘
```

### 6.6 Querying Fact History

**Active facts for a subject:**
```python
lore.get_active_facts(subject="project")
# → Returns only non-invalidated facts
```

**All facts from a memory (including invalidated):**
```python
lore.get_facts(memory_id="abc123")
# → Returns all facts, with invalidated_by/at showing history
```

**Full evolution of a subject+predicate:**
```python
# Query via conflict log
conflicts = lore.list_conflicts()
# Filter for subject+predicate of interest
db_history = [c for c in conflicts if c.subject == "project" and c.predicate == "uses_database"]
# Shows: MySQL 5.7 → PostgreSQL 16 → PostgreSQL 17
```

---

## 7. Lore Facade Integration

### 7.1 Constructor Changes (`src/lore/lore.py`)

```python
class Lore:
    def __init__(
        self,
        # ... existing params ...
        fact_extraction: bool = False,
        fact_confidence_threshold: float = 0.3,
    ) -> None:
        # ... existing init ...

        # Fact extraction setup
        self._fact_extraction_enabled = fact_extraction
        self._fact_extractor: Optional[FactExtractor] = None
        self._conflict_resolver: Optional[ConflictResolver] = None

        if fact_extraction:
            # Requires LLM client from F6 shared abstraction
            if self._llm_client is None:
                logger.warning(
                    "fact_extraction=True but no LLM configured. "
                    "Fact extraction will be skipped."
                )
                self._fact_extraction_enabled = False
            else:
                self._fact_extractor = FactExtractor(
                    llm_client=self._llm_client,
                    store=self._store,
                    confidence_threshold=fact_confidence_threshold,
                )
                self._conflict_resolver = ConflictResolver(store=self._store)
```

### 7.2 Extended `remember()` Method

```python
def remember(self, content: str, *, type: str = "general", ...) -> str:
    # ... existing: validate, redact, embed, create Memory, save ...
    memory_id = memory.id
    self._store.save(memory)

    # === F2: Fact Extraction (after memory is saved) ===
    if self._fact_extraction_enabled and self._fact_extractor:
        try:
            # Get enrichment context (from F6/F9 if available)
            enrichment_context = memory.metadata

            # Extract facts
            extracted = self._fact_extractor.extract(
                memory=memory,
                enrichment_context=enrichment_context,
            )

            # Resolve conflicts and persist
            if extracted:
                self._conflict_resolver.resolve_all(
                    extracted_facts=extracted,
                    memory_id=memory_id,
                )
        except Exception as e:
            logger.warning(f"Fact extraction failed for memory {memory_id}: {e}")
            # Memory is already saved — extraction failure is non-fatal

    return memory_id
```

### 7.3 New Public Methods

```python
def extract_facts(self, text: str) -> List[Fact]:
    """Extract facts from arbitrary text. Does not store them.

    Requires LLM to be configured. Raises RuntimeError if not.
    """
    if not self._fact_extractor:
        raise RuntimeError(
            "Fact extraction requires an LLM. Configure with "
            "fact_extraction=True and an LLM provider."
        )
    return self._fact_extractor.extract_preview(text)

def get_facts(self, memory_id: str) -> List[Fact]:
    """Get all facts extracted from a specific memory."""
    return self._store.get_facts(memory_id)

def get_active_facts(
    self,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    limit: int = 50,
) -> List[Fact]:
    """Get active (non-invalidated) facts, optionally filtered."""
    return self._store.get_active_facts(
        subject=subject, predicate=predicate, limit=limit
    )

def list_conflicts(
    self,
    resolution: Optional[str] = None,
    limit: int = 20,
) -> List[ConflictEntry]:
    """List recent conflict log entries."""
    return self._store.list_conflicts(resolution=resolution, limit=limit)

def backfill_facts(
    self,
    project: Optional[str] = None,
    limit: int = 100,
) -> int:
    """Extract facts from existing memories that have no facts yet.

    Returns count of facts extracted.
    """
    if not self._fact_extractor:
        raise RuntimeError("Fact extraction requires an LLM.")

    memories = self._store.list(project=project, limit=limit)
    count = 0
    for memory in memories:
        existing = self._store.get_facts(memory.id)
        if existing:
            continue
        try:
            extracted = self._fact_extractor.extract(memory=memory)
            if extracted:
                result = self._conflict_resolver.resolve_all(
                    extracted_facts=extracted,
                    memory_id=memory.id,
                )
                count += len(result.saved_facts)
        except Exception as e:
            logger.warning(f"Backfill failed for memory {memory.id}: {e}")
    return count
```

---

## 8. Recall Enhancement — Fact-Aware Retrieval

### 8.1 `recall()` Extension

```python
def recall(
    self,
    query: str,
    *,
    # ... existing params ...
    use_facts: bool = False,   # NEW: enable fact-aware retrieval
) -> List[RecallResult]:
    results = self._recall_internal(query, ...)

    if use_facts and self._fact_extraction_enabled:
        fact_results = self._recall_by_facts(query)
        results = self._merge_recall_results(results, fact_results)

    return results
```

### 8.2 Fact-Based Recall (`_recall_by_facts`)

```python
def _recall_by_facts(self, query: str) -> List[RecallResult]:
    """Find memories via fact matching.

    Strategy:
    1. Normalize the query to extract potential subject/predicate hints.
    2. Search active facts for matching subjects.
    3. Return source memories for matching facts, scored by fact confidence.
    """
    # Simple keyword-based subject extraction (no LLM call on read path)
    query_lower = query.lower().strip()
    words = query_lower.split()

    # Search for facts with subjects matching any word in the query
    matching_facts = []
    for word in words:
        if len(word) < 3:  # skip short words
            continue
        facts = self._store.get_active_facts(subject=word, limit=10)
        matching_facts.extend(facts)

    # Deduplicate by memory_id, keep highest-confidence fact per memory
    memory_scores: Dict[str, float] = {}
    for fact in matching_facts:
        if fact.memory_id not in memory_scores:
            memory_scores[fact.memory_id] = fact.confidence
        else:
            memory_scores[fact.memory_id] = max(
                memory_scores[fact.memory_id], fact.confidence
            )

    # Fetch memories and build RecallResults
    results = []
    for mid, confidence in memory_scores.items():
        memory = self._store.get(mid)
        if memory:
            results.append(RecallResult(memory=memory, score=confidence))

    return results

def _merge_recall_results(
    self,
    vector_results: List[RecallResult],
    fact_results: List[RecallResult],
) -> List[RecallResult]:
    """Merge vector similarity results with fact-based results.

    Fact matches boost the score of existing results or add new ones.
    """
    result_map = {r.memory.id: r for r in vector_results}

    for fr in fact_results:
        if fr.memory.id in result_map:
            # Boost existing result score
            existing = result_map[fr.memory.id]
            existing.score = min(1.0, existing.score + 0.1 * fr.score)
        else:
            # Add as new result with lower base score
            fr.score *= 0.7  # fact-only matches score lower than vector matches
            result_map[fr.memory.id] = fr

    merged = sorted(result_map.values(), key=lambda r: r.score, reverse=True)
    return merged
```

---

## 9. Enrichment Pipeline Integration

### 9.1 Pipeline Position

```
remember(content)
    │
    ▼
┌─────────────────────────────────────────────────┐
│           Enrichment Pipeline                    │
│                                                  │
│  Step 1: F6 — Metadata Enrichment               │
│    Input:  raw content                           │
│    Output: topics, sentiment, entities, categories│
│    Stored: metadata.enrichment                   │
│                                                  │
│  Step 2: F9 — Dialog Classification             │
│    Input:  raw content                           │
│    Output: intent, domain, emotion               │
│    Stored: metadata.classification               │
│                                                  │
│  Step 3: F2 — Fact Extraction  ← THIS FEATURE   │
│    Input:  raw content + F6 entities + F9 class  │
│    Output: atomic facts + conflict resolutions   │
│    Stored: facts table + conflict_log table       │
│                                                  │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
              Store memory + facts
```

### 9.2 Pipeline Data Flow

F2 receives upstream enrichment data through the memory's `metadata` dict:

```python
# After F6 + F9 have run, memory.metadata looks like:
{
    "enrichment": {
        "topics": ["deployment", "kubernetes"],
        "entities": [
            {"name": "Kubernetes", "type": "tool"},
            {"name": "AWS", "type": "platform"},
        ],
        "categories": ["infrastructure"],
    },
    "classification": {
        "intent": "decision",
        "domain": "technical",
        "emotion": "confident",
    }
}

# F2 uses this to:
# 1. Seed subject hints from F6 entities → ["kubernetes", "aws"]
# 2. Include entity context in LLM prompt for better extraction
# 3. Potentially skip extraction for certain intents (e.g., "question")
```

### 9.3 Pipeline Configuration

```python
Lore(
    # F6
    enrichment=True,
    enrichment_model="gpt-4o-mini",

    # F9
    classify=True,

    # F2
    fact_extraction=True,
    fact_confidence_threshold=0.3,

    # Shared LLM
    llm_provider="openai",
    llm_model="gpt-4o-mini",
    llm_api_key="sk-...",
)
```

### 9.4 Pipeline Ordering Guarantee

The pipeline runs steps sequentially in `remember()`:

```python
def remember(self, content, ...):
    # 1. Create memory, embed, redact
    # 2. F6: Enrich (if enabled) → updates metadata
    # 3. F9: Classify (if enabled) → updates metadata
    # 4. Save memory (with enrichment + classification in metadata)
    # 5. F2: Extract facts (if enabled) → writes to facts + conflict_log tables
    # 6. Return memory_id
```

F2 runs AFTER the memory is saved because:
- Facts reference `memory_id` via FK — the memory must exist first.
- If fact extraction fails, the memory is already safely persisted.
- Enrichment metadata is available in the saved memory's metadata.

### 9.5 Tier-Based Extraction Control

Working-tier memories are typically ephemeral and may not warrant fact extraction:

```python
# In FactExtractor.extract():
if memory.tier == "working":
    logger.debug("Skipping fact extraction for working-tier memory")
    return []
```

This is configurable — default behavior is to skip `working` tier. Can be overridden with a future `extract_facts_from_tiers` parameter.

---

## 10. API & CLI

### 10.1 MCP Tools

#### `extract_facts` Tool

```python
@mcp.tool(
    description=(
        "Extract structured facts from text without storing them. "
        "Returns atomic (subject, predicate, object) triples with confidence scores. "
        "USE THIS WHEN: you need to understand what facts are contained in text, "
        "or to preview what facts would be extracted before remembering."
    ),
)
def extract_facts(text: str) -> str:
    lore = _get_lore()
    try:
        facts = lore.extract_facts(text)
    except RuntimeError as e:
        return f"Error: {e}"

    if not facts:
        return "No facts extracted from the provided text."

    lines = [f"Extracted {len(facts)} fact(s):\n"]
    for i, fact in enumerate(facts, 1):
        lines.append(
            f"{i}. ({fact.subject}, {fact.predicate}, {fact.object}) "
            f"[confidence: {fact.confidence:.2f}]"
        )
    return "\n".join(lines)
```

#### `list_facts` Tool

```python
@mcp.tool(
    description=(
        "List active facts stored in Lore's knowledge base. "
        "Facts are atomic (subject, predicate, object) triples extracted from memories. "
        "USE THIS WHEN: you need to check what Lore knows about a specific topic "
        "or entity, or to review the current state of extracted knowledge."
    ),
)
def list_facts(
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    memory_id: Optional[str] = None,
    limit: int = 20,
) -> str:
    lore = _get_lore()

    if memory_id:
        facts = lore.get_facts(memory_id)
        header = f"Facts for memory {memory_id}:"
    else:
        facts = lore.get_active_facts(subject=subject, predicate=predicate, limit=limit)
        filters = []
        if subject:
            filters.append(f"subject={subject}")
        if predicate:
            filters.append(f"predicate={predicate}")
        filter_str = f" ({', '.join(filters)})" if filters else ""
        header = f"Active facts{filter_str}:"

    if not facts:
        return "No facts found."

    lines = [f"{header}\n"]
    for f in facts:
        status = "active" if f.invalidated_by is None else "invalidated"
        lines.append(
            f"  ({f.subject}, {f.predicate}, {f.object}) "
            f"[confidence: {f.confidence:.2f}, {status}]"
        )
    return "\n".join(lines)
```

#### `conflicts` Tool

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
    resolution: Optional[str] = None,
    limit: int = 10,
) -> str:
    lore = _get_lore()
    entries = lore.list_conflicts(resolution=resolution, limit=limit)

    if not entries:
        return "No conflicts found."

    lines = [f"Recent conflicts ({len(entries)} total):\n"]
    for i, c in enumerate(entries, 1):
        if c.resolution == "SUPERSEDE":
            lines.append(
                f"{i}. [SUPERSEDE] {c.subject}/{c.predicate}: "
                f'"{c.old_value}" -> "{c.new_value}"'
            )
        elif c.resolution == "CONTRADICT":
            lines.append(
                f"{i}. [CONTRADICT] {c.subject}/{c.predicate}: "
                f'"{c.old_value}" vs "{c.new_value}"'
            )
        elif c.resolution == "MERGE":
            lines.append(
                f"{i}. [MERGE] {c.subject}/{c.predicate}: "
                f'"{c.old_value}" + "{c.new_value}"'
            )
        # Add reasoning if available
        reasoning = (c.metadata or {}).get("reasoning", "")
        if reasoning:
            lines.append(f"   Reason: {reasoning}")
        lines.append(f"   Memory: {c.new_memory_id} ({c.resolved_at[:10]})")
        lines.append("")

    return "\n".join(lines)
```

### 10.2 REST API Endpoints (Server)

```
GET    /api/v1/facts                     # List active facts (query params: subject, predicate, limit)
GET    /api/v1/facts/:memory_id          # Get facts for a specific memory
GET    /api/v1/conflicts                 # List conflict log (query params: resolution, limit)
POST   /api/v1/facts/extract             # Extract facts from text (preview, no storage)
POST   /api/v1/facts/backfill            # Trigger backfill for existing memories
```

These are P2 (Could Have) — deferred until the server adds fact support.

### 10.3 CLI Commands

#### `lore facts`

```
Usage:
  lore facts <memory-id>                     # Show facts for a specific memory
  lore facts --subject <subject>             # List active facts filtered by subject
  lore facts --predicate <predicate>         # List active facts filtered by predicate
  lore facts --limit <n>                     # Limit results (default: 20)

Output:
  Subject          Predicate       Object             Confidence  Status
  project          uses_database   PostgreSQL 16      0.95        active
  project          deployed_on     AWS us-east-1      0.88        active
  team             size            5 engineers        0.72        active
```

#### `lore conflicts`

```
Usage:
  lore conflicts                             # List recent conflicts
  lore conflicts --resolution CONTRADICT     # Filter by resolution type
  lore conflicts --limit <n>                 # Limit results (default: 10)

Output:
  [SUPERSEDE] project/uses_database: "MySQL 5.7" -> "PostgreSQL 16"
    Memory: abc123 (2026-03-06)
    Reason: Temporal update — explicit migration statement.

  [CONTRADICT] project/database: "MySQL 5.7" vs "PostgreSQL 16"
    Memory: def456 (2026-03-06)
    Reason: Both stated as current — needs clarification.
```

#### `lore backfill-facts`

```
Usage:
  lore backfill-facts                        # Backfill all memories
  lore backfill-facts --project <name>       # Backfill specific project
  lore backfill-facts --limit <n>            # Limit memories processed (default: 100)

Output:
  Backfilled 47 facts from 15 memories.
```

### 10.4 CLI Implementation Pattern

```python
# In build_parser():
p_facts = sub.add_parser("facts", help="List or query extracted facts")
p_facts.add_argument("memory_id", nargs="?", default=None, help="Memory ID")
p_facts.add_argument("--subject", default=None)
p_facts.add_argument("--predicate", default=None)
p_facts.add_argument("--limit", type=int, default=20)

p_conflicts = sub.add_parser("conflicts", help="List fact conflicts")
p_conflicts.add_argument("--resolution", default=None)
p_conflicts.add_argument("--limit", type=int, default=10)

p_backfill = sub.add_parser("backfill-facts", help="Extract facts from existing memories")
p_backfill.add_argument("--project", default=None)
p_backfill.add_argument("--limit", type=int, default=100)

# In handlers dict:
handlers = {
    # ... existing ...
    "facts": cmd_facts,
    "conflicts": cmd_conflicts,
    "backfill-facts": cmd_backfill_facts,
}
```

---

## 11. Testing & Edge Cases

### 11.1 Test File Structure

```
tests/
    test_fact_extraction.py          # Extraction logic + LLM prompt parsing
    test_conflict_resolution.py      # All 4 resolution strategies
    test_fact_store.py               # Store CRUD for facts + conflict_log
    test_fact_integration.py         # End-to-end: remember → extract → resolve → recall
```

### 11.2 Test Categories

#### Extraction Tests (`test_fact_extraction.py`)

| Test | Description |
|------|-------------|
| `test_extract_single_fact` | "We use PostgreSQL 16" → 1 fact |
| `test_extract_multiple_facts` | Multi-sentence content → 2-5 facts |
| `test_extract_no_facts` | Greetings, questions → 0 facts |
| `test_confidence_scoring` | Explicit statements → high confidence, speculative → low |
| `test_confidence_threshold_filters` | Facts below threshold are discarded |
| `test_subject_normalization` | "PostgreSQL" → "postgresql", " AWS " → "aws" |
| `test_predicate_normalization` | "Lives In" → "lives_in" |
| `test_malformed_llm_json` | Invalid JSON → empty result, no crash |
| `test_partial_llm_json` | Missing fields → partial extraction |
| `test_markdown_code_block_response` | LLM wraps JSON in ``` → parsed correctly |
| `test_empty_content` | "" → 0 facts |
| `test_redacted_content` | "[REDACTED]" tokens in content → facts with redacted values |
| `test_enrichment_context_improves_extraction` | F6 entities seed better subject hints |

#### Conflict Resolution Tests (`test_conflict_resolution.py`)

| Test | Description |
|------|-------------|
| `test_noop_no_existing_facts` | New subject+predicate → NOOP, no conflict log |
| `test_noop_same_value` | Same subject+predicate+object → NOOP |
| `test_supersede_invalidates_old` | Old fact gets `invalidated_by`, `invalidated_at` |
| `test_supersede_saves_new` | New fact saved as active |
| `test_supersede_creates_conflict_log` | Conflict entry with old/new values |
| `test_merge_both_active` | Both old and new facts remain active |
| `test_merge_creates_conflict_log` | Conflict entry with MERGE resolution |
| `test_contradict_no_new_fact` | New fact is NOT saved |
| `test_contradict_old_stays_active` | Old fact remains active |
| `test_contradict_creates_conflict_log` | Conflict entry with proposed_fact in metadata |
| `test_multi_step_supersede_chain` | A → B → C, only C is active |
| `test_invalid_resolution_defaults_to_noop` | "UNKNOWN" → treated as NOOP |
| `test_resolve_all_mixed` | Multiple facts with different resolutions in one batch |

#### Store Tests (`test_fact_store.py`)

| Test | Description |
|------|-------------|
| `test_save_and_get_fact` | Round-trip save → get |
| `test_get_facts_by_memory` | Multiple facts for one memory |
| `test_get_active_facts_excludes_invalidated` | Invalidated facts filtered |
| `test_get_active_facts_filter_by_subject` | Subject filter works |
| `test_get_active_facts_filter_by_predicate` | Predicate filter works |
| `test_invalidate_fact` | Sets invalidated_by + invalidated_at |
| `test_invalidate_already_invalidated` | Idempotent (no-op) |
| `test_save_conflict_entry` | Round-trip save → list |
| `test_list_conflicts_filter_by_resolution` | Resolution filter works |
| `test_list_conflicts_ordered_by_resolved_at` | Most recent first |
| `test_cascade_delete_facts_on_forget` | `forget(memory_id)` deletes facts |
| `test_cascade_preserves_conflict_log` | `forget()` does NOT delete conflict_log entries |
| `test_fact_metadata_json_roundtrip` | JSON metadata serialization |

#### Integration Tests (`test_fact_integration.py`)

| Test | Description |
|------|-------------|
| `test_remember_with_extraction_disabled` | No LLM calls, no facts, identical to v0.5.x |
| `test_remember_with_extraction_enabled` | Memory saved + facts extracted |
| `test_remember_extraction_failure_saves_memory` | LLM error → memory saved, no facts |
| `test_recall_with_use_facts` | Fact-aware recall returns relevant memories |
| `test_recall_without_use_facts` | Backward compatible, no fact queries |
| `test_backfill_facts_skips_existing` | Already-extracted memories skipped |
| `test_backfill_facts_processes_new` | Un-extracted memories get facts |
| `test_end_to_end_supersede_flow` | Remember A → Remember B (supersedes) → only B active |
| `test_end_to_end_contradict_flow` | Remember A → Remember B (contradicts) → conflict logged |

### 11.3 Edge Cases

#### Redacted Content

```python
# Input: "We store passwords in [REDACTED] using [REDACTED] encryption"
# Expected facts:
#   (system, stores, passwords) [confidence: 0.7]
#   (system, encryption, [REDACTED]) [confidence: 0.4]  ← low confidence
# Note: Facts containing [REDACTED] get lower confidence
```

#### Multi-Step Resolution Chains

```python
# Memory 1: "Database is MySQL"     → Fact A (active)
# Memory 2: "Migrated to Postgres"  → Fact B (active), Fact A (invalidated)
# Memory 3: "Upgraded to Postgres 17" → Fact C (active), Fact B (invalidated)
# Delete Memory 2:
#   → Fact B deleted (CASCADE)
#   → Fact A stays invalidated (invalidation is permanent)
#   → Conflict log entries for Memory 2 preserved
#   → Fact C stays active
```

#### Contradicting Extractions Within Single Memory

```python
# Input: "We use both MySQL and PostgreSQL — MySQL for legacy, Postgres for new services"
# Expected: MERGE resolution, not CONTRADICT
# Both facts: (project, uses_database, MySQL) and (project, uses_database, PostgreSQL)
```

#### Empty/Minimal Content

```python
# Input: "Hi"  → 0 facts
# Input: "OK"  → 0 facts
# Input: "Thanks for the help"  → 0 facts (no substantive knowledge)
```

#### Very Long Content

```python
# Input: 5000-word document
# Expected: LLM extracts 5-15 key facts, not one per sentence
# Prompt instructs: "Extract only substantive, reusable facts"
```

### 11.4 Test Mocking Strategy

All tests mock the LLM client. No real API calls in tests.

```python
class MockLLMClient:
    def __init__(self, response: str):
        self.response = response
        self.model = "mock-model"
        self.calls = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


# Usage:
mock_llm = MockLLMClient(response=json.dumps({
    "facts": [
        {
            "subject": "project",
            "predicate": "uses_database",
            "object": "PostgreSQL 16",
            "confidence": 0.95,
            "resolution": "NOOP",
            "reasoning": "New fact, no existing match"
        }
    ]
}))
extractor = FactExtractor(llm_client=mock_llm, store=memory_store)
```

---

## 12. File Changes Summary

| File | Change | Priority |
|------|--------|----------|
| `src/lore/types.py` | Add `Fact`, `ConflictEntry` dataclasses, `VALID_RESOLUTIONS` | P0 |
| `src/lore/extract/__init__.py` | **NEW** — exports `FactExtractor`, `ConflictResolver` | P0 |
| `src/lore/extract/extractor.py` | **NEW** — LLM-powered fact extraction | P0 |
| `src/lore/extract/resolver.py` | **NEW** — conflict resolution logic | P0 |
| `src/lore/extract/prompts.py` | **NEW** — LLM prompt templates | P0 |
| `src/lore/store/base.py` | Add fact + conflict methods (default no-op) | P0 |
| `src/lore/store/sqlite.py` | Create `facts` + `conflict_log` tables, implement CRUD | P0 |
| `src/lore/store/memory.py` | In-memory fact/conflict storage | P0 |
| `src/lore/store/http.py` | Stub methods (`NotImplementedError`) | P0 |
| `src/lore/lore.py` | Constructor + `remember()` + new methods | P0 |
| `src/lore/mcp/server.py` | `extract_facts`, `list_facts`, `conflicts` tools | P0 |
| `src/lore/cli.py` | `facts`, `conflicts`, `backfill-facts` subcommands | P0 |
| `tests/test_fact_extraction.py` | **NEW** — extraction unit tests | P0 |
| `tests/test_conflict_resolution.py` | **NEW** — resolution unit tests | P0 |
| `tests/test_fact_store.py` | **NEW** — store CRUD tests | P0 |
| `tests/test_fact_integration.py` | **NEW** — end-to-end tests | P0 |

---

## 13. Dependency Graph

```
                    ┌──────────────┐
                    │  types.py    │
                    │  Fact        │
                    │  ConflictEntry│
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
     ┌────────────┐ ┌────────────┐ ┌──────────┐
     │ extractor  │ │ resolver   │ │ store/   │
     │   .py      │ │   .py      │ │ base.py  │
     └──────┬─────┘ └──────┬─────┘ └────┬─────┘
            │              │             │
            │    ┌─────────┘    ┌────────┤────────┐
            │    │              ▼        ▼        ▼
            │    │        sqlite.py  memory.py  http.py
            │    │
            ▼    ▼
     ┌─────────────────┐
     │    lore.py       │
     │  (Lore facade)   │
     └────────┬─────────┘
              │
        ┌─────┴─────┐
        ▼           ▼
   mcp/server.py  cli.py
```

---

## 14. Migration & Backward Compatibility

### 14.1 Zero-Migration Schema

Both `facts` and `conflict_log` are **new tables** — no `ALTER TABLE` needed on `memories`. The `CREATE TABLE IF NOT EXISTS` pattern makes schema creation idempotent and safe to run on existing databases.

### 14.2 Backward Compatibility Guarantees

| Concern | Guarantee |
|---------|-----------|
| `fact_extraction=False` (default) | Zero overhead. No LLM calls, no DB queries, no schema interaction. |
| Existing `remember()` calls | All parameters unchanged. New behavior is opt-in only. |
| Existing `recall()` calls | `use_facts` defaults to `False`. Existing behavior unchanged. |
| Existing `Store` subclasses | Base class provides default no-op implementations. No breakage. |
| `HttpStore` | Stubs raise `NotImplementedError`. Server endpoints are P2. |
| Database format | New tables only. Existing `memories` table untouched. |
| All existing tests | Must pass without modification. |

### 14.3 Feature Flag

```python
# Environment variable
LORE_FACT_EXTRACTION=true    # Enable fact extraction

# Constructor
Lore(fact_extraction=True)   # Explicit enable
```

When the environment variable is set but no LLM is configured, a warning is logged and extraction is disabled gracefully.
