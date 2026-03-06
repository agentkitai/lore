# PRD: F1 — Knowledge Graph Layer

**Feature:** F1 — Knowledge Graph Layer
**Version:** v0.6.0 ("Open Brain")
**Status:** Draft
**Author:** John (PM)
**Date:** 2026-03-06
**Phase:** 3 — Graph Layer
**Depends on:** F2 (Fact Extraction — SPO triples become graph edges), F6 (Metadata Enrichment — extracted entities feed into graph)
**Dependents:** F3 (Memory Consolidation — uses graph for intelligent grouping), F7 (Webhook Ingestion — graph auto-updated on ingest)

---

## 1. Problem Statement

Lore's recall is purely vector-based — it finds memories that are semantically similar to a query. This works well for direct similarity ("What did I say about Kubernetes?") but fails for relational queries ("What tools does the auth-service depend on?", "Show me everything connected to the deployment pipeline", "What does Alice work on?").

Competitive platforms have recognized this gap:
- **Mem0** added a knowledge graph layer in 2024, but requires Neo4j — a separate graph database that complicates deployment and raises infrastructure costs.
- **Zep** implements bi-temporal knowledge graphs with entity/relationship tracking, but is a closed SaaS product.
- **Cognee** uses graph-based memory with LLM-powered entity extraction, but requires separate graph infrastructure.
- **Memary** uses Neo4j for knowledge graph storage, coupling memory to a second database.

Every competitor requires either Neo4j, a proprietary backend, or a separate graph database. Lore's opportunity is to deliver the same knowledge graph capability using **pure Postgres** — no additional database, no additional infrastructure, just the same single database that already stores memories and embeddings.

### The Gap

F2 (Fact Extraction) already extracts `(subject, predicate, object)` triples from memories. F6 (Metadata Enrichment) already extracts named entities (people, tools, projects, platforms). But these are disconnected:

- Facts are flat triples in a `facts` table — no traversal, no multi-hop queries.
- Enrichment entities are embedded in `metadata["enrichment"]["entities"]` JSON — not queryable as first-class objects.
- There's no way to ask "What's 2 hops away from PostgreSQL?" or "Show me the entity graph for the auth-service project."

The Knowledge Graph Layer bridges this gap by promoting facts and entities into a proper graph structure with entities as nodes, relationships as edges, and recursive traversal queries for multi-hop discovery.

## 2. Goals

1. **Entity extraction on `remember()`** — Automatically extract entities (people, tools, projects, concepts, organizations) from memory content. Leverage F6 enrichment entities and F2 fact subjects/objects as primary inputs.
2. **Relationship extraction** — Extract typed relationships between entities (uses, depends_on, prefers, works_with, created_by, etc.) from memory content and F2 fact triples.
3. **Graph storage in Postgres** — `entities` and `relationships` tables using adjacency model. No Neo4j, no Apache AGE, no external dependencies. Pure SQL with recursive CTEs for traversal.
4. **Temporal edges** — Relationships carry `valid_from`/`valid_until` timestamps for bi-temporal tracking. Know not just what's related, but when the relationship was valid.
5. **Hybrid retrieval** — Combine existing vector similarity search with graph traversal for richer, more contextual recall results.
6. **Graph-enhanced recall** — `recall('query', graph_depth=2)` returns matching memories + related entities + connected memories up to N hops.
7. **Entity deduplication** — Normalize entity names (case-insensitive, alias tracking) to prevent "PostgreSQL", "postgres", and "pg" from being separate entities.
8. **New MCP tools** — `graph_query`, `related`, `entity_map` for graph exploration.
9. **CLI commands** — `lore graph`, `lore entities`, `lore relationships` for command-line graph access.
10. **Visualization endpoint** — D3-compatible JSON output for graph visualization.
11. **F2 integration** — Facts `(subject, predicate, object)` automatically become graph entities and edges. No duplication — the graph layer reads from and builds upon F2.
12. **F6 integration** — Enrichment entities from `metadata["enrichment"]["entities"]` are automatically promoted to the `entities` table.

## 3. Non-Goals

- **Apache AGE integration** — While AGE adds Cypher support to Postgres, it's an extension that complicates deployment (requires compiling against specific Postgres versions). Pure adjacency tables + recursive CTEs provide sufficient traversal for our use case (2-3 hops). AGE can be revisited post-v0.6.0 if query complexity demands it.
- **Separate graph database** — No Neo4j, Memgraph, or any external graph DB. Single-DB simplicity is our competitive edge.
- **Visual graph editor** — We provide JSON output for visualization; building a UI is out of scope.
- **Ontology enforcement** — No rigid schema for entity types or relationship types. Types are free-text with recommended conventions, not enforced enums.
- **Graph-based reasoning / inference** — No automatic inference of transitive relationships. "A uses B, B depends_on C" does not automatically produce "A indirectly depends_on C." Traversal exposes the path; inference is left to the consuming agent.
- **Real-time graph updates from external sources** — Graph is updated on `remember()` and backfill. External push-based graph updates are not in scope (F7 webhook ingestion will feed through `remember()`).
- **Embedding entities** — Entities are not independently embedded. They're discoverable via their linked memories' embeddings and via name/type queries.

## 4. Architecture Decision: Pure Postgres vs. Apache AGE

### Option 1: Pure Postgres (Adjacency Tables + Recursive CTEs) — SELECTED

**Pros:**
- Zero additional dependencies — works with any Postgres 12+ (or even SQLite with recursive CTE support)
- Standard SQL — any developer can read and maintain queries
- Deployable with `docker compose up` as-is — no extension compilation
- Sufficient for 2-3 hop traversals with well-indexed tables
- Works with existing pgvector setup without conflicts

**Cons:**
- Verbose multi-hop queries (recursive CTEs are more complex than Cypher)
- Performance degrades at 4+ hops on large graphs (acceptable — we cap at 3)
- No built-in shortest-path or pattern matching (would need custom SQL)

### Option 2: Apache AGE (Postgres Extension)

**Pros:**
- Cypher query language for elegant graph traversal
- Optimized graph storage and traversal
- Still Postgres-based (single DB)

**Cons:**
- Requires compiling extension for specific Postgres version — breaks `docker compose up` simplicity
- Not available on many managed Postgres services (Aurora, Cloud SQL, Neon)
- Adds compile-time dependency and maintenance burden
- Relatively immature ecosystem (fewer users than pure Postgres patterns)

### Decision: Pure Postgres

The adjacency table approach with recursive CTEs is sufficient for our needs (2-3 hop traversal, entity lookup, relationship queries). It preserves deployment simplicity and works everywhere Postgres runs. If query complexity grows beyond what recursive CTEs handle elegantly, AGE can be added as an optional extension in a future version.

## 5. Design

### 5.1 Data Model — Entities

```python
@dataclass
class Entity:
    """A knowledge graph entity (node).

    Represents a person, tool, project, concept, organization, or other
    named entity extracted from memories.
    """

    id: str                              # UUID
    name: str                            # Canonical name (lowercase, normalized)
    entity_type: str                     # person, tool, concept, project, organization, platform, language, framework
    aliases: List[str] = field(default_factory=list)  # Alternative names (e.g., ["pg", "PostgreSQL"])
    metadata: Optional[Dict[str, Any]] = None  # Extra structured data
    first_seen_at: str = ""              # ISO timestamp — first memory that mentioned this entity
    last_seen_at: str = ""               # ISO timestamp — most recent mention
    mention_count: int = 1               # How many memories reference this entity
```

**Design rationale:**
- `name` is the canonical, normalized form (lowercase, trimmed). All lookups use this.
- `aliases` tracks alternative names that resolve to this entity. "PostgreSQL", "postgres", "pg" → canonical "postgresql" with aliases `["PostgreSQL", "postgres", "pg"]`.
- `entity_type` uses the same type vocabulary as F6 enrichment (`person`, `tool`, `project`, `platform`, `organization`, `concept`, `language`, `framework`) for seamless integration.
- `mention_count` enables lightweight importance ranking without hitting the relationships table.
- `first_seen_at` / `last_seen_at` support temporal queries ("When did we first mention Kubernetes?").

### 5.2 Data Model — Relationships

```python
@dataclass
class Relationship:
    """A knowledge graph relationship (edge).

    Represents a typed, weighted, temporal connection between two entities,
    derived from a specific memory.
    """

    id: str                              # UUID
    source_entity_id: str                # FK → entities.id
    target_entity_id: str                # FK → entities.id
    relation_type: str                   # uses, depends_on, prefers, works_with, created_by, etc.
    weight: float = 1.0                  # Relationship strength (0.0-1.0)
    valid_from: Optional[str] = None     # ISO timestamp — when relationship became valid
    valid_until: Optional[str] = None    # ISO timestamp — when relationship ended (None = still valid)
    memory_id: Optional[str] = None      # FK → memories.id — source memory for provenance
    fact_id: Optional[str] = None        # FK → facts.id — source fact (if derived from F2)
    metadata: Optional[Dict[str, Any]] = None  # Extra structured data
    created_at: str = ""                 # ISO timestamp
```

**Design rationale:**
- `source_entity_id` → `target_entity_id` gives directed edges: "auth-service" → uses → "PostgreSQL".
- `valid_from` / `valid_until` enable Zep-style bi-temporal tracking. A relationship with `valid_until = NULL` is currently active. A relationship with `valid_until` set is historical.
- `memory_id` provides provenance — every edge traces back to the memory that created it.
- `fact_id` links to F2 facts — when a fact triple `(subject, predicate, object)` becomes a graph edge, `fact_id` tracks the source fact.
- `weight` enables scored traversal — higher-weight relationships are more significant. Weight increases when multiple memories confirm the same relationship.
- `relation_type` is free-text with conventions (see Section 5.7) rather than an enum, allowing organic growth.

### 5.3 Data Model — Entity-Memory Junction

```python
@dataclass
class EntityMention:
    """Links an entity to a memory that mentions it."""

    entity_id: str                       # FK → entities.id
    memory_id: str                       # FK → memories.id
    mentioned_at: str = ""               # ISO timestamp
```

This junction table enables:
- "Which memories mention entity X?" (graph → memories)
- "Which entities appear in memory Y?" (memory → graph)
- Cascade: when a memory is deleted, entity mention counts can be decremented.

### 5.4 Schema — Postgres

```sql
-- Entities: knowledge graph nodes
CREATE TABLE IF NOT EXISTS entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,              -- canonical lowercase name
    entity_type     TEXT NOT NULL DEFAULT 'concept',
    aliases         JSONB DEFAULT '[]'::jsonb,  -- alternative names
    metadata        JSONB,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mention_count   INT NOT NULL DEFAULT 1
);

-- Unique constraint: one entity per (name, entity_type) pair
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_type
    ON entities(name, entity_type);

-- Fast lookup by name (case-insensitive via normalized name)
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

-- Lookup by type
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

-- Relationships: knowledge graph edges
CREATE TABLE IF NOT EXISTS relationships (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_entity_id    UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id    UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type       TEXT NOT NULL,
    weight              REAL NOT NULL DEFAULT 1.0,
    valid_from          TIMESTAMPTZ,
    valid_until         TIMESTAMPTZ,                -- NULL = currently active
    memory_id           UUID REFERENCES memories(id) ON DELETE SET NULL,
    fact_id             UUID REFERENCES facts(id) ON DELETE SET NULL,
    metadata            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Traversal: find all relationships FROM an entity
CREATE INDEX IF NOT EXISTS idx_relationships_source
    ON relationships(source_entity_id);

-- Traversal: find all relationships TO an entity
CREATE INDEX IF NOT EXISTS idx_relationships_target
    ON relationships(target_entity_id);

-- Filter by relationship type
CREATE INDEX IF NOT EXISTS idx_relationships_type
    ON relationships(relation_type);

-- Temporal queries: find active relationships
CREATE INDEX IF NOT EXISTS idx_relationships_active
    ON relationships(valid_until) WHERE valid_until IS NULL;

-- Provenance: find relationships from a memory
CREATE INDEX IF NOT EXISTS idx_relationships_memory
    ON relationships(memory_id);

-- Compound: common traversal pattern (source + active)
CREATE INDEX IF NOT EXISTS idx_relationships_source_active
    ON relationships(source_entity_id, valid_until) WHERE valid_until IS NULL;

-- Entity-Memory junction: which memories mention which entities
CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    memory_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    mentioned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (entity_id, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_mentions_memory
    ON entity_mentions(memory_id);
```

### 5.5 Schema — SQLite

```sql
-- Entities
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    entity_type     TEXT NOT NULL DEFAULT 'concept',
    aliases         TEXT DEFAULT '[]',       -- JSON array
    metadata        TEXT,                    -- JSON
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    mention_count   INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_type
    ON entities(name, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

-- Relationships
CREATE TABLE IF NOT EXISTS relationships (
    id                  TEXT PRIMARY KEY,
    source_entity_id    TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id    TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type       TEXT NOT NULL,
    weight              REAL NOT NULL DEFAULT 1.0,
    valid_from          TEXT,
    valid_until         TEXT,
    memory_id           TEXT REFERENCES memories(id) ON DELETE SET NULL,
    fact_id             TEXT REFERENCES facts(id) ON DELETE SET NULL,
    metadata            TEXT,                -- JSON
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_relationships_type ON relationships(relation_type);
CREATE INDEX IF NOT EXISTS idx_relationships_memory ON relationships(memory_id);

-- Entity-Memory junction
CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    memory_id   TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    mentioned_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_mentions_memory ON entity_mentions(memory_id);
```

### 5.6 F2 Integration — Facts Become Graph Edges

This is the **most critical integration point**. F2 already extracts `(subject, predicate, object)` triples from memories. The graph layer automatically converts these into entities and relationships:

```
F2 Fact: ("auth-service", "uses", "PostgreSQL 16")
    ↓
Graph:  Entity("auth-service", type=project)
        Entity("postgresql 16", type=tool)
        Relationship(auth-service → uses → postgresql 16, fact_id=<fact_id>)
```

**Pipeline flow on `remember()`:**

```
remember(content)
    │
    ▼
┌─────────────────────┐
│ F6: Enrichment      │  → extracts entities: [{name: "Alice", type: "person"}, ...]
│ F9: Classification  │  → classifies intent/domain/emotion
│ F2: Fact Extraction │  → extracts facts: [(auth-service, uses, PostgreSQL), ...]
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ F1: Graph Update    │  ← NEW: runs AFTER enrichment pipeline
│                     │
│ 1. Promote F6       │  → enrichment entities → entities table
│    entities to graph│
│ 2. Convert F2 facts │  → fact triples → entities + relationships
│    to graph edges   │
│ 3. Extract additional│  → LLM extracts relationships not captured by F2
│    relationships    │
│ 4. Deduplicate      │  → merge duplicate entities via alias resolution
│    entities         │
└─────────┬───────────┘
          │
          ▼
    Store memory + facts + entities + relationships
```

**Key principle: No duplication.** The graph layer does not re-extract what F2 and F6 already provide. It promotes and connects their outputs:

| Source | Produces | Graph Action |
|--------|----------|-------------|
| F6 enrichment entities | `{name, type}` dicts in metadata | Upsert into `entities` table, create `entity_mentions` |
| F2 fact subjects | Subject strings | Upsert as entities (infer type from enrichment if available) |
| F2 fact objects | Object strings | Upsert as entities (infer type if possible, default to `concept`) |
| F2 fact triples | `(subject, predicate, object)` | Create relationship edge with `fact_id` reference |
| F1 relationship extraction | Additional relationships from LLM | Create relationship edges with `memory_id` reference |

### 5.7 Relationship Type Conventions

Standard relationship types (recommended, not enforced):

| Category | Types |
|----------|-------|
| **Technical** | `uses`, `depends_on`, `integrates_with`, `deployed_on`, `written_in`, `extends`, `replaces` |
| **Organizational** | `works_on`, `manages`, `created_by`, `owned_by`, `belongs_to`, `reports_to` |
| **Preference** | `prefers`, `recommends`, `avoids`, `chose_over` |
| **Knowledge** | `knows_about`, `learned`, `teaches`, `documented_in` |
| **Temporal** | `migrated_from`, `upgraded_to`, `replaced_by`, `preceded_by` |

Predicates from F2 facts are used directly as relationship types. The LLM extraction prompt includes these conventions to encourage consistency.

### 5.8 Entity Deduplication + Alias Resolution

Entity deduplication is critical for graph quality. Without it, "PostgreSQL", "postgres", "Postgres", and "pg" would be 4 separate nodes with disjoint edges.

**Strategy: Normalize-on-write with alias tracking.**

1. **Canonical name normalization:**
   - Lowercase
   - Strip leading/trailing whitespace
   - Collapse multiple spaces to single space
   - Remove trailing version numbers for alias matching (but keep in aliases): "PostgreSQL 16" → canonical "postgresql", alias "postgresql 16"

2. **Alias resolution on entity upsert:**
   ```python
   def upsert_entity(name: str, entity_type: str) -> Entity:
       canonical = normalize_entity_name(name)
       existing = lookup_entity(canonical, entity_type)  # check entities table
       if existing:
           # Add original name as alias if not already present
           if name not in existing.aliases:
               existing.aliases.append(name)
           existing.mention_count += 1
           existing.last_seen_at = now()
           return existing
       else:
           # Check aliases of existing entities
           alias_match = lookup_by_alias(name, entity_type)
           if alias_match:
               alias_match.aliases.append(name)
               alias_match.mention_count += 1
               alias_match.last_seen_at = now()
               return alias_match
           else:
               return create_entity(canonical, entity_type, aliases=[name])
   ```

3. **LLM-assisted deduplication (optional, on backfill):**
   - After bulk entity creation, run a deduplication pass that asks the LLM to identify likely duplicates: "Are 'react' (framework) and 'React.js' (framework) the same entity?"
   - Merge confirmed duplicates, redirect relationships.

### 5.9 Graph Traversal — Recursive CTEs

Multi-hop traversal uses Postgres recursive CTEs with configurable depth limits.

**Core traversal query (Postgres):**

```sql
-- Find all entities within N hops of a starting entity
WITH RECURSIVE graph_walk AS (
    -- Base case: starting entity's direct relationships
    SELECT
        r.target_entity_id AS entity_id,
        r.relation_type,
        r.weight,
        1 AS depth,
        ARRAY[r.source_entity_id, r.target_entity_id] AS path
    FROM relationships r
    WHERE r.source_entity_id = :start_entity_id
      AND r.valid_until IS NULL  -- only active relationships

    UNION ALL

    -- Recursive case: follow edges from discovered entities
    SELECT
        r.target_entity_id,
        r.relation_type,
        r.weight,
        gw.depth + 1,
        gw.path || r.target_entity_id
    FROM relationships r
    JOIN graph_walk gw ON r.source_entity_id = gw.entity_id
    WHERE gw.depth < :max_depth
      AND r.valid_until IS NULL
      AND NOT r.target_entity_id = ANY(gw.path)  -- prevent cycles
)
SELECT DISTINCT ON (gw.entity_id)
    e.id, e.name, e.entity_type, e.mention_count,
    gw.relation_type, gw.weight, gw.depth, gw.path
FROM graph_walk gw
JOIN entities e ON e.id = gw.entity_id
ORDER BY gw.entity_id, gw.depth ASC;
```

**Bidirectional traversal:**

The query above follows outgoing edges. For full graph exploration, we also follow incoming edges:

```sql
-- Add UNION in the base case:
UNION ALL
SELECT
    r.source_entity_id AS entity_id,
    r.relation_type,
    r.weight,
    1 AS depth,
    ARRAY[:start_entity_id, r.source_entity_id] AS path
FROM relationships r
WHERE r.target_entity_id = :start_entity_id
  AND r.valid_until IS NULL
```

**Performance constraints:**
- `max_depth` defaults to 2, capped at 3. Depth 3 on a well-indexed table with thousands of entities should complete in <50ms.
- Path tracking (`path` array) prevents infinite cycles.
- `DISTINCT ON` prevents duplicate entity results from different paths.

### 5.10 Hybrid Retrieval — Vector + Graph

This is the core innovation. When `graph_depth > 0`, recall combines vector similarity search with graph traversal:

```python
def recall(
    self,
    query: str,
    *,
    limit: int = 10,
    graph_depth: int = 0,           # 0 = vector only (backward compatible)
    graph_weight: float = 0.3,      # weight of graph score in final ranking
    include_entities: bool = False,  # include related entities in results
    ...
) -> List[RecallResult]:
    # Step 1: Vector similarity search (existing)
    vector_results = self._recall_vector(query, limit=limit * 2)

    if graph_depth == 0:
        return vector_results[:limit]

    # Step 2: Extract entities from query (lightweight)
    query_entities = self._identify_query_entities(query)

    # Step 3: Graph traversal from query entities
    graph_memories = set()
    related_entities = []
    for entity in query_entities:
        traversal = self._traverse_graph(entity.id, depth=graph_depth)
        for node in traversal:
            related_entities.append(node)
            # Get memories linked to each discovered entity
            linked_memories = self._get_entity_memories(node.entity_id)
            graph_memories.update(linked_memories)

    # Step 4: Score and merge
    # Vector results have similarity scores [0, 1]
    # Graph results get a graph score based on hop distance and relationship weight
    merged = self._merge_vector_and_graph(
        vector_results=vector_results,
        graph_memory_ids=graph_memories,
        graph_weight=graph_weight,
        limit=limit,
    )

    # Step 5: Attach entity context if requested
    if include_entities:
        for result in merged:
            result.related_entities = related_entities

    return merged
```

**Scoring formula for hybrid results:**

```
final_score = (1 - graph_weight) * vector_score + graph_weight * graph_score

where:
  vector_score = cosine similarity (0.0 - 1.0), existing
  graph_score  = 1.0 / (1.0 + hop_distance) * relationship_weight
                 (closer entities score higher, stronger relationships score higher)
```

A memory that appears in BOTH vector results and graph results gets boosted by both scores. A memory that only appears via graph traversal gets a zero vector_score but may still rank if graph_score is high enough.

### 5.11 Query Entity Identification

For graph-enhanced recall to work, we need to identify which entities the query refers to. Three strategies, from cheapest to most expensive:

1. **Exact match (default):** Tokenize the query, look up each token (and bigrams) against the `entities.name` and `entities.aliases` fields. Fast, no LLM call.

2. **Fuzzy match:** Use trigram similarity or Levenshtein distance for approximate entity matching. Handles typos and abbreviations.

3. **LLM-assisted (optional):** For complex queries like "What tools does our backend rely on?", ask the LLM to identify entity references. Only used when `graph_depth > 0` and no exact matches found.

```python
def _identify_query_entities(self, query: str) -> List[Entity]:
    """Identify entities referenced in a recall query."""
    # Strategy 1: Exact token match against entity names/aliases
    tokens = tokenize(query)  # split, lowercase, generate bigrams
    entities = []
    for token in tokens:
        matches = self._store.find_entities_by_name(token)
        entities.extend(matches)

    if entities:
        return entities

    # Strategy 2: Fuzzy match (if configured)
    if self._graph_fuzzy_match:
        for token in tokens:
            matches = self._store.find_entities_fuzzy(token, threshold=0.6)
            entities.extend(matches)

    return entities
```

### 5.12 Graph Update on `remember()`

When a new memory is stored, the graph is updated in a single transaction:

```python
def _update_graph(self, memory: Memory, facts: List[Fact], enrichment: Dict) -> None:
    """Update knowledge graph from a newly stored memory."""

    entities_in_memory = []

    # 1. Promote F6 enrichment entities
    for ent in enrichment.get("entities", []):
        entity = self._store.upsert_entity(
            name=ent["name"],
            entity_type=ent["type"],
        )
        self._store.add_entity_mention(entity.id, memory.id)
        entities_in_memory.append(entity)

    # 2. Convert F2 facts to graph edges
    for fact in facts:
        if fact.invalidated_by:
            continue  # skip invalidated facts

        source = self._store.upsert_entity(
            name=fact.subject,
            entity_type=self._infer_entity_type(fact.subject, enrichment),
        )
        target = self._store.upsert_entity(
            name=fact.object,
            entity_type=self._infer_entity_type(fact.object, enrichment),
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
        # Add entity mentions
        self._store.add_entity_mention(source.id, memory.id)
        self._store.add_entity_mention(target.id, memory.id)
        entities_in_memory.append(source)
        entities_in_memory.append(target)

    # 3. Extract additional relationships via LLM (optional)
    if self._graph_llm_extraction and self._enrichment_pipeline:
        additional_rels = self._extract_relationships_llm(
            memory.content, entities_in_memory
        )
        for rel in additional_rels:
            self._store.upsert_relationship(**rel)
```

### 5.13 Relationship Upsert Logic

When a relationship between two entities with the same `relation_type` already exists:

```python
def upsert_relationship(
    self,
    source_entity_id: str,
    target_entity_id: str,
    relation_type: str,
    weight: float = 1.0,
    memory_id: Optional[str] = None,
    fact_id: Optional[str] = None,
    valid_from: Optional[str] = None,
) -> Relationship:
    """Create or strengthen a relationship edge."""
    existing = self._find_active_relationship(
        source_entity_id, target_entity_id, relation_type
    )
    if existing:
        # Strengthen existing relationship
        existing.weight = min(1.0, existing.weight + 0.1)  # diminishing returns
        existing.metadata = existing.metadata or {}
        existing.metadata.setdefault("confirmed_by", []).append(memory_id)
        return existing
    else:
        # Create new relationship
        return self._create_relationship(
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            relation_type=relation_type,
            weight=weight,
            memory_id=memory_id,
            fact_id=fact_id,
            valid_from=valid_from,
        )
```

Multiple memories confirming the same relationship increase its `weight`, making it rank higher in traversal results.

### 5.14 Temporal Edge Management

When a fact is superseded (F2 conflict resolution = SUPERSEDE), the corresponding graph edge is temporally closed:

```python
def _on_fact_superseded(self, old_fact: Fact, new_fact: Fact) -> None:
    """Update graph when a fact is superseded."""
    # Close the old relationship's temporal window
    old_rel = self._store.find_relationship_by_fact(old_fact.id)
    if old_rel:
        old_rel.valid_until = now()
        self._store.update_relationship(old_rel)

    # Create new relationship from the new fact
    source = self._store.upsert_entity(name=new_fact.subject, ...)
    target = self._store.upsert_entity(name=new_fact.object, ...)
    self._store.upsert_relationship(
        source_entity_id=source.id,
        target_entity_id=target.id,
        relation_type=new_fact.predicate,
        valid_from=now(),
        fact_id=new_fact.id,
    )
```

**Example temporal timeline:**

```
2026-01: remember("We use MySQL for the auth service")
  → Relationship: auth-service --uses--> mysql (valid_from: Jan, valid_until: NULL)

2026-03: remember("We migrated the auth service from MySQL to PostgreSQL")
  → F2 detects SUPERSEDE on (auth-service, uses, mysql)
  → Relationship: auth-service --uses--> mysql (valid_from: Jan, valid_until: Mar)
  → Relationship: auth-service --uses--> postgresql (valid_from: Mar, valid_until: NULL)
  → Relationship: auth-service --migrated_from--> mysql (valid_from: Mar)
```

Queries default to `valid_until IS NULL` (current state). Historical queries can include expired edges.

### 5.15 LLM Relationship Extraction Prompt

For cases where F2 facts don't capture all relationships (e.g., implicit connections between entities), an optional LLM extraction step identifies additional relationships:

```
Extract relationships between the following entities found in this memory.

MEMORY CONTENT:
{content}

ENTITIES FOUND:
{entities_json}

For each relationship:
1. Source entity (must be from the list above)
2. Target entity (must be from the list above)
3. Relationship type (use: uses, depends_on, works_with, created_by, prefers, etc.)
4. Confidence (0.0-1.0)
5. Is this relationship currently valid? (valid_from: now, valid_until: null if current)

Return JSON only:
{
  "relationships": [
    {
      "source": "entity_name",
      "target": "entity_name",
      "relation_type": "uses",
      "confidence": 0.9,
      "valid": true
    }
  ]
}
```

This step is OPTIONAL and controlled by `graph_llm_extraction=True` in config. When disabled, the graph is built entirely from F2 facts + F6 entities (no additional LLM call).

### 5.16 Configuration

```python
lore = Lore(
    # Existing config...
    enrichment=True,                    # F6: enables entity extraction
    fact_extraction=True,               # F2: enables fact triples

    # NEW: Graph layer config
    knowledge_graph=True,               # Enable graph (default: False)
    graph_depth_default=2,              # Default traversal depth for recall
    graph_depth_max=3,                  # Maximum allowed depth
    graph_weight=0.3,                   # Weight of graph score in hybrid recall
    graph_llm_extraction=False,         # Extra LLM call for relationship extraction
    graph_fuzzy_match=True,             # Fuzzy entity matching in queries
)
```

**Environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_KNOWLEDGE_GRAPH` | `false` | Enable/disable knowledge graph |
| `LORE_GRAPH_DEPTH_DEFAULT` | `2` | Default traversal depth |
| `LORE_GRAPH_DEPTH_MAX` | `3` | Maximum traversal depth |
| `LORE_GRAPH_WEIGHT` | `0.3` | Graph score weight in hybrid recall |
| `LORE_GRAPH_LLM_EXTRACTION` | `false` | Enable extra LLM relationship extraction |
| `LORE_GRAPH_FUZZY_MATCH` | `true` | Enable fuzzy entity name matching |

**Dependency behavior:**
- `knowledge_graph=True` without `enrichment=True` — Graph works but only gets entities from F2 fact subjects/objects (no F6 enrichment entities).
- `knowledge_graph=True` without `fact_extraction=True` — Graph gets F6 enrichment entities but no fact-derived relationships. LLM relationship extraction (if enabled) is the only source of edges.
- `knowledge_graph=True` with both enabled — Full pipeline: F6 entities + F2 fact edges + optional LLM relationships.

### 5.17 Cascade Behavior

| Event | Graph Action |
|-------|-------------|
| `forget(memory_id)` | Remove `entity_mentions` for this memory. Decrement `mention_count` on affected entities. Delete entities with `mention_count = 0`. Set `memory_id = NULL` on relationships (provenance lost but relationship preserved if confirmed by other memories). Delete relationships where `memory_id` was the only source. |
| Fact invalidated (SUPERSEDE) | Close temporal window on corresponding relationship (`valid_until = now()`). Create new relationship from superseding fact. |
| Fact invalidated (CONTRADICT) | No graph change — both relationships remain active until resolved. |
| Entity merge (dedup) | Redirect all relationships from merged entity to canonical entity. Update `entity_mentions`. Delete merged entity. |

## 6. MCP Tools

### 6.1 `graph_query` — Traverse Knowledge Graph

```python
@mcp.tool(
    description=(
        "Traverse the knowledge graph to find entities and relationships connected to a query. "
        "Returns entities within N hops and their relationships. "
        "USE THIS WHEN: you want to explore what's connected to a concept, person, tool, "
        "or project — e.g., 'What tools does auth-service use?', 'What does Alice work on?', "
        "'What depends on PostgreSQL?'"
    ),
)
def graph_query(
    query: str,                          # Entity name or search term
    depth: int = 2,                      # How many hops to traverse (1-3)
    entity_type: Optional[str] = None,   # Filter by entity type
    relation_type: Optional[str] = None, # Filter by relationship type
    include_expired: bool = False,       # Include historical (expired) relationships
    project: Optional[str] = None,
) -> str:
    """Traverse knowledge graph from matching entities."""
```

**Output format:**
```
Graph query: "auth-service" (depth: 2)

Entity: auth-service (project)
  Mentions: 12 | First seen: 2026-01-15 | Last seen: 2026-03-06

Direct relationships (depth 1):
  auth-service --uses--> postgresql (tool) [weight: 0.9]
  auth-service --written_in--> python (language) [weight: 0.8]
  auth-service --deployed_on--> aws (platform) [weight: 0.7]
  alice --works_on--> auth-service (project) [weight: 0.85]

Extended relationships (depth 2):
  postgresql --used_by--> analytics-service (project) [weight: 0.6]
  python --used_by--> data-pipeline (project) [weight: 0.75]
  aws --hosts--> staging-env (concept) [weight: 0.5]

Total: 1 root entity, 7 connected entities, 7 relationships
```

### 6.2 `related` — Find Related Memories via Graph

```python
@mcp.tool(
    description=(
        "Find memories that are related to a query through knowledge graph connections, "
        "not just semantic similarity. Discovers memories linked via shared entities and relationships. "
        "USE THIS WHEN: you want memories connected to a topic through relationships — "
        "e.g., 'What memories are related to our database migration?', "
        "'Find everything connected to the auth-service.'"
    ),
)
def related(
    query: str,                          # Topic, entity name, or search term
    depth: int = 2,                      # Graph traversal depth (1-3)
    limit: int = 10,                     # Max memories to return
    project: Optional[str] = None,
) -> str:
    """Find memories connected via graph relationships."""
```

**Output format:**
```
Related memories for "auth-service" (depth: 2, via graph):

1. [0.92] "We deployed auth-service v2.3 to production on AWS"
   Via: auth-service (direct mention) | 2026-03-01
   Connected entities: auth-service, aws, production

2. [0.78] "PostgreSQL 16 upgrade completed for all services"
   Via: auth-service → uses → postgresql (1 hop) | 2026-02-20
   Connected entities: postgresql

3. [0.65] "Alice fixed the authentication rate limiter bug"
   Via: alice → works_on → auth-service (1 hop) | 2026-02-15
   Connected entities: alice, auth-service

4. [0.52] "Analytics pipeline now reads from the shared PostgreSQL cluster"
   Via: auth-service → uses → postgresql → used_by → analytics (2 hops) | 2026-01-10
   Connected entities: postgresql, analytics-service

Found 4 related memories via 6 graph connections.
```

### 6.3 `entity_map` — Get Entity Graph

```python
@mcp.tool(
    description=(
        "Get a visual entity map for a topic, project, or domain. "
        "Returns a structured graph of entities and their relationships. "
        "USE THIS WHEN: you want to see the big picture — all entities and connections "
        "for a project or topic. Useful for onboarding, architecture review, or context building."
    ),
)
def entity_map(
    topic: str,                          # Topic, project name, or domain
    depth: int = 2,                      # How deep to explore
    entity_types: Optional[str] = None,  # Comma-separated type filter
    format: str = "text",                # "text", "json", or "d3"
    project: Optional[str] = None,
) -> str:
    """Get entity graph for a topic."""
```

**Text output format:**
```
Entity map for "auth-service" (depth: 2)

auth-service (project)
├── uses
│   ├── postgresql (tool)
│   │   └── used_by: analytics-service (project)
│   ├── redis (tool)
│   │   └── used_by: cache-service (project)
│   └── jwt (concept)
├── written_in
│   └── python (language)
├── deployed_on
│   └── aws (platform)
└── team
    ├── alice (person) → works_on
    └── bob (person) → works_on

Entities: 10 | Relationships: 9
```

**D3 JSON output format:**

```json
{
  "nodes": [
    {"id": "uuid-1", "name": "auth-service", "type": "project", "mentions": 12},
    {"id": "uuid-2", "name": "postgresql", "type": "tool", "mentions": 8},
    {"id": "uuid-3", "name": "alice", "type": "person", "mentions": 5}
  ],
  "links": [
    {"source": "uuid-1", "target": "uuid-2", "type": "uses", "weight": 0.9},
    {"source": "uuid-3", "target": "uuid-1", "type": "works_on", "weight": 0.85}
  ],
  "metadata": {
    "root": "uuid-1",
    "depth": 2,
    "total_nodes": 10,
    "total_links": 9
  }
}
```

## 7. CLI Commands

### 7.1 `lore graph <query>`

Traverse the knowledge graph from a starting entity.

```bash
$ lore graph "auth-service" --depth 2
$ lore graph "alice" --type person
$ lore graph "postgresql" --relation uses --depth 1
$ lore graph "auth-service" --format d3 > graph.json
```

### 7.2 `lore entities`

List and search entities in the knowledge graph.

```bash
$ lore entities                           # List all entities
$ lore entities --type person             # Filter by type
$ lore entities --search "post"           # Search by name
$ lore entities --sort mentions           # Sort by mention count
$ lore entities --limit 20
```

**Output:**
```
Entities (filtered by type: person):

  Name          Type     Mentions  First Seen   Last Seen
  alice         person   12        2026-01-15   2026-03-06
  bob           person   8         2026-01-20   2026-03-05
  charlie       person   3         2026-02-01   2026-02-28

Total: 3 entities
```

### 7.3 `lore relationships`

List and filter relationships.

```bash
$ lore relationships                               # List all active
$ lore relationships --entity "auth-service"        # For a specific entity
$ lore relationships --type uses                    # Filter by type
$ lore relationships --include-expired              # Include historical
$ lore relationships --limit 20
```

### 7.4 `lore graph-backfill`

Build the graph from existing memories that predate the graph feature.

```bash
$ lore graph-backfill                     # Backfill all memories
$ lore graph-backfill --project myproject  # Specific project
$ lore graph-backfill --limit 500         # Limit batch size
```

## 8. Lore Facade Changes

### 8.1 New Methods

```python
class Lore:
    # ... existing methods ...

    # Graph traversal
    def graph_query(
        self,
        query: str,
        depth: int = 2,
        entity_type: Optional[str] = None,
        relation_type: Optional[str] = None,
        include_expired: bool = False,
    ) -> GraphResult:
        """Traverse knowledge graph from matching entities."""
        ...

    def get_related_memories(
        self,
        query: str,
        depth: int = 2,
        limit: int = 10,
    ) -> List[RecallResult]:
        """Find memories connected via graph relationships."""
        ...

    def get_entity_map(
        self,
        topic: str,
        depth: int = 2,
        entity_types: Optional[List[str]] = None,
        format: str = "text",
    ) -> Union[str, Dict]:
        """Get entity graph for a topic (text, JSON, or D3 format)."""
        ...

    # Entity management
    def get_entity(self, name: str, entity_type: Optional[str] = None) -> Optional[Entity]:
        """Look up a specific entity."""
        ...

    def list_entities(
        self,
        entity_type: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
    ) -> List[Entity]:
        """List entities with optional filtering."""
        ...

    def merge_entities(self, source_id: str, target_id: str) -> Entity:
        """Merge two entities (redirect relationships, update aliases)."""
        ...

    # Relationship management
    def list_relationships(
        self,
        entity_name: Optional[str] = None,
        relation_type: Optional[str] = None,
        include_expired: bool = False,
        limit: int = 50,
    ) -> List[Relationship]:
        """List relationships with optional filtering."""
        ...

    # Graph backfill
    def graph_backfill(
        self,
        project: Optional[str] = None,
        limit: int = 100,
    ) -> int:
        """Build graph from existing memories. Returns count of entities created."""
        ...
```

### 8.2 Extended `recall()` Signature

```python
def recall(
    self,
    query: str,
    *,
    limit: int = 10,
    # ... existing parameters ...
    # NEW graph parameters:
    graph_depth: int = 0,            # 0 = vector-only (backward compatible)
    graph_weight: float = 0.3,       # Weight of graph score in ranking
    include_entities: bool = False,  # Include related entities in results
) -> List[RecallResult]:
```

### 8.3 Extended `RecallResult`

```python
@dataclass
class RecallResult:
    # ... existing fields ...
    # NEW:
    related_entities: Optional[List[Entity]] = None  # Populated when include_entities=True
    graph_score: Optional[float] = None              # Graph-based relevance score
    graph_path: Optional[List[str]] = None           # Entity path that connected this result
```

### 8.4 New Result Types

```python
@dataclass
class GraphResult:
    """Result of a graph traversal query."""
    root_entity: Entity
    entities: List[Entity]               # All discovered entities
    relationships: List[Relationship]    # All discovered edges
    depth_reached: int                   # Actual max depth reached
    total_entities: int
    total_relationships: int

@dataclass
class GraphNode:
    """An entity with its traversal context."""
    entity: Entity
    depth: int                           # Hop distance from root
    path: List[str]                      # Entity IDs in the path
    incoming_relation: Optional[str]     # Relationship type that led here
    incoming_weight: Optional[float]     # Weight of that relationship
```

## 9. Store ABC Extensions

```python
class Store(ABC):
    # ... existing methods ...

    # Entity CRUD
    @abstractmethod
    def upsert_entity(self, name: str, entity_type: str, aliases: Optional[List[str]] = None, metadata: Optional[Dict] = None) -> Entity: ...

    @abstractmethod
    def get_entity(self, entity_id: str) -> Optional[Entity]: ...

    @abstractmethod
    def find_entities_by_name(self, name: str, entity_type: Optional[str] = None) -> List[Entity]: ...

    @abstractmethod
    def find_entities_fuzzy(self, name: str, entity_type: Optional[str] = None, threshold: float = 0.6) -> List[Entity]: ...

    @abstractmethod
    def list_entities(self, entity_type: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Entity]: ...

    @abstractmethod
    def merge_entities(self, source_id: str, target_id: str) -> Entity: ...

    @abstractmethod
    def delete_entity(self, entity_id: str) -> bool: ...

    # Relationship CRUD
    @abstractmethod
    def upsert_relationship(self, source_entity_id: str, target_entity_id: str, relation_type: str, weight: float = 1.0, valid_from: Optional[str] = None, memory_id: Optional[str] = None, fact_id: Optional[str] = None, metadata: Optional[Dict] = None) -> Relationship: ...

    @abstractmethod
    def find_active_relationship(self, source_entity_id: str, target_entity_id: str, relation_type: str) -> Optional[Relationship]: ...

    @abstractmethod
    def list_relationships(self, entity_id: Optional[str] = None, relation_type: Optional[str] = None, include_expired: bool = False, limit: int = 50) -> List[Relationship]: ...

    @abstractmethod
    def close_relationship(self, relationship_id: str) -> None: ...

    @abstractmethod
    def find_relationship_by_fact(self, fact_id: str) -> Optional[Relationship]: ...

    # Entity-Memory junction
    @abstractmethod
    def add_entity_mention(self, entity_id: str, memory_id: str) -> None: ...

    @abstractmethod
    def get_entity_memories(self, entity_id: str, limit: int = 50) -> List[str]: ...

    @abstractmethod
    def get_memory_entities(self, memory_id: str) -> List[Entity]: ...

    # Graph traversal
    @abstractmethod
    def traverse_graph(self, start_entity_id: str, max_depth: int = 2, relation_type: Optional[str] = None, include_expired: bool = False, direction: str = "both") -> List[GraphNode]: ...

    # Graph stats
    @abstractmethod
    def graph_stats(self) -> Dict[str, Any]: ...
```

**Default implementations:** Abstract methods will have default no-op or `NotImplementedError` implementations on the base class so that existing stores (MemoryStore, HttpStore) don't break until updated.

**Implementation priority:**
1. `SqliteStore` — full implementation (local development)
2. `MemoryStore` — in-memory implementation (testing)
3. Postgres server — server-side implementation
4. `HttpStore` — client stub (deferred to when server adds graph endpoints)

## 10. File Changes

| File | Change |
|------|--------|
| `src/lore/types.py` | Add `Entity`, `Relationship`, `EntityMention`, `GraphResult`, `GraphNode` dataclasses |
| `src/lore/lore.py` | Add graph methods (`graph_query`, `get_related_memories`, `get_entity_map`, `list_entities`, `list_relationships`, `merge_entities`, `graph_backfill`). Extend `remember()` with graph update step. Extend `recall()` with `graph_depth`, `graph_weight`, `include_entities` parameters. Extend `forget()` with graph cascade. |
| `src/lore/store/base.py` | Add graph abstract methods (entity CRUD, relationship CRUD, traversal, entity mentions) |
| `src/lore/store/sqlite.py` | Create `entities`, `relationships`, `entity_mentions` tables. Implement all graph store methods including recursive CTE traversal. |
| `src/lore/store/memory.py` | In-memory graph implementation (for testing) |
| `src/lore/store/http.py` | Stub methods (raise NotImplementedError until server adds endpoints) |
| `src/lore/graph/` | **NEW** module: `__init__.py`, `extraction.py` (entity/relationship extraction from memories + LLM prompt), `dedup.py` (entity normalization + alias resolution), `traversal.py` (graph traversal logic + scoring), `visualization.py` (D3 JSON output) |
| `src/lore/mcp/server.py` | Add `graph_query`, `related`, `entity_map` tools |
| `src/lore/cli.py` | Add `graph`, `entities`, `relationships`, `graph-backfill` subcommands |
| `src/lore/server/` | Add graph endpoints: `/api/v1/graph/query`, `/api/v1/graph/entities`, `/api/v1/graph/relationships` |
| `tests/test_knowledge_graph.py` | **NEW** — comprehensive graph tests |
| `tests/test_graph_traversal.py` | **NEW** — recursive CTE traversal tests |
| `tests/test_graph_integration.py` | **NEW** — integration tests (F2 facts → graph, F6 entities → graph, hybrid recall) |
| `tests/test_entity_dedup.py` | **NEW** — entity normalization and alias resolution tests |

## 11. Implementation Plan

### 11.1 Task Breakdown

1. **Data model** — Add `Entity`, `Relationship`, `EntityMention`, `GraphResult`, `GraphNode` to `types.py`.
2. **Schema** — Create `entities`, `relationships`, `entity_mentions` tables in `SqliteStore` (with indexes). Create matching Postgres schema.
3. **Entity normalization** — Create `src/lore/graph/dedup.py` with `normalize_entity_name()`, alias matching logic.
4. **Store layer — Entity CRUD** — Implement `upsert_entity`, `find_entities_by_name`, `find_entities_fuzzy`, `list_entities`, `merge_entities`, `delete_entity` in `SqliteStore` and `MemoryStore`.
5. **Store layer — Relationship CRUD** — Implement `upsert_relationship`, `find_active_relationship`, `list_relationships`, `close_relationship`, `find_relationship_by_fact` in `SqliteStore` and `MemoryStore`.
6. **Store layer — Entity mentions** — Implement `add_entity_mention`, `get_entity_memories`, `get_memory_entities`.
7. **Store layer — Graph traversal** — Implement `traverse_graph` with recursive CTEs (SQLite and Postgres variants).
8. **Graph update pipeline** — Create `src/lore/graph/extraction.py`. Wire graph update into `remember()` after enrichment pipeline.
9. **F2 integration** — Convert facts to graph edges. Handle fact supersession (temporal edge closing).
10. **F6 integration** — Promote enrichment entities to graph entities.
11. **Hybrid recall** — Extend `recall()` with `graph_depth`, `graph_weight`, `include_entities`. Implement query entity identification, graph scoring, result merging.
12. **Cascade behavior** — Extend `forget()` to handle graph cleanup (entity mention removal, entity deletion if orphaned).
13. **MCP tools** — Add `graph_query`, `related`, `entity_map` to `server.py`.
14. **CLI** — Add `graph`, `entities`, `relationships`, `graph-backfill` subcommands to `cli.py`.
15. **Visualization** — Create `src/lore/graph/visualization.py` for D3 JSON output.
16. **Graph backfill** — Implement `graph_backfill()` for existing memories.
17. **Tests** — Comprehensive test suite.

### 11.2 Suggested Story Sequencing

| Story | Description | Depends on |
|-------|-------------|-----------|
| S1 | Data model + schema (types, tables, indexes) | — |
| S2 | Entity normalization + dedup module | S1 |
| S3 | Entity CRUD in stores (SQLite + Memory) | S1, S2 |
| S4 | Relationship CRUD in stores | S1, S3 |
| S5 | Entity mention junction + cascade behavior | S3, S4 |
| S6 | Graph traversal (recursive CTEs) | S3, S4 |
| S7 | Graph update pipeline (remember → graph) | S3, S4, S5 |
| S8 | F2 integration (facts → edges, supersession → temporal close) | S4, S7 |
| S9 | F6 integration (enrichment entities → graph) | S3, S7 |
| S10 | Hybrid recall (vector + graph scoring) | S6 |
| S11 | MCP tools (graph_query, related, entity_map) | S6, S10 |
| S12 | CLI commands (graph, entities, relationships) | S3, S4, S6 |
| S13 | Visualization (D3 JSON output) | S6 |
| S14 | Graph backfill | S7 |
| S15 | Comprehensive tests | S1-S14 |

## 12. Acceptance Criteria

### Must Have (P0)

- [ ] AC-1: `Entity` dataclass exists with fields: id, name, entity_type, aliases, metadata, first_seen_at, last_seen_at, mention_count.
- [ ] AC-2: `Relationship` dataclass exists with fields: id, source_entity_id, target_entity_id, relation_type, weight, valid_from, valid_until, memory_id, fact_id, metadata, created_at.
- [ ] AC-3: `entities` table created in SQLite with schema and indexes (unique on name+type, index on name, index on type).
- [ ] AC-4: `relationships` table created in SQLite with schema and indexes (source, target, type, active, memory).
- [ ] AC-5: `entity_mentions` junction table created with composite PK and indexes.
- [ ] AC-6: `entities` table created in Postgres with matching schema.
- [ ] AC-7: `relationships` table created in Postgres with matching schema.
- [ ] AC-8: `upsert_entity()` normalizes names to lowercase, tracks aliases, increments mention_count on existing entities.
- [ ] AC-9: Entity deduplication: "PostgreSQL", "postgres", "Postgres" all resolve to the same entity.
- [ ] AC-10: F2 facts automatically become graph entities (subjects/objects) and relationships (predicates) on `remember()`.
- [ ] AC-11: F6 enrichment entities automatically become graph entities on `remember()`.
- [ ] AC-12: `traverse_graph()` performs multi-hop traversal using recursive CTEs with configurable depth (default 2, max 3).
- [ ] AC-13: Graph traversal prevents infinite cycles (path tracking).
- [ ] AC-14: `recall()` with `graph_depth > 0` returns hybrid results (vector + graph scored).
- [ ] AC-15: `recall()` with `graph_depth=0` (default) behaves identically to v0.5.x — no graph queries, no performance impact.
- [ ] AC-16: Temporal edges: relationships have `valid_from`/`valid_until`. Fact supersession closes old edges.
- [ ] AC-17: `forget(memory_id)` removes entity mentions, decrements entity mention counts, deletes orphaned entities, handles relationship cleanup.
- [ ] AC-18: MCP `graph_query` tool traverses graph and returns formatted entity/relationship results.
- [ ] AC-19: MCP `related` tool finds memories connected via graph relationships.
- [ ] AC-20: MCP `entity_map` tool returns entity graph in text and D3 JSON formats.
- [ ] AC-21: CLI `lore graph <query>` traverses and displays graph results.
- [ ] AC-22: CLI `lore entities` lists entities with type/search filtering.
- [ ] AC-23: CLI `lore relationships` lists relationships with entity/type filtering.
- [ ] AC-24: `knowledge_graph=False` (default) disables all graph features — no table creation, no graph queries, no performance impact.
- [ ] AC-25: All existing tests pass without modification (backward compatibility).
- [ ] AC-26: New tests cover: entity CRUD, relationship CRUD, traversal (1-3 hops), cycle prevention, entity dedup, F2 integration, F6 integration, hybrid recall, cascade deletion, temporal edges.

### Should Have (P1)

- [ ] AC-27: `entity_map` produces D3-compatible JSON suitable for force-directed graph visualization.
- [ ] AC-28: Graph backfill command processes existing memories and builds graph.
- [ ] AC-29: Fuzzy entity matching in queries (handles typos/abbreviations).
- [ ] AC-30: `graph_stats()` returns entity count, relationship count, average connections per entity, most connected entities.
- [ ] AC-31: `merge_entities()` consolidates two entities, redirecting all relationships and updating aliases.
- [ ] AC-32: Relationship weight increases when multiple memories confirm the same relationship.
- [ ] AC-33: LLM-assisted relationship extraction (optional, `graph_llm_extraction=True`).
- [ ] AC-34: CLI `lore graph-backfill` builds graph from existing memories.

### Could Have (P2)

- [ ] AC-35: Postgres server endpoints for graph query, entities, relationships.
- [ ] AC-36: HttpStore implementation for graph methods.
- [ ] AC-37: LLM-assisted entity deduplication on backfill.
- [ ] AC-38: Bidirectional traversal (follow both incoming and outgoing edges).
- [ ] AC-39: Historical graph queries (include expired relationships for temporal exploration).

## 13. Success Metrics

| Metric | Target |
|--------|--------|
| All existing tests pass | 100% |
| New test count | >= 60 tests |
| Entity extraction from a memory with 3 named entities | Correctly creates 3 entities in graph |
| F2 fact → graph edge conversion | 100% of active facts have corresponding graph edges |
| Graph traversal (2 hops, 1000 entities) | < 50ms query time |
| Hybrid recall relevance | Graph-enhanced recall returns at least 1 additional relevant memory not found by vector-only search (validated on test scenarios) |
| Entity deduplication | "PostgreSQL" and "postgres" resolve to same entity in 100% of test cases |
| Zero-overhead when disabled | `recall()` latency unchanged when `knowledge_graph=False` |
| Cascade correctness | `forget()` leaves no orphaned entity mentions or dangling relationships |

## 14. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Recursive CTE performance at scale | Medium — slow traversal on large graphs | Depth capped at 3. Compound indexes on (source, valid_until). Benchmark at 10K entities. |
| Entity dedup quality (without LLM) | Medium — "React" vs "React.js" may not merge | Alias tracking + normalized names catch common variants. LLM dedup available as optional backfill. |
| Graph noise from low-quality F2 facts | Medium — bad facts create meaningless edges | Filter facts by confidence threshold (default >= 0.5). Low-confidence facts don't create graph edges. |
| Increased `remember()` latency | Medium — graph update adds DB writes | Graph update is lightweight (upserts + inserts). Most overhead is in the F6/F2 LLM calls which are already optional. Graph update itself is pure DB ops (~5-10ms). |
| Schema migration complexity | Low — new tables only | `entities`, `relationships`, `entity_mentions` are all new tables. No ALTER on existing tables. |
| Hybrid recall scoring tuning | Medium — graph_weight may need adjustment | Configurable via `graph_weight` parameter. Default 0.3 is conservative. Users can tune per use case. |
| Cyclic graph traversal | Low — infinite loops in CTE | Path array tracking prevents revisiting nodes. Tested explicitly. |
| SQLite recursive CTE limitations | Low — SQLite supports WITH RECURSIVE | SQLite has supported recursive CTEs since 3.8.3 (2014). No compatibility concerns. |
| F2/F6 not enabled when graph is enabled | Low — graph has fewer inputs | Graph works with partial inputs. Documented behavior for each configuration combination (Section 5.16). |

## 15. Interaction with Existing Systems

### F2 — Fact Extraction + Conflict Resolution
The primary data source for graph edges. Every active fact `(subject, predicate, object)` maps to entities + relationship. Fact supersession (SUPERSEDE resolution) triggers temporal edge closure. Fact contradiction (CONTRADICT resolution) leaves both edges active — the graph reflects the ambiguity.

### F6 — Metadata Enrichment
Enrichment entities (`metadata["enrichment"]["entities"]`) are promoted to the `entities` table. Entity types from F6 (`person`, `tool`, `project`, etc.) are reused directly. When both F6 and F2 produce the same entity, dedup merges them.

### F5 — Importance Scoring
Entity mention count is a lightweight importance signal. In future, entity importance could be derived from connected memory importance scores. For now, `mention_count` serves as a proxy.

### F4 — Memory Tiers
Working-tier memories may be excluded from graph updates (configurable: `graph_from_tiers=("short", "long")`). This prevents ephemeral context from polluting the knowledge graph.

### F9 — Dialog Classification
Classification metadata (intent, domain) is available but not directly consumed by the graph layer. Future consideration: use domain classification to auto-tag entities.

### F10 — Prompt Export
`as_prompt()` could be extended to include graph context: "Here are the entities and relationships relevant to this conversation." This is a future enhancement, not in scope for initial F1.

### Recall (existing)
Hybrid recall is additive. `graph_depth=0` (default) gives identical behavior to current vector-only recall. No breaking changes.

## 16. Competitive Positioning

| Feature | Lore (F1) | Mem0 | Zep | Cognee |
|---------|-----------|------|-----|--------|
| Knowledge graph | Yes | Yes | Yes | Yes |
| Graph DB required | **No (Postgres only)** | Neo4j required | Proprietary | Neo4j/custom |
| Temporal edges | Yes (valid_from/until) | No | Yes | Limited |
| Hybrid retrieval | Yes (vector + graph) | Yes | Yes | Yes |
| Entity dedup | Yes (aliases + normalization) | Basic | Yes | Basic |
| Single DB deployment | **Yes** | No (Postgres + Neo4j) | N/A (SaaS) | No |
| Self-hosted | **Yes** | Yes | No (SaaS only) | Yes |
| `docker compose up` | **Yes** | Complex setup | N/A | Complex setup |
| Open source | **Yes (MIT)** | Yes (Apache 2.0) | No | Yes |
| MCP-native | **Yes** | No | No | No |

**Key differentiator:** Lore delivers knowledge graph capabilities comparable to Mem0 and Zep without requiring Neo4j or proprietary infrastructure. Single Postgres instance, `docker compose up`, done.

## 17. Future Considerations (Out of Scope)

- **Apache AGE migration** — If query complexity outgrows recursive CTEs, AGE can be added as an optional Postgres extension for Cypher support.
- **Entity embeddings** — Embedding individual entities for semantic entity search (find entities similar to a concept).
- **Weighted shortest path** — Find the shortest/strongest path between two entities (Dijkstra on graph).
- **Entity importance scoring** — Derive entity importance from connected memory scores, relationship weights, and mention frequency. Could feed into recall ranking.
- **Graph-based consolidation** — F3 (Memory Consolidation) can use graph clusters to identify consolidation candidates. The graph provides "topic islands" for grouping.
- **Cross-project graph** — Currently graph is per-project. Cross-project entity linking (same entity in multiple projects) is a future consideration.
- **Ontology / type hierarchy** — Entity type hierarchy (e.g., "framework" is-a "tool") for richer type-based queries.
- **Graph export/import** — Export graph as RDF/Turtle, import from external knowledge bases.
- **Real-time graph visualization** — WebSocket-based live graph updates for a dashboard UI.
