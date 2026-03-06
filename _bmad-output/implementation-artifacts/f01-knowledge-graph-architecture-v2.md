# Architecture: F1 -- Knowledge Graph Layer (v2)

**Version:** 2.0
**Author:** Architect Agent
**Date:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f01-knowledge-graph-prd.md`
**Supersedes:** `f01-knowledge-graph-architecture.md` (v1 used recursive CTEs; v2 uses app-level hop-by-hop traversal)
**Phase:** 3 -- Graph Layer
**Depends on:** F2 (Fact Extraction -- SPO triples become graph edges), F6 (Metadata Enrichment -- extracted entities feed into graph)
**Dependents:** F3 (Memory Consolidation -- uses graph for intelligent grouping), F7 (Webhook Ingestion -- graph auto-updated on ingest)

---

## 1. Overview

F1 transforms Lore from a flat memory store with disconnected facts into a **connected knowledge graph**. Every entity mentioned in memories becomes a node, every relationship between entities becomes a directed edge, and multi-hop traversal lets agents answer relational queries that pure vector search cannot.

The graph lives entirely in Postgres (or SQLite for local). No Neo4j, no Apache AGE, no external graph database.

### Key Design Decision: App-Level Hop-by-Hop Traversal (NO Recursive CTEs)

**Why not recursive CTEs:**
- Cannot use indexes effectively inside the recursion body
- Impossible to inject scoring/weighting logic between traversal steps
- No EXPLAIN insight into recursion steps -- debugging is painful
- Risk of runaway queries if depth limits aren't strict
- Overkill for our use case: 1-3 hops max, thousands of nodes (not millions)

**App-level approach:**
- Each hop = one simple, indexed SQL query
- Python controls depth (default 2, configurable max 3)
- Between hops: apply scoring, filter by weight/importance threshold, prune low-relevance branches
- Results assembled in Python with full control over ordering, dedup, caching
- Predictable performance: each hop is O(edges from current frontier), never recursive

### Architecture Principles

1. **Pure Postgres/SQLite** -- Adjacency tables with simple indexed queries. No extensions, no second database.
2. **Opt-in, zero-overhead when off** -- `knowledge_graph=False` (default) means no table creation, no graph queries. `graph_depth=0` on `recall()` gives identical behavior to v0.5.x.
3. **Multiplicative scoring** -- Graph relevance multiplies into existing `cosine_similarity * importance` pipeline. No additive mixing.
4. **Temporal edges** -- Relationships have `valid_from`/`valid_until`. Queries default to current-time filtering.
5. **Deterministic traversal** -- App-level hop-by-hop means every step is debuggable, testable, and explainable.

---

## 2. Database Schema

### 2.1 `entities` Table

```sql
CREATE TABLE entities (
    id              TEXT PRIMARY KEY,       -- ULID
    name            TEXT NOT NULL,          -- Canonical lowercase name
    entity_type     TEXT NOT NULL,          -- person, tool, project, concept, organization, platform, language, framework
    aliases         TEXT DEFAULT '[]',      -- JSON array of alternative surface forms
    description     TEXT,                   -- Optional short description
    metadata        TEXT,                   -- JSON blob for extensibility
    mention_count   INTEGER DEFAULT 1,     -- How many memories reference this entity
    first_seen_at   TEXT NOT NULL,          -- ISO 8601
    last_seen_at    TEXT NOT NULL,          -- ISO 8601
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Lookup by canonical name (case-insensitive via stored lowercase)
CREATE UNIQUE INDEX idx_entities_name ON entities(name);
-- Filter by type
CREATE INDEX idx_entities_type ON entities(entity_type);
-- Sort by mention frequency
CREATE INDEX idx_entities_mention_count ON entities(mention_count DESC);
```

### 2.2 `relationships` Table

```sql
CREATE TABLE relationships (
    id                  TEXT PRIMARY KEY,   -- ULID
    source_entity_id    TEXT NOT NULL,      -- FK to entities.id
    target_entity_id    TEXT NOT NULL,      -- FK to entities.id
    rel_type            TEXT NOT NULL,      -- depends_on, uses, implements, mentions, works_on, related_to, etc.
    weight              REAL DEFAULT 1.0,   -- 0.0-1.0 confidence/strength
    properties          TEXT,               -- JSON blob for edge metadata
    source_fact_id      TEXT,               -- FK to facts.id (which fact created this edge)
    source_memory_id    TEXT,               -- FK to memories.id (which memory this came from)
    valid_from          TEXT NOT NULL,      -- ISO 8601 -- when this relationship became true
    valid_until         TEXT,               -- ISO 8601 -- NULL = still active
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (source_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (target_entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

-- HOP QUERY: "Find all relationships FROM these entities" (Hop 1 outbound)
CREATE INDEX idx_rel_source ON relationships(source_entity_id);
-- HOP QUERY: "Find all relationships TO these entities" (Hop 1 inbound)
CREATE INDEX idx_rel_target ON relationships(target_entity_id);
-- Filter active relationships (valid_until IS NULL = currently active)
CREATE INDEX idx_rel_active ON relationships(source_entity_id) WHERE valid_until IS NULL;
-- Filter by relationship type
CREATE INDEX idx_rel_type ON relationships(rel_type);
-- Prevent duplicate edges
CREATE UNIQUE INDEX idx_rel_unique_edge ON relationships(source_entity_id, target_entity_id, rel_type)
    WHERE valid_until IS NULL;
-- Temporal queries
CREATE INDEX idx_rel_temporal ON relationships(valid_from, valid_until);
```

### 2.3 `entity_mentions` Table

Links entities to the memories that mention them. Enables: "which memories mention this entity?" and "which entities appear in this memory?"

```sql
CREATE TABLE entity_mentions (
    id              TEXT PRIMARY KEY,       -- ULID
    entity_id       TEXT NOT NULL,          -- FK to entities.id
    memory_id       TEXT NOT NULL,          -- FK to memories.id
    mention_type    TEXT DEFAULT 'explicit', -- explicit (from F6), inferred (from F2 SPO)
    confidence      REAL DEFAULT 1.0,       -- 0.0-1.0
    created_at      TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

-- "Which memories mention entity X?"
CREATE INDEX idx_em_entity ON entity_mentions(entity_id);
-- "Which entities are in memory Y?"
CREATE INDEX idx_em_memory ON entity_mentions(memory_id);
-- Prevent duplicate mentions
CREATE UNIQUE INDEX idx_em_unique ON entity_mentions(entity_id, memory_id);
```

### 2.4 Migration File: `migrations/007_knowledge_graph.sql`

```sql
-- Migration 007: Knowledge Graph tables (F1)
-- Depends on: 001_initial.sql (memories table)

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
CREATE INDEX IF NOT EXISTS idx_entities_mention_count ON entities(mention_count DESC);

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
CREATE INDEX IF NOT EXISTS idx_rel_active ON relationships(source_entity_id) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(rel_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rel_unique_edge ON relationships(source_entity_id, target_entity_id, rel_type) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_rel_temporal ON relationships(valid_from, valid_until);

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_em_unique ON entity_mentions(entity_id, memory_id);
```

---

## 3. Data Types

### 3.1 New Types in `types.py`

```python
@dataclass
class Entity:
    id: str                                 # ULID
    name: str                               # Canonical lowercase name
    entity_type: str                        # person, tool, project, etc.
    aliases: List[str] = field(default_factory=list)
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    mention_count: int = 1
    first_seen_at: str = ""
    last_seen_at: str = ""
    created_at: str = ""
    updated_at: str = ""

VALID_ENTITY_TYPES = (
    "person", "tool", "project", "concept", "organization",
    "platform", "language", "framework", "service", "other",
)


@dataclass
class Relationship:
    id: str                                 # ULID
    source_entity_id: str                   # FK to entities
    target_entity_id: str                   # FK to entities
    rel_type: str                           # depends_on, uses, etc.
    weight: float = 1.0                     # 0.0-1.0
    properties: Optional[Dict[str, Any]] = None
    source_fact_id: Optional[str] = None
    source_memory_id: Optional[str] = None
    valid_from: str = ""
    valid_until: Optional[str] = None       # NULL = still active
    created_at: str = ""
    updated_at: str = ""

VALID_REL_TYPES = (
    "depends_on", "uses", "implements", "mentions", "works_on",
    "related_to", "part_of", "created_by", "deployed_on",
    "communicates_with", "extends", "configures",
)


@dataclass
class EntityMention:
    id: str
    entity_id: str
    memory_id: str
    mention_type: str = "explicit"          # explicit, inferred
    confidence: float = 1.0
    created_at: str = ""


@dataclass
class GraphContext:
    """Result of a graph traversal, merged into recall results."""
    entities: List[Entity]
    relationships: List[Relationship]
    paths: List[List[str]]                  # Entity ID chains showing connectivity
    relevance_score: float                  # 0.0-1.0, used in hybrid scoring
```

---

## 4. Entity Extraction Pipeline

Entities flow into the graph from two sources:

### 4.1 From F6 Enrichment (Primary Source)

When `remember()` is called with enrichment enabled, F6 extracts entities:

```python
# In enrichment result:
{
    "entities": [
        {"name": "auth-service", "type": "service"},
        {"name": "Alice", "type": "person"},
        {"name": "Kubernetes", "type": "platform"},
    ]
}
```

**Pipeline: `EntityManager.ingest_from_enrichment(memory_id, enrichment_entities)`**

```python
class EntityManager:
    def __init__(self, store: Store):
        self.store = store

    def ingest_from_enrichment(
        self, memory_id: str, entities: List[Dict[str, str]]
    ) -> List[Entity]:
        """Process entities from F6 enrichment into graph nodes."""
        result = []
        for raw in entities:
            name = self._normalize_name(raw["name"])
            entity_type = raw.get("type", "concept")

            # Deduplicate: find existing entity or create new
            entity = self._resolve_entity(name, entity_type)

            # Create mention link
            self.store.save_entity_mention(EntityMention(
                id=ulid(),
                entity_id=entity.id,
                memory_id=memory_id,
                mention_type="explicit",
                confidence=1.0,
                created_at=now_iso(),
            ))

            # Update mention count and last_seen
            entity.mention_count += 1
            entity.last_seen_at = now_iso()
            self.store.update_entity(entity)

            result.append(entity)
        return result
```

### 4.2 From F2 Facts (Secondary Source)

SPO triples from fact extraction implicitly define entities:

```python
# Fact: subject="auth-service", predicate="depends_on", object="redis"
# This implies two entities: "auth-service" and "redis"
```

**Pipeline: `EntityManager.ingest_from_fact(memory_id, fact)`**

```python
def ingest_from_fact(self, memory_id: str, fact: Fact) -> Tuple[Entity, Entity]:
    """Extract entities from a fact's subject and object."""
    subject_entity = self._resolve_entity(
        self._normalize_name(fact.subject),
        entity_type="concept",  # Default; may be refined by F6 context
    )
    object_entity = self._resolve_entity(
        self._normalize_name(fact.object),
        entity_type="concept",
    )

    # Create mention links for both
    for entity in (subject_entity, object_entity):
        self.store.save_entity_mention(EntityMention(
            id=ulid(),
            entity_id=entity.id,
            memory_id=memory_id,
            mention_type="inferred",
            confidence=fact.confidence,
            created_at=now_iso(),
        ))

    return subject_entity, object_entity
```

---

## 5. Entity Deduplication

### 5.1 Normalization Rules

```python
def _normalize_name(self, raw: str) -> str:
    """Normalize entity name for dedup matching."""
    name = raw.strip().lower()
    # Collapse whitespace
    name = " ".join(name.split())
    # Remove trailing punctuation
    name = name.rstrip(".,;:!?")
    return name
```

### 5.2 Resolution Algorithm

```python
def _resolve_entity(self, name: str, entity_type: str) -> Entity:
    """Find existing entity by name/alias or create new one."""
    # 1. Exact match on canonical name
    existing = self.store.get_entity_by_name(name)
    if existing:
        # Promote type if more specific (e.g., concept -> service)
        if entity_type != "concept" and existing.entity_type == "concept":
            existing.entity_type = entity_type
            self.store.update_entity(existing)
        return existing

    # 2. Alias match -- check if name is a known alias
    existing = self.store.get_entity_by_alias(name)
    if existing:
        return existing

    # 3. Create new entity
    now = now_iso()
    entity = Entity(
        id=ulid(),
        name=name,
        entity_type=entity_type,
        aliases=[],
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    self.store.save_entity(entity)
    return entity
```

### 5.3 Alias Management

Aliases allow the same entity to be found under different surface forms (e.g., "k8s" -> "kubernetes", "JS" -> "javascript").

```python
def add_alias(self, entity_id: str, alias: str) -> None:
    """Add an alias to an entity."""
    entity = self.store.get_entity(entity_id)
    if entity:
        normalized = self._normalize_name(alias)
        if normalized not in entity.aliases and normalized != entity.name:
            entity.aliases.append(normalized)
            entity.updated_at = now_iso()
            self.store.update_entity(entity)

def merge_entities(self, keep_id: str, merge_id: str) -> Entity:
    """Merge two entities. Keep entity absorbs merge entity's aliases and mentions."""
    keep = self.store.get_entity(keep_id)
    merge = self.store.get_entity(merge_id)

    # Absorb name as alias
    if merge.name not in keep.aliases:
        keep.aliases.append(merge.name)
    # Absorb aliases
    for alias in merge.aliases:
        if alias not in keep.aliases and alias != keep.name:
            keep.aliases.append(alias)

    # Transfer mentions
    self.store.transfer_entity_mentions(from_id=merge_id, to_id=keep_id)
    # Transfer relationships (repoint edges)
    self.store.transfer_entity_relationships(from_id=merge_id, to_id=keep_id)
    # Update counts
    keep.mention_count += merge.mention_count
    keep.updated_at = now_iso()

    self.store.update_entity(keep)
    self.store.delete_entity(merge_id)
    return keep
```

### 5.4 Store Methods for Alias Lookup

```python
# In SqliteStore:
def get_entity_by_alias(self, alias: str) -> Optional[Entity]:
    """Search entity aliases JSON array for a match."""
    # SQLite JSON: json_each to search array elements
    row = self._execute(
        """SELECT * FROM entities
           WHERE id IN (
               SELECT e.id FROM entities e, json_each(e.aliases) AS a
               WHERE a.value = ?
           )""",
        (alias,),
    ).fetchone()
    return self._row_to_entity(row) if row else None
```

---

## 6. Relationship Extraction

### 6.1 From F2 Facts (Primary Source)

Every SPO triple from fact extraction becomes a directed edge:

```python
class RelationshipManager:
    def __init__(self, store: Store, entity_manager: EntityManager):
        self.store = store
        self.entity_manager = entity_manager

    def ingest_from_fact(self, memory_id: str, fact: Fact) -> Optional[Relationship]:
        """Convert an SPO fact into a graph edge."""
        # Resolve entities (creates them if needed)
        source_entity, target_entity = self.entity_manager.ingest_from_fact(
            memory_id, fact
        )

        # Map predicate to relationship type
        rel_type = self._map_predicate(fact.predicate)

        # Check for existing active edge
        existing = self.store.get_active_relationship(
            source_entity.id, target_entity.id, rel_type
        )
        if existing:
            # Strengthen weight with repeated mentions
            existing.weight = min(1.0, existing.weight + 0.1)
            existing.updated_at = now_iso()
            self.store.update_relationship(existing)
            return existing

        # Create new relationship
        now = now_iso()
        rel = Relationship(
            id=ulid(),
            source_entity_id=source_entity.id,
            target_entity_id=target_entity.id,
            rel_type=rel_type,
            weight=fact.confidence,
            source_fact_id=fact.id,
            source_memory_id=memory_id,
            valid_from=now,
            valid_until=None,  # Active
            created_at=now,
            updated_at=now,
        )
        self.store.save_relationship(rel)
        return rel
```

### 6.2 Predicate Mapping

```python
# Common predicate -> relationship type mappings
PREDICATE_TO_REL_TYPE = {
    "depends_on": "depends_on",
    "uses": "uses",
    "implements": "implements",
    "works_on": "works_on",
    "created": "created_by",       # Reversed: object created_by subject
    "deployed_on": "deployed_on",
    "part_of": "part_of",
    "extends": "extends",
    "configures": "configures",
    "communicates_with": "communicates_with",
    "is": "related_to",            # Generic "is" -> related_to
    "has": "related_to",           # Generic "has" -> related_to
}

def _map_predicate(self, predicate: str) -> str:
    """Map a fact predicate to a relationship type."""
    normalized = predicate.lower().replace(" ", "_")
    return PREDICATE_TO_REL_TYPE.get(normalized, "related_to")
```

### 6.3 From F6 Enrichment (Co-occurrence)

When F6 extracts multiple entities from the same memory, they are implicitly related. We create lightweight `co_occurs_with` relationships:

```python
def ingest_co_occurrences(
    self, memory_id: str, entities: List[Entity], weight: float = 0.3
) -> List[Relationship]:
    """Create co-occurrence edges between entities mentioned in the same memory."""
    relationships = []
    for i, e1 in enumerate(entities):
        for e2 in entities[i + 1:]:
            # Bidirectional: create both directions with lower weight
            for source, target in [(e1, e2), (e2, e1)]:
                existing = self.store.get_active_relationship(
                    source.id, target.id, "co_occurs_with"
                )
                if existing:
                    existing.weight = min(1.0, existing.weight + 0.05)
                    existing.updated_at = now_iso()
                    self.store.update_relationship(existing)
                    relationships.append(existing)
                else:
                    now = now_iso()
                    rel = Relationship(
                        id=ulid(),
                        source_entity_id=source.id,
                        target_entity_id=target.id,
                        rel_type="co_occurs_with",
                        weight=weight,
                        source_memory_id=memory_id,
                        valid_from=now,
                        created_at=now,
                        updated_at=now,
                    )
                    self.store.save_relationship(rel)
                    relationships.append(rel)
    return relationships
```

### 6.4 Temporal Edge Management

When a fact is invalidated (superseded), the corresponding relationship is expired:

```python
def expire_relationship_for_fact(self, fact_id: str) -> None:
    """Mark relationship as expired when its source fact is invalidated."""
    rel = self.store.get_relationship_by_fact(fact_id)
    if rel:
        rel.valid_until = now_iso()
        rel.updated_at = now_iso()
        self.store.update_relationship(rel)
```

---

## 7. Graph Traversal Engine (App-Level Hop-by-Hop)

This is the core of the v2 architecture. **No recursive CTEs.** Each hop is a simple indexed SQL query. Python controls traversal between hops.

### 7.1 `GraphTraverser` Class

```python
class GraphTraverser:
    """App-level hop-by-hop graph traversal engine.

    Each hop = one indexed SQL query.
    Between hops: score, filter, prune.
    """

    DEFAULT_DEPTH = 2
    MAX_DEPTH = 3
    DEFAULT_MIN_WEIGHT = 0.1
    DEFAULT_MAX_FANOUT = 20  # Max edges to follow per hop

    def __init__(self, store: Store):
        self.store = store

    def traverse(
        self,
        seed_entity_ids: List[str],
        depth: int = DEFAULT_DEPTH,
        min_weight: float = DEFAULT_MIN_WEIGHT,
        max_fanout: int = DEFAULT_MAX_FANOUT,
        rel_types: Optional[List[str]] = None,
        direction: str = "both",       # "outbound", "inbound", "both"
        active_only: bool = True,      # Filter by valid_until IS NULL
        at_time: Optional[str] = None, # Temporal query: relationships valid at this time
    ) -> GraphContext:
        """Traverse the graph hop-by-hop from seed entities.

        Args:
            seed_entity_ids: Starting entity IDs (from vector recall match)
            depth: Number of hops (1-3, default 2)
            min_weight: Minimum relationship weight to follow
            max_fanout: Max edges per hop (prevents fan-out explosion)
            rel_types: Optional filter on relationship types
            direction: Traversal direction
            active_only: Only follow currently-active edges
            at_time: Optional temporal filter (ISO 8601)

        Returns:
            GraphContext with discovered entities, relationships, paths, and relevance score.
        """
        depth = min(depth, self.MAX_DEPTH)
        visited_entities: Set[str] = set(seed_entity_ids)
        all_relationships: List[Relationship] = []
        all_entities: Dict[str, Entity] = {}
        paths: List[List[str]] = [[eid] for eid in seed_entity_ids]

        # Load seed entities
        for eid in seed_entity_ids:
            entity = self.store.get_entity(eid)
            if entity:
                all_entities[eid] = entity

        frontier = set(seed_entity_ids)

        for hop_num in range(depth):
            if not frontier:
                break

            # --- HOP: One indexed SQL query ---
            hop_edges = self._hop(
                frontier, direction, rel_types, active_only, at_time
            )

            # --- SCORE: Apply weight-based scoring ---
            scored_edges = self._score(hop_edges, hop_num)

            # --- PRUNE: Filter by weight, limit fanout ---
            surviving_edges = self._prune(
                scored_edges, min_weight, max_fanout
            )

            if not surviving_edges:
                break

            # Collect results
            all_relationships.extend(surviving_edges)

            # Determine next frontier (new entities not yet visited)
            next_frontier: Set[str] = set()
            for edge in surviving_edges:
                for eid in (edge.source_entity_id, edge.target_entity_id):
                    if eid not in visited_entities:
                        next_frontier.add(eid)
                        visited_entities.add(eid)

            # Load newly discovered entities
            for eid in next_frontier:
                entity = self.store.get_entity(eid)
                if entity:
                    all_entities[eid] = entity

            # Extend paths
            new_paths = []
            for edge in surviving_edges:
                for path in paths:
                    tail = path[-1]
                    if tail == edge.source_entity_id and edge.target_entity_id not in path:
                        new_paths.append(path + [edge.target_entity_id])
                    elif tail == edge.target_entity_id and edge.source_entity_id not in path:
                        new_paths.append(path + [edge.source_entity_id])
            if new_paths:
                paths.extend(new_paths)

            frontier = next_frontier

        # Compute aggregate relevance score
        relevance = self._compute_relevance(
            all_relationships, len(seed_entity_ids), depth
        )

        return GraphContext(
            entities=list(all_entities.values()),
            relationships=all_relationships,
            paths=paths,
            relevance_score=relevance,
        )
```

### 7.2 `hop()` -- Single Indexed SQL Query

Each hop is ONE SQL query that uses indexes efficiently:

```python
def _hop(
    self,
    frontier: Set[str],
    direction: str,
    rel_types: Optional[List[str]],
    active_only: bool,
    at_time: Optional[str],
) -> List[Relationship]:
    """Execute one hop: find all edges connected to frontier entities.

    This is a SINGLE indexed SQL query, NOT a recursive CTE.
    """
    placeholders = ",".join("?" * len(frontier))
    params: List[Any] = []
    clauses: List[str] = []

    # Direction filter
    if direction == "outbound":
        clauses.append(f"source_entity_id IN ({placeholders})")
        params.extend(frontier)
    elif direction == "inbound":
        clauses.append(f"target_entity_id IN ({placeholders})")
        params.extend(frontier)
    else:  # both
        clauses.append(
            f"(source_entity_id IN ({placeholders}) OR target_entity_id IN ({placeholders}))"
        )
        params.extend(frontier)
        params.extend(frontier)

    # Active-only filter
    if active_only:
        clauses.append("valid_until IS NULL")

    # Temporal filter
    if at_time:
        clauses.append("valid_from <= ?")
        clauses.append("(valid_until IS NULL OR valid_until >= ?)")
        params.extend([at_time, at_time])

    # Relationship type filter
    if rel_types:
        type_ph = ",".join("?" * len(rel_types))
        clauses.append(f"rel_type IN ({type_ph})")
        params.extend(rel_types)

    where = " AND ".join(clauses)
    query = f"SELECT * FROM relationships WHERE {where} ORDER BY weight DESC"

    rows = self.store._execute(query, params).fetchall()
    return [self.store._row_to_relationship(row) for row in rows]
```

**Exact SQL generated for Hop 1 (outbound, active only):**

```sql
-- Hop 1: Find relationships FROM seed entities
-- Uses idx_rel_active (source_entity_id WHERE valid_until IS NULL)
SELECT * FROM relationships
WHERE source_entity_id IN ('01HQ3K...', '01HQ3L...')
  AND valid_until IS NULL
ORDER BY weight DESC;
```

**Exact SQL generated for Hop 2 (both directions, active only):**

```sql
-- Hop 2: Find relationships connected to surviving entities from hop 1
-- Uses idx_rel_source and idx_rel_target
SELECT * FROM relationships
WHERE (source_entity_id IN ('01HQ3M...', '01HQ3N...', '01HQ3P...')
    OR target_entity_id IN ('01HQ3M...', '01HQ3N...', '01HQ3P...'))
  AND valid_until IS NULL
ORDER BY weight DESC;
```

### 7.3 `score()` -- Weight Scoring Between Hops

```python
def _score(
    self, edges: List[Relationship], hop_num: int
) -> List[Relationship]:
    """Apply hop-distance decay to edge weights.

    Edges farther from seed get diminishing relevance.
    Hop 0 edges: weight * 1.0
    Hop 1 edges: weight * 0.7
    Hop 2 edges: weight * 0.5
    """
    HOP_DECAY = [1.0, 0.7, 0.5]
    decay = HOP_DECAY[min(hop_num, len(HOP_DECAY) - 1)]

    for edge in edges:
        # Store effective weight (original weight * hop decay)
        # We use a transient attribute for scoring; doesn't persist
        edge._effective_weight = edge.weight * decay

    return edges
```

### 7.4 `prune()` -- Filter Low-Relevance Branches

```python
def _prune(
    self,
    edges: List[Relationship],
    min_weight: float,
    max_fanout: int,
) -> List[Relationship]:
    """Prune edges below weight threshold and limit fanout.

    This is the key advantage over recursive CTEs:
    we can apply arbitrary Python logic between hops.
    """
    # Filter by minimum weight
    surviving = [
        e for e in edges
        if getattr(e, "_effective_weight", e.weight) >= min_weight
    ]

    # Sort by effective weight descending
    surviving.sort(
        key=lambda e: getattr(e, "_effective_weight", e.weight),
        reverse=True,
    )

    # Limit fanout to prevent explosion
    return surviving[:max_fanout]
```

### 7.5 `_compute_relevance()` -- Aggregate Graph Relevance Score

```python
def _compute_relevance(
    self,
    relationships: List[Relationship],
    seed_count: int,
    depth: int,
) -> float:
    """Compute aggregate graph relevance score (0.0-1.0).

    Based on:
    - Number of connections found relative to seed count
    - Average edge weight
    - Graph density (connections per entity)
    """
    if not relationships:
        return 0.0

    avg_weight = sum(
        getattr(r, "_effective_weight", r.weight) for r in relationships
    ) / len(relationships)

    # Normalize connection count (diminishing returns)
    connection_factor = min(1.0, len(relationships) / (seed_count * 5))

    return min(1.0, avg_weight * (0.5 + 0.5 * connection_factor))
```

---

## 8. Hybrid Recall Scoring

### 8.1 Integration with Existing Recall

The existing recall pipeline computes: `final_score = cosine_similarity * time_adjusted_importance * tier_weight`

With F1, this becomes: `final_score = cosine_similarity * time_adjusted_importance * tier_weight * graph_boost`

```python
def _recall_local(self, query: str, ..., graph_depth: int = 0) -> List[RecallResult]:
    """Extended recall with optional graph context."""
    # 1. Standard vector recall (unchanged)
    candidates = self._vector_recall(query, ...)

    if graph_depth == 0 or not self._knowledge_graph_enabled:
        return candidates  # No graph augmentation

    # 2. Extract entities from query (lightweight, no LLM needed)
    query_entities = self._find_query_entities(query)
    if not query_entities:
        return candidates  # No entities found, skip graph

    # 3. Find entity IDs for matched entities
    seed_entity_ids = [e.id for e in query_entities]

    # 4. Traverse graph (app-level hop-by-hop)
    graph_context = self.traverser.traverse(
        seed_entity_ids=seed_entity_ids,
        depth=graph_depth,
    )

    # 5. Find additional memories connected via graph
    graph_memory_ids = self._memories_from_graph(graph_context)

    # 6. Merge graph-discovered memories with vector results
    merged = self._merge_results(candidates, graph_memory_ids, graph_context)

    return merged
```

### 8.2 Graph Boost Calculation

```python
def _compute_graph_boost(
    self, memory_id: str, graph_context: GraphContext
) -> float:
    """Compute multiplicative graph boost for a memory.

    Returns 1.0 (no boost) if memory has no graph connections.
    Returns up to 1.5 for strongly connected memories.
    """
    if not graph_context.relationships:
        return 1.0

    # Check if memory is mentioned by any graph entity
    memory_entity_ids = {
        em.entity_id
        for em in self.store.get_entity_mentions_for_memory(memory_id)
    }

    if not memory_entity_ids:
        return 1.0

    graph_entity_ids = {e.id for e in graph_context.entities}
    overlap = memory_entity_ids & graph_entity_ids

    if not overlap:
        return 1.0

    # Boost proportional to graph overlap and relevance
    overlap_ratio = len(overlap) / max(len(memory_entity_ids), 1)
    boost = 1.0 + (overlap_ratio * graph_context.relevance_score * 0.5)

    return min(boost, 1.5)  # Cap at 1.5x
```

### 8.3 Full Scoring Formula

```
final_score = cosine_similarity
            * time_adjusted_importance
            * tier_weight
            * graph_boost

Where:
  cosine_similarity     = dot(query_embedding, memory_embedding) / (||q|| * ||m||)
  time_adjusted_importance = importance_score * 0.5^(age_days / half_life)
  tier_weight           = {working: 1.0, short: 1.1, long: 1.2}
  graph_boost           = 1.0 + (entity_overlap_ratio * graph_relevance * 0.5)
                          capped at 1.5
```

### 8.4 Entity Matching for Queries

```python
def _find_query_entities(self, query: str) -> List[Entity]:
    """Find entities mentioned in a recall query.

    Uses simple substring matching against entity names/aliases.
    No LLM call needed -- this must be fast.
    """
    query_lower = query.lower()
    words = set(query_lower.split())

    # Get all entity names (cached in memory for performance)
    all_entities = self._get_entity_cache()

    matches = []
    for entity in all_entities:
        # Check if entity name appears in query
        if entity.name in query_lower:
            matches.append(entity)
            continue
        # Check aliases
        for alias in entity.aliases:
            if alias in query_lower:
                matches.append(entity)
                break

    return matches
```

---

## 9. Performance

### 9.1 Index Usage Per Hop

| Query Pattern | Index Used | Expected Performance |
|---|---|---|
| Hop outbound from entity set | `idx_rel_active` (source WHERE valid_until IS NULL) | O(edges from frontier), typically < 5ms |
| Hop inbound to entity set | `idx_rel_target` | O(edges to frontier), typically < 5ms |
| Hop both directions | `idx_rel_source` + `idx_rel_target` | Two index scans, < 10ms |
| Entity lookup by name | `idx_entities_name` (UNIQUE) | O(1), < 1ms |
| Entity mentions for memory | `idx_em_memory` | O(mentions), < 1ms |
| Temporal relationship filter | `idx_rel_temporal` | O(log n), < 5ms |

### 9.2 Worst-Case Analysis

For a graph with 10K entities and 50K relationships:

- **Hop 1:** Frontier of ~5 entities -> ~50 edges. One indexed query, < 5ms.
- **Prune:** Filter to top 20 by weight. Pure Python, < 0.1ms.
- **Hop 2:** Frontier of ~20 entities -> ~200 edges. One indexed query, < 10ms.
- **Prune:** Filter to top 20. < 0.1ms.
- **Total traversal:** < 20ms (well under 50ms target).

### 9.3 Entity Cache

To avoid querying all entities on every recall, maintain an in-memory cache:

```python
class EntityCache:
    """In-memory cache of entity names for fast query matching."""

    def __init__(self, store: Store, ttl_seconds: int = 300):
        self.store = store
        self.ttl = ttl_seconds
        self._cache: Optional[List[Entity]] = None
        self._cached_at: float = 0

    def get_all(self) -> List[Entity]:
        now = time.time()
        if self._cache is None or (now - self._cached_at) > self.ttl:
            self._cache = self.store.list_entities()
            self._cached_at = now
        return self._cache

    def invalidate(self) -> None:
        self._cache = None
```

### 9.4 Configurable Depth Limits

```python
# In Lore.__init__():
self._graph_config = {
    "default_depth": 2,       # Default hops for recall
    "max_depth": 3,           # Hard limit
    "min_weight": 0.1,        # Minimum edge weight to follow
    "max_fanout": 20,         # Max edges per hop
    "cache_ttl": 300,         # Entity cache TTL in seconds
    "co_occurrence_weight": 0.3,  # Default weight for co-occurrence edges
}
```

---

## 10. Temporal Edges

### 10.1 Schema

Every relationship has `valid_from` (NOT NULL) and `valid_until` (NULL = active):

```
valid_from          valid_until         Meaning
2024-01-01          NULL                Active since Jan 1 2024
2024-01-01          2024-06-15          Was active Jan-Jun 2024, now expired
```

### 10.2 Temporal Queries

```python
def traverse_at_time(
    self, seed_entity_ids: List[str], at_time: str, depth: int = 2
) -> GraphContext:
    """Traverse the graph as it existed at a specific point in time."""
    return self.traverse(
        seed_entity_ids=seed_entity_ids,
        depth=depth,
        active_only=False,
        at_time=at_time,
    )
```

SQL for temporal filtering:

```sql
-- Find relationships that were active at a specific time
SELECT * FROM relationships
WHERE source_entity_id IN (?, ?, ?)
  AND valid_from <= ?
  AND (valid_until IS NULL OR valid_until >= ?)
ORDER BY weight DESC;
```

### 10.3 Fact Invalidation -> Edge Expiration

When F2 conflict resolution invalidates a fact (SUPERSEDE), the corresponding relationship is expired:

```python
# In ConflictResolver.resolve_all(), after invalidating old fact:
if self.relationship_manager:
    self.relationship_manager.expire_relationship_for_fact(old_fact.id)
```

---

## 11. Store Interface Extensions

### 11.1 New Methods on `Store` Base Class

```python
class Store(ABC):
    # ... existing methods ...

    # ------------------------------------------------------------------
    # Graph storage (default no-op implementations)
    # ------------------------------------------------------------------

    def save_entity(self, entity: Entity) -> None:
        pass

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return None

    def get_entity_by_name(self, name: str) -> Optional[Entity]:
        return None

    def get_entity_by_alias(self, alias: str) -> Optional[Entity]:
        return None

    def update_entity(self, entity: Entity) -> None:
        pass

    def delete_entity(self, entity_id: str) -> None:
        pass

    def list_entities(
        self,
        entity_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Entity]:
        return []

    def save_relationship(self, rel: Relationship) -> None:
        pass

    def get_relationship(self, rel_id: str) -> Optional[Relationship]:
        return None

    def get_active_relationship(
        self, source_id: str, target_id: str, rel_type: str
    ) -> Optional[Relationship]:
        return None

    def get_relationship_by_fact(self, fact_id: str) -> Optional[Relationship]:
        return None

    def update_relationship(self, rel: Relationship) -> None:
        pass

    def get_relationships_from(
        self, entity_ids: List[str], active_only: bool = True
    ) -> List[Relationship]:
        return []

    def get_relationships_to(
        self, entity_ids: List[str], active_only: bool = True
    ) -> List[Relationship]:
        return []

    def save_entity_mention(self, mention: EntityMention) -> None:
        pass

    def get_entity_mentions_for_memory(
        self, memory_id: str
    ) -> List[EntityMention]:
        return []

    def get_entity_mentions_for_entity(
        self, entity_id: str
    ) -> List[EntityMention]:
        return []

    def transfer_entity_mentions(
        self, from_id: str, to_id: str
    ) -> None:
        pass

    def transfer_entity_relationships(
        self, from_id: str, to_id: str
    ) -> None:
        pass
```

---

## 12. MCP Tools

### 12.1 `graph_query` -- Traverse the Knowledge Graph

```python
@mcp.tool()
def graph_query(
    entity: str,
    depth: int = 2,
    rel_types: Optional[str] = None,  # Comma-separated
    direction: str = "both",
    min_weight: float = 0.1,
) -> str:
    """Traverse the knowledge graph from an entity.

    Args:
        entity: Entity name to start traversal from
        depth: Number of hops (1-3, default 2)
        rel_types: Optional comma-separated relationship types to follow
        direction: "outbound", "inbound", or "both"
        min_weight: Minimum edge weight to follow (0.0-1.0)

    Returns:
        Formatted graph traversal results showing entities and relationships.
    """
    lore = _get_lore()
    entity_obj = lore.entity_manager.resolve_by_name(entity)
    if not entity_obj:
        return f"Entity '{entity}' not found."

    type_list = [t.strip() for t in rel_types.split(",")] if rel_types else None

    context = lore.traverser.traverse(
        seed_entity_ids=[entity_obj.id],
        depth=min(depth, 3),
        rel_types=type_list,
        direction=direction,
        min_weight=min_weight,
    )

    return _format_graph_context(context)
```

### 12.2 `related` -- Find Related Memories via Graph

```python
@mcp.tool()
def related(
    query: str,
    limit: int = 5,
    graph_depth: int = 2,
) -> str:
    """Find memories related to query using both vector search and knowledge graph.

    Combines semantic similarity with graph connectivity for richer results.

    Args:
        query: Search query
        limit: Maximum results to return
        graph_depth: Graph traversal depth (0=vector only, 1-3=graph-enhanced)

    Returns:
        Formatted results with similarity scores and graph connections.
    """
    lore = _get_lore()
    results = lore.recall(query, limit=limit, graph_depth=graph_depth)
    return _format_recall_results(results)
```

### 12.3 `entity_map` -- Show Entity Overview

```python
@mcp.tool()
def entity_map(
    entity_type: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Show an overview of entities in the knowledge graph.

    Args:
        entity_type: Optional filter by type (person, tool, project, etc.)
        limit: Maximum entities to show

    Returns:
        Formatted entity list with types, mention counts, and top connections.
    """
    lore = _get_lore()
    entities = lore.store.list_entities(entity_type=entity_type, limit=limit)

    lines = [f"Knowledge Graph: {len(entities)} entities"]
    for e in entities:
        rels = lore.store.get_relationships_from([e.id], active_only=True)
        connections = len(rels)
        aliases_str = f" (aliases: {', '.join(e.aliases)})" if e.aliases else ""
        lines.append(
            f"  [{e.entity_type}] {e.name}{aliases_str} "
            f"- {e.mention_count} mentions, {connections} connections"
        )
    return "\n".join(lines)
```

---

## 13. CLI Commands

### 13.1 `lore graph` -- Graph Traversal

```
Usage: lore graph <entity> [OPTIONS]

Traverse the knowledge graph from an entity.

Arguments:
  entity          Entity name to start from

Options:
  --depth INT     Traversal depth 1-3 (default: 2)
  --type TEXT     Filter relationship types (comma-separated)
  --direction     outbound|inbound|both (default: both)
  --min-weight    Minimum edge weight 0.0-1.0 (default: 0.1)
```

### 13.2 `lore entities` -- List Entities

```
Usage: lore entities [OPTIONS]

List entities in the knowledge graph.

Options:
  --type TEXT     Filter by entity type
  --limit INT    Max results (default: 20)
  --sort TEXT     Sort by: mentions|name|recent (default: mentions)
```

### 13.3 `lore relationships` -- List Relationships

```
Usage: lore relationships [OPTIONS]

List relationships in the knowledge graph.

Options:
  --entity TEXT   Filter by entity name
  --type TEXT     Filter by relationship type
  --limit INT     Max results (default: 20)
  --include-expired  Include expired relationships
```

---

## 14. Integration: Complete Data Flow

### 14.1 `remember()` Flow with Graph

```
User calls: lore.remember("auth-service depends on redis for session caching")

1. Redaction (existing)
2. Classification (F9, existing)
3. Enrichment (F6, existing)
   -> entities: [{"name": "auth-service", "type": "service"},
                 {"name": "redis", "type": "tool"}]
4. Embedding (existing)
5. Save memory to DB (existing)
6. Fact extraction (F2, existing)
   -> facts: [{"subject": "auth-service", "predicate": "depends_on", "object": "redis"}]
7. Conflict resolution (F2, existing)

--- NEW F1 STEPS ---

8. Entity ingestion (from F6 enrichment)
   -> EntityManager.ingest_from_enrichment(memory_id, enrichment_entities)
   -> Creates/updates Entity("auth-service", type="service")
   -> Creates/updates Entity("redis", type="tool")
   -> Creates EntityMention links

9. Relationship ingestion (from F2 facts)
   -> RelationshipManager.ingest_from_fact(memory_id, fact)
   -> Creates Relationship(auth-service -> redis, type="depends_on", weight=0.8)

10. Co-occurrence edges (from F6)
    -> RelationshipManager.ingest_co_occurrences(memory_id, entities)
    -> Creates Relationship(auth-service <-> redis, type="co_occurs_with", weight=0.3)

11. Invalidate entity cache
    -> self.entity_cache.invalidate()
```

### 14.2 `recall()` Flow with Graph

```
User calls: lore.recall("what does auth-service depend on?", graph_depth=2)

1. Embed query (existing)
2. Vector similarity search (existing)
   -> candidates: [RecallResult(memory, score=0.85), ...]

--- NEW F1 STEPS ---

3. Entity matching in query
   -> _find_query_entities("what does auth-service depend on?")
   -> matches: [Entity("auth-service")]

4. Graph traversal (hop-by-hop)
   Hop 0 seeds: ["auth-service" entity ID]

   Hop 1 query:
     SELECT * FROM relationships
     WHERE source_entity_id IN ('01HQ3K...')
       AND valid_until IS NULL
     ORDER BY weight DESC;
   -> edges: [auth-service->redis (0.8), auth-service->postgres (0.7)]

   Score: weight * 1.0 (hop 0 decay)
   Prune: keep edges with weight >= 0.1, limit 20
   -> surviving: [redis, postgres]

   Hop 2 query:
     SELECT * FROM relationships
     WHERE (source_entity_id IN ('redis_id', 'postgres_id')
         OR target_entity_id IN ('redis_id', 'postgres_id'))
       AND valid_until IS NULL
     ORDER BY weight DESC;
   -> edges: [redis->cache-service (0.6), postgres->user-service (0.5)]

   Score: weight * 0.7 (hop 1 decay)
   Prune: keep edges with effective weight >= 0.1
   -> surviving: [cache-service, user-service]

5. Find memories connected to graph entities
   -> entity_mentions for redis, postgres, cache-service, user-service
   -> additional memory IDs

6. Compute graph boost per memory
   -> memories mentioning graph entities get 1.0-1.5x boost

7. Merge and re-rank
   -> final_score = cosine_sim * importance * tier_weight * graph_boost
   -> Return top-k
```

---

## 15. File Layout

```
src/lore/
  graph/
    __init__.py              # Exports: EntityManager, RelationshipManager, GraphTraverser
    entities.py              # EntityManager class (extraction, dedup, aliases)
    relationships.py         # RelationshipManager class (extraction, co-occurrence, temporal)
    traverser.py             # GraphTraverser class (hop, score, prune)
    cache.py                 # EntityCache for fast query matching
  types.py                   # +Entity, +Relationship, +EntityMention, +GraphContext
  store/
    base.py                  # +graph storage methods (no-op defaults)
    sqlite.py                # +graph table implementations
  lore.py                    # +graph_depth param on recall(), graph integration in remember()
  mcp/
    server.py                # +graph_query, +related, +entity_map tools
  cli.py                     # +graph, +entities, +relationships commands
migrations/
  007_knowledge_graph.sql    # New tables: entities, relationships, entity_mentions
```

---

## 16. Testing Strategy

### 16.1 Unit Tests

**`tests/test_graph_entities.py`** -- Entity management:
- `test_normalize_name` -- whitespace, case, punctuation normalization
- `test_resolve_entity_creates_new` -- first mention creates entity
- `test_resolve_entity_dedup_exact` -- same name returns existing entity
- `test_resolve_entity_dedup_alias` -- alias match returns existing entity
- `test_type_promotion` -- concept -> service when more specific type seen
- `test_merge_entities` -- aliases, mentions, relationships transferred
- `test_ingest_from_enrichment` -- F6 entities become graph nodes
- `test_ingest_from_fact` -- F2 subject/object become graph nodes
- `test_mention_count_increments` -- repeated mentions update count

**`tests/test_graph_relationships.py`** -- Relationship management:
- `test_ingest_from_fact_creates_edge` -- SPO triple becomes directed edge
- `test_predicate_mapping` -- depends_on, uses, etc. map correctly
- `test_unknown_predicate_maps_to_related_to` -- fallback
- `test_duplicate_edge_strengthens_weight` -- repeated edges increase weight
- `test_co_occurrence_edges` -- F6 multi-entity creates co_occurs_with
- `test_expire_relationship` -- valid_until set on invalidation
- `test_temporal_edge_lifecycle` -- create active, expire, verify timestamps

**`tests/test_graph_traverser.py`** -- Hop-by-hop traversal:
- `test_single_hop` -- depth=1 finds direct neighbors
- `test_two_hop` -- depth=2 finds neighbors of neighbors
- `test_max_depth_clamped` -- depth=5 clamped to 3
- `test_weight_threshold_prunes` -- low-weight edges filtered
- `test_fanout_limit` -- max_fanout=5 limits per hop
- `test_hop_decay` -- hop 0: 1.0x, hop 1: 0.7x, hop 2: 0.5x
- `test_direction_outbound` -- only follows outbound edges
- `test_direction_inbound` -- only follows inbound edges
- `test_direction_both` -- follows both directions
- `test_empty_graph` -- no entities returns empty GraphContext
- `test_disconnected_entity` -- entity with no edges returns only self
- `test_cycle_detection` -- visited set prevents infinite loops
- `test_temporal_traversal` -- at_time filters by valid_from/valid_until

**`tests/test_graph_scoring.py`** -- Hybrid recall scoring:
- `test_graph_boost_no_overlap` -- returns 1.0 when no graph entities match
- `test_graph_boost_full_overlap` -- maximum boost when all entities overlap
- `test_graph_boost_capped` -- never exceeds 1.5
- `test_recall_with_graph_depth_0` -- identical to v0.5.x behavior
- `test_recall_with_graph_depth_2` -- graph-enhanced results differ from depth 0
- `test_multiplicative_scoring` -- cosine * importance * tier * graph_boost

**`tests/test_graph_store.py`** -- Store operations:
- `test_save_get_entity` -- round-trip entity storage
- `test_get_entity_by_name` -- case-insensitive lookup
- `test_get_entity_by_alias` -- JSON array search
- `test_save_get_relationship` -- round-trip relationship storage
- `test_unique_edge_constraint` -- duplicate active edges rejected
- `test_entity_mention_links` -- memory <-> entity links
- `test_cascade_delete_entity` -- deleting entity cascades to mentions + relationships
- `test_cascade_delete_memory` -- deleting memory cascades to mentions

### 16.2 Integration Tests

**`tests/test_graph_integration.py`**:
- `test_remember_creates_graph` -- full remember() flow creates entities + relationships
- `test_recall_with_graph` -- recall with graph_depth > 0 returns graph-boosted results
- `test_fact_supersede_expires_edge` -- F2 SUPERSEDE invalidation expires relationship
- `test_enrichment_entities_become_nodes` -- F6 entities flow into graph
- `test_graph_disabled_no_overhead` -- knowledge_graph=False skips all graph work

### 16.3 Edge Cases

- Entity name with special characters (Unicode, hyphens, dots)
- Empty graph traversal (no entities, no relationships)
- Self-referencing relationship (entity -> itself)
- Extremely long entity names (truncation/rejection)
- Concurrent entity creation (dedup race condition)
- Memory deletion cascading to graph cleanup
- Large fan-out graph (entity with 1000+ connections)

---

## 17. Configuration

### 17.1 Environment Variables

```
LORE_KNOWLEDGE_GRAPH=true           # Enable/disable graph (default: false)
LORE_GRAPH_DEPTH=2                  # Default traversal depth (1-3)
LORE_GRAPH_MIN_WEIGHT=0.1           # Minimum edge weight to traverse
LORE_GRAPH_MAX_FANOUT=20            # Max edges per hop
LORE_GRAPH_CACHE_TTL=300            # Entity cache TTL in seconds
LORE_GRAPH_CO_OCCURRENCE=true       # Create co-occurrence edges
LORE_GRAPH_CO_OCCURRENCE_WEIGHT=0.3 # Default co-occurrence weight
```

### 17.2 SDK Configuration

```python
lore = Lore(
    knowledge_graph=True,
    graph_depth=2,
    graph_config={
        "min_weight": 0.1,
        "max_fanout": 20,
        "cache_ttl": 300,
        "co_occurrence": True,
        "co_occurrence_weight": 0.3,
    },
)
```

---

## 18. Migration Path from v1 Architecture

The v1 architecture document (`f01-knowledge-graph-architecture.md`) used recursive CTEs. Key changes in v2:

| Aspect | v1 (Recursive CTEs) | v2 (App-Level Hop-by-Hop) |
|---|---|---|
| Traversal | `WITH RECURSIVE` SQL | Python loop + indexed queries |
| Scoring between hops | Not possible | `_score()` between each hop |
| Pruning between hops | Not possible | `_prune()` between each hop |
| Debugging | `EXPLAIN` opaque for recursion | Each hop is a simple query |
| Index usage | Poor inside recursion body | Full index utilization per hop |
| Depth control | SQL LIMIT (fragile) | Python `min(depth, MAX_DEPTH)` |
| Runaway query risk | Possible without strict guards | Impossible (Python controls loop) |
| Performance profile | Unpredictable | Predictable: O(hops * frontier_size) |
| Code complexity | Complex SQL, simple Python | Simple SQL, moderate Python |

---

## Appendix A: Complete Hop-by-Hop Traversal Example

**Scenario:** User asks "What does auth-service depend on?"

```
Step 1: Entity matching
  Query: "what does auth-service depend on?"
  Match: Entity(name="auth-service", id="01ABC")

Step 2: Hop 1 (depth=0)
  SQL: SELECT * FROM relationships
       WHERE source_entity_id = '01ABC'
         AND valid_until IS NULL
       ORDER BY weight DESC;
  Result:
    01ABC -> 01DEF (redis)      | depends_on | weight=0.9
    01ABC -> 01GHI (postgres)   | depends_on | weight=0.8
    01ABC -> 01JKL (kafka)      | uses       | weight=0.6
    01ABC -> 01MNO (alice)      | works_on   | weight=0.3

  Score (hop 0, decay=1.0):
    redis:    0.9 * 1.0 = 0.90
    postgres: 0.8 * 1.0 = 0.80
    kafka:    0.6 * 1.0 = 0.60
    alice:    0.3 * 1.0 = 0.30

  Prune (min_weight=0.1, max_fanout=20):
    All survive (all >= 0.1, count <= 20)

  New frontier: {01DEF, 01GHI, 01JKL, 01MNO}

Step 3: Hop 2 (depth=1)
  SQL: SELECT * FROM relationships
       WHERE (source_entity_id IN ('01DEF','01GHI','01JKL','01MNO')
           OR target_entity_id IN ('01DEF','01GHI','01JKL','01MNO'))
         AND valid_until IS NULL
       ORDER BY weight DESC;
  Result:
    01DEF (redis)    -> 01PQR (cache-layer)   | used_by     | weight=0.7
    01GHI (postgres) -> 01STU (user-service)  | used_by     | weight=0.6
    01JKL (kafka)    -> 01VWX (event-bus)     | part_of     | weight=0.5
    01MNO (alice)    -> 01YZA (team-backend)  | part_of     | weight=0.4
    01DEF (redis)    -> 01BCD (monitoring)    | monitored_by| weight=0.2

  Score (hop 1, decay=0.7):
    cache-layer:   0.7 * 0.7 = 0.49
    user-service:  0.6 * 0.7 = 0.42
    event-bus:     0.5 * 0.7 = 0.35
    team-backend:  0.4 * 0.7 = 0.28
    monitoring:    0.2 * 0.7 = 0.14

  Prune (min_weight=0.1, max_fanout=20):
    All survive

Step 4: Assemble GraphContext
  Entities: auth-service, redis, postgres, kafka, alice, cache-layer, user-service, event-bus, team-backend, monitoring
  Relationships: 9 edges
  Relevance: 0.52

Step 5: Find memories mentioning these entities
  SELECT DISTINCT memory_id FROM entity_mentions
  WHERE entity_id IN ('01ABC','01DEF','01GHI', ...);
  -> [mem_001, mem_002, mem_003, ...]

Step 6: Compute graph boost per memory
  mem_001 mentions auth-service + redis -> overlap_ratio=0.67 -> boost=1.17
  mem_002 mentions postgres             -> overlap_ratio=0.50 -> boost=1.13
  mem_003 mentions kafka + event-bus    -> overlap_ratio=1.00 -> boost=1.26

Step 7: Final scoring
  mem_001: cosine=0.85 * importance=0.9 * tier=1.2 * graph_boost=1.17 = 1.073
  mem_002: cosine=0.72 * importance=0.8 * tier=1.1 * graph_boost=1.13 = 0.716
  mem_003: cosine=0.60 * importance=0.7 * tier=1.0 * graph_boost=1.26 = 0.529
```
