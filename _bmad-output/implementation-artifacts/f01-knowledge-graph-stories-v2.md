# F1 -- Knowledge Graph Layer: User Stories (v2)

**Feature:** F1 -- Knowledge Graph Layer
**Version:** v0.6.0 ("Open Brain")
**Sprint Planning Date:** 2026-03-06
**Architecture Doc:** `_bmad-output/implementation-artifacts/f01-knowledge-graph-architecture-v2.md`
**PRD:** `_bmad-output/planning-artifacts/f01-knowledge-graph-prd.md`
**Key Design:** App-level hop-by-hop traversal (NO recursive CTEs)

---

## Sprint Overview

| Sprint | Theme | Stories | Focus |
|--------|-------|---------|-------|
| **Sprint 1** | Foundation | S1-S5 | Schema, types, normalization, entity/relationship CRUD, mentions |
| **Sprint 2** | Core Traversal | S6-S10 | GraphTraverser engine, hop/score/prune, temporal, entity cache |
| **Sprint 3** | Hybrid Recall + Integration | S11-S13 | Multiplicative scoring, F2 fact integration, F6 enrichment integration |
| **Sprint 4** | Surface + Polish | S14-S15 | MCP tools, CLI commands, visualization, backfill |

**Critical path:** S1 -> S3 -> S4 -> S6 -> S7 -> S8 -> S9 -> S11

**Parallelism notes:**
- Sprint 1 (S1-S5): S2 depends on S1 only for types; S3 depends on S1+S2; S4 depends on S1+S3; S5 depends on S3+S4. S1 and S2 can begin in parallel once S1 types are defined.
- Sprint 2 (S6-S10): Sequential -- each builds on the prior.
- Sprint 3 (S11-S13): S12 and S13 can run in parallel once S11 is complete.
- Sprint 4 (S14-S15): All stories can run in parallel once Sprint 3 core is done.

---

## Sprint 1: Foundation

### S1: Schema, Types & Migrations

**As a** developer,
**I want** the knowledge graph schema (entities, relationships, entity_mentions tables) defined as Python dataclasses and SQL tables with proper indexes,
**so that** all subsequent graph stories have a stable data foundation.

**Estimate:** M

**Dependencies:** None

**Scope:**
- Add `Entity`, `Relationship`, `EntityMention`, `GraphContext` dataclasses to `src/lore/types.py` per architecture doc section 3.1
- Add `VALID_ENTITY_TYPES` and `VALID_REL_TYPES` tuples
- Create `entities` table in SqliteStore with indexes: `idx_entities_name` (UNIQUE), `idx_entities_type`, `idx_entities_mention_count`
- Create `relationships` table with indexes: `idx_rel_source`, `idx_rel_target`, `idx_rel_active` (partial: WHERE valid_until IS NULL), `idx_rel_type`, `idx_rel_unique_edge` (UNIQUE partial), `idx_rel_temporal`
- Create `entity_mentions` table with indexes: `idx_em_entity`, `idx_em_memory`, `idx_em_unique` (UNIQUE)
- Create `migrations/007_knowledge_graph.sql` for Postgres with all three tables and all indexes (idempotent with IF NOT EXISTS)
- SQLite tables created lazily via `_maybe_create_graph_tables()` only when `knowledge_graph=True`
- Create `src/lore/graph/__init__.py` module skeleton
- Add `knowledge_graph` config param to `Lore.__init__()` + env var `LORE_KNOWLEDGE_GRAPH` (default: False)
- Add default no-op graph method stubs to `Store` base class in `src/lore/store/base.py` (per architecture doc section 11.1)

**Acceptance Criteria:**

```
GIVEN knowledge_graph=True in Lore config
WHEN SqliteStore initializes
THEN entities, relationships, and entity_mentions tables are created with all specified indexes

GIVEN knowledge_graph=False (default)
WHEN SqliteStore initializes
THEN no graph tables are created

GIVEN the Postgres migration 007_knowledge_graph.sql
WHEN run against a Postgres database
THEN all three tables and all indexes are created idempotently (safe to run multiple times)

GIVEN the new dataclasses
WHEN importing from src/lore/types.py
THEN Entity, Relationship, EntityMention, GraphContext are available with all fields matching architecture doc section 3.1

GIVEN a Store subclass that does not override graph methods
WHEN any graph method is called
THEN default no-op implementations return empty lists/None without error
```

---

### S2: Entity Name Normalization & Alias Resolution

**As a** developer,
**I want** a deterministic name normalization algorithm for entity names,
**so that** "PostgreSQL 16", "postgres", and "  React.js  " all normalize to consistent canonical forms.

**Estimate:** S

**Dependencies:** S1 (types only)

**Scope:**
- Create `src/lore/graph/entities.py` with `EntityManager` class containing `_normalize_name()` method
- Normalization steps: strip whitespace, lowercase, collapse multiple spaces, strip trailing punctuation (`.,;:!?`)
- No builtin alias map in normalization itself -- aliases are managed at the entity level (architecture doc section 5.1)
- Tests in `tests/test_graph_entities.py` for normalization edge cases

**Acceptance Criteria:**

```
GIVEN the name "  PostgreSQL 16  "
WHEN _normalize_name() is called
THEN it returns "postgresql 16" (stripped + lowered)

GIVEN the name "  React.js  "
WHEN _normalize_name() is called
THEN it returns "react.js" (stripped + lowered, trailing punctuation stripped only for .,;:!?)

GIVEN the name "k8s"
WHEN _normalize_name() is called
THEN it returns "k8s" (no builtin alias map -- aliases are entity-level)

GIVEN the name "My   Custom   Service."
WHEN _normalize_name() is called
THEN it returns "my custom service" (collapsed spaces, trailing punctuation stripped)

GIVEN the name "alice"
WHEN _normalize_name() is called
THEN it returns "alice" (already canonical)

GIVEN an empty string
WHEN _normalize_name() is called
THEN it returns "" (no error)
```

---

### S3: Entity CRUD & Deduplication

**As a** developer,
**I want** full entity create/read/update/delete operations with deduplication via name and alias matching,
**so that** entities can be managed and deduplicated at write time.

**Estimate:** M

**Dependencies:** S1 (schema + types), S2 (normalization)

**Scope:**
- Implement `EntityManager._resolve_entity(name, entity_type)` with 3-step resolution per architecture doc section 5.2:
  1. Exact match on canonical name via `store.get_entity_by_name()`
  2. Alias match via `store.get_entity_by_alias()` using SQLite `json_each(aliases)` per section 5.4
  3. Create new entity with ULID id
- Type promotion: if incoming type is more specific than "concept" and existing is "concept", promote
- Implement in `SqliteStore`: `save_entity()`, `get_entity()`, `get_entity_by_name()`, `get_entity_by_alias()`, `update_entity()`, `delete_entity()`, `list_entities()`
- Implement `EntityManager.add_alias()` per architecture doc section 5.3
- Implement `EntityManager.merge_entities()` -- redirect relationships, transfer mentions, union aliases, sum mention counts, delete source (per architecture doc section 5.3)
- Implement `store.transfer_entity_mentions()` and `store.transfer_entity_relationships()` for merge support
- MemoryStore implementations for all entity operations (in-memory dict)
- Tests for all CRUD + dedup + merge paths

**Acceptance Criteria:**

```
GIVEN _resolve_entity("auth-service", "service") called for the first time
WHEN the entity does not exist
THEN a new Entity is created with name="auth-service", entity_type="service", ULID id, mention_count=1

GIVEN an existing entity with name="auth-service", entity_type="concept"
WHEN _resolve_entity("auth-service", "service") is called
THEN the existing entity is returned with entity_type promoted to "service"

GIVEN an entity with aliases=["k8s", "kube"]
WHEN get_entity_by_alias("k8s") is called
THEN the entity is returned via JSON array search

GIVEN two entities A (3 mentions) and B (2 mentions) for the same concept
WHEN merge_entities(keep_id=A.id, merge_id=B.id) is called
THEN entity A has mention_count=5, aliases include B's name and aliases, B is deleted, B's relationships now point to A

GIVEN delete_entity(entity_id) where the entity has relationships
WHEN called
THEN the entity and all its relationships and mentions are deleted (CASCADE)
```

---

### S4: Relationship CRUD & Temporal Tracking

**As a** developer,
**I want** full relationship CRUD with temporal `valid_from`/`valid_until` tracking and weight strengthening on repeated confirmation,
**so that** edges between entities reflect current state and strengthen with evidence.

**Estimate:** M

**Dependencies:** S1 (schema), S3 (entities exist to reference)

**Scope:**
- Create `src/lore/graph/relationships.py` with `RelationshipManager` class
- Implement `RelationshipManager.ingest_from_fact(memory_id, fact)` per architecture doc section 6.1:
  - Resolve subject + object entities via `EntityManager`
  - Map predicate via `_map_predicate()` using `PREDICATE_TO_REL_TYPE` dict (architecture doc section 6.2)
  - If active edge exists for (source, target, rel_type): strengthen weight by +0.1 (capped at 1.0)
  - Otherwise: create new relationship with weight = fact.confidence, valid_from = now, valid_until = NULL
- Implement in `SqliteStore`: `save_relationship()`, `get_relationship()`, `get_active_relationship()`, `get_relationship_by_fact()`, `update_relationship()`, `get_relationships_from()`, `get_relationships_to()`
- `idx_rel_unique_edge` prevents duplicate active edges (UNIQUE on source+target+rel_type WHERE valid_until IS NULL)
- Implement `RelationshipManager.expire_relationship_for_fact(fact_id)` per architecture doc section 6.4
- MemoryStore implementations for all relationship operations
- Tests for CRUD + strengthening + temporal close + predicate mapping

**Acceptance Criteria:**

```
GIVEN a new fact ("auth-service", "depends_on", "redis") with confidence 0.85
WHEN ingest_from_fact() is called
THEN a Relationship is created: source=auth-service, target=redis, rel_type="depends_on", weight=0.85, valid_from=now, valid_until=NULL

GIVEN an existing active relationship with weight=0.85
WHEN ingest_from_fact() is called with the same (source, target, rel_type)
THEN weight becomes 0.95 (0.85 + 0.1)

GIVEN a relationship at weight=0.95
WHEN ingest_from_fact() strengthens it again
THEN weight becomes 1.0 (capped, not 1.05)

GIVEN the fact predicate "uses"
WHEN _map_predicate("uses") is called
THEN it returns "uses"

GIVEN an unknown predicate "invented_by"
WHEN _map_predicate("invented_by") is called
THEN it returns "related_to" (fallback)

GIVEN an active relationship sourced from fact_id="abc123"
WHEN expire_relationship_for_fact("abc123") is called
THEN the relationship's valid_until is set to current timestamp

GIVEN get_relationships_from([entity_id], active_only=True)
WHEN there are both active and expired relationships from that entity
THEN only relationships with valid_until IS NULL are returned
```

---

### S5: Entity-Memory Mentions & Junction Tracking

**As a** developer,
**I want** a junction table linking entities to the memories that mention them with mention type and confidence tracking,
**so that** I can find all memories for an entity and all entities for a memory.

**Estimate:** S

**Dependencies:** S3 (entity CRUD)

**Scope:**
- Implement in `SqliteStore`: `save_entity_mention()`, `get_entity_mentions_for_memory()`, `get_entity_mentions_for_entity()`
- `EntityMention` has `mention_type` ("explicit" from F6, "inferred" from F2) and `confidence` fields
- Idempotent via `idx_em_unique` (entity_id, memory_id) -- INSERT OR IGNORE
- MemoryStore implementations
- Tests for junction operations, idempotency, and bidirectional lookups

**Acceptance Criteria:**

```
GIVEN entity E1 and memory M1
WHEN save_entity_mention(EntityMention(entity_id=E1.id, memory_id=M1.id, mention_type="explicit")) is called
THEN a mention row is created with the current timestamp

GIVEN a mention for (E1, M1) already exists
WHEN save_entity_mention with the same (entity_id, memory_id) is called again
THEN no error occurs and no duplicate row is created (idempotent via UNIQUE index)

GIVEN entity E1 mentioned in memories M1, M2, M3
WHEN get_entity_mentions_for_entity(E1.id) is called
THEN 3 EntityMention objects are returned

GIVEN memory M1 mentioning entities E1, E2
WHEN get_entity_mentions_for_memory(M1.id) is called
THEN 2 EntityMention objects are returned

GIVEN entity E1 deleted (CASCADE)
WHEN get_entity_mentions_for_entity(E1.id) is called
THEN 0 mentions are returned (cascaded delete)
```

---

## Sprint 2: Core Traversal Engine

### S6: GraphTraverser Class -- Core Engine

**As a** developer,
**I want** a `GraphTraverser` class that implements app-level hop-by-hop traversal with `traverse()`, `_hop()`, `_score()`, `_prune()` methods,
**so that** multi-hop graph queries are executed via simple indexed SQL queries with Python-controlled depth, scoring, and pruning between hops.

**Estimate:** L

**Dependencies:** S3 (entities), S4 (relationships), S5 (mentions)

**Scope:**
- Create `src/lore/graph/traverser.py` with `GraphTraverser` class per architecture doc section 7.1
- `traverse()` method: takes `seed_entity_ids`, `depth` (default 2, max 3), `min_weight`, `max_fanout`, `rel_types`, `direction`, `active_only`, `at_time`
- Loop `depth` times: `_hop()` -> `_score()` -> `_prune()` -> update frontier
- Track `visited_entities` set for cycle prevention
- Collect entities, relationships, paths; compute aggregate `relevance_score`
- Returns `GraphContext` dataclass
- **NO recursive CTEs** -- each hop is a single indexed SQL query
- Frontier-based: start with seed entity IDs, expand outward
- Constants: `DEFAULT_DEPTH=2`, `MAX_DEPTH=3`, `DEFAULT_MIN_WEIGHT=0.1`, `DEFAULT_MAX_FANOUT=20`
- Tests in `tests/test_graph_traverser.py` with a known test graph: `A --uses--> B --depends_on--> C --deployed_on--> D; A --works_with--> E; E --manages--> F; F --uses--> B`

**Acceptance Criteria:**

```
GIVEN the test graph (A->B->C->D, A->E->F->B)
WHEN traverse(seed=[A.id], depth=1) is called
THEN GraphContext contains entities B and E (direct neighbors only)
AND exactly the edges A->B and A->E are in relationships

GIVEN the test graph
WHEN traverse(seed=[A.id], depth=2) is called
THEN GraphContext contains entities B, C, E, F (two hops)
AND paths include [A,B,C] and [A,E,F]

GIVEN traverse(seed=[A.id], depth=5)
WHEN called
THEN depth is clamped to 3 (MAX_DEPTH)

GIVEN the test graph with cycle (F->B, and B already visited)
WHEN traverse(seed=[A.id], depth=3) is called
THEN B is NOT re-added to frontier from F (visited set prevents cycles)
AND D is reached via C

GIVEN an entity with no edges
WHEN traverse(seed=[lonely.id], depth=2) is called
THEN GraphContext has entities=[lonely], relationships=[], relevance_score=0.0

GIVEN traverse() returns a GraphContext
WHEN relevance_score is inspected
THEN it is a float between 0.0 and 1.0
```

---

### S7: Hop Query Builder -- Single Indexed SQL Per Hop

**As a** developer,
**I want** the `_hop()` method to execute a single indexed SQL query per traversal step, supporting outbound, inbound, and bidirectional edge discovery,
**so that** each hop uses database indexes efficiently and generates predictable, debuggable SQL.

**Estimate:** M

**Dependencies:** S6 (GraphTraverser class structure)

**Scope:**
- Implement `GraphTraverser._hop(frontier, direction, rel_types, active_only, at_time)` per architecture doc section 7.2
- Direction="outbound": `WHERE source_entity_id IN (?)` -- uses `idx_rel_active` or `idx_rel_source`
- Direction="inbound": `WHERE target_entity_id IN (?)` -- uses `idx_rel_target`
- Direction="both": `WHERE (source_entity_id IN (?) OR target_entity_id IN (?))`
- Active-only filter: `AND valid_until IS NULL`
- Temporal filter: `AND valid_from <= ? AND (valid_until IS NULL OR valid_until >= ?)`
- Relationship type filter: `AND rel_type IN (?)`
- Results ordered by `weight DESC`
- **Explicit: NO recursive CTEs, NO subqueries, NO JOINs** -- just a flat SELECT with WHERE clauses
- Tests verifying correct SQL generation for each direction and filter combination

**Acceptance Criteria:**

```
GIVEN a frontier of [entity_A, entity_B] and direction="outbound"
WHEN _hop() is called with active_only=True
THEN SQL executed is: SELECT * FROM relationships WHERE source_entity_id IN (?,?) AND valid_until IS NULL ORDER BY weight DESC
AND only outbound edges from A and B are returned

GIVEN direction="inbound"
WHEN _hop() is called
THEN SQL queries target_entity_id IN (...) and returns only inbound edges

GIVEN direction="both"
WHEN _hop() is called
THEN SQL queries both source_entity_id and target_entity_id with OR

GIVEN rel_types=["uses", "depends_on"]
WHEN _hop() is called
THEN SQL includes AND rel_type IN (?,?) filtering

GIVEN active_only=False and at_time="2025-06-15T00:00:00Z"
WHEN _hop() is called
THEN SQL includes valid_from <= ? AND (valid_until IS NULL OR valid_until >= ?)

GIVEN an empty frontier
WHEN _hop() is called
THEN an empty list is returned without executing a query
```

---

### S8: Score & Prune -- Weight Decay and Branch Filtering

**As a** developer,
**I want** `_score()` to apply hop-distance decay to edge weights and `_prune()` to filter low-relevance branches and limit fanout,
**so that** farther hops contribute less relevance and the traversal doesn't explode on highly-connected graphs.

**Estimate:** M

**Dependencies:** S6 (GraphTraverser class structure)

**Scope:**
- Implement `GraphTraverser._score(edges, hop_num)` per architecture doc section 7.3
  - Hop decay factors: hop 0 = 1.0, hop 1 = 0.7, hop 2 = 0.5
  - Set transient `_effective_weight = weight * decay` on each edge
- Implement `GraphTraverser._prune(edges, min_weight, max_fanout)` per architecture doc section 7.4
  - Filter edges where `_effective_weight < min_weight`
  - Sort by effective weight descending
  - Limit to `max_fanout` edges
- Implement `GraphTraverser._compute_relevance(relationships, seed_count, depth)` per architecture doc section 7.5
  - Based on avg effective weight and connection density
  - Returns float in [0.0, 1.0]
- Tests for decay factors, pruning thresholds, fanout limits, and relevance computation

**Acceptance Criteria:**

```
GIVEN edges with weights [0.9, 0.7, 0.5] at hop_num=0
WHEN _score() is called
THEN effective weights are [0.9, 0.7, 0.5] (decay=1.0)

GIVEN edges with weights [0.9, 0.7, 0.5] at hop_num=1
WHEN _score() is called
THEN effective weights are [0.63, 0.49, 0.35] (decay=0.7)

GIVEN edges with weights [0.9, 0.7, 0.5] at hop_num=2
WHEN _score() is called
THEN effective weights are [0.45, 0.35, 0.25] (decay=0.5)

GIVEN scored edges with effective weights [0.45, 0.35, 0.08, 0.05] and min_weight=0.1
WHEN _prune() is called
THEN edges with effective weight 0.08 and 0.05 are removed

GIVEN 30 scored edges and max_fanout=20
WHEN _prune() is called
THEN only the top 20 by effective weight are returned

GIVEN relationships with mixed effective weights and seed_count=2
WHEN _compute_relevance() is called
THEN a float between 0.0 and 1.0 is returned

GIVEN no relationships (empty traversal)
WHEN _compute_relevance() is called
THEN 0.0 is returned
```

---

### S9: Temporal Edge Support

**As a** developer,
**I want** the traversal engine to support temporal queries via `at_time` parameter and edge expiration via `expire_relationship_for_fact()`,
**so that** users can query the graph as it existed at a specific point in time and fact invalidation properly closes edges.

**Estimate:** S

**Dependencies:** S4 (relationship CRUD with valid_from/valid_until), S7 (hop query builder with at_time filter)

**Scope:**
- Implement `GraphTraverser.traverse_at_time(seed_entity_ids, at_time, depth)` convenience method per architecture doc section 10.2
  - Delegates to `traverse()` with `active_only=False, at_time=at_time`
- Verify `_hop()` correctly filters: `valid_from <= at_time AND (valid_until IS NULL OR valid_until >= at_time)`
- Verify `idx_rel_temporal` index is used for temporal range queries
- Integration with F2 ConflictResolver: when SUPERSEDE invalidates a fact, call `RelationshipManager.expire_relationship_for_fact(old_fact_id)` per architecture doc section 10.3
- Tests for temporal traversal scenarios

**Acceptance Criteria:**

```
GIVEN a relationship valid_from="2025-01-01" and valid_until="2025-06-15"
WHEN traverse_at_time(at_time="2025-03-01") is called
THEN the relationship IS included (active at that time)

GIVEN a relationship valid_from="2025-01-01" and valid_until="2025-06-15"
WHEN traverse_at_time(at_time="2025-12-01") is called
THEN the relationship is NOT included (expired before that time)

GIVEN a relationship valid_from="2025-07-01" and valid_until=NULL
WHEN traverse_at_time(at_time="2025-03-01") is called
THEN the relationship is NOT included (not yet valid at that time)

GIVEN default traverse() with active_only=True
WHEN a relationship has valid_until set (expired)
THEN the expired relationship is NOT followed

GIVEN a fact superseded by F2 conflict resolution
WHEN expire_relationship_for_fact(old_fact_id) is called
THEN the corresponding relationship's valid_until is set to current timestamp
AND subsequent active_only=True traversals skip this edge
```

---

### S10: Entity Cache for Fast Query Matching

**As a** developer,
**I want** an in-memory `EntityCache` with TTL that caches all entity names and aliases,
**so that** `_find_query_entities()` can match entities in recall queries without hitting the database on every call.

**Estimate:** S

**Dependencies:** S3 (entity CRUD, list_entities)

**Scope:**
- Create `src/lore/graph/cache.py` with `EntityCache` class per architecture doc section 9.3
- `get_all()` returns cached `List[Entity]`, refreshes if `ttl_seconds` (default 300) elapsed
- `invalidate()` clears cache (called after entity mutations in remember())
- Implement `_find_query_entities(query)` on `EntityManager` or `GraphTraverser` per architecture doc section 8.4
  - Lowercases query, checks each entity's `name` and `aliases` for substring match
  - No LLM call -- must be fast (< 1ms for 10K entities from cache)
- Tests for cache hit/miss/TTL/invalidation and entity matching logic

**Acceptance Criteria:**

```
GIVEN an EntityCache with ttl_seconds=300
WHEN get_all() is called twice within 300 seconds
THEN the database is queried only once (cache hit)

GIVEN an EntityCache past its TTL
WHEN get_all() is called
THEN the database is queried again (cache refresh)

GIVEN invalidate() is called
WHEN get_all() is called next
THEN the database is queried again regardless of TTL

GIVEN entities ["auth-service", "redis", "postgresql"] in cache
WHEN _find_query_entities("what does auth-service depend on?") is called
THEN Entity("auth-service") is returned

GIVEN entity "kubernetes" with aliases=["k8s"]
WHEN _find_query_entities("k8s cluster issues") is called
THEN Entity("kubernetes") is returned via alias match

GIVEN query "how do I fix this bug?"
WHEN _find_query_entities() is called and no entity names match
THEN an empty list is returned
```

---

## Sprint 3: Hybrid Recall & Integration

### S11: Hybrid Recall Scoring -- Multiplicative Graph Boost

**As a** developer,
**I want** `recall()` to apply a multiplicative graph boost when `graph_depth > 0`,
**so that** memories connected via the knowledge graph get boosted scores while `graph_depth=0` remains identical to v0.5.x.

**Estimate:** L

**Dependencies:** S6 (traversal engine), S10 (entity cache + query entity matching)

**Scope:**
- Extend `Lore.recall()` signature with `graph_depth` (default 0), per architecture doc section 8.1
- When `graph_depth > 0` and `knowledge_graph=True`:
  1. Run standard vector recall (unchanged)
  2. Call `_find_query_entities(query)` to identify entities in query
  3. If entities found: traverse graph via `GraphTraverser.traverse(seed_entity_ids, depth=graph_depth)`
  4. Find additional memory IDs via entity_mentions for discovered entities
  5. Compute `graph_boost` per memory via `_compute_graph_boost()` per architecture doc section 8.2
  6. Apply multiplicative scoring: `final_score = cosine_similarity * time_adjusted_importance * tier_weight * graph_boost`
  7. Merge graph-discovered memories with vector results, re-rank
- `graph_boost` range: 1.0 (no boost) to 1.5 (max boost), per architecture doc section 8.2
- `graph_depth=0` (default): zero additional cost, identical to v0.5.x -- **no graph queries at all**
- Add `graph_depth` config params: `LORE_GRAPH_DEPTH` (default), `LORE_GRAPH_MAX_DEPTH` (hard limit)
- Tests in `tests/test_graph_scoring.py`

**Acceptance Criteria:**

```
GIVEN recall("auth-service dependencies", graph_depth=2) with graph containing auth-service -> redis
WHEN "auth-service" matches as a known entity
THEN memories mentioning redis are included via graph traversal (even if vector cosine is low)

GIVEN a memory appearing in BOTH vector results (cosine=0.85) and graph results (entity overlap)
WHEN graph_boost is computed
THEN final_score = 0.85 * importance * tier_weight * graph_boost (multiplicative, NOT additive)

GIVEN a memory with no graph entity overlap
WHEN graph_boost is computed
THEN graph_boost = 1.0 (no effect on score)

GIVEN graph_boost computation with full entity overlap and high relevance
WHEN computed
THEN graph_boost is capped at 1.5

GIVEN recall(query, graph_depth=0)
WHEN called
THEN behavior is identical to v0.5.x (no graph queries, no graph scoring, no overhead)

GIVEN knowledge_graph=False
WHEN recall(query, graph_depth=2) is called
THEN graph_depth is ignored, behavior is identical to v0.5.x
```

---

### S12: F2 Integration -- SPO Triples to Entities + Edges

**As a** developer,
**I want** `remember()` to automatically convert F2 fact triples (subject, predicate, object) into graph entities and edges,
**so that** every extracted fact contributes to the knowledge graph.

**Estimate:** M

**Dependencies:** S3 (entity CRUD), S4 (relationship CRUD), S5 (mentions)

**Scope:**
- Create `src/lore/graph/extraction.py` with `_update_graph_from_facts(memory_id, facts, entity_manager, relationship_manager)` function
- For each fact: call `RelationshipManager.ingest_from_fact(memory_id, fact)` which resolves entities + creates edge
- Apply `graph_confidence_threshold` (default 0.5) -- skip facts below threshold
- Skip invalidated facts (`fact.invalidated_by` is set)
- Handle F2 SUPERSEDE events: when ConflictResolver supersedes a fact, call `expire_relationship_for_fact(old_fact_id)`
- Hook into `Lore.remember()` after fact extraction; wrap in try/except (failures logged, memory still saved)
- Co-occurrence edges: `RelationshipManager.ingest_co_occurrences(memory_id, entities, weight=0.3)` per architecture doc section 6.3
- Add `graph_confidence_threshold` config param + env var `LORE_GRAPH_CONFIDENCE_THRESHOLD`
- Add `LORE_GRAPH_CO_OCCURRENCE` and `LORE_GRAPH_CO_OCCURRENCE_WEIGHT` config
- Tests for fact -> edge conversion, threshold filtering, supersession handling, co-occurrence

**Acceptance Criteria:**

```
GIVEN knowledge_graph=True and a fact ("auth-service", "depends_on", "redis") with confidence 0.9
WHEN remember() processes the fact
THEN entities "auth-service" and "redis" are upserted, a relationship (auth-service --depends_on--> redis) is created with weight=0.9

GIVEN a fact with confidence 0.3 and graph_confidence_threshold=0.5
WHEN the graph update processes the fact
THEN no graph edge is created (below threshold)

GIVEN an invalidated fact (fact.invalidated_by is set)
WHEN the graph update processes facts
THEN the invalidated fact is skipped

GIVEN a prior fact ("auth-service", "uses", "mysql") and a new SUPERSEDE fact ("auth-service", "uses", "postgresql")
WHEN conflict resolution supersedes the old fact
THEN the (auth-service --uses--> mysql) edge gets valid_until set
AND a new (auth-service --uses--> postgresql) edge is created with valid_until=NULL

GIVEN 3 entities extracted from the same memory with co_occurrence=True
WHEN co-occurrence processing runs
THEN 6 co_occurs_with edges are created (3 pairs * 2 directions) with default weight=0.3

GIVEN knowledge_graph=True and the graph update fails with an exception
WHEN remember() runs
THEN the memory is still saved successfully and a warning is logged
```

---

### S13: F6 Integration -- Enrichment Entities to Graph Nodes

**As a** developer,
**I want** F6 enrichment entities to be automatically promoted to graph nodes via `EntityManager.ingest_from_enrichment()`,
**so that** every entity identified by enrichment becomes a first-class graph node with mention tracking.

**Estimate:** M

**Dependencies:** S3 (entity CRUD), S5 (mention tracking)

**Scope:**
- Implement `EntityManager.ingest_from_enrichment(memory_id, enrichment_entities)` per architecture doc section 4.1
- For each enrichment entity: normalize name, resolve/create entity, create EntityMention with type="explicit", update mention_count + last_seen_at
- Handle F6 disabled gracefully (enrichment is None or empty dict)
- Hook into `Lore.remember()` after enrichment step, before fact extraction
- Entity type vocabulary: person, tool, project, concept, organization, platform, language, framework, service, other
- Invalidate entity cache after ingestion
- Tests for F6 -> graph flow, empty enrichment, type handling

**Acceptance Criteria:**

```
GIVEN enrichment data {"entities": [{"name": "Alice", "type": "person"}, {"name": "Kubernetes", "type": "platform"}]}
WHEN EntityManager.ingest_from_enrichment(memory_id, entities) is called
THEN entities "alice" (person) and "kubernetes" (platform) exist in the entities table
AND EntityMention rows link each entity to the memory with mention_type="explicit"

GIVEN the same enrichment entity "redis" mentioned across two memories
WHEN both memories are processed
THEN entity "redis" has mention_count=2 and two entity_mention rows

GIVEN F6 is disabled (enrichment is None or empty)
WHEN the graph update runs
THEN no entities are created from enrichment (no error)

GIVEN enrichment entity with type not in VALID_ENTITY_TYPES
WHEN processed
THEN entity_type defaults to "other"

GIVEN enrichment processing completes
WHEN entity cache is checked
THEN cache has been invalidated (fresh data on next query)
```

---

## Sprint 4: Surface & Polish

### S14: MCP Tools + CLI Commands + Visualization

**As a** developer,
**I want** MCP tools (graph_query, related, entity_map), CLI commands (graph, entities, relationships), and D3-compatible JSON visualization,
**so that** agents and users can query and explore the knowledge graph through all interfaces.

**Estimate:** L

**Dependencies:** S6 (traversal), S11 (hybrid recall)

**Scope:**

**MCP Tools** (in `src/lore/mcp/server.py` per architecture doc section 12):
- `graph_query(entity, depth, rel_types, direction, min_weight)` -- traverse from entity, return formatted results
- `related(query, limit, graph_depth)` -- recall with graph-enhanced scoring
- `entity_map(entity_type, limit)` -- list entities with connection counts
- All tools return graceful "Knowledge graph is not enabled" when `knowledge_graph=False`

**CLI Commands** (in `src/lore/cli.py` per architecture doc section 13):
- `lore graph <entity> --depth --type --direction --min-weight` -- graph traversal
- `lore entities --type --limit --sort` -- list entities
- `lore relationships --entity --type --limit --include-expired` -- list relationships

**Visualization** (in `src/lore/graph/visualization.py`):
- `to_d3_json(graph_context)` -- D3 force-graph compatible JSON: `{"nodes": [...], "links": [...]}`
- `to_text_tree(graph_context)` -- indented ASCII tree
- Used by MCP entity_map (format param) and CLI --format flag

- Tests for MCP tool registration, CLI argument parsing, output formatting, D3 JSON structure

**Acceptance Criteria:**

```
GIVEN knowledge_graph=True and entities in the graph
WHEN graph_query("auth-service", depth=2) MCP tool is called
THEN a formatted response with connected entities and relationship types is returned

GIVEN knowledge_graph=False
WHEN any graph MCP tool is called
THEN a message "Knowledge graph is not enabled" is returned (not an error)

GIVEN entities in the graph
WHEN `lore entities --type person --sort mentions` is run
THEN a table of person entities sorted by mention_count descending is displayed

GIVEN a populated graph
WHEN `lore graph "auth-service" --depth 2` is run
THEN a tree visualization of entities within 2 hops is displayed

GIVEN a GraphContext with entities and relationships
WHEN to_d3_json() is called
THEN output is {"nodes": [...], "links": [...]} where every link.source and link.target references a valid node.id

GIVEN an empty GraphContext
WHEN to_d3_json() is called
THEN {"nodes": [], "links": []} is returned

GIVEN a GraphContext
WHEN to_text_tree() is called
THEN an indented ASCII tree showing entity names, types, and relationship labels is returned
```

---

### S15: Graph Backfill & Cascade on forget()

**As a** developer,
**I want** a backfill command that builds the graph from existing memories/facts and proper cascade cleanup when memories are forgotten,
**so that** users enabling the graph on existing data get a populated graph, and deleting memories cleans up orphaned graph data.

**Estimate:** M

**Dependencies:** S12 (F2 integration), S13 (F6 integration)

**Scope:**

**Backfill:**
- Implement `Lore.graph_backfill(project, limit)` -- iterate existing memories, re-run graph extraction pipeline (EntityManager + RelationshipManager)
- Process memories with their existing F6 enrichment metadata and F2 facts
- Skip memories that already have entity_mentions (idempotent)
- Return count of memories processed
- CLI: `lore graph-backfill --project NAME --limit N`

**Cascade on forget():**
- Implement `_cascade_graph_on_forget(memory_id)` in `Lore`
- Step 1: Get entities mentioned by the memory via `get_entity_mentions_for_memory()`
- Step 2: For each entity, decrement `mention_count`; if `mention_count <= 0`, delete entity (CASCADE removes relationships)
- Step 3: Delete entity_mention rows for this memory
- Step 4: Find relationships with `source_memory_id=memory_id`; delete if not confirmed by other memories
- Hook into `Lore.forget()` before `store.delete(memory_id)`, only when `knowledge_graph=True`

**Store stubs:**
- Add `HttpStore` stubs for all graph methods (raise `NotImplementedError`)
- Verify `Store` ABC has all graph methods with default no-op implementations

- Tests for backfill idempotency, cascade scenarios, HttpStore stubs

**Acceptance Criteria:**

```
GIVEN 50 existing memories with enrichment metadata and facts
WHEN graph_backfill(limit=100) is called
THEN entities and relationships are created for all 50 memories, returning count=50

GIVEN graph_backfill() already run once
WHEN graph_backfill() is called again
THEN no duplicate entities or relationships are created (idempotent via upsert + mention check)

GIVEN entity "redis" with mention_count=1, mentioned only in memory M1
WHEN forget(M1.id) is called
THEN entity "redis" is deleted along with all its relationships

GIVEN entity "postgresql" with mention_count=3, mentioned in M1, M2, M3
WHEN forget(M1.id) is called
THEN entity "postgresql" survives with mention_count=2

GIVEN a relationship with source_memory_id=M1 only
WHEN forget(M1.id) is called
THEN the relationship is deleted

GIVEN knowledge_graph=False
WHEN forget(memory_id) is called
THEN no graph cascade occurs (same as v0.5.x)

GIVEN HttpStore
WHEN any graph method is called
THEN NotImplementedError is raised
```

---

## Dependency Graph

```
S1 (Schema + Types + Migrations)
 |
 +---> S2 (Name Normalization)
 |      |
 |      +---> S3 (Entity CRUD + Dedup)
 |             |
 |             +---> S4 (Relationship CRUD + Temporal)
 |             |      |
 |             |      +---> S5 (Mentions + Junction)
 |             |      |
 |             |      +---> S6 (GraphTraverser Core)
 |             |             |
 |             |             +---> S7 (Hop Query Builder)
 |             |             |
 |             |             +---> S8 (Score & Prune)
 |             |             |
 |             |             +---> S9 (Temporal Edge Support)
 |             |
 |             +---> S10 (Entity Cache)
 |             |
 |             +---> S12 (F2 Integration)
 |             |
 |             +---> S13 (F6 Integration)
 |
 +--- Sprint 2 outputs feed into:
       |
       S11 (Hybrid Recall) -- depends on S6, S10
       |
       S14 (MCP + CLI + Viz) -- depends on S6, S11
       |
       S15 (Backfill + Cascade) -- depends on S12, S13
```

**Parallel opportunities:**
- S7, S8 can be developed in parallel (both extend S6 internals)
- S9 can follow S7 (needs hop query builder for at_time)
- S10 can be developed in parallel with S6-S9 (only needs S3)
- S12, S13 can be developed in parallel (both need S3 + S4/S5)
- S14, S15 can be developed in parallel (different interfaces)

---

## Size Summary

| Story | Title | Estimate | Sprint |
|-------|-------|----------|--------|
| S1 | Schema, Types & Migrations | M | 1 |
| S2 | Entity Name Normalization | S | 1 |
| S3 | Entity CRUD & Deduplication | M | 1 |
| S4 | Relationship CRUD & Temporal Tracking | M | 1 |
| S5 | Entity-Memory Mentions & Junction | S | 1 |
| S6 | GraphTraverser Core Engine | L | 2 |
| S7 | Hop Query Builder | M | 2 |
| S8 | Score & Prune | M | 2 |
| S9 | Temporal Edge Support | S | 2 |
| S10 | Entity Cache | S | 2 |
| S11 | Hybrid Recall (Multiplicative) | L | 3 |
| S12 | F2 Integration (Facts to Edges) | M | 3 |
| S13 | F6 Integration (Enrichment to Nodes) | M | 3 |
| S14 | MCP + CLI + Visualization | L | 4 |
| S15 | Backfill + Cascade on forget() | M | 4 |

**Total: 15 stories (4S + 7M + 3L = 14 stories + 1 combined = 15)**

---

## Key Design Decisions (Traceability)

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | App-level hop-by-hop, NOT recursive CTEs | Index usage per hop, scoring between hops, predictable performance |
| 2 | Multiplicative scoring (graph_boost), NOT additive | Consistent with existing cosine * importance * tier pipeline |
| 3 | graph_boost capped at 1.5 | Prevents graph from dominating vector similarity |
| 4 | Default graph_depth=0 | Zero overhead when graph not used; backward compatible |
| 5 | MAX_DEPTH=3 hard limit | Prevents runaway traversal; 1-3 hops sufficient for our scale |
| 6 | Hop decay [1.0, 0.7, 0.5] | Farther hops contribute less relevance |
| 7 | max_fanout=20 per hop | Prevents explosion on highly-connected entities |
| 8 | Entity cache with 300s TTL | Fast query entity matching without DB hit per recall |
| 9 | Co-occurrence edges at weight=0.3 | Lightweight signal; strengthens with repeated co-mention |
| 10 | Initial edge weight = fact.confidence | Not always 1.0; reflects extraction confidence |
