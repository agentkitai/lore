# Sprint 3: E3 + E4 — Story Breakdown

**Version:** v0.12.0
**Author:** Bob (Scrum Master)
**Date:** 2026-03-14
**Sprint Goal:** Deliver session snapshot (context rescue) and topic notes (auto-summaries) capabilities

---

## Batching Strategy

Stories are grouped into 4 implementation batches. Batches 1-2 (E3) and Batches 1-2 (E4) can run in parallel since the epics are independent.

| Batch | Focus | Dependencies |
|-------|-------|-------------|
| E3-B1 | Foundation: types, SDK, tests | None |
| E3-B2 | Surfaces: MCP, REST, CLI, hook | E3-B1 |
| E4-B1 | Foundation: types, cache, SDK, tests | None |
| E4-B2 | Surfaces: MCP, REST, CLI, web UI | E4-B1 |

---

# EPIC 3 — Pre-Compaction Hook (Context Rescue)

## E3-S1: Add `session_snapshot` Memory Type + Decay Config

**Size:** S
**Batch:** E3-B1
**Dependencies:** None

**Description:**
Register `session_snapshot` as a valid memory type and configure decay half-lives across all tiers. This is the foundational data model change that all other E3 stories depend on.

**Acceptance Criteria:**
- [ ] `"session_snapshot"` added to `VALID_MEMORY_TYPES` in `src/lore/types.py`
- [ ] Decay half-lives configured: working=0.5, short=3, long=7 days
- [ ] Existing memory types unaffected
- [ ] A memory with `type="session_snapshot"` can be created via `remember()` without validation errors

**Test Scenarios:**
1. Create a memory with `type="session_snapshot"` — succeeds
2. Verify `session_snapshot` is in `VALID_MEMORY_TYPES`
3. Verify decay half-life for each tier matches spec (working=0.5, short=3, long=7)
4. Verify all previously valid memory types still pass validation

**Files:** `src/lore/types.py`

---

## E3-S2: SDK `Lore.save_snapshot()` Method (Raw Path)

**Size:** M
**Batch:** E3-B1
**Dependencies:** E3-S1

**Description:**
Implement the core `save_snapshot()` method on the `Lore` class. This story covers the raw content path only (no LLM extraction). The method auto-generates `session_id` and `title` when not provided, sets high importance, and stores via the existing `remember()` pipeline.

**Acceptance Criteria:**
- [ ] `Lore.save_snapshot(content)` saves a memory with `type="session_snapshot"`, `tier="long"`
- [ ] `importance_score` is set to `0.95` after save
- [ ] `session_id` auto-generated as `uuid4().hex[:12]` when not provided
- [ ] `title` auto-generated from first 80 chars of content when not provided
- [ ] User-provided `session_id`, `title`, and `tags` are respected
- [ ] Tags always include `["session_snapshot", session_id]` plus any user tags
- [ ] Metadata includes `session_id`, `title`, `extraction_method: "raw"`
- [ ] Empty content raises `ValueError`
- [ ] Returns the saved `Memory` object

**Test Scenarios:**
1. Save with only content — verify all auto-generated fields
2. Save with explicit session_id, title, tags — verify they're used
3. Save with empty content — raises `ValueError`
4. Verify importance_score is 0.95 on returned memory
5. Verify memory type is `session_snapshot` and tier is `long`
6. Verify tags contain `session_snapshot` + session_id + user tags
7. Round-trip: save_snapshot then recall — snapshot appears in results

**Files:** `src/lore/lore.py`

---

## E3-S3: LLM Extraction for Snapshots

**Size:** M
**Batch:** E3-B1
**Dependencies:** E3-S2

**Description:**
When LLM enrichment is enabled and content exceeds 500 chars, extract key decisions, task state, action items, and context into a concise bulleted summary. Original content preserved in `context` field. Falls back to raw save on LLM failure.

**Acceptance Criteria:**
- [ ] When `enrichment_enabled` and `len(content) > 500`: LLM extraction runs
- [ ] Extracted output replaces `content`; original stored in `context` field
- [ ] `metadata.extraction_method` set to `"llm"` on successful extraction
- [ ] Extraction prompt requests: key decisions, task state, action items, non-obvious context
- [ ] Extraction capped at 300 words
- [ ] Content <= 500 chars: skips extraction, saves raw (`extraction_method: "raw"`)
- [ ] LLM failure: logs warning, falls back to raw save
- [ ] Extraction adds <2 seconds (no test enforcement, design target)

**Test Scenarios:**
1. Content >500 chars with LLM enabled — verify extraction runs, content replaced, original in context
2. Content <=500 chars with LLM enabled — verify raw save, no extraction
3. LLM disabled — verify raw save regardless of content length
4. LLM call fails — verify graceful fallback to raw save with warning logged
5. Verify extraction_method metadata is correct for each path

**Files:** `src/lore/lore.py`

---

## E3-S4: Snapshot Surfacing in `recent_activity`

**Size:** S
**Batch:** E3-B1
**Dependencies:** E3-S2

**Description:**
Ensure session snapshots appear distinctly in `recent_activity` output. Snapshots are regular memories with high importance, so they surface naturally — the only change is formatting them with a `[Session Snapshot]` prefix for visual distinction.

**Acceptance Criteria:**
- [ ] Session snapshots appear in `recent_activity()` output
- [ ] Snapshots prefixed with `[Session Snapshot]` label in formatted output
- [ ] Snapshots rank at or near top due to `importance_score=0.95`
- [ ] Snapshots >48h old still appear if within `recent_activity` time window
- [ ] Old snapshots decay naturally via configured half-lives

**Test Scenarios:**
1. Save a snapshot, call `recent_activity` — snapshot appears with `[Session Snapshot]` prefix
2. Save a snapshot + regular memory — snapshot ranks higher due to importance
3. Verify snapshot formatting is distinct from regular memories

**Files:** `src/lore/recent.py`

---

## E3-S5: `save_snapshot` MCP Tool

**Size:** S
**Batch:** E3-B2
**Dependencies:** E3-S2

**Description:**
Add the `save_snapshot` MCP tool as a thin wrapper around `Lore.save_snapshot()`. The tool description is directive — it tells agents when to use it.

**Acceptance Criteria:**
- [ ] `save_snapshot` tool registered in MCP server
- [ ] Parameters: `content` (required), `title`, `session_id`, `tags` (all optional)
- [ ] Calls `Lore.save_snapshot()` and returns formatted confirmation string
- [ ] Confirmation includes snapshot ID, session_id, and extraction method
- [ ] Tool description includes "USE THIS when" directive language
- [ ] Works end-to-end via FastMCP test client

**Test Scenarios:**
1. Call via MCP with content only — returns confirmation with auto-generated fields
2. Call via MCP with all parameters — returns confirmation with provided values
3. Call with empty content — returns error message
4. Verify tool description contains directive usage guidance

**Files:** `src/lore/mcp/server.py`

---

## E3-S6: `POST /v1/snapshots` REST Endpoint

**Size:** M
**Batch:** E3-B2
**Dependencies:** E3-S2

**Description:**
REST endpoint for creating session snapshots, primarily used by the OpenClaw hook. Includes request/response models and auth.

**Acceptance Criteria:**
- [ ] `POST /v1/snapshots` creates a session snapshot and returns 201
- [ ] Request body: `content` (required), `title`, `session_id`, `tags`, `project` (optional)
- [ ] Response: `id`, `session_id`, `title`, `extraction_method`, `created_at`
- [ ] Missing content returns 400
- [ ] Unauthorized request returns 401
- [ ] Auth requires `writer` or `admin` role
- [ ] Router registered in `app.py`

**Test Scenarios:**
1. POST with valid content — 201 with correct response shape
2. POST with all optional fields — values reflected in response
3. POST with empty/missing content — 400
4. POST without auth — 401
5. POST with reader role — 403

**Files:** `src/lore/server/routes/snapshots.py` (new), `src/lore/server/models.py`, `src/lore/server/app.py`

---

## E3-S7: `lore snapshot save` CLI Command

**Size:** S
**Batch:** E3-B2
**Dependencies:** E3-S2

**Description:**
Add `save` subcommand under the existing `snapshot` CLI group. The existing `lore snapshot` (export) command is unchanged.

**Acceptance Criteria:**
- [ ] `lore snapshot save "content here"` saves a session snapshot
- [ ] `--title` and `--session-id` options supported
- [ ] Outputs snapshot ID on success
- [ ] Existing `lore snapshot` (export) command unaffected
- [ ] Error on empty content

**Test Scenarios:**
1. `lore snapshot save "key decisions..."` — prints snapshot ID
2. `lore snapshot save --title "Auth work" "content"` — title reflected
3. `lore snapshot save ""` — error message

**Files:** `src/lore/cli.py`

---

## E3-S8: Snapshot Management via Existing Tools

**Size:** S
**Batch:** E3-B2
**Dependencies:** E3-S2

**Description:**
Verify (and fix if needed) that session snapshots are manageable through existing CRUD tools — list with type filter, delete via `forget`.

**Acceptance Criteria:**
- [ ] `lore.list_memories(type="session_snapshot")` returns only snapshots
- [ ] `lore memories --type session_snapshot` lists snapshots in CLI
- [ ] Snapshots deletable via `forget` tool/CLI
- [ ] No new management tools created

**Test Scenarios:**
1. Save 2 snapshots + 1 regular memory — list with type filter returns only snapshots
2. Save a snapshot, forget it — verify deleted
3. CLI `--type session_snapshot` filter works

**Files:** None expected (verification story — fix if gaps found)

---

## E3-S9: OpenClaw Pre-Compaction Hook

**Size:** M
**Batch:** E3-B2
**Dependencies:** E3-S6

**Description:**
TypeScript hook handler that fires on `session:compacting`, concatenates messages being compacted (capped at 4000 chars), and POSTs to the REST endpoint. Fire-and-forget — never blocks compaction.

**Acceptance Criteria:**
- [ ] Hook registered for `session:compacting` event
- [ ] `blocking: false` — compaction proceeds regardless
- [ ] Timeout: 3000ms
- [ ] Concatenates compaction message payload, truncated to 4000 chars
- [ ] POSTs to `POST /v1/snapshots` with content + session_id
- [ ] Logs success/failure, never throws
- [ ] Fails silently if Lore server is unreachable

**Test Scenarios:**
1. Simulate compaction event with messages — verify POST sent to /v1/snapshots
2. Simulate Lore server unreachable — verify hook completes without error
3. Simulate timeout (>3s response) — verify hook does not block
4. Content >4000 chars — verify truncation

**Files:** `hooks/lore-precompact.ts` (new)

---

## E3-S10: Update Setup Commands with Snapshot Protocol

**Size:** S
**Batch:** E3-B2
**Dependencies:** E3-S5

**Description:**
Update `lore setup claude-code` and `lore setup cursor` to include snapshot usage instructions in generated config files.

**Acceptance Criteria:**
- [ ] `lore setup claude-code` adds snapshot guidance to CLAUDE.md template
- [ ] `lore setup cursor` adds snapshot guidance to .cursorrules template
- [ ] Instructions specify: call `save_snapshot` when conversation is long, after key decisions, before ending complex sessions
- [ ] MCP tool description is directive enough for unprompted agent use

**Test Scenarios:**
1. Run `lore setup claude-code` — verify output includes snapshot instructions
2. Run `lore setup cursor` — verify output includes snapshot instructions

**Files:** Setup template files (wherever `lore setup` templates live)

---

# EPIC 4 — Topic Notes / Auto-Summaries

## E4-S1: Add Topic Data Types

**Size:** S
**Batch:** E4-B1
**Dependencies:** None

**Description:**
Add `TopicSummary`, `TopicDetail`, and `RelatedEntity` dataclasses to `types.py`. These are output-only types — no persistence changes.

**Acceptance Criteria:**
- [ ] `TopicSummary` dataclass with fields: `entity_id`, `name`, `entity_type`, `mention_count`, `first_seen_at`, `last_seen_at`, `related_entity_count`
- [ ] `TopicDetail` dataclass with fields: `entity`, `related_entities`, `memories`, `summary`, `summary_method`, `summary_generated_at`, `memory_count`
- [ ] `RelatedEntity` dataclass with fields: `name`, `entity_type`, `relationship`, `direction`
- [ ] All types importable from `lore.types`

**Test Scenarios:**
1. Instantiate each dataclass with required fields — succeeds
2. Verify default values (e.g., `related_entity_count=0`, `summary=None`)
3. Import from `lore.types` — all three available

**Files:** `src/lore/types.py`

---

## E4-S2: Topic Summary Cache

**Size:** S
**Batch:** E4-B1
**Dependencies:** None

**Description:**
Implement `TopicSummaryCache` — an in-memory TTL cache for LLM-generated topic summaries. 1-hour TTL, explicit invalidation support.

**Acceptance Criteria:**
- [ ] `TopicSummaryCache` class with `get()`, `set()`, `invalidate()` methods
- [ ] `get()` returns `None` for missing or expired entries
- [ ] `set()` stores summary text + method + timestamp
- [ ] `invalidate()` removes a specific entry
- [ ] Default TTL: 3600 seconds (1 hour)
- [ ] Expired entries cleaned up on `get()`

**Test Scenarios:**
1. Set then get — returns cached value
2. Get missing key — returns None
3. Set, wait for TTL expiry, get — returns None
4. Set then invalidate then get — returns None
5. Multiple entities cached independently

**Files:** `src/lore/graph/cache.py`

---

## E4-S3: Cache Invalidation on Entity Mention

**Size:** S
**Batch:** E4-B1
**Dependencies:** E4-S2

**Description:**
Wire cache invalidation into `EntityManager` so that when a new mention increments an entity's `mention_count`, the topic summary cache is invalidated for that entity.

**Acceptance Criteria:**
- [ ] `EntityManager` accepts a `TopicSummaryCache` reference (optional, for backward compat)
- [ ] `ingest_from_enrichment()` calls `cache.invalidate(entity_id)` after incrementing mention_count
- [ ] `ingest_from_fact()` calls `cache.invalidate(entity_id)` after incrementing mention_count
- [ ] No error if cache is None (not provided)

**Test Scenarios:**
1. Ingest enrichment that updates entity — verify cache invalidated for that entity
2. Ingest fact that creates new mention — verify cache invalidated
3. EntityManager without cache — no error on ingest

**Files:** `src/lore/graph/entities.py`

---

## E4-S4: SDK `Lore.list_topics()` Method

**Size:** M
**Batch:** E4-B1
**Dependencies:** E4-S1

**Description:**
Implement `list_topics()` on the `Lore` class. Queries entities with `mention_count >= threshold`, supports entity type filter and project filter, returns sorted `TopicSummary` list.

**Acceptance Criteria:**
- [ ] Returns entities with `mention_count >= min_mentions` (default: 3)
- [ ] Sorted by `mention_count` descending
- [ ] Filterable by `entity_type`
- [ ] Filterable by `project` (post-filter in Python for v1)
- [ ] Respects `limit` parameter
- [ ] Returns empty list when knowledge graph disabled
- [ ] Returns empty list when no entities meet threshold
- [ ] Each result includes `related_entity_count`

**Test Scenarios:**
1. Create entities with varying mention_counts — list with default threshold returns only 3+
2. Filter by entity_type — only matching type returned
3. Custom threshold (min_mentions=5) — only 5+ returned
4. Limit=2 with 5 eligible — only 2 returned, highest mention_count first
5. No entities meet threshold — returns empty list
6. Knowledge graph disabled — returns empty list

**Files:** `src/lore/lore.py`

---

## E4-S5: SDK `Lore.topic_detail()` Method (Structured Path)

**Size:** M
**Batch:** E4-B1
**Dependencies:** E4-S1, E4-S2

**Description:**
Implement `topic_detail()` — resolves entity by name (case-insensitive, alias-aware), loads mentions, memories, and relationships, assembles `TopicDetail`. This story covers the structured (no-LLM) path only.

**Acceptance Criteria:**
- [ ] Resolves entity by exact name (case-insensitive)
- [ ] Falls back to alias lookup if name not found
- [ ] Returns `None` if entity doesn't exist
- [ ] Loads linked memories via entity mentions, sorted by `created_at` desc
- [ ] Caps memories at `max_memories` parameter
- [ ] Includes related entities with relationship type and direction
- [ ] Without LLM: `summary=None`, `summary_method="structured"`
- [ ] `memory_count` reflects total (not capped) count

**Test Scenarios:**
1. Query existing entity by name — returns full TopicDetail
2. Query by alias — resolves correctly
3. Query non-existent name — returns None
4. Entity with >max_memories mentions — capped, memory_count shows total
5. Entity with relationships — related_entities populated with correct directions
6. Entity with no relationships — related_entities is empty list

**Files:** `src/lore/lore.py`

---

## E4-S6: LLM Topic Summary Generation

**Size:** M
**Batch:** E4-B1
**Dependencies:** E4-S5, E4-S2

**Description:**
When LLM is available and `include_summary=True`, generate a 2-4 sentence narrative summary for the topic. Cache the result. Fall back to structured listing on LLM failure.

**Acceptance Criteria:**
- [ ] With LLM + cache miss: generates narrative summary via extraction prompt
- [ ] Summary covers: what it is, key decisions, current state (2-4 sentences)
- [ ] Generated summary cached in `TopicSummaryCache`
- [ ] With cache hit: uses cached summary (no LLM call)
- [ ] LLM failure: falls back to `summary_method="structured"`, no error
- [ ] `summary_generated_at` timestamp set on generation
- [ ] Memory contents truncated to ~3000 chars for LLM prompt budget

**Test Scenarios:**
1. Topic detail with LLM enabled, cache miss — summary generated and cached
2. Topic detail with LLM enabled, cache hit — cached summary used, no LLM call
3. LLM call fails — falls back to structured, no error raised
4. Verify summary prompt includes entity name, type, related entities, memories
5. Cache invalidation → next request regenerates

**Files:** `src/lore/lore.py`

---

## E4-S7: `topics` and `topic_detail` MCP Tools

**Size:** M
**Batch:** E4-B2
**Dependencies:** E4-S4, E4-S5

**Description:**
Add two MCP tools: `topics` for listing and `topic_detail` for detail view. Both are thin wrappers around SDK methods with directive tool descriptions.

**Acceptance Criteria:**
- [ ] `topics` tool: parameters `entity_type`, `min_mentions`, `limit`, `project` (all optional)
- [ ] `topics` returns formatted list with name, type, mention count, related count
- [ ] `topics` returns guidance message when knowledge graph disabled
- [ ] `topics` returns "No topics found" when list is empty
- [ ] `topic_detail` tool: parameters `name` (required), `max_memories`, `format` (optional)
- [ ] `topic_detail` returns formatted detail with entity info, related entities, memories, summary
- [ ] `topic_detail` returns "No topic found" for non-existent name
- [ ] Both tool descriptions include "USE THIS WHEN" directive language

**Test Scenarios:**
1. Call `topics` via MCP — returns formatted list
2. Call `topics` with knowledge graph disabled — returns guidance message
3. Call `topic_detail` with valid name — returns formatted detail
4. Call `topic_detail` with invalid name — returns not-found message
5. Verify brief vs detailed format controls memory content length

**Files:** `src/lore/mcp/server.py`

---

## E4-S8: Topics REST Endpoints

**Size:** M
**Batch:** E4-B2
**Dependencies:** E4-S4, E4-S5

**Description:**
Add `GET /v1/topics` (list) and `GET /v1/topics/:name` (detail) REST endpoints with Pydantic response models.

**Acceptance Criteria:**
- [ ] `GET /v1/topics` returns JSON with `topics` array, `total`, `threshold`
- [ ] Query params: `entity_type`, `min_mentions` (default 3), `limit` (default 50), `project`
- [ ] Each topic object: `entity_id`, `name`, `entity_type`, `mention_count`, `first_seen_at`, `last_seen_at`, `related_entity_count`
- [ ] `GET /v1/topics/:name` returns JSON with `entity`, `related_entities`, `memories`, `summary`, `summary_method`, `summary_generated_at`, `memory_count`
- [ ] Query params: `max_memories` (default 20), `format` (brief/detailed)
- [ ] 404 for non-existent topic name
- [ ] Both endpoints require auth
- [ ] Router registered in `app.py`

**Test Scenarios:**
1. GET /v1/topics — 200 with correct response shape
2. GET /v1/topics?entity_type=project — filtered results
3. GET /v1/topics?min_mentions=100 — empty topics array, not error
4. GET /v1/topics/auth — 200 with full detail
5. GET /v1/topics/nonexistent — 404
6. GET without auth — 401

**Files:** `src/lore/server/routes/topics.py` (new), `src/lore/server/app.py`

---

## E4-S9: `lore topics` CLI Command

**Size:** S
**Batch:** E4-B2
**Dependencies:** E4-S4, E4-S5

**Description:**
Single `lore topics` CLI command. Without arguments: lists topics. With a `NAME` argument: shows topic detail.

**Acceptance Criteria:**
- [ ] `lore topics` lists all topics (3+ mentions) with name, type, mention count
- [ ] `lore topics <name>` shows full topic detail
- [ ] `--type` filters by entity type
- [ ] `--min-mentions` sets threshold (default 3)
- [ ] `--format brief|detailed` controls memory content length
- [ ] `--limit` caps list results
- [ ] Knowledge graph disabled: shows guidance message, not error

**Test Scenarios:**
1. `lore topics` — lists topics sorted by mention count
2. `lore topics auth` — shows detail for "auth"
3. `lore topics --type project` — only project entities
4. `lore topics --min-mentions 10` — higher threshold
5. Knowledge graph disabled — guidance message

**Files:** `src/lore/cli.py`

---

## E4-S10: Web UI Topics Sidebar (E1 Integration)

**Size:** L
**Batch:** E4-B2
**Dependencies:** E4-S8

**Description:**
Add a topics sidebar to the E1 graph visualization page. Sidebar lists topics on page load. Clicking a topic centers the graph on that entity, highlights 1-hop neighbors, and shows a detail panel with summary + memory list.

**Acceptance Criteria:**
- [ ] Topics sidebar renders on the left side of the graph page
- [ ] Sidebar populated from `GET /v1/topics?limit=20` on page load
- [ ] Each topic shows: name, mention count
- [ ] Clicking a topic calls `GET /v1/topics/:name` and renders detail panel
- [ ] Detail panel shows: summary (if available), related entities, memory list
- [ ] Clicking a topic highlights entity node + 1-hop neighbors in the graph
- [ ] Uses existing `centerOnNode()` graph function from E1
- [ ] Sidebar updates on page load (no manual refresh)
- [ ] Empty state: "No topics found" message when list is empty

**Test Scenarios:**
1. Page load with topics — sidebar populated with topic list
2. Click topic — graph centers on entity, detail panel appears
3. Topic with LLM summary — summary displayed in detail panel
4. Topic without summary — structured listing shown
5. No topics — empty state message shown
6. Knowledge graph disabled — sidebar shows guidance message

**Files:** `src/lore/server/routes/ui.py` (frontend JS/HTML)

---

## E4-S11: Update Setup Commands with Topics Guidance

**Size:** S
**Batch:** E4-B2
**Dependencies:** E4-S7

**Description:**
Update `lore setup claude-code` and `lore setup cursor` to include topic tool usage guidance.

**Acceptance Criteria:**
- [ ] CLAUDE.md template includes: "Call `topics` to see recurring concepts, `topic_detail <name>` for deep context"
- [ ] .cursorrules template includes equivalent guidance
- [ ] Instructions mention that topics require knowledge graph enabled

**Test Scenarios:**
1. Run `lore setup claude-code` — verify output includes topics guidance
2. Run `lore setup cursor` — verify output includes topics guidance

**Files:** Setup template files

---

# Sprint Summary

| Epic | Stories | Total Size | Batch 1 (Foundation) | Batch 2 (Surfaces) |
|------|---------|-----------|---------------------|-------------------|
| E3 | 10 | 4S + 4M + 1M + 1S = ~30 pts | S1, S2, S3, S4 | S5, S6, S7, S8, S9, S10 |
| E4 | 11 | 4S + 5M + 1L + 1S = ~38 pts | S1, S2, S3, S4, S5, S6 | S7, S8, S9, S10, S11 |
| **Total** | **21** | **~68 pts** | | |

**Sizing key:** S=2pts, M=5pts, L=8pts

**Critical path:** E3-S1 → E3-S2 → E3-S5/S6/S7 (MCP/REST/CLI can parallelize)
**Critical path:** E4-S1 → E4-S4/S5 → E4-S7/S8/S9/S10 (all surfaces can parallelize)

**Parallelization:** E3 and E4 are fully independent. E3-B1 and E4-B1 can start simultaneously. Within each epic, Batch 2 stories are parallelizable once their Batch 1 dependencies are met.
