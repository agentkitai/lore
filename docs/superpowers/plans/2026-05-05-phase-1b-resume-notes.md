# Phase 1B Resume Notes

**Status when this doc was written:** Phase 1B plan committed (`3d943f0`); no implementation tasks started yet. Worktree ready, test DB running. Phase 1A is merged on `main` and is the template to mirror.

**To resume:** open a fresh Claude Code session in `/home/amit/projects/lore` and paste the kickoff prompt at the bottom of this doc.

---

## Where things stand

- **Phase 1A:** merged via PR #4. `lore.persistence` + `lore.services` + memories/retrieve route refactor are on `main`.
- **Phase 1B plan:** `docs/superpowers/plans/2026-05-05-phase-1b-graph-slice.md` (24 tasks, not yet started).
- **Phase 1B worktree:** `/home/amit/projects/lore-phase-1b` on branch `solo-mode/phase-1b`. Plan committed; no other commits.
- **Test DB:** Postgres+pgvector container `lore-test-pg` running on `localhost:5432`. DB `lore_test` has all 15 migrations applied + orgs `solo`, `org_a`, `org_b` pre-seeded. `LORE_TEST_DATABASE_URL=postgresql://lore:lore@localhost:5432/lore_test` is the default the conftest uses.

To verify the DB is still up: `docker ps | grep lore-test-pg` and `pytest /home/amit/projects/lore-phase-1b/tests/persistence/ 2>&1 | tail -3` (should show 34+ persistence tests passing).

If the DB is down, restart: `docker start lore-test-pg`.

If the container was removed, recreate per Phase 1A's setup:
```bash
docker run -d --name lore-test-pg -e POSTGRES_DB=lore -e POSTGRES_USER=lore -e POSTGRES_PASSWORD=lore -p 5432:5432 pgvector/pgvector:pg16
# wait for ready, then:
docker exec lore-test-pg psql -U lore -d lore -c "CREATE DATABASE lore_test;"
docker exec lore-test-pg psql -U lore -d lore_test -c "CREATE EXTENSION vector;"
for f in /home/amit/projects/lore-phase-1b/migrations/*.sql; do
  docker exec -i lore-test-pg psql -U lore -d lore_test < "$f"
done
docker exec lore-test-pg psql -U lore -d lore_test -c "INSERT INTO orgs (id, name) VALUES ('solo','Solo'),('org_a','Org A'),('org_b','Org B') ON CONFLICT DO NOTHING;"
```

---

## The graph slice — full reference

This section is the detailed map an earlier code-explorer subagent produced. Use it as the authoritative SQL/method-signature reference when dispatching implementer subagents per the plan.

### Routes inventory (8 graph + 6 review handlers)

All graph routes mount at `/v1/ui` prefix.

**`src/lore/server/routes/graph/memories.py`**

`GET /v1/ui/graph` — `get_graph` — full graph for visualization. Queries:
1. `SELECT COUNT(*) FROM memories`
2. Dynamic filter on `memories` selecting all columns ordered by `importance_score DESC LIMIT $N`
3. `SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=$1)` for each of `entities`, `relationships`, `entity_mentions` (drop these guards in refactor)
4. `SELECT COUNT(*) FROM entities`
5. `SELECT id, name, entity_type, aliases, mention_count, first_seen_at, last_seen_at FROM entities`
6. `SELECT entity_id, memory_id, confidence FROM entity_mentions`
7. `SELECT COUNT(*) FROM relationships`
8. `SELECT source_entity_id, target_entity_id, rel_type, weight FROM relationships WHERE COALESCE(status,'approved')='approved'`

Non-SQL: orphan filtering in Python; label truncation at 60 chars.

`POST /v1/ui/search` — `search_memories` — `SELECT id, content, project, created_at, importance_score, meta FROM memories WHERE content ILIKE $1 ORDER BY importance_score DESC NULLS LAST LIMIT $2`. Returns first 200 chars of content.

`GET /v1/ui/memory/{memory_id}` — `get_memory_detail`:
1. Full select on `memories WHERE id=$1`
2. `SELECT e.id, e.name, e.entity_type FROM entity_mentions em JOIN entities e ON e.id=em.entity_id WHERE em.memory_id=$1`
3. `SELECT DISTINCT m.id, m.content, m.meta FROM entity_mentions em JOIN memories m ON m.id=em.memory_id WHERE em.entity_id=ANY($1) AND em.memory_id != $2 LIMIT 20`

**`src/lore/server/routes/graph/entities.py`**

`GET /v1/ui/entity/{entity_id}` — `get_entity_detail`:
1. `SELECT id, name, entity_type, mention_count, first_seen_at, last_seen_at FROM entities WHERE id=$1`
2. `SELECT m.id, m.content, m.meta, m.created_at FROM entity_mentions em JOIN memories m ON m.id=em.memory_id WHERE em.entity_id=$1 ORDER BY m.created_at DESC LIMIT 30`
3. `SELECT DISTINCT ON (e.id) e.id, e.name, e.entity_type, r.rel_type, r.weight FROM relationships r JOIN entities e ON e.id=CASE WHEN r.source_entity_id=$1 THEN r.target_entity_id ELSE r.source_entity_id END WHERE (r.source_entity_id=$1 OR r.target_entity_id=$1) AND e.id != $1 AND COALESCE(r.status,'approved')='approved' ORDER BY e.id, r.weight DESC LIMIT 20`

`DISTINCT ON` is Postgres-specific (note for Phase 3 SQLite port).

**`src/lore/server/routes/graph/stats.py`**

`GET /v1/ui/stats` — `get_stats` — 8-11 queries: COUNT on memories; 24h/7d windows; AVG importance; MIN/MAX created_at; GROUP BY meta->>'type'; GROUP BY project; entity counts/type breakdown/top-5. Branches on `project` arg.

`GET /v1/ui/graph/clusters` — `get_clusters` — full memories scan up to 10000 rows + Python-side grouping by project/type/tier.

`GET /v1/ui/timeline` — `get_timeline` — `SELECT date_trunc('{trunc}', created_at) as bucket_date, COALESCE(meta->>'type','general') as mem_type, COUNT(*) as cnt FROM memories [...] GROUP BY bucket_date, mem_type ORDER BY bucket_date`. Trunc interval is dynamic SQL (must be validated string, never user input). Postgres-specific (note for Phase 3).

**`src/lore/server/routes/graph/topics.py`**

`GET /v1/ui/topics` — `get_topics` — `SELECT id, name, entity_type, mention_count FROM entities WHERE mention_count >= $1 ORDER BY mention_count DESC LIMIT $2`

`GET /v1/ui/topics/{name}` — `get_topic_detail_graph`:
1. `SELECT * FROM entities WHERE LOWER(name)=LOWER($1)`
2. `SELECT r.rel_type, r.source_entity_id, r.target_entity_id, e.name as other_name, e.entity_type as other_type FROM relationships r JOIN entities e ON (CASE WHEN r.source_entity_id=$1 THEN r.target_entity_id ELSE r.source_entity_id END = e.id) WHERE (r.source_entity_id=$1 OR r.target_entity_id=$1) AND r.valid_until IS NULL AND COALESCE(r.status,'approved')='approved' LIMIT 50`
3. `SELECT DISTINCT m.id, m.content, m.type, m.created_at, m.tags FROM entity_mentions em JOIN memories m ON em.memory_id=m.id WHERE em.entity_id=$1 ORDER BY m.created_at DESC LIMIT $2`
4. `SELECT COUNT(DISTINCT memory_id) FROM entity_mentions WHERE entity_id=$1`

Non-SQL: direction classification (outgoing/incoming) computed in Python.

**`src/lore/server/routes/review.py`** (6 handlers)

Writes to `relationships` (status update) and `rejected_patterns` (INSERT ON CONFLICT). Reads `relationships JOIN entities` with risk-score computation. Contains `_compute_risk_score` (lines 83-120 of that file). Move risk-score to `services/graph/review.py` as a pure function.

### Schema (migration 007 + 011)

| Table | Key columns |
|---|---|
| `entities` | `id PK, name (UNIQUE idx), entity_type, aliases JSONB, description, metadata JSONB, mention_count INT, first_seen_at TIMESTAMPTZ, last_seen_at TIMESTAMPTZ` |
| `relationships` | `id PK, source_entity_id FK→entities, target_entity_id FK→entities, rel_type, weight REAL, properties JSONB, source_fact_id, source_memory_id, valid_from, valid_until, status TEXT DEFAULT 'approved'`. Unique partial index on `(source, target, rel_type) WHERE valid_until IS NULL`. |
| `entity_mentions` | `id PK, entity_id FK→entities CASCADE, memory_id FK→lessons (now memories via view) CASCADE, mention_type, confidence REAL` |
| `rejected_patterns` | `(source_name, target_name, rel_type)` UNIQUE |

### Proposed GraphOps method signatures (20 methods)

These go on `Store` Protocol in `lore.persistence.protocol`. Implementations on `PostgresStore`. Phase 1A `MemoryOps` is the template.

```python
# Entity ops
async def get_entity(self, entity_id: str) -> Optional[StoredEntity]: ...
async def get_entity_by_name(self, name: str) -> Optional[StoredEntity]: ...
async def list_entities(self, *, entity_type: Optional[str] = None, min_mentions: int = 0, limit: int = 100) -> Sequence[StoredEntity]: ...
async def upsert_entity(self, entity: NewEntity) -> StoredEntity: ...
async def update_entity_counts(self, entity_id: str, *, mention_delta: int, last_seen_at: datetime) -> None: ...
async def delete_entity(self, entity_id: str) -> bool: ...

# Mention ops
async def get_mentions_for_memory(self, memory_id: str) -> Sequence[StoredMention]: ...
async def get_mentions_for_entity(self, entity_id: str, *, limit: int = 100) -> Sequence[StoredMention]: ...
async def save_mention(self, mention: NewMention) -> None: ...  # idempotent ON CONFLICT
async def count_memories_for_entity(self, entity_id: str) -> int: ...

# Relationship ops
async def get_relationship(self, rel_id: str) -> Optional[StoredRelationship]: ...
async def get_active_relationship(self, source_id: str, target_id: str, rel_type: str) -> Optional[StoredRelationship]: ...
async def list_relationships_for_entity(self, entity_id: str, *, status: Optional[str] = None, limit: int = 100) -> Sequence[StoredRelationship]: ...
async def save_relationship(self, rel: NewRelationship) -> StoredRelationship: ...
async def update_relationship_status(self, rel_id: str, status: str) -> StoredRelationship: ...
async def update_relationship_weight(self, rel_id: str, weight: float) -> None: ...
async def expire_relationship(self, rel_id: str) -> None: ...
async def list_pending_relationships(self, *, rel_type: Optional[str] = None, limit: int = 100) -> Sequence[PendingRelationshipRow]: ...
async def save_rejected_pattern(self, source_name: str, target_name: str, rel_type: str, *, source_memory_id: Optional[str] = None, reason: Optional[str] = None) -> None: ...

# Traversal/stats
async def query_relationships(self, entity_ids: Sequence[str], *, direction: str = "both", active_only: bool = True, at_time: Optional[datetime] = None, rel_types: Optional[Sequence[str]] = None) -> Sequence[StoredRelationship]: ...
async def get_graph_stats(self, *, project: Optional[str] = None) -> GraphStats: ...
async def get_timeline_buckets(self, *, trunc: str, project: Optional[str] = None) -> Sequence[TimelineBucketRow]: ...
async def get_memories_by_entities(self, entity_ids: Sequence[str], *, exclude_memory_id: Optional[str] = None, limit: int = 20) -> Sequence[StoredMemory]: ...
async def search_memories_text(self, query: str, *, limit: int = 20) -> Sequence[StoredMemory]: ...  # ILIKE for the UI search box
```

### Proposed services (3 modules)

`services/graph/entities.py` — `get_entity`, `list_topics`, `get_topic_detail`, `get_entity_with_connections`. Includes name normalization (`name.strip().lower()`) before passing to `store.get_entity_by_name`.

`services/graph/graph.py` — `get_graph_data` (orphan filter in Python), `search_graph_memories` (calls `store.search_memories_text`), `get_memory_with_graph`, `get_stats`, `get_clusters` (calls `store.list_memories(MemoryFilter(limit=10000))` and groups in Python by `group_by` ∈ {"project","type","tier"}), `get_timeline` (validates `bucket` ∈ {"hour","day","week","month"} → maps to trunc string).

`services/graph/review.py` — `list_pending_reviews` (calls `store.list_pending_relationships`, scores each), `review_relationship` (action ∈ {"approve","reject"}; on reject calls `save_rejected_pattern`), `bulk_review`. Pure function `_compute_risk_score` lifted from current `routes/review.py:83-120`.

### Risks (carried forward from the plan)

- **`DISTINCT ON`** in entities query (Postgres-specific) — note for Phase 3 SQLite port.
- **`date_trunc` dynamic interval** — service layer must validate `bucket` string before passing to store; never accept arbitrary user input.
- **`entity_mentions.memory_id` FK** references `lessons` (pre-009 name); migration 009 made `lessons` a view backed by `memories`. Tests must run on a fully-migrated DB.
- **`save_relationship` semantics** — existing graph code uses upsert-then-expire pattern. Contract test must mirror this exactly. If unclear, read `src/lore/graph/relationships.py` first.
- **No existing route tests** for graph routes — T21 adds them; if any handler has subtle behavior the new tests miss, it surfaces in production.

---

## Kickoff prompt for a fresh session

Open a new Claude Code session in `/home/amit/projects/lore` and paste this:

> I'm continuing Phase 1B of the SQLite-solo-mode work for Lore. Phase 1A merged via PR #4 (the persistence + services foundation + memories slice). Phase 1B applies the same template to the graph slice.
>
> Read these files in this order before starting:
> 1. `docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md` — overall design
> 2. `docs/superpowers/plans/2026-05-05-phase-1a-foundation-and-memories.md` — the template (Phase 1A is merged; this is the pattern to mirror)
> 3. `docs/superpowers/plans/2026-05-05-phase-1b-graph-slice.md` — the Phase 1B task list (the actual plan)
> 4. `docs/superpowers/plans/2026-05-05-phase-1b-resume-notes.md` — graph slice map with full SQL details and proposed method signatures
>
> Working directory for execution: `/home/amit/projects/lore-phase-1b` (worktree on branch `solo-mode/phase-1b`). The Postgres test DB is running at `localhost:5432`/`lore_test` with all migrations applied and orgs `solo`, `org_a`, `org_b` pre-seeded.
>
> Use `superpowers:subagent-driven-development` to execute the 24 tasks in the Phase 1B plan, one implementer per task, with spec + code-quality reviews per the skill. The graph slice map in the resume notes has the SQL and method signatures you'll feed to each implementer.
>
> Confirm the test DB is reachable (run `pytest tests/persistence/ 2>&1 | tail -3` from the worktree — should show ~34 tests passing) before dispatching the first implementer.

End of resume notes.
