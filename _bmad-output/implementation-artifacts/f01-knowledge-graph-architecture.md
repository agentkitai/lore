# Architecture: F1 -- Knowledge Graph Layer

**Version:** 1.0
**Author:** Architect Agent
**Date:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f01-knowledge-graph-prd.md`
**Phase:** 3 -- Graph Layer
**Depends on:** F2 (Fact Extraction -- SPO triples become graph edges), F6 (Metadata Enrichment -- extracted entities feed into graph)
**Dependents:** F3 (Memory Consolidation -- uses graph for intelligent grouping), F7 (Webhook Ingestion -- graph auto-updated on ingest)

---

## 1. Overview

F1 transforms Lore from a flat memory store with disconnected facts into a **connected knowledge graph**. Every entity mentioned in memories becomes a node, every relationship between entities becomes a directed edge, and multi-hop traversal lets agents answer relational queries that pure vector search cannot ("What tools does auth-service depend on?", "Show me everything connected to the deployment pipeline").

The graph lives entirely in Postgres (or SQLite for local). No Neo4j, no Apache AGE, no external graph database. Adjacency tables + recursive CTEs + well-placed indexes deliver 2-3 hop traversal under 50ms for graphs up to 10K entities. This is our competitive edge: same knowledge graph capabilities as Mem0/Zep, deployed with `docker compose up`.

### Architecture Principles

1. **Pure Postgres** -- Adjacency tables with recursive CTEs. No extensions, no second database. Works on Postgres 12+, SQLite 3.8.3+, every managed Postgres service.
2. **Opt-in, zero-overhead when off** -- `knowledge_graph=False` (default) means no table creation, no graph queries, no performance impact. `graph_depth=0` on `recall()` gives identical behavior to v0.5.x.
3. **No duplication** -- The graph layer does not re-extract what F2 and F6 already provide. It *promotes* and *connects* their outputs: F6 entities become graph nodes, F2 fact triples become graph edges.
4. **Temporal edges** -- Every relationship carries `valid_from`/`valid_until` for bi-temporal tracking. Fact supersession closes old edges and opens new ones.
5. **Graceful degradation** -- Graph update failures never block `remember()`. If graph extraction fails, the memory is saved without graph updates and a warning is logged.
6. **Normalize on write** -- Entity deduplication happens at write time via name normalization + alias tracking. "PostgreSQL", "postgres", "pg" all resolve to the same canonical entity.
7. **Bidirectional traversal** -- Graph queries follow both outgoing and incoming edges by default, treating the graph as undirected for discovery while preserving edge directionality for semantics.

---

## 2. Entity & Relationship Schema

### 2.1 Data Model -- Python Dataclasses (`src/lore/types.py`)

```python
@dataclass
class Entity:
    """A knowledge graph node (person, tool, project, concept, etc.)."""
    id: str                                        # ULID
    name: str                                      # Canonical lowercase name
    entity_type: str                               # person, tool, concept, project, organization, platform, language, framework
    aliases: List[str] = field(default_factory=list)  # Alternative surface forms
    metadata: Optional[Dict[str, Any]] = None
    first_seen_at: str = ""                        # ISO timestamp
    last_seen_at: str = ""                         # ISO timestamp
    mention_count: int = 1                         # Memory mention count

@dataclass
class Relationship:
    """A knowledge graph edge (directed, typed, weighted, temporal)."""
    id: str                                        # ULID
    source_entity_id: str                          # FK -> entities.id
    target_entity_id: str                          # FK -> entities.id
    relation_type: str                             # uses, depends_on, works_with, etc.
    weight: float = 1.0                            # Strength: 0.0-1.0
    valid_from: Optional[str] = None               # ISO timestamp (when relationship began)
    valid_until: Optional[str] = None              # ISO timestamp (None = currently active)
    memory_id: Optional[str] = None                # FK -> memories.id (provenance)
    fact_id: Optional[str] = None                  # FK -> facts.id (F2 source)
    metadata: Optional[Dict[str, Any]] = None
    created_at: str = ""                           # ISO timestamp

@dataclass
class EntityMention:
    """Junction: links an entity to a memory that mentions it."""
    entity_id: str                                 # FK -> entities.id
    memory_id: str                                 # FK -> memories.id
    mentioned_at: str = ""                         # ISO timestamp

@dataclass
class GraphResult:
    """Result of a graph traversal query."""
    root_entity: Entity
    entities: List[Entity]                         # All discovered entities
    relationships: List[Relationship]              # All discovered edges
    depth_reached: int
    total_entities: int
    total_relationships: int

@dataclass
class GraphNode:
    """An entity with traversal context (used internally during traversal)."""
    entity: Entity
    depth: int                                     # Hop distance from root
    path: List[str]                                # Entity IDs in the path from root
    incoming_relation: Optional[str] = None        # Relationship type that led here
    incoming_weight: Optional[float] = None        # Weight of that relationship
```

### 2.2 Extended RecallResult

```python
@dataclass
class RecallResult:
    memory: Memory
    score: float
    staleness: Any = None
    # NEW fields for graph-enhanced recall:
    related_entities: Optional[List[Entity]] = None   # Populated when include_entities=True
    graph_score: Optional[float] = None               # Graph proximity score
    graph_path: Optional[List[str]] = None            # Entity names in traversal path
```

### 2.3 Relationship Type Conventions

Free-text with recommended vocabulary (not enforced enums):

| Category | Types |
|----------|-------|
| **Technical** | `uses`, `depends_on`, `integrates_with`, `deployed_on`, `written_in`, `extends`, `replaces` |
| **Organizational** | `works_on`, `manages`, `created_by`, `owned_by`, `belongs_to`, `reports_to` |
| **Preference** | `prefers`, `recommends`, `avoids`, `chose_over` |
| **Knowledge** | `knows_about`, `learned`, `teaches`, `documented_in` |
| **Temporal** | `migrated_from`, `upgraded_to`, `replaced_by`, `preceded_by` |

F2 fact predicates map directly to relationship types. The LLM extraction prompt includes these conventions for consistency.

---

## 3. Graph Storage Strategy -- SQL Tables

### 3.1 SQLite Schema (`src/lore/store/sqlite.py`)

```sql
-- Knowledge graph: entities (nodes)
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,                  -- canonical lowercase name
    entity_type     TEXT NOT NULL DEFAULT 'concept',
    aliases         TEXT DEFAULT '[]',              -- JSON array of alternative names
    metadata        TEXT,                           -- JSON object
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    mention_count   INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_type
    ON entities(name, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

-- Knowledge graph: relationships (edges)
CREATE TABLE IF NOT EXISTS relationships (
    id                  TEXT PRIMARY KEY,
    source_entity_id    TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id    TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type       TEXT NOT NULL,
    weight              REAL NOT NULL DEFAULT 1.0,
    valid_from          TEXT,
    valid_until         TEXT,                       -- NULL = currently active
    memory_id           TEXT REFERENCES memories(id) ON DELETE SET NULL,
    fact_id             TEXT REFERENCES facts(id) ON DELETE SET NULL,
    metadata            TEXT,                       -- JSON object
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationships_type ON relationships(relation_type);
CREATE INDEX IF NOT EXISTS idx_relationships_memory ON relationships(memory_id);

-- Knowledge graph: entity-memory junction
CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    memory_id   TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    mentioned_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_mentions_memory ON entity_mentions(memory_id);
```

### 3.2 Postgres Schema (`migrations/007_knowledge_graph.sql`)

```sql
-- F1: Knowledge Graph Layer
-- Additive only -- new tables, no ALTER on existing tables
-- Idempotent -- safe to run multiple times

-- Entities: knowledge graph nodes
CREATE TABLE IF NOT EXISTS entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    entity_type     TEXT NOT NULL DEFAULT 'concept',
    aliases         JSONB DEFAULT '[]'::jsonb,
    metadata        JSONB,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mention_count   INT NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_type
    ON entities(name, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

-- GIN index on aliases for containment queries (@>)
CREATE INDEX IF NOT EXISTS idx_entities_aliases ON entities USING GIN (aliases);

-- Relationships: knowledge graph edges
CREATE TABLE IF NOT EXISTS relationships (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_entity_id    UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id    UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type       TEXT NOT NULL,
    weight              REAL NOT NULL DEFAULT 1.0,
    valid_from          TIMESTAMPTZ,
    valid_until         TIMESTAMPTZ,
    memory_id           UUID REFERENCES lessons(id) ON DELETE SET NULL,
    fact_id             UUID REFERENCES facts(id) ON DELETE SET NULL,
    metadata            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_relationships_source
    ON relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationships_target
    ON relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationships_type
    ON relationships(relation_type);
CREATE INDEX IF NOT EXISTS idx_relationships_active
    ON relationships(valid_until) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_relationships_memory
    ON relationships(memory_id);

-- Compound index: the hot path for traversal (source + active only)
CREATE INDEX IF NOT EXISTS idx_relationships_source_active
    ON relationships(source_entity_id, valid_until) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_relationships_target_active
    ON relationships(target_entity_id, valid_until) WHERE valid_until IS NULL;

-- Entity-Memory junction
CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    memory_id   UUID NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    mentioned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (entity_id, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_mentions_memory
    ON entity_mentions(memory_id);
```

**Design decisions:**

- **JSONB `aliases`** on Postgres with GIN index enables `aliases @> '["pg"]'::jsonb` containment queries for alias lookup without a separate table. SQLite uses TEXT with JSON array (parsed in Python).
- **Partial indexes** (`WHERE valid_until IS NULL`) on the active-relationships path ensures the planner only scans active edges during traversal -- the hot path.
- **Compound index** `(source_entity_id, valid_until) WHERE valid_until IS NULL` is the single most important index: it's what the recursive CTE base case hits on every traversal step.
- **`ON DELETE CASCADE`** from entities to relationships ensures no orphaned edges.
- **`ON DELETE SET NULL`** from memories to relationships preserves relationship structure even when source memory is deleted (relationship was confirmed by other memories).
- **No `org_id` on graph tables** -- Entities and relationships inherit project scoping through their linked memories. The `entity_mentions` junction provides the org boundary. If cross-org isolation is needed, add `org_id` to entities in a future migration.

---

## 4. Entity Deduplication

### 4.1 Name Normalization Algorithm

```python
# src/lore/graph/dedup.py

import re

# Common aliases: maps alternative forms to canonical names
# Loaded once, extended by user config in future
_BUILTIN_ALIASES: Dict[str, str] = {
    "pg": "postgresql",
    "postgres": "postgresql",
    "k8s": "kubernetes",
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "react.js": "react",
    "reactjs": "react",
    "node.js": "nodejs",
    "vue.js": "vue",
    "next.js": "nextjs",
}

_VERSION_SUFFIX = re.compile(r"\s+v?\d+[\.\d]*\s*$")

def normalize_entity_name(name: str) -> str:
    """Normalize entity name to canonical form.

    Steps:
    1. Strip whitespace
    2. Lowercase
    3. Collapse multiple spaces
    4. Apply builtin alias map
    5. Strip trailing version numbers (kept in aliases)
    """
    canonical = name.strip().lower()
    canonical = re.sub(r"\s+", " ", canonical)

    # Check builtin aliases
    if canonical in _BUILTIN_ALIASES:
        return _BUILTIN_ALIASES[canonical]

    # Strip version suffix: "postgresql 16" -> "postgresql"
    base = _VERSION_SUFFIX.sub("", canonical)
    if base and base != canonical:
        # Check alias map on base form too
        return _BUILTIN_ALIASES.get(base, base)

    return canonical
```

### 4.2 Entity Upsert with Alias Resolution

The upsert path is the **single point of entity creation**. Every code path that introduces entities (F2 facts, F6 enrichment, LLM extraction, backfill) goes through `upsert_entity()`.

```python
def upsert_entity(
    self,
    name: str,
    entity_type: str,
    aliases: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Entity:
    """Create or update an entity. Deduplicates via normalization + alias lookup.

    Resolution order:
    1. Exact match on (canonical_name, entity_type)
    2. Alias containment match on any existing entity of same type
    3. Create new entity
    """
    canonical = normalize_entity_name(name)
    now = _utc_now_iso()

    # Step 1: Exact match on canonical name + type
    existing = self._find_entity_by_name_type(canonical, entity_type)
    if existing:
        # Update: add original name as alias, bump counters
        if name.lower() != canonical and name not in existing.aliases:
            existing.aliases.append(name)
        existing.mention_count += 1
        existing.last_seen_at = now
        self._update_entity(existing)
        return existing

    # Step 2: Alias containment match
    alias_match = self._find_entity_by_alias(name, entity_type)
    if alias_match:
        if name not in alias_match.aliases:
            alias_match.aliases.append(name)
        alias_match.mention_count += 1
        alias_match.last_seen_at = now
        self._update_entity(alias_match)
        return alias_match

    # Step 3: Create new entity
    all_aliases = [name] if name.lower() != canonical else []
    if aliases:
        all_aliases.extend(a for a in aliases if a not in all_aliases)

    entity = Entity(
        id=str(ULID()),
        name=canonical,
        entity_type=entity_type,
        aliases=all_aliases,
        metadata=metadata,
        first_seen_at=now,
        last_seen_at=now,
        mention_count=1,
    )
    self._insert_entity(entity)
    return entity
```

### 4.3 Alias Lookup Implementation

**SQLite:**
```sql
-- Find entity where aliases JSON array contains a value (case-insensitive)
SELECT * FROM entities
WHERE entity_type = ?
  AND EXISTS (
    SELECT 1 FROM json_each(aliases)
    WHERE LOWER(json_each.value) = LOWER(?)
  );
```

**Postgres:**
```sql
-- JSONB containment: find entity with matching alias
SELECT * FROM entities
WHERE entity_type = $1
  AND aliases @> to_jsonb($2::text);
```

### 4.4 Entity Merge

When two entities are identified as duplicates (either manually or via LLM-assisted dedup):

```python
def merge_entities(self, source_id: str, target_id: str) -> Entity:
    """Merge source entity into target entity.

    1. Redirect all relationships from/to source -> target
    2. Move entity_mentions from source -> target
    3. Merge aliases (union)
    4. Sum mention_counts
    5. Take min(first_seen_at), max(last_seen_at)
    6. Delete source entity (CASCADE cleans up remaining refs)
    """
```

This is a single-transaction operation. The target entity survives; the source entity is deleted.

---

## 5. Relationship Extraction -- How Data Flows Into the Graph

### 5.1 Data Flow: remember() -> Graph

```
remember(content)
    |
    v
[Redact] -> [Embed] -> [Classify (F9)] -> [Enrich (F6)] -> [Save Memory]
                                                |                    |
                                                v                    v
                                        enrichment_data       memory.id exists
                                                |                    |
                                                v                    v
                                    [Extract Facts (F2)] -----> [Resolve Conflicts]
                                                |                    |
                                                v                    v
                                          facts[]             saved to facts table
                                                |
                                                v
                                    [Graph Update (F1)] <--- NEW STEP
                                        |       |       |
                                        v       v       v
                                    Promote   Convert   Optional LLM
                                    F6 ents   F2 facts  relationship
                                    to nodes  to edges  extraction
                                        |       |       |
                                        v       v       v
                                    [Dedup entities] -> [Store in DB]
```

### 5.2 F6 Integration: Enrichment Entities -> Graph Nodes

F6 enrichment extracts entities like `{"name": "Alice", "type": "person"}` into `metadata["enrichment"]["entities"]`. The graph layer promotes these to first-class `entities` table rows.

```python
# In _update_graph(), step 1:
for ent in enrichment.get("entities", []):
    entity = self._store.upsert_entity(
        name=ent["name"],
        entity_type=ent["type"],
    )
    self._store.add_entity_mention(entity.id, memory.id)
```

**Key invariant:** Every F6 entity becomes a graph node. The type vocabulary is shared: `person`, `tool`, `project`, `platform`, `organization`, `concept`, `language`, `framework`.

### 5.3 F2 Integration: Fact Triples -> Graph Edges

Every active F2 fact `(subject, predicate, object)` becomes:
- Two entity nodes (subject + object)
- One directed relationship edge (subject -[predicate]-> object)

```python
# In _update_graph(), step 2:
for fact in facts:
    if fact.invalidated_by:
        continue  # skip invalidated facts

    source = self._store.upsert_entity(
        name=fact.subject,
        entity_type=_infer_entity_type(fact.subject, enrichment),
    )
    target = self._store.upsert_entity(
        name=fact.object,
        entity_type=_infer_entity_type(fact.object, enrichment),
    )
    self._store.upsert_relationship(
        source_entity_id=source.id,
        target_entity_id=target.id,
        relation_type=fact.predicate,
        weight=fact.confidence,
        memory_id=memory.id,
        fact_id=fact.id,
        valid_from=memory.created_at,
    )
    self._store.add_entity_mention(source.id, memory.id)
    self._store.add_entity_mention(target.id, memory.id)
```

**Entity type inference:** When creating entities from F2 facts, check if F6 enrichment already identified the entity with a type. Fall back to `"concept"` if unknown.

```python
def _infer_entity_type(name: str, enrichment: Optional[Dict]) -> str:
    """Infer entity type from F6 enrichment data, defaulting to 'concept'."""
    if not enrichment:
        return "concept"
    canonical = normalize_entity_name(name)
    for ent in enrichment.get("entities", []):
        if normalize_entity_name(ent["name"]) == canonical:
            return ent.get("type", "concept")
    return "concept"
```

### 5.4 Relationship Upsert Logic (Strengthening)

When a relationship `(source, target, relation_type)` already exists as an active edge:

```python
def upsert_relationship(self, ...) -> Relationship:
    existing = self._find_active_relationship(
        source_entity_id, target_entity_id, relation_type
    )
    if existing:
        # Strengthen: weight increases with diminishing returns
        existing.weight = min(1.0, existing.weight + 0.1)
        existing.metadata = existing.metadata or {}
        existing.metadata.setdefault("confirmed_by", []).append(memory_id)
        self._update_relationship(existing)
        return existing
    else:
        return self._create_relationship(...)
```

Multiple memories confirming the same relationship make it stronger. The 0.1 increment with a 1.0 cap means a relationship needs ~10 confirmations to reach max weight. This is intentional -- relationships should earn their weight.

### 5.5 Temporal Edge Management

When F2 conflict resolution detects a SUPERSEDE:

```
2026-01: remember("We use MySQL for auth-service")
  -> Relationship: auth-service --uses--> mysql [valid_from: Jan, valid_until: NULL]

2026-03: remember("We migrated auth-service from MySQL to PostgreSQL")
  -> F2 SUPERSEDE: (auth-service, uses, mysql) superseded by (auth-service, uses, postgresql)
  -> Relationship: auth-service --uses--> mysql [valid_from: Jan, valid_until: Mar]    <-- CLOSED
  -> Relationship: auth-service --uses--> postgresql [valid_from: Mar, valid_until: NULL]  <-- NEW
  -> Relationship: auth-service --migrated_from--> mysql [valid_from: Mar]              <-- NEW
```

**Implementation hook:** The graph layer registers a callback (or is called directly by ConflictResolver) when `resolution == "SUPERSEDE"`:

```python
def _on_fact_superseded(self, old_fact: Fact, new_fact: Fact) -> None:
    old_rel = self._store.find_relationship_by_fact(old_fact.id)
    if old_rel:
        old_rel.valid_until = _utc_now_iso()
        self._store.update_relationship(old_rel)
    # New relationship created via normal upsert path
```

CONTRADICT resolution leaves both edges active -- the graph reflects the ambiguity. Resolution is left to the consuming agent.

### 5.6 Optional LLM Relationship Extraction

When `graph_llm_extraction=True`, an additional LLM call extracts relationships that F2 facts may not capture (implicit connections, co-occurrence patterns):

```python
# Only runs when: knowledge_graph=True AND graph_llm_extraction=True
# AND enrichment pipeline is available (for LLM client)
RELATIONSHIP_EXTRACTION_PROMPT = """
Extract relationships between the following entities found in this memory.

MEMORY CONTENT:
{content}

ENTITIES FOUND:
{entities_json}

For each relationship, provide:
1. Source entity (must be from the list above)
2. Target entity (must be from the list above)
3. Relationship type (use: uses, depends_on, works_with, created_by,
   prefers, written_in, deployed_on, integrates_with, etc.)
4. Confidence (0.0-1.0)

Return JSON only:
{
  "relationships": [
    {"source": "entity_name", "target": "entity_name",
     "relation_type": "uses", "confidence": 0.9}
  ]
}
"""
```

**Cost control:** This is off by default. When enabled, it adds one LLM call per `remember()`. The prompt is lightweight (entity list + memory content). For most use cases, F2 facts provide sufficient relationship coverage.

---

## 6. Graph Traversal -- Recursive CTEs

### 6.1 Core Bidirectional Traversal (SQLite)

```sql
WITH RECURSIVE graph_walk AS (
    -- Base case: outgoing edges from start entity
    SELECT
        r.target_entity_id AS entity_id,
        r.source_entity_id AS from_entity_id,
        r.relation_type,
        r.weight,
        1 AS depth,
        r.source_entity_id || ',' || r.target_entity_id AS path_str
    FROM relationships r
    WHERE r.source_entity_id = :start_entity_id
      AND r.valid_until IS NULL

    UNION ALL

    -- Base case: incoming edges to start entity
    SELECT
        r.source_entity_id AS entity_id,
        r.target_entity_id AS from_entity_id,
        r.relation_type,
        r.weight,
        1 AS depth,
        r.target_entity_id || ',' || r.source_entity_id AS path_str
    FROM relationships r
    WHERE r.target_entity_id = :start_entity_id
      AND r.valid_until IS NULL

    UNION ALL

    -- Recursive: follow edges from discovered entities (outgoing)
    SELECT
        r.target_entity_id,
        r.source_entity_id,
        r.relation_type,
        r.weight,
        gw.depth + 1,
        gw.path_str || ',' || r.target_entity_id
    FROM relationships r
    JOIN graph_walk gw ON r.source_entity_id = gw.entity_id
    WHERE gw.depth < :max_depth
      AND r.valid_until IS NULL
      AND INSTR(gw.path_str, r.target_entity_id) = 0  -- cycle prevention

    UNION ALL

    -- Recursive: follow edges from discovered entities (incoming)
    SELECT
        r.source_entity_id,
        r.target_entity_id,
        r.relation_type,
        r.weight,
        gw.depth + 1,
        gw.path_str || ',' || r.source_entity_id
    FROM relationships r
    JOIN graph_walk gw ON r.target_entity_id = gw.entity_id
    WHERE gw.depth < :max_depth
      AND r.valid_until IS NULL
      AND INSTR(gw.path_str, r.source_entity_id) = 0  -- cycle prevention
)
SELECT
    e.id, e.name, e.entity_type, e.mention_count,
    gw.relation_type, gw.weight, gw.depth, gw.path_str,
    gw.from_entity_id
FROM graph_walk gw
JOIN entities e ON e.id = gw.entity_id
ORDER BY gw.depth ASC, gw.weight DESC;
```

### 6.2 Core Bidirectional Traversal (Postgres)

```sql
WITH RECURSIVE graph_walk AS (
    -- Base case: outgoing
    SELECT
        r.target_entity_id AS entity_id,
        r.source_entity_id AS from_entity_id,
        r.relation_type,
        r.weight,
        1 AS depth,
        ARRAY[r.source_entity_id, r.target_entity_id] AS path
    FROM relationships r
    WHERE r.source_entity_id = :start_entity_id
      AND r.valid_until IS NULL

    UNION ALL

    -- Base case: incoming
    SELECT
        r.source_entity_id,
        r.target_entity_id,
        r.relation_type,
        r.weight,
        1,
        ARRAY[r.target_entity_id, r.source_entity_id]
    FROM relationships r
    WHERE r.target_entity_id = :start_entity_id
      AND r.valid_until IS NULL

    UNION ALL

    -- Recursive: outgoing from discovered
    SELECT
        r.target_entity_id,
        r.source_entity_id,
        r.relation_type,
        r.weight,
        gw.depth + 1,
        gw.path || r.target_entity_id
    FROM relationships r
    JOIN graph_walk gw ON r.source_entity_id = gw.entity_id
    WHERE gw.depth < :max_depth
      AND r.valid_until IS NULL
      AND NOT r.target_entity_id = ANY(gw.path)

    UNION ALL

    -- Recursive: incoming from discovered
    SELECT
        r.source_entity_id,
        r.target_entity_id,
        r.relation_type,
        r.weight,
        gw.depth + 1,
        gw.path || r.source_entity_id
    FROM relationships r
    JOIN graph_walk gw ON r.target_entity_id = gw.entity_id
    WHERE gw.depth < :max_depth
      AND r.valid_until IS NULL
      AND NOT r.source_entity_id = ANY(gw.path)
)
SELECT DISTINCT ON (gw.entity_id)
    e.id, e.name, e.entity_type, e.mention_count,
    gw.relation_type, gw.weight, gw.depth, gw.path,
    gw.from_entity_id
FROM graph_walk gw
JOIN entities e ON e.id = gw.entity_id
ORDER BY gw.entity_id, gw.depth ASC;
```

### 6.3 Performance Guardrails

| Guardrail | Value | Rationale |
|-----------|-------|-----------|
| Default depth | 2 | Covers most useful relational queries |
| Max depth | 4 | Hard cap. Depth 4 = O(branching_factor^4) rows. At avg 5 edges/node, depth 4 = 625 candidate rows -- manageable. PRD says 3, but we allow 4 with explicit opt-in. |
| Cycle prevention | Path array/string check | Prevents infinite loops. Tested explicitly. |
| Result limit | 100 entities per traversal | Prevents runaway result sets on dense hubs |
| Timeout | 5 seconds | SQLite `PRAGMA busy_timeout`. Postgres `statement_timeout`. |
| Active-only default | `valid_until IS NULL` | Filters historical edges unless `include_expired=True` |

**Why max 4 instead of PRD's 3:** The PRD suggests 3 as max, but depth 4 is useful for cross-domain discovery ("what's connected to what's connected to auth-service's dependencies?"). The partial indexes on active relationships keep this fast. We default to 2 and allow up to 4 with the understanding that depth 4 may be slower on very dense graphs.

### 6.4 Python Traversal Wrapper

```python
# src/lore/graph/traversal.py

def traverse_graph(
    store: Store,
    start_entity_id: str,
    max_depth: int = 2,
    relation_type: Optional[str] = None,
    include_expired: bool = False,
    direction: str = "both",  # "out", "in", "both"
    result_limit: int = 100,
) -> List[GraphNode]:
    """Execute graph traversal and return GraphNode list.

    Delegates to store.traverse_graph() which runs the appropriate
    recursive CTE for the backend (SQLite or Postgres).
    """
    # Clamp depth
    effective_depth = min(max_depth, 4)

    raw_nodes = store.traverse_graph(
        start_entity_id=start_entity_id,
        max_depth=effective_depth,
        relation_type=relation_type,
        include_expired=include_expired,
        direction=direction,
    )

    # Apply result limit
    return raw_nodes[:result_limit]
```

---

## 7. Hybrid Recall -- Vector + Graph

### 7.1 Algorithm

When `graph_depth > 0` on `recall()`:

```
Step 1: Vector search (existing)
  -> query embedding -> cosine similarity -> top N*2 candidates

Step 2: Identify query entities (lightweight, no LLM)
  -> tokenize query -> lookup tokens against entities.name + aliases
  -> bigram matching for multi-word entities
  -> fallback: fuzzy match (trigram similarity, threshold 0.6)

Step 3: Graph traversal from query entities
  -> for each identified entity: traverse_graph(depth=graph_depth)
  -> collect all discovered entity IDs
  -> lookup entity_mentions to get connected memory IDs

Step 4: Score and merge
  -> vector results: score = cosine * TAI * tier_weight (existing)
  -> graph results: graph_score = 1.0 / (1.0 + hop_distance) * edge_weight
  -> combined: final_score = (1 - graph_weight) * vector_score + graph_weight * graph_score
  -> memories appearing in BOTH sets get boosted by both scores

Step 5: Return top-K merged results
```

### 7.2 Scoring Formula

```
final_score = (1 - graph_weight) * vector_score + graph_weight * graph_score

where:
  vector_score = cosine_similarity * time_adjusted_importance * tier_weight
                 (existing multiplicative model, unchanged)

  graph_score  = sum_over_paths(1.0 / (1.0 + hop_distance) * edge_weight)
                 normalized to [0, 1] by dividing by max graph_score in result set

  graph_weight = 0.3 (default, configurable)
```

**Why additive not multiplicative for hybrid?** A memory discovered *only* through graph traversal (not in vector results) has `vector_score = 0`. With multiplicative scoring, it would never appear. Additive scoring lets pure-graph discoveries surface when `graph_weight > 0`.

**Score example:**
- Memory A: vector_score=0.8, graph_score=0.9 (1 hop, weight 0.9)
  - final = 0.7 * 0.8 + 0.3 * 0.9 = 0.56 + 0.27 = **0.83**
- Memory B: vector_score=0.9, graph_score=0.0 (not in graph results)
  - final = 0.7 * 0.9 + 0.3 * 0.0 = 0.63 + 0.00 = **0.63**
- Memory C: vector_score=0.0, graph_score=0.8 (2 hops, weight 0.8)
  - final = 0.7 * 0.0 + 0.3 * 0.8 = 0.00 + 0.24 = **0.24**

Memory A wins: it's both semantically similar AND graph-connected. Memory C -- discovered purely through graph -- still surfaces.

### 7.3 Query Entity Identification

```python
def _identify_query_entities(self, query: str) -> List[Entity]:
    """Identify entities referenced in a recall query. No LLM call."""
    tokens = _tokenize_query(query)  # lowercase, split, generate bigrams
    entities: List[Entity] = []
    seen_ids: set = set()

    # Strategy 1: Exact name/alias match
    for token in tokens:
        matches = self._store.find_entities_by_name(token)
        for e in matches:
            if e.id not in seen_ids:
                entities.append(e)
                seen_ids.add(e.id)

    if entities:
        return entities

    # Strategy 2: Fuzzy match (when configured)
    if self._graph_fuzzy_match:
        for token in tokens:
            if len(token) < 3:
                continue  # Skip very short tokens for fuzzy
            matches = self._store.find_entities_fuzzy(token, threshold=0.6)
            for e in matches:
                if e.id not in seen_ids:
                    entities.append(e)
                    seen_ids.add(e.id)

    return entities

def _tokenize_query(query: str) -> List[str]:
    """Tokenize query into unigrams and bigrams for entity matching."""
    words = query.lower().split()
    tokens = list(words)  # unigrams
    # Add bigrams: "auth service" -> "auth service"
    for i in range(len(words) - 1):
        tokens.append(f"{words[i]} {words[i+1]}")
    return tokens
```

### 7.4 Backward Compatibility

`graph_depth=0` (the default) produces **identical behavior** to v0.5.x:

```python
def recall(self, query, *, graph_depth=0, graph_weight=0.3, include_entities=False, ...):
    # Existing vector recall
    results = self._recall_local(query_vec, ...)

    # Fact-aware recall (existing)
    if use_facts and self._fact_extraction_enabled:
        results = self._merge_results(results, self._recall_by_facts(query))

    # Graph enhancement: only when graph_depth > 0 AND knowledge_graph enabled
    if graph_depth > 0 and self._knowledge_graph_enabled:
        results = self._enhance_with_graph(results, query, graph_depth, graph_weight, include_entities)

    return results
```

No new DB queries, no new computations, no performance impact when graph is off or `graph_depth=0`.

---

## 8. API Contract

### 8.1 Lore Facade -- New Methods

```python
class Lore:
    # Extended recall()
    def recall(
        self,
        query: str,
        *,
        # ... existing parameters unchanged ...
        graph_depth: int = 0,            # 0 = vector-only (default, backward compatible)
        graph_weight: float = 0.3,       # Weight of graph score in hybrid ranking
        include_entities: bool = False,  # Attach related entities to results
    ) -> List[RecallResult]: ...

    # Graph traversal
    def graph_query(
        self,
        query: str,
        depth: int = 2,
        entity_type: Optional[str] = None,
        relation_type: Optional[str] = None,
        include_expired: bool = False,
    ) -> GraphResult: ...

    # Related memories via graph
    def get_related_memories(
        self,
        query: str,
        depth: int = 2,
        limit: int = 10,
    ) -> List[RecallResult]: ...

    # Entity map (text, JSON, or D3)
    def get_entity_map(
        self,
        topic: str,
        depth: int = 2,
        entity_types: Optional[List[str]] = None,
        format: str = "text",   # "text", "json", "d3"
    ) -> Union[str, Dict]: ...

    # Entity CRUD
    def get_entity(self, name: str, entity_type: Optional[str] = None) -> Optional[Entity]: ...
    def list_entities(self, entity_type: Optional[str] = None, search: Optional[str] = None, limit: int = 50) -> List[Entity]: ...
    def merge_entities(self, source_id: str, target_id: str) -> Entity: ...

    # Relationship queries
    def list_relationships(self, entity_name: Optional[str] = None, relation_type: Optional[str] = None, include_expired: bool = False, limit: int = 50) -> List[Relationship]: ...

    # Backfill
    def graph_backfill(self, project: Optional[str] = None, limit: int = 100) -> int: ...
```

### 8.2 Store ABC Extensions

New methods on the `Store` base class, with **default no-op implementations** so existing stores don't break:

```python
class Store(ABC):
    # ... existing methods unchanged ...

    # ---- Entity CRUD (defaults: no-op / empty) ----
    def upsert_entity(self, name, entity_type, aliases=None, metadata=None) -> Entity:
        raise NotImplementedError("Graph not supported by this store")

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return None

    def find_entities_by_name(self, name, entity_type=None) -> List[Entity]:
        return []

    def find_entities_fuzzy(self, name, entity_type=None, threshold=0.6) -> List[Entity]:
        return []

    def list_entities(self, entity_type=None, limit=50, offset=0) -> List[Entity]:
        return []

    def merge_entities(self, source_id, target_id) -> Entity:
        raise NotImplementedError("Graph not supported by this store")

    def delete_entity(self, entity_id) -> bool:
        return False

    # ---- Relationship CRUD (defaults: no-op / empty) ----
    def upsert_relationship(self, source_entity_id, target_entity_id, relation_type, weight=1.0, valid_from=None, memory_id=None, fact_id=None, metadata=None) -> Relationship:
        raise NotImplementedError("Graph not supported by this store")

    def find_active_relationship(self, source_entity_id, target_entity_id, relation_type) -> Optional[Relationship]:
        return None

    def list_relationships(self, entity_id=None, relation_type=None, include_expired=False, limit=50) -> List[Relationship]:
        return []

    def close_relationship(self, relationship_id) -> None:
        pass

    def find_relationship_by_fact(self, fact_id) -> Optional[Relationship]:
        return None

    # ---- Entity-Memory junction (defaults: no-op / empty) ----
    def add_entity_mention(self, entity_id, memory_id) -> None:
        pass

    def get_entity_memories(self, entity_id, limit=50) -> List[str]:
        return []

    def get_memory_entities(self, memory_id) -> List[Entity]:
        return []

    # ---- Graph traversal (default: empty) ----
    def traverse_graph(self, start_entity_id, max_depth=2, relation_type=None, include_expired=False, direction="both") -> List[GraphNode]:
        return []

    # ---- Graph stats ----
    def graph_stats(self) -> Dict[str, Any]:
        return {"entities": 0, "relationships": 0, "mentions": 0}
```

### 8.3 `graph_depth` Parameter Semantics

| Value | Behavior |
|-------|----------|
| `0` | **No graph** -- pure vector recall. Identical to v0.5.x. Default. |
| `1` | Direct connections only. Entities mentioned in query -> their immediate neighbors' memories. |
| `2` | Two hops. Discovers entities connected through one intermediary. **Recommended default when graph is enabled.** |
| `3` | Three hops. Broader discovery, potentially noisier. |
| `4` | Max allowed. Four hops. Wide net. May include loosely related content. |

### 8.4 MCP Tools

Three new MCP tools:

```python
@mcp.tool(description="Traverse the knowledge graph to find entities and relationships connected to a query.")
def graph_query(query, depth=2, entity_type=None, relation_type=None, include_expired=False, project=None) -> str: ...

@mcp.tool(description="Find memories related through knowledge graph connections, not just semantic similarity.")
def related(query, depth=2, limit=10, project=None) -> str: ...

@mcp.tool(description="Get a visual entity map for a topic, project, or domain.")
def entity_map(topic, depth=2, entity_types=None, format="text", project=None) -> str: ...
```

### 8.5 CLI Commands

```bash
lore graph <query> [--depth N] [--type TYPE] [--relation TYPE] [--format text|json|d3]
lore entities [--type TYPE] [--search TEXT] [--sort mentions|name] [--limit N]
lore relationships [--entity NAME] [--type TYPE] [--include-expired] [--limit N]
lore graph-backfill [--project NAME] [--limit N]
```

---

## 9. Integration Points

### 9.1 Graph Update Pipeline Entry Point

The graph update runs as the **final step** in `remember()`, after the memory and facts are already saved:

```python
# In Lore.remember(), after fact extraction:
if self._knowledge_graph_enabled:
    try:
        self._update_graph(
            memory=memory,
            facts=extracted_facts,       # from F2 (may be empty if F2 disabled)
            enrichment=enrichment_data,   # from F6 (may be empty if F6 disabled)
        )
    except Exception:
        logger.warning("Graph update failed, memory saved without graph data", exc_info=True)
```

### 9.2 Cascade on forget()

When `forget(memory_id)` is called:

```python
def forget(self, memory_id: str) -> bool:
    if self._knowledge_graph_enabled:
        self._cascade_graph_on_forget(memory_id)
    return self._store.delete(memory_id)

def _cascade_graph_on_forget(self, memory_id: str) -> None:
    """Clean up graph data when a memory is deleted."""
    # 1. Get entities mentioned by this memory
    entities = self._store.get_memory_entities(memory_id)

    # 2. Remove entity_mentions for this memory (handled by CASCADE, but be explicit)
    # 3. For each affected entity: decrement mention_count
    for entity in entities:
        entity.mention_count -= 1
        if entity.mention_count <= 0:
            # Orphaned entity: no memories reference it anymore
            self._store.delete_entity(entity.id)  # CASCADE removes its relationships
        else:
            self._store.update_entity(entity)

    # 4. Relationships: ON DELETE SET NULL handles memory_id.
    # But check for relationships that were ONLY sourced from this memory:
    orphan_rels = self._store.list_relationships_by_memory(memory_id)
    for rel in orphan_rels:
        confirmed_by = (rel.metadata or {}).get("confirmed_by", [])
        if not confirmed_by or confirmed_by == [memory_id]:
            # Only source -- delete the relationship
            self._store.delete_relationship(rel.id)
```

### 9.3 Configuration

```python
class Lore:
    def __init__(
        self,
        # ... existing params ...
        knowledge_graph: bool = False,           # Enable graph layer
        graph_depth_default: int = 2,            # Default traversal depth
        graph_depth_max: int = 4,                # Maximum allowed depth
        graph_weight: float = 0.3,               # Graph score weight in hybrid recall
        graph_llm_extraction: bool = False,      # Extra LLM call for relationships
        graph_fuzzy_match: bool = True,          # Fuzzy entity matching in queries
        graph_confidence_threshold: float = 0.5, # Min fact confidence for graph edges
    ): ...
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_KNOWLEDGE_GRAPH` | `false` | Enable/disable knowledge graph |
| `LORE_GRAPH_DEPTH_DEFAULT` | `2` | Default traversal depth |
| `LORE_GRAPH_DEPTH_MAX` | `4` | Maximum traversal depth |
| `LORE_GRAPH_WEIGHT` | `0.3` | Graph score weight in hybrid recall |
| `LORE_GRAPH_LLM_EXTRACTION` | `false` | Enable extra LLM relationship extraction |
| `LORE_GRAPH_FUZZY_MATCH` | `true` | Fuzzy entity name matching |
| `LORE_GRAPH_CONFIDENCE_THRESHOLD` | `0.5` | Min fact confidence for creating graph edges |

### 9.4 Dependency Matrix

| Graph + | F2 on | F2 off |
|---------|-------|--------|
| **F6 on** | Full pipeline: F6 entities + F2 edges + optional LLM rels | F6 entities become nodes, LLM extraction (if enabled) is only edge source |
| **F6 off** | F2 subjects/objects become entities, predicates become edges | Graph exists but has no inputs. LLM extraction (if enabled) is the only source. Mostly useless. |

---

## 10. Performance Considerations

### 10.1 Index Strategy

**Hot path indexes** (used on every `traverse_graph()` call):

| Index | Table | Purpose |
|-------|-------|---------|
| `idx_relationships_source_active` | relationships | CTE base case: find outgoing active edges from entity |
| `idx_relationships_target_active` | relationships | CTE base case: find incoming active edges to entity |
| `idx_entities_name_type` (UNIQUE) | entities | Entity upsert dedup check |
| `idx_entity_mentions_memory` | entity_mentions | Memory -> entities lookup |

**Write path indexes** (used on `remember()` graph update):

| Index | Table | Purpose |
|-------|-------|---------|
| `idx_entities_name` | entities | Name lookup for alias resolution |
| `idx_entities_aliases` (GIN) | entities (Postgres) | Alias containment check |
| `idx_relationships_memory` | relationships | Find relationships from a memory (cascade on forget) |

### 10.2 Query Optimization

1. **Traversal always filters active edges** via partial index `WHERE valid_until IS NULL`. The planner only scans current relationships.
2. **DISTINCT ON (Postgres)** or result dedup (SQLite) ensures each entity appears once in results, at minimum depth.
3. **Result limit** (100 nodes) prevents the CTE from expanding unbounded on hub entities.
4. **Confidence threshold** (default 0.5) prevents low-confidence facts from creating graph noise.

### 10.3 Write Path Cost

Additional cost per `remember()` when graph is enabled:

| Operation | Cost |
|-----------|------|
| Entity upserts (from F6 + F2) | ~2-8 INSERT OR UPDATE per memory |
| Relationship upserts (from F2) | ~1-5 INSERT OR UPDATE per memory |
| Entity mention inserts | ~2-8 INSERT per memory |
| Total additional DB ops | ~5-20 ops, ~5-15ms |
| LLM extraction (if enabled) | 1 additional LLM call, ~200-500ms |

The non-LLM graph operations add negligible latency. The LLM extraction is opt-in.

### 10.4 Read Path Cost

Additional cost per `recall()` when `graph_depth > 0`:

| Operation | Cost |
|-----------|------|
| Query entity identification | 1-3 SELECT queries, ~1-3ms |
| Graph traversal per entity | 1 recursive CTE, ~5-20ms per entity (depth 2) |
| Entity-memory lookup | 1-5 SELECT queries, ~2-5ms |
| Result merge + scoring | In-memory, ~1ms |
| Total | ~10-30ms additional per recall |

At `graph_depth=0`, zero additional cost.

### 10.5 Scalability Targets

| Metric | Target |
|--------|--------|
| Entities | 10K+ |
| Relationships | 50K+ |
| Traversal (depth 2, 10K entities) | < 50ms |
| Traversal (depth 3, 10K entities) | < 200ms |
| Traversal (depth 4, 10K entities) | < 1000ms (acceptable) |
| `remember()` overhead (no LLM) | < 15ms |
| `recall()` overhead (graph_depth=2) | < 30ms |

---

## 11. Testing Strategy

### 11.1 Unit Tests

**Entity deduplication** (`tests/test_entity_dedup.py`):
- `normalize_entity_name("PostgreSQL 16")` -> `"postgresql"`
- `normalize_entity_name("pg")` -> `"postgresql"` (builtin alias)
- `normalize_entity_name("  React.js  ")` -> `"react"` (strip + alias)
- `upsert_entity("postgres", "tool")` then `upsert_entity("PostgreSQL", "tool")` -> same entity
- `upsert_entity("alice", "person")` then `upsert_entity("alice", "tool")` -> different entities (type differs)
- Alias lookup: entity with alias "k8s" found by name "k8s"

**Relationship CRUD** (`tests/test_knowledge_graph.py`):
- Create relationship, verify fields
- Upsert existing relationship increases weight by 0.1
- Weight capped at 1.0 after multiple upserts
- `find_active_relationship` returns only `valid_until IS NULL`
- `close_relationship` sets `valid_until`
- `find_relationship_by_fact` returns correct edge

**Entity-Memory junction**:
- `add_entity_mention` creates junction row
- `get_entity_memories` returns memory IDs for entity
- `get_memory_entities` returns entities for memory
- Duplicate `add_entity_mention` is idempotent (no error, no duplicate)

### 11.2 Graph Traversal Tests (`tests/test_graph_traversal.py`)

**Setup:** Build a known test graph:
```
A --uses--> B --depends_on--> C --deployed_on--> D
A --works_with--> E
E --manages--> F
F --uses--> B  (creates a cycle path)
```

**Tests:**
- Depth 1 from A: returns B, E (direct neighbors)
- Depth 2 from A: returns B, C, E, F (two hops)
- Depth 3 from A: returns B, C, D, E, F (three hops, no duplication)
- Cycle prevention: F->B does not re-traverse B->C->D
- Direction "out" from A: only outgoing edges
- Direction "in" to B: returns A, F (entities pointing to B)
- Direction "both" (default): union of in + out
- Expired edges excluded by default
- `include_expired=True` includes edges with `valid_until` set
- `relation_type="uses"` filters to only "uses" edges
- Empty graph: traversal returns empty list
- Single node, no edges: returns empty list
- Max depth clamped at 4

### 11.3 Integration Tests (`tests/test_graph_integration.py`)

**F2 -> Graph:**
- `remember()` with fact extraction enabled creates entities and relationships from facts
- Fact `("auth-service", "uses", "postgresql")` creates 2 entities + 1 relationship
- Fact confidence below `graph_confidence_threshold` does not create graph edge
- Superseded fact: old relationship gets `valid_until` set, new relationship created

**F6 -> Graph:**
- `remember()` with enrichment creates entities from enrichment metadata
- Enrichment entity `{"name": "Alice", "type": "person"}` creates graph entity
- Entity type from enrichment is used when F2 creates same entity

**Hybrid recall:**
- `recall("auth-service", graph_depth=2)` returns graph-connected memories not found by vector-only
- `recall(query, graph_depth=0)` identical to pre-graph behavior
- Memory in both vector and graph results gets boosted score
- Memory only in graph results has graph_score > 0, vector_score = 0
- `include_entities=True` populates `related_entities` on RecallResult

**Cascade:**
- `forget(memory_id)` decrements entity mention counts
- Entity with `mention_count=0` after forget is deleted
- Entity with `mention_count > 0` after forget survives
- Relationships sourced only from forgotten memory are deleted
- Relationships confirmed by multiple memories survive forget

### 11.4 End-to-End Scenario Test

```python
def test_knowledge_graph_full_scenario():
    """E2E: remember -> graph build -> recall with graph -> forget -> cascade."""
    lore = Lore(knowledge_graph=True, fact_extraction=True, enrichment=True, ...)

    # 1. Build knowledge through memories
    lore.remember("Alice works on the auth-service project using Python")
    lore.remember("The auth-service depends on PostgreSQL for data storage")
    lore.remember("Bob manages the deployment pipeline on AWS")

    # 2. Graph should now have: alice, auth-service, python, postgresql, bob, deployment-pipeline, aws
    entities = lore.list_entities()
    assert len(entities) >= 7

    # 3. Graph traversal from auth-service
    result = lore.graph_query("auth-service", depth=2)
    assert "postgresql" in [e.name for e in result.entities]
    assert "python" in [e.name for e in result.entities]
    assert "alice" in [e.name for e in result.entities]

    # 4. Hybrid recall finds graph-connected content
    results = lore.recall("database dependencies", graph_depth=2)
    # Should find PostgreSQL memory via graph even if vector score is low

    # 5. Forget and cascade
    mem_id = lore.remember("Temporary note about redis")
    lore.forget(mem_id)
    # Redis entity should be deleted if only mentioned in forgotten memory
```

### 11.5 Test Count Target

| Category | Estimated Tests |
|----------|----------------|
| Entity dedup / normalization | 12 |
| Entity CRUD | 8 |
| Relationship CRUD | 10 |
| Entity-Memory junction | 6 |
| Graph traversal (depth, cycle, direction) | 15 |
| F2 integration | 8 |
| F6 integration | 5 |
| Hybrid recall | 10 |
| Cascade / forget | 8 |
| Temporal edges | 5 |
| MCP tools | 6 |
| CLI commands | 5 |
| Configuration / backward compat | 4 |
| **Total** | **~102 tests** |

---

## 12. Backward Compatibility

### 12.1 Zero Breaking Changes Guarantee

| Concern | Guarantee |
|---------|-----------|
| `knowledge_graph=False` (default) | No graph tables created, no graph queries, no performance impact |
| `recall()` without graph params | Identical to v0.5.x behavior |
| `remember()` without graph | Identical pipeline, graph update step skipped |
| `forget()` without graph | Same as before, no cascade |
| Store ABC | New methods have default no-op implementations |
| `RecallResult` | New fields (`related_entities`, `graph_score`, `graph_path`) default to `None` |
| Existing tests | Must all pass without modification |
| SQLite databases | Existing DBs work; graph tables created lazily on first graph-enabled init |

### 12.2 Migration Strategy

**SQLite:** Graph tables created in `SqliteStore.__init__()` via `_maybe_create_graph_tables()`, only when `knowledge_graph=True` is configured. Existing databases get the tables added non-destructively.

**Postgres:** New migration `007_knowledge_graph.sql` creates `entities`, `relationships`, `entity_mentions` tables. Fully idempotent. No ALTER on existing tables.

---

## 13. File Changes Summary

| File | Change |
|------|--------|
| `src/lore/types.py` | Add `Entity`, `Relationship`, `EntityMention`, `GraphResult`, `GraphNode` dataclasses. Extend `RecallResult` with graph fields. |
| `src/lore/lore.py` | Add `knowledge_graph` config + init. Add `graph_query()`, `get_related_memories()`, `get_entity_map()`, `list_entities()`, `list_relationships()`, `merge_entities()`, `graph_backfill()`. Extend `remember()` with graph update step. Extend `recall()` with `graph_depth`, `graph_weight`, `include_entities`. Extend `forget()` with graph cascade. |
| `src/lore/store/base.py` | Add graph abstract methods with default implementations. |
| `src/lore/store/sqlite.py` | Add graph table schema. Implement all graph store methods including recursive CTE traversal. |
| `src/lore/store/memory.py` | In-memory graph implementation (dicts for entities, relationships, mentions). |
| `src/lore/store/http.py` | Stub methods (raise `NotImplementedError`). |
| `src/lore/graph/__init__.py` | **NEW** module init. |
| `src/lore/graph/dedup.py` | **NEW** -- `normalize_entity_name()`, alias resolution, entity merge logic. |
| `src/lore/graph/extraction.py` | **NEW** -- `_update_graph()`, `_infer_entity_type()`, `_on_fact_superseded()`, LLM relationship extraction prompt. |
| `src/lore/graph/traversal.py` | **NEW** -- `traverse_graph()` wrapper, `_identify_query_entities()`, `_tokenize_query()`. |
| `src/lore/graph/scoring.py` | **NEW** -- `compute_graph_score()`, `merge_vector_and_graph()`, hybrid scoring logic. |
| `src/lore/graph/visualization.py` | **NEW** -- D3 JSON output, text tree output for `entity_map`. |
| `src/lore/mcp/server.py` | Add `graph_query`, `related`, `entity_map` tools. |
| `src/lore/cli.py` | Add `graph`, `entities`, `relationships`, `graph-backfill` subcommands. |
| `src/lore/server/routes/` | Add graph endpoints (P2 -- deferred). |
| `migrations/007_knowledge_graph.sql` | **NEW** -- Postgres schema for graph tables. |
| `tests/test_entity_dedup.py` | **NEW** |
| `tests/test_knowledge_graph.py` | **NEW** |
| `tests/test_graph_traversal.py` | **NEW** |
| `tests/test_graph_integration.py` | **NEW** |

---

## 14. Story Sequencing

| Story | Description | Depends | Est. Size |
|-------|-------------|---------|-----------|
| **S1** | Data model + schema (types, SQLite tables, Postgres migration) | -- | M |
| **S2** | Entity normalization + dedup module (`graph/dedup.py`) | S1 | S |
| **S3** | Entity CRUD in SqliteStore + MemoryStore | S1, S2 | M |
| **S4** | Relationship CRUD in SqliteStore + MemoryStore | S1, S3 | M |
| **S5** | Entity-Memory junction + entity mention tracking | S3, S4 | S |
| **S6** | Graph traversal (recursive CTEs, cycle prevention) | S3, S4 | L |
| **S7** | Graph update pipeline (`remember()` -> graph) | S3, S4, S5 | M |
| **S8** | F2 integration (facts -> edges, supersession -> temporal close) | S4, S7 | M |
| **S9** | F6 integration (enrichment entities -> graph nodes) | S3, S7 | S |
| **S10** | Hybrid recall (vector + graph scoring, query entity ID) | S6 | L |
| **S11** | Cascade behavior (`forget()` cleanup) | S5, S7 | M |
| **S12** | MCP tools (`graph_query`, `related`, `entity_map`) | S6, S10 | M |
| **S13** | CLI commands | S3, S4, S6 | M |
| **S14** | Visualization (D3 JSON, text tree) | S6 | S |
| **S15** | Graph backfill | S7 | S |
| **S16** | Store ABC + HttpStore stubs | S3, S4 | S |

**Critical path:** S1 -> S2 -> S3 -> S4 -> S6 -> S10 (data model through hybrid recall)

**Parallelizable after S4:** S5, S6, S7 can run concurrently. S8+S9 after S7. S10 after S6. S12+S13+S14 after S6+S10.

---

## 15. Opinionated Decisions

These are deliberate architectural choices that differ from or refine the PRD:

1. **Max depth 4, not 3.** The PRD says cap at 3. We cap at 4 because depth 4 is useful for cross-domain discovery and the partial indexes keep it performant. Default remains 2.

2. **Confidence threshold for graph edges.** Not in the PRD, but critical: facts with confidence < 0.5 should not create graph edges. Low-confidence facts create noise. Configurable via `graph_confidence_threshold`.

3. **No `org_id` on graph tables.** The PRD doesn't mention it, and we shouldn't add it. Entities are scoped through their memory mentions. Adding `org_id` to entities would complicate cross-project entity sharing (same "PostgreSQL" entity across projects). If org isolation is needed, filter via `entity_mentions JOIN memories WHERE org_id = ?`.

4. **Additive hybrid scoring, not multiplicative.** The scoring formula `(1-w)*vector + w*graph` ensures pure-graph discoveries can surface. Multiplicative would zero them out. This is the only sane approach for hybrid retrieval.

5. **Bidirectional traversal by default.** The PRD shows unidirectional traversal with a note about bidirectional. We default to bidirectional because graph discovery should be symmetric: if "auth-service uses PostgreSQL", querying from either side should find the other.

6. **Graph tables created lazily.** Only when `knowledge_graph=True`. A user who never enables the graph never sees the tables. This prevents schema clutter and keeps the SQLite file small.

7. **`graph/` module structure.** Five focused files instead of one monolith: `dedup.py`, `extraction.py`, `traversal.py`, `scoring.py`, `visualization.py`. Each is independently testable and has a single responsibility.

8. **Entity merge is destructive and intentional.** `merge_entities()` permanently consolidates two entities. There's no undo. This is correct -- entity dedup should be a deliberate decision, not something that auto-reverses.

9. **SQLite path uses string-based cycle prevention.** `INSTR(path_str, entity_id)` instead of Postgres `ANY(path)`. This is slightly less precise (substring matching on ULIDs, but ULID uniqueness makes false positives essentially impossible) and avoids the need for array operations that SQLite doesn't support.

10. **Relationship weight starts at fact confidence, not 1.0.** When a relationship is created from an F2 fact, its initial weight equals the fact's confidence score. This ensures high-confidence facts create stronger edges from the start.
