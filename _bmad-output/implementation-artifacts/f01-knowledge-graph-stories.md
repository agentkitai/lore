# F1 -- Knowledge Graph Layer: User Stories

**Feature:** F1 -- Knowledge Graph Layer
**Version:** v0.6.0 ("Open Brain")
**Sprint Planning Date:** 2026-03-06
**Architecture Doc:** `_bmad-output/implementation-artifacts/f01-knowledge-graph-architecture.md`
**PRD:** `_bmad-output/planning-artifacts/f01-knowledge-graph-prd.md`

---

## Sprint Overview

| Sprint | Theme | Stories | Focus |
|--------|-------|---------|-------|
| **Sprint 1** | Foundation | S1-S5 | Schema, types, dedup, entity/relationship CRUD, mention tracking |
| **Sprint 2** | Core Logic | S6-S9 | Traversal engine, graph update pipeline, F2/F6 integration |
| **Sprint 3** | Hybrid Recall + Integration | S10-S11 | Hybrid scoring, cascade on forget |
| **Sprint 4** | Surface + Polish | S12-S15 | MCP tools, CLI, visualization, backfill |

**Critical path:** S1 -> S2 -> S3 -> S4 -> S6 -> S10

---

## Sprint 1: Foundation

### S1: Data Model, Schema & Migrations

**As a** developer,
**I want** the knowledge graph schema (entities, relationships, entity_mentions tables) defined as Python dataclasses and SQL tables,
**so that** all subsequent graph stories have a stable data foundation to build on.

**Estimate:** M

**Dependencies:** None

**Scope:**
- Add `Entity`, `Relationship`, `EntityMention`, `GraphResult`, `GraphNode` dataclasses to `src/lore/types.py`
- Extend `RecallResult` with `related_entities: Optional[List[Entity]]`, `graph_score: Optional[float]`, `graph_path: Optional[List[str]]` (all default `None`)
- Add SQLite schema in `SqliteStore` -- `entities`, `relationships`, `entity_mentions` tables with all indexes (`idx_entities_name_type` UNIQUE, `idx_entities_name`, `idx_entities_type`, `idx_relationships_source`, `idx_relationships_target`, `idx_relationships_type`, `idx_relationships_memory`, `idx_entity_mentions_memory`)
- Create `migrations/007_knowledge_graph.sql` for Postgres -- `entities` (UUID PK, JSONB aliases, GIN index on aliases), `relationships` (UUID PK, partial indexes on `valid_until IS NULL`, compound indexes `idx_relationships_source_active` and `idx_relationships_target_active`), `entity_mentions` (composite PK)
- SQLite tables created lazily via `_maybe_create_graph_tables()` only when `knowledge_graph=True`
- Create `src/lore/graph/__init__.py` module

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
THEN Entity, Relationship, EntityMention, GraphResult, GraphNode are available with all fields matching architecture doc section 2.1

GIVEN an existing RecallResult
WHEN accessing new graph fields
THEN related_entities, graph_score, graph_path all default to None (backward compatible)
```

---

### S2: Entity Name Normalization & Alias Resolution

**As a** developer,
**I want** a deterministic name normalization algorithm with builtin alias mapping,
**so that** "PostgreSQL", "postgres", and "pg" all resolve to the same canonical entity.

**Estimate:** S

**Dependencies:** S1 (types)

**Scope:**
- Create `src/lore/graph/dedup.py` with `normalize_entity_name()` function
- Normalization steps: strip whitespace, lowercase, collapse multiple spaces, apply `_BUILTIN_ALIASES` map, strip trailing version numbers
- Builtin alias map: `pg -> postgresql`, `postgres -> postgresql`, `k8s -> kubernetes`, `js -> javascript`, `ts -> typescript`, `py -> python`, `react.js -> react`, `reactjs -> react`, `node.js -> nodejs`, `vue.js -> vue`, `next.js -> nextjs`
- Version suffix regex: `\s+v?\d+[\.\d]*\s*$` -- "postgresql 16" -> "postgresql"
- Tests in `tests/test_entity_dedup.py`

**Acceptance Criteria:**

```
GIVEN the name "PostgreSQL 16"
WHEN normalize_entity_name() is called
THEN it returns "postgresql"

GIVEN the name "pg"
WHEN normalize_entity_name() is called
THEN it returns "postgresql" (builtin alias)

GIVEN the name "  React.js  "
WHEN normalize_entity_name() is called
THEN it returns "react" (strip + alias)

GIVEN the name "k8s"
WHEN normalize_entity_name() is called
THEN it returns "kubernetes"

GIVEN the name "my-custom-service"
WHEN normalize_entity_name() is called
THEN it returns "my-custom-service" (no alias match, returned lowercase)

GIVEN the name "Python v3.12"
WHEN normalize_entity_name() is called
THEN it returns "python" (version stripped + alias applied)
```

---

### S3: Entity CRUD in SqliteStore & MemoryStore

**As a** developer,
**I want** full entity create/read/update/delete operations in both SqliteStore and MemoryStore,
**so that** entities can be managed and deduplicated at write time.

**Estimate:** M

**Dependencies:** S1 (schema), S2 (normalization)

**Scope:**
- Implement `upsert_entity()` in `SqliteStore` and `MemoryStore` with 3-step resolution: exact match on `(canonical_name, entity_type)` -> alias containment match -> create new
- On match: add original name as alias (if different from canonical), bump `mention_count`, update `last_seen_at`
- On create: generate ULID id, set `first_seen_at`/`last_seen_at` to now, `mention_count=1`, store non-canonical name as alias
- Implement `get_entity()`, `find_entities_by_name()`, `list_entities()`, `delete_entity()`
- SQLite alias lookup: `json_each(aliases)` with `LOWER()` comparison
- Implement `merge_entities()` -- redirect relationships, move mentions, union aliases, sum mention counts, take min/max timestamps, delete source (single transaction)
- Add default no-op implementations to `Store` base class in `src/lore/store/base.py`
- Tests for all CRUD paths

**Acceptance Criteria:**

```
GIVEN upsert_entity("postgres", "tool") followed by upsert_entity("PostgreSQL", "tool")
WHEN both calls complete
THEN only one entity exists with name "postgresql", mention_count=2, and "PostgreSQL" in aliases

GIVEN upsert_entity("alice", "person") followed by upsert_entity("alice", "tool")
WHEN both calls complete
THEN two distinct entities exist (different entity_type = different entity)

GIVEN an entity with aliases ["k8s", "kube"]
WHEN find_entities_by_name("k8s") is called
THEN the entity is returned via alias containment match

GIVEN two entities A (3 mentions) and B (2 mentions) for the same concept
WHEN merge_entities(source_id=B.id, target_id=A.id) is called
THEN entity A has mention_count=5, aliases include B's aliases, B is deleted, and B's relationships now point to A

GIVEN delete_entity(entity_id)
WHEN the entity has relationships
THEN the entity and all its relationships are deleted (CASCADE)
```

---

### S4: Relationship CRUD in SqliteStore & MemoryStore

**As a** developer,
**I want** full relationship create/read/update operations with temporal tracking and weight strengthening,
**so that** edges between entities can be created, confirmed, and expired over time.

**Estimate:** M

**Dependencies:** S1 (schema), S3 (entities exist to reference)

**Scope:**
- Implement `upsert_relationship()` -- if active edge `(source, target, relation_type)` exists: strengthen weight by +0.1 (capped at 1.0), append `memory_id` to `metadata.confirmed_by[]`; otherwise create new with ULID id
- Initial weight from fact confidence (not always 1.0) per architecture decision #10
- Implement `find_active_relationship()` -- query `WHERE valid_until IS NULL`
- Implement `list_relationships()` with optional `entity_id`, `relation_type`, `include_expired` filters
- Implement `close_relationship()` -- set `valid_until` to now
- Implement `find_relationship_by_fact()` -- lookup by `fact_id`
- Relationship type is free-text (not enum) with recommended vocabulary from architecture doc section 2.3
- Add default no-op implementations to `Store` base class
- Tests for CRUD + strengthening + temporal close

**Acceptance Criteria:**

```
GIVEN a new relationship (auth-service --uses--> postgresql) with confidence 0.85
WHEN upsert_relationship() is called
THEN a relationship is created with weight=0.85, valid_until=NULL

GIVEN an existing active relationship with weight=0.85
WHEN upsert_relationship() is called with the same (source, target, relation_type)
THEN weight becomes 0.95 (0.85 + 0.1), memory_id appended to metadata.confirmed_by

GIVEN a relationship at weight=0.95
WHEN upsert_relationship() is called again
THEN weight becomes 1.0 (capped, not 1.05)

GIVEN an active relationship
WHEN close_relationship() is called
THEN valid_until is set to current timestamp

GIVEN list_relationships(include_expired=False)
WHEN called on a store with both active and expired edges
THEN only relationships with valid_until IS NULL are returned

GIVEN a relationship created from fact_id="abc123"
WHEN find_relationship_by_fact("abc123") is called
THEN that relationship is returned
```

---

### S5: Entity-Memory Junction & Mention Tracking

**As a** developer,
**I want** a junction table linking entities to the memories that mention them with mention counting,
**so that** I can find all memories for an entity and all entities for a memory.

**Estimate:** S

**Dependencies:** S3 (entity CRUD), S4 (relationship CRUD)

**Scope:**
- Implement `add_entity_mention(entity_id, memory_id)` -- INSERT with `mentioned_at` timestamp; idempotent (no error on duplicate due to composite PK)
- Implement `get_entity_memories(entity_id, limit)` -- return memory IDs for an entity
- Implement `get_memory_entities(memory_id)` -- return Entity objects for a memory
- Both SqliteStore and MemoryStore implementations
- Tests for junction operations, idempotency, and bidirectional lookups

**Acceptance Criteria:**

```
GIVEN entity E1 and memory M1
WHEN add_entity_mention(E1.id, M1.id) is called
THEN a junction row is created with the current timestamp

GIVEN add_entity_mention(E1.id, M1.id) called twice
WHEN the second call executes
THEN no error occurs and no duplicate row is created (idempotent)

GIVEN entity E1 mentioned in memories M1, M2, M3
WHEN get_entity_memories(E1.id) is called
THEN [M1.id, M2.id, M3.id] are returned

GIVEN memory M1 mentioning entities E1, E2
WHEN get_memory_entities(M1.id) is called
THEN [E1, E2] Entity objects are returned

GIVEN get_entity_memories(entity_id, limit=2)
WHEN the entity has 5 memory mentions
THEN only 2 memory IDs are returned
```

---

## Sprint 2: Core Logic

### S6: Graph Traversal Engine (Recursive CTEs)

**As a** developer,
**I want** a bidirectional graph traversal engine using recursive CTEs with cycle prevention,
**so that** multi-hop relational queries can be answered efficiently in pure SQL.

**Estimate:** L

**Dependencies:** S3 (entities), S4 (relationships)

**Scope:**
- Implement `traverse_graph()` in SqliteStore using recursive CTE with INSTR-based cycle prevention (architecture doc section 6.1)
- Implement `traverse_graph()` in MemoryStore using iterative BFS (dict-based adjacency)
- Bidirectional traversal by default (`direction="both"`): follow both outgoing and incoming edges
- Support `direction="out"` (outgoing only) and `direction="in"` (incoming only)
- Clamp `max_depth` to 4 (hard cap)
- Filter active edges only by default (`valid_until IS NULL`); `include_expired=True` includes all
- Optional `relation_type` filter
- Result limit of 100 entities per traversal
- Create `src/lore/graph/traversal.py` with `traverse_graph()` wrapper function
- Performance guardrails: 5-second timeout (SQLite PRAGMA, Postgres statement_timeout)
- Tests in `tests/test_graph_traversal.py` with known test graph: `A --uses--> B --depends_on--> C --deployed_on--> D; A --works_with--> E; E --manages--> F; F --uses--> B`

**Acceptance Criteria:**

```
GIVEN the test graph (A->B->C->D, A->E->F->B)
WHEN traverse_graph(A, depth=1) is called
THEN entities B and E are returned (direct neighbors only)

GIVEN the test graph
WHEN traverse_graph(A, depth=2) is called
THEN entities B, C, E, F are returned (two hops)

GIVEN the test graph
WHEN traverse_graph(A, depth=3) is called
THEN entities B, C, D, E, F are returned (no duplicates despite F->B cycle)

GIVEN the test graph with a cycle (F->B)
WHEN traverse_graph(A, depth=4) is called
THEN cycle prevention ensures B is not re-traversed from F

GIVEN traverse_graph(A, direction="out")
WHEN called
THEN only entities reachable via outgoing edges from A are returned

GIVEN traverse_graph(B, direction="in")
WHEN called
THEN entities A and F are returned (entities with edges pointing TO B)

GIVEN a relationship with valid_until set (expired)
WHEN traverse_graph with include_expired=False (default)
THEN the expired edge is not followed

GIVEN traverse_graph(A, depth=5)
WHEN called
THEN depth is clamped to 4

GIVEN an entity with no edges
WHEN traverse_graph is called on it
THEN an empty list is returned

GIVEN traverse_graph with relation_type="uses"
WHEN called from A
THEN only "uses" edges are followed (B returned, not E)
```

---

### S7: Graph Update Pipeline (remember -> graph)

**As a** developer,
**I want** `remember()` to automatically update the knowledge graph from F6 enrichment entities and F2 fact triples,
**so that** every memory contributes to the connected knowledge graph.

**Estimate:** M

**Dependencies:** S3 (entity CRUD), S4 (relationship CRUD), S5 (mention tracking)

**Scope:**
- Create `src/lore/graph/extraction.py` with `_update_graph(memory, facts, enrichment)` function
- Step 1: Promote F6 enrichment entities to graph nodes via `upsert_entity()` + `add_entity_mention()`
- Step 2: Convert F2 fact triples `(subject, predicate, object)` to graph edges via `upsert_entity()` for both subject/object + `upsert_relationship()`
- Implement `_infer_entity_type()` -- check F6 enrichment data for entity type, fallback to "concept"
- Apply `graph_confidence_threshold` (default 0.5) -- skip facts below threshold
- Skip invalidated facts (`fact.invalidated_by` is set)
- Hook into `Lore.remember()` as final step after fact extraction; wrap in try/except (failures logged, memory still saved)
- Add `knowledge_graph` config param to `Lore.__init__()` + env var `LORE_KNOWLEDGE_GRAPH`
- Add `graph_confidence_threshold` config param + env var `LORE_GRAPH_CONFIDENCE_THRESHOLD`

**Acceptance Criteria:**

```
GIVEN knowledge_graph=True and enrichment data with entities [{"name": "Alice", "type": "person"}]
WHEN remember("Alice works on auth-service") is called
THEN entity "alice" (type: person) is created in the entities table with an entity_mention linking to the memory

GIVEN knowledge_graph=True and a fact ("auth-service", "uses", "postgresql") with confidence 0.9
WHEN remember() processes the fact
THEN entities "auth-service" and "postgresql" are upserted, a relationship (auth-service --uses--> postgresql) is created with weight=0.9

GIVEN a fact with confidence 0.3 and graph_confidence_threshold=0.5
WHEN the graph update processes the fact
THEN no graph edge is created (below threshold)

GIVEN a fact where subject matches an F6 enrichment entity with type "tool"
WHEN _infer_entity_type() is called for the subject
THEN it returns "tool" (not default "concept")

GIVEN knowledge_graph=True and the graph update fails with an exception
WHEN remember() runs
THEN the memory is still saved successfully and a warning is logged

GIVEN knowledge_graph=False (default)
WHEN remember() runs
THEN no graph update occurs, zero additional DB operations
```

---

### S8: F2 Integration -- Facts to Edges & Temporal Close

**As a** developer,
**I want** F2 fact supersession events to close old graph edges and open new ones with temporal tracking,
**so that** the knowledge graph reflects fact evolution over time.

**Estimate:** M

**Dependencies:** S4 (relationship CRUD), S7 (graph update pipeline)

**Scope:**
- Implement `_on_fact_superseded(old_fact, new_fact)` in `src/lore/graph/extraction.py`
- When F2 conflict resolution detects SUPERSEDE: find relationship by `old_fact.id`, set `valid_until` to now
- New fact's relationship created via normal upsert path (already handled in S7)
- Optionally create a `migrated_from` relationship between old and new target entities when applicable
- CONTRADICT resolution: leave both edges active (graph reflects ambiguity)
- Integration point: call from ConflictResolver or register as callback
- Tests for supersession and contradiction scenarios

**Acceptance Criteria:**

```
GIVEN memory "We use MySQL for auth-service" creating edge (auth-service --uses--> mysql)
WHEN a new memory "We migrated auth-service to PostgreSQL" causes SUPERSEDE
THEN the (auth-service --uses--> mysql) edge gets valid_until set to now
AND a new (auth-service --uses--> postgresql) edge is created with valid_until=NULL

GIVEN a superseded relationship
WHEN list_relationships(include_expired=False) is called
THEN the old closed edge is NOT returned

GIVEN a superseded relationship
WHEN list_relationships(include_expired=True) is called
THEN both the old (closed) and new (active) edges are returned

GIVEN two contradicting facts about the same subject-predicate
WHEN CONTRADICT resolution occurs
THEN both relationship edges remain active (valid_until=NULL)

GIVEN find_relationship_by_fact(old_fact_id)
WHEN called after supersession
THEN the closed relationship with valid_until set is returned
```

---

### S9: F6 Integration -- Enrichment Entities to Graph Nodes

**As a** developer,
**I want** F6 enrichment entities to be automatically promoted to first-class graph nodes,
**so that** every entity identified by the enrichment pipeline becomes part of the knowledge graph.

**Estimate:** S

**Dependencies:** S3 (entity CRUD), S7 (graph update pipeline)

**Scope:**
- In `_update_graph()`, iterate over `enrichment.get("entities", [])` and call `upsert_entity()` for each
- Shared entity type vocabulary: `person`, `tool`, `project`, `platform`, `organization`, `concept`, `language`, `framework`
- Create `add_entity_mention()` for each enrichment entity linked to the current memory
- Handle gracefully when F6 is disabled (enrichment is None or empty dict)
- Optional LLM relationship extraction when `graph_llm_extraction=True` (off by default) -- uses `RELATIONSHIP_EXTRACTION_PROMPT` from architecture doc section 5.6
- Add `graph_llm_extraction` config param + env var `LORE_GRAPH_LLM_EXTRACTION`
- Tests for F6 -> graph flow

**Acceptance Criteria:**

```
GIVEN enrichment data {"entities": [{"name": "Alice", "type": "person"}, {"name": "Kubernetes", "type": "platform"}]}
WHEN _update_graph() processes the enrichment
THEN entities "alice" (person) and "kubernetes" (platform) exist in the entities table

GIVEN the same enrichment entity mentioned across two memories
WHEN both memories are processed
THEN the entity has mention_count=2 and two entity_mention rows

GIVEN F6 is disabled (enrichment is None)
WHEN _update_graph() runs
THEN no entities are created from enrichment (no error)

GIVEN graph_llm_extraction=True
WHEN _update_graph() runs with entities identified
THEN an LLM call extracts additional relationships between entities

GIVEN graph_llm_extraction=False (default)
WHEN _update_graph() runs
THEN no additional LLM call is made for relationship extraction
```

---

## Sprint 3: Hybrid Recall + Integration

### S10: Hybrid Recall Scoring (Vector + Graph)

**As a** developer,
**I want** `recall()` to combine vector similarity with graph proximity scoring when `graph_depth > 0`,
**so that** relational queries surface graph-connected memories alongside semantically similar ones.

**Estimate:** L

**Dependencies:** S6 (traversal engine)

**Scope:**
- Create `src/lore/graph/scoring.py` with `compute_graph_score()` and `merge_vector_and_graph()`
- Graph score formula: `graph_score = sum_over_paths(1.0 / (1.0 + hop_distance) * edge_weight)`, normalized to [0, 1]
- Hybrid formula: `final_score = (1 - graph_weight) * vector_score + graph_weight * graph_score` (additive, not multiplicative)
- Default `graph_weight=0.3` (configurable via `LORE_GRAPH_WEIGHT`)
- Implement `_identify_query_entities()` in `src/lore/graph/traversal.py` -- no LLM: tokenize query -> unigrams + bigrams -> exact name/alias match -> optional fuzzy match (threshold 0.6)
- Implement `_enhance_with_graph()` in `Lore.recall()`: identify query entities -> traverse -> collect memory IDs via entity_mentions -> compute graph scores -> merge with vector results
- `graph_depth=0` (default): zero additional cost, identical to v0.5.x
- `include_entities=True` on recall populates `RecallResult.related_entities`
- Extend `recall()` signature with `graph_depth`, `graph_weight`, `include_entities` params
- Add `graph_weight`, `graph_depth_default`, `graph_depth_max`, `graph_fuzzy_match` config params
- Tests in hybrid scoring scenarios

**Acceptance Criteria:**

```
GIVEN recall("auth-service dependencies", graph_depth=2) with a graph containing auth-service -> postgresql
WHEN the query matches "auth-service" as a known entity
THEN memories mentioning postgresql are included in results via graph traversal (even if vector similarity is low)

GIVEN a memory appearing in BOTH vector results (cosine=0.8) and graph results (1 hop, weight=0.9)
WHEN hybrid scoring is computed with graph_weight=0.3
THEN final_score = 0.7 * 0.8 + 0.3 * 0.9 = 0.83

GIVEN a memory appearing ONLY in graph results (graph_score=0.8)
WHEN hybrid scoring is computed with graph_weight=0.3
THEN final_score = 0.7 * 0.0 + 0.3 * 0.8 = 0.24 (pure-graph discovery surfaces)

GIVEN recall(query, graph_depth=0)
WHEN called
THEN behavior is identical to v0.5.x (no graph queries, no graph scoring)

GIVEN recall(query, graph_depth=2, include_entities=True)
WHEN results are returned
THEN each RecallResult has related_entities populated with Entity objects from graph

GIVEN a query "What does Alice use?"
WHEN _identify_query_entities() runs
THEN entity "alice" is identified via exact name match (no LLM needed)

GIVEN graph_fuzzy_match=True and query containing "postgre"
WHEN _identify_query_entities() runs with no exact match
THEN fuzzy matching finds entity "postgresql" (trigram similarity > 0.6)
```

---

### S11: Cascade Behavior on forget()

**As a** developer,
**I want** `forget()` to properly clean up graph data when a memory is deleted,
**so that** orphaned entities and unsupported relationships are removed.

**Estimate:** M

**Dependencies:** S5 (mention tracking), S7 (graph update pipeline)

**Scope:**
- Implement `_cascade_graph_on_forget(memory_id)` in `Lore`
- Step 1: Get entities mentioned by the memory via `get_memory_entities()`
- Step 2: For each entity, decrement `mention_count`; if `mention_count <= 0`, delete entity (CASCADE removes relationships)
- Step 3: Find relationships sourced only from this memory; if `metadata.confirmed_by` is empty or only contains this memory_id, delete the relationship
- Relationships confirmed by multiple memories survive (only this memory_id removed from confirmed_by)
- Hook into `Lore.forget()` before `store.delete(memory_id)`, only when `knowledge_graph=True`
- Tests for cascade scenarios

**Acceptance Criteria:**

```
GIVEN entity "redis" with mention_count=1, mentioned only in memory M1
WHEN forget(M1.id) is called
THEN entity "redis" is deleted along with all its relationships

GIVEN entity "postgresql" with mention_count=3, mentioned in memories M1, M2, M3
WHEN forget(M1.id) is called
THEN entity "postgresql" survives with mention_count=2

GIVEN a relationship confirmed_by=[M1.id] only
WHEN forget(M1.id) is called
THEN the relationship is deleted

GIVEN a relationship confirmed_by=[M1.id, M2.id]
WHEN forget(M1.id) is called
THEN the relationship survives with confirmed_by=[M2.id]

GIVEN knowledge_graph=False
WHEN forget(memory_id) is called
THEN no graph cascade occurs (same as v0.5.x)
```

---

## Sprint 4: Surface + Polish

### S12: MCP Tools (graph_query, related, entity_map)

**As a** developer,
**I want** three new MCP tools exposing graph capabilities to AI agents,
**so that** agents can traverse the knowledge graph, find related memories, and visualize entity maps.

**Estimate:** M

**Dependencies:** S6 (traversal), S10 (hybrid recall)

**Scope:**
- Add `graph_query` MCP tool in `src/lore/mcp/server.py`: traverse graph from a query entity, return entities + relationships as formatted text
- Add `related` MCP tool: find memories connected through graph (not just semantic similarity), using `Lore.get_related_memories()`
- Add `entity_map` MCP tool: visual entity map for a topic with `format` param ("text", "json", "d3"), using `Lore.get_entity_map()`
- Implement `Lore.graph_query()`, `Lore.get_related_memories()`, `Lore.get_entity_map()` facade methods
- All tools accept optional `project` parameter
- Tools return graceful "knowledge graph not enabled" message when `knowledge_graph=False`
- Tests for MCP tool registration and output formatting

**Acceptance Criteria:**

```
GIVEN knowledge_graph=True and entities in the graph
WHEN graph_query("auth-service", depth=2) MCP tool is called
THEN a formatted response with connected entities and relationship types is returned

GIVEN knowledge_graph=True
WHEN related("database dependencies", depth=2, limit=5) MCP tool is called
THEN up to 5 memories connected via graph relationships are returned

GIVEN knowledge_graph=True
WHEN entity_map("auth-service", format="text") MCP tool is called
THEN a text tree visualization of connected entities is returned

GIVEN knowledge_graph=False
WHEN any graph MCP tool is called
THEN a message "Knowledge graph is not enabled" is returned (not an error)

GIVEN entity_map("auth-service", format="json")
WHEN called
THEN a JSON object with nodes[] and edges[] arrays is returned
```

---

### S13: CLI Commands (graph, entities, relationships)

**As a** developer,
**I want** CLI commands to query the graph, list entities, and list relationships,
**so that** users can explore the knowledge graph from the terminal.

**Estimate:** M

**Dependencies:** S3 (entities), S4 (relationships), S6 (traversal)

**Scope:**
- Add `lore graph <query>` subcommand: `--depth N`, `--type TYPE`, `--relation TYPE`, `--format text|json|d3`
- Add `lore entities` subcommand: `--type TYPE`, `--search TEXT`, `--sort mentions|name`, `--limit N`
- Add `lore relationships` subcommand: `--entity NAME`, `--type TYPE`, `--include-expired`, `--limit N`
- Add `lore graph-backfill` subcommand: `--project NAME`, `--limit N`
- Table-formatted output for entities and relationships (name, type, mentions, aliases)
- Tree-formatted output for graph traversal
- All commands in `src/lore/cli.py`
- Tests for CLI argument parsing and output formatting

**Acceptance Criteria:**

```
GIVEN entities in the graph
WHEN lore entities --type person --sort mentions is run
THEN a table of person entities sorted by mention_count descending is displayed

GIVEN entities in the graph
WHEN lore entities --search "postgres" is run
THEN entities matching "postgres" by name or alias are returned

GIVEN relationships in the graph
WHEN lore relationships --entity "auth-service" is run
THEN all relationships involving auth-service (source or target) are displayed

GIVEN a populated graph
WHEN lore graph "auth-service" --depth 2 is run
THEN a tree visualization of entities within 2 hops is displayed

GIVEN lore graph "auth-service" --format json
WHEN run
THEN JSON output with nodes and edges arrays is printed to stdout
```

---

### S14: Visualization Endpoint (D3-Compatible JSON)

**As a** developer,
**I want** a D3-compatible JSON output format for graph visualization,
**so that** frontends can render interactive knowledge graph visualizations.

**Estimate:** S

**Dependencies:** S6 (traversal)

**Scope:**
- Create `src/lore/graph/visualization.py` with `to_d3_json()` and `to_text_tree()` functions
- D3 JSON format: `{"nodes": [{"id", "name", "type", "mention_count", "depth"}], "links": [{"source", "target", "relation_type", "weight"}]}`
- Text tree format: indented ASCII tree showing entity names, types, and relationship labels
- Used by `get_entity_map(format="d3")` facade method and CLI `--format d3`
- Tests for output format correctness

**Acceptance Criteria:**

```
GIVEN a GraphResult with entities and relationships
WHEN to_d3_json() is called
THEN output is {"nodes": [...], "links": [...]} with correct D3 force-graph structure

GIVEN a D3 JSON output
WHEN parsed
THEN every link.source and link.target references a valid node.id

GIVEN a GraphResult
WHEN to_text_tree() is called
THEN an indented ASCII tree is returned showing entity relationships

GIVEN an empty GraphResult (no entities)
WHEN to_d3_json() is called
THEN {"nodes": [], "links": []} is returned
```

---

### S15: Graph Backfill & Store ABC Stubs

**As a** developer,
**I want** a backfill command that builds the knowledge graph from existing memories and facts,
**so that** users enabling the graph on an existing Lore instance get a populated graph.

**Estimate:** M

**Dependencies:** S7 (graph update pipeline)

**Scope:**
- Implement `Lore.graph_backfill(project, limit)` -- iterate existing memories, re-run graph extraction pipeline
- Process memories with their existing F6 enrichment metadata and F2 facts
- Skip memories that already have entity_mentions (idempotent)
- Return count of memories processed
- Add `HttpStore` stubs for all graph methods (raise `NotImplementedError` per architecture doc)
- Ensure `Store` ABC in `base.py` has all graph methods with default no-op implementations (done in S3/S4 but verify completeness)
- CLI integration via `lore graph-backfill --project NAME --limit N`
- Tests for backfill idempotency and progress counting

**Acceptance Criteria:**

```
GIVEN 50 existing memories with enrichment metadata and facts
WHEN graph_backfill(limit=100) is called
THEN entities and relationships are created for all 50 memories, returning 50

GIVEN graph_backfill() already run once
WHEN graph_backfill() is called again
THEN no duplicate entities or relationships are created (idempotent via upsert)

GIVEN graph_backfill(limit=10) with 50 memories
WHEN called
THEN only 10 memories are processed, returning 10

GIVEN HttpStore
WHEN any graph method is called
THEN NotImplementedError is raised

GIVEN Store ABC
WHEN a new store subclass does not override graph methods
THEN default no-op implementations are used (return empty lists/None, no errors)
```

---

## Dependency Graph

```
S1 (Schema + Types)
 |
 +-> S2 (Name Normalization)
 |    |
 |    +-> S3 (Entity CRUD) --------+------+------+------+
 |         |                       |      |      |      |
 |         +-> S4 (Rel CRUD) ------+      |      |      |
 |              |       |          |      |      |      |
 |              |       |          v      |      |      |
 |              |       |    S5 (Mentions)|      |      |
 |              |       |          |      |      |      |
 |              v       v          v      |      |      |
 |         S6 (Traversal)    S7 (Pipeline)|      |      |
 |              |              |    |     |      |      |
 |              |              |    |     v      v      |
 |              |              |    +-> S8(F2) S9(F6)  |
 |              |              |    |                   |
 |              v              v    |                   |
 |         S10 (Hybrid Recall) S11(Cascade)             |
 |              |                                       |
 |              v                                       v
 |         S12 (MCP)  S13 (CLI)  S14 (Viz)    S15 (Backfill+Stubs)
```

---

## Size Summary

| Story | Title | Estimate | Sprint |
|-------|-------|----------|--------|
| S1 | Schema + Types + Migrations | M | 1 |
| S2 | Name Normalization + Aliases | S | 1 |
| S3 | Entity CRUD | M | 1 |
| S4 | Relationship CRUD | M | 1 |
| S5 | Entity-Memory Junction | S | 1 |
| S6 | Graph Traversal Engine | L | 2 |
| S7 | Graph Update Pipeline | M | 2 |
| S8 | F2 Integration (Temporal) | M | 2 |
| S9 | F6 Integration (Entities) | S | 2 |
| S10 | Hybrid Recall Scoring | L | 3 |
| S11 | Cascade on forget() | M | 3 |
| S12 | MCP Tools | M | 4 |
| S13 | CLI Commands | M | 4 |
| S14 | Visualization (D3 JSON) | S | 4 |
| S15 | Backfill + Store Stubs | M | 4 |

**Total: 15 stories (4S + 8M + 2L + 1XL = 15)**
