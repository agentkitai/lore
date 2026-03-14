# Sprint 3: E3 + E4 — PRD

**Epics:** E3 (Pre-Compaction Hook / Context Rescue), E4 (Topic Notes / Auto-Summaries)
**Version:** v0.12.0
**Author:** John (PM)
**Date:** 2026-03-14
**Status:** Draft

---

# PART 1: E3 — Pre-Compaction Hook (Context Rescue)

## 1. Overview & Problem Statement

### The Problem

Long conversations hit context window limits. When compaction fires, early-session context — decisions, action items, debugging state — gets compressed or dropped. The agent loses its working memory mid-task. This is the **"amnesia mid-session" problem**.

The Obsidian "Second Brain" prompt works around this with a `PreCompact` hook that _reminds_ the agent to save. That's fragile — it relies on the agent actually doing it. We can do better: **automatically save session state to Lore before compaction, so the next session (or post-compaction context) has full continuity.**

### The Solution

A two-pronged approach:
1. **OpenClaw hook** (`lore-precompact`) — fires on `session:compacting` event, auto-saves a session snapshot to Lore. Zero agent effort.
2. **MCP tool** (`save_snapshot`) — for Claude Code, Codex, Cursor. Agent calls it explicitly when instructed via CLAUDE.md/.cursorrules protocol.

Session snapshots are stored as `type: "session_snapshot"` memories with high importance so they surface reliably in the next session's `recent_activity` (E2) and auto-inject.

### Why This Matters

- **Context rescue**: Decisions made in hour 1 of a long session survive compaction
- **Cross-session continuity**: Snapshots bridge sessions even when semantic search wouldn't find them
- **Complements E2**: `recent_activity` already surfaces recent memories — session snapshots are the highest-value recent memories

---

## 2. User Stories

### US-1: Automatic Pre-Compaction Save (OpenClaw)

**As** an OpenClaw user in a long session,
**I want** my key decisions and task state automatically saved before compaction,
**So that** I don't lose context when the conversation gets compressed.

**Acceptance Criteria:**
- [ ] Hook fires on `session:compacting` event (or platform equivalent)
- [ ] Saves conversation snapshot to Lore as `type: "session_snapshot"`
- [ ] Snapshot includes the compaction payload (messages being compacted)
- [ ] Tagged with `session_id` for traceability
- [ ] Completes within 3 seconds (must not block compaction significantly)
- [ ] Fails silently if Lore is unavailable (compaction must proceed regardless)

### US-2: Manual Snapshot Save (MCP Tool)

**As** a Claude Code / Codex / Cursor user,
**I want** an MCP tool to save a session snapshot on demand,
**So that** I can preserve important context before it's lost.

**Acceptance Criteria:**
- [ ] `save_snapshot` MCP tool accepts `content` parameter (what to save)
- [ ] Optional `session_id` parameter (auto-generated if omitted)
- [ ] Optional `title` parameter for human-readable identification
- [ ] Stored with `type: "session_snapshot"`, `tier: "long"`, high `importance_score`
- [ ] Returns confirmation with snapshot ID
- [ ] Works without LLM — saves raw content as-is

### US-3: LLM-Enhanced Extraction (Optional)

**As** a user with an LLM configured,
**I want** the snapshot to extract key points from raw conversation content,
**So that** the saved snapshot is concise and high-signal, not a raw dump.

**Acceptance Criteria:**
- [ ] When LLM is available, extracts: key decisions, action items, current task state, blockers
- [ ] Extraction prompt is deterministic (same input → same structure)
- [ ] Falls back to raw content save if LLM call fails
- [ ] Extraction adds <2 seconds to the save operation
- [ ] Extracted output tagged with `metadata.extraction_method: "llm"` vs `"raw"`

### US-4: Snapshot Surfacing in Next Session

**As** an agent starting a new session,
**I want** recent session snapshots to appear in my context automatically,
**So that** I pick up exactly where the last session left off.

**Acceptance Criteria:**
- [ ] Session snapshots appear in `recent_activity` (E2) output
- [ ] Snapshots are visually distinct from regular memories (prefixed or labeled)
- [ ] In OpenClaw auto-inject, snapshots appear before other recent memories
- [ ] Snapshots have `importance_score >= 0.9` so they rank high in any retrieval
- [ ] Old snapshots (>48h) decay naturally via existing importance decay

### US-5: Cross-Platform Protocol Instruction

**As** a non-OpenClaw user,
**I want** clear instructions in my config file telling the agent when to save snapshots,
**So that** I get context rescue even without automatic hooks.

**Acceptance Criteria:**
- [ ] CLAUDE.md protocol instruction added: "When context is getting long, call `save_snapshot` with key decisions and current task state"
- [ ] `.cursorrules` equivalent instruction added
- [ ] `lore setup claude-code` and `lore setup cursor` commands updated to include this
- [ ] MCP tool description is directive enough that agents call it unprompted

### US-6: Snapshot Management

**As** a user,
**I want** to list and manage session snapshots,
**So that** I can review what was saved and clean up old ones.

**Acceptance Criteria:**
- [ ] `lore.list_memories` with `type="session_snapshot"` filter returns snapshots
- [ ] Snapshots can be deleted via existing `forget` tool
- [ ] No new management tools needed — reuse existing CRUD
- [ ] CLI: `lore memories --type session_snapshot` lists them

---

## 3. Functional Requirements

### FR-1: MCP Tool — `save_snapshot`

```
Tool: save_snapshot
Description: "Save a session snapshot to preserve key decisions, action items,
  and context before it's lost to compaction or session end. USE THIS when your
  conversation is getting long, when you've made important decisions, or before
  ending a complex session. Saves as a high-importance memory that surfaces in
  the next session's recent_activity."

Parameters:
  content: str               # What to save (required). Key decisions, task state, etc.
  title: str? = None         # Short title for the snapshot (auto-generated if omitted)
  session_id: str? = None    # Session identifier (auto-generated UUID if omitted)
  tags: list[str]? = None    # Additional tags

Returns: str                 # Confirmation with snapshot ID
```

**Implementation notes:**

- Creates a `Memory` with:
  - `type = "session_snapshot"`
  - `tier = "long"` (should survive beyond working/short decay)
  - `importance_score = 0.95` (near-max so it always surfaces)
  - `metadata = {"session_id": ..., "title": ..., "extraction_method": "raw"|"llm"}`
  - `tags = ["session_snapshot", session_id] + user_tags`
- If LLM is available and `content` is >500 chars, run extraction to distill key points
- If LLM is unavailable or content is short, save raw
- Project scoped via `LORE_PROJECT` env var (same as all other tools)

### FR-2: SDK Method — `Lore.save_snapshot()`

```python
def save_snapshot(
    self,
    content: str,
    *,
    title: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Memory:
    """Save a session snapshot as a high-importance memory.

    Returns the saved Memory object.
    """
```

**Implementation notes:**

- Generate `session_id` via `uuid4().hex[:12]` if not provided
- Auto-generate title from first 80 chars of content if not provided
- If `self._enrichment_enabled` and `len(content) > 500`:
  - Call LLM with extraction prompt (see FR-5)
  - Use extracted output as `content`, store original in `context` field
- Else: save raw content
- Call `self.remember()` with the assembled Memory fields
- Return the saved Memory

### FR-3: OpenClaw Hook — `lore-precompact`

```typescript
// Hook registration in openclaw.json:
{
  "hooks": {
    "session:compacting": {
      "handler": "lore-precompact",
      "timeout": 3000,
      "blocking": false  // Must not block compaction
    }
  }
}
```

**Handler logic:**

1. Receive compaction event with `messages` payload (messages being compacted)
2. Concatenate message contents (truncate to 4000 chars max for LLM budget)
3. POST to Lore MCP `save_snapshot` tool or REST endpoint
4. Fire-and-forget: log success/failure, never block

**Design decisions:**
- `blocking: false` — compaction proceeds regardless. The snapshot save is best-effort.
- 3-second timeout — generous for a fire-and-forget POST
- Truncate to 4000 chars — balances completeness vs LLM extraction cost
- Use the REST endpoint (`POST /v1/snapshots`) not the MCP tool, since hooks are TypeScript not MCP clients

### FR-4: REST Endpoint — `POST /v1/snapshots`

```
POST /v1/snapshots
  Body:
    {
      "content": "...",
      "title": "Optional title",
      "session_id": "optional-session-id",
      "tags": ["optional", "tags"],
      "project": "optional-project"
    }

  Headers:
    Authorization: Bearer <api_key>

  Response: 201 Created
    {
      "id": "memory-id",
      "session_id": "...",
      "title": "...",
      "extraction_method": "raw" | "llm",
      "created_at": "..."
    }

  Errors:
    400: Missing content
    401: Unauthorized
```

**Implementation notes:**
- Thin wrapper around `Lore.save_snapshot()`
- Computes embedding for the snapshot content (same as `remember`)
- Returns the snapshot ID so the hook can log it

### FR-5: LLM Extraction Prompt

When LLM is available, extract structured key points from raw session content:

```
System: You are extracting key information from a conversation session that is
about to be compacted. Extract ONLY what would be critical to know in a future
session. Be concise.

Extract:
1. Key decisions made (with rationale)
2. Current task state (what's in progress, what's blocked)
3. Action items or next steps
4. Important context that wouldn't be obvious from code alone

Format as a bulleted list. Omit categories with nothing to report.
Max 300 words.
```

**Design decisions:**
- 300-word cap keeps snapshots within token budget for auto-inject
- Categories are optional — omit empty ones to avoid noise
- "Wouldn't be obvious from code alone" filters out things git history covers

### FR-6: Session Snapshot Type Registration

Add `"session_snapshot"` to `VALID_MEMORY_TYPES` in `types.py`.

Configure decay:
```python
# In TIER_DECAY_HALF_LIVES:
"long": {
    ...
    "session_snapshot": 7,  # 7-day half-life — recent sessions matter, old ones fade
}
```

**Opinionated decision:** 7-day half-life for long-tier session snapshots. Fresh snapshots are critical; week-old ones have diminishing value. The 48h surfacing window (US-4) is handled by `recent_activity` already — decay handles the long tail.

---

## 4. Non-Functional Requirements

### NFR-1: Performance

| Metric | Target | Rationale |
|--------|--------|-----------|
| `save_snapshot` (no LLM) | <200ms | Must not noticeably delay compaction |
| `save_snapshot` (with LLM) | <5s | LLM extraction is optional; acceptable for background save |
| Hook total time | <3s | Hard timeout; fire-and-forget |
| Snapshot retrieval via `recent_activity` | No additional cost | Snapshots are regular memories — same query path |

### NFR-2: Reliability

- **Fire-and-forget**: Hook failure must never block compaction. Compaction is more important than the snapshot.
- **Idempotent**: Multiple snapshot saves with same `session_id` are fine — each creates a distinct memory (not an update). Different snapshots from the same session capture different points in time.
- **Graceful degradation**: LLM unavailable → raw save. Store unavailable → log and continue.

### NFR-3: Token Budget

Session snapshots that surface via auto-inject should target **<400 tokens**. This means:
- LLM extraction prompt enforces 300-word max (~400 tokens)
- Raw saves (no LLM) are truncated to first 1000 chars in `brief` format display
- Auto-inject shows at most 1 recent snapshot (the most recent one)

### NFR-4: Backward Compatibility

- New memory type `session_snapshot` — no impact on existing types
- No changes to existing MCP tool signatures
- No changes to existing store schema (snapshots are regular memories)
- `recent_activity` (E2) returns snapshots naturally (they're recent memories)

### NFR-5: Testing

- Unit tests: save_snapshot with/without LLM, title auto-generation, session_id auto-generation
- Integration tests: MCP tool, REST endpoint, round-trip (save → recall via recent_activity)
- Edge cases: empty content (reject), very long content (truncate), concurrent saves
- Hook simulation: verify fire-and-forget behavior, timeout handling

---

## 5. API Design

### 5.1 MCP Tool

```python
@mcp.tool(
    description=(
        "Save a session snapshot to preserve important context before it's lost. "
        "USE THIS when your conversation is getting long, when you've made "
        "important decisions, or before ending a complex session. The snapshot "
        "will surface in the next session's recent_activity so you pick up "
        "where you left off. Works without LLM (saves raw) — enhanced with "
        "LLM (extracts key decisions, action items, and task state)."
    ),
)
def save_snapshot(
    content: str,
    title: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> str:
```

### 5.2 REST Endpoint

```
POST /v1/snapshots
  Body: { content, title?, session_id?, tags?, project? }
  Response: 201 { id, session_id, title, extraction_method, created_at }
```

### 5.3 SDK Method

```python
class Lore:
    def save_snapshot(
        self,
        content: str,
        *,
        title: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Memory:
```

### 5.4 CLI

```bash
lore snapshot save "Key decisions: ..."          # Quick save
lore snapshot save --title "Auth refactor" "..."  # With title
lore memories --type session_snapshot             # List snapshots
```

**Note:** `lore snapshot` already exists for export snapshots (E5). We add `lore snapshot save` as a subcommand. The existing `lore snapshot` (no subcommand) continues to create export snapshots. Naming: `lore snapshot save` = session snapshot, `lore snapshot` = export snapshot. Clear enough.

---

## 6. Integration Patterns

### 6.1 OpenClaw (Hook — Automatic)

**Primary integration.** The `lore-precompact` hook fires automatically before compaction. The user does nothing.

```
Event flow:
1. Context window fills up → OpenClaw triggers session:compacting
2. lore-precompact hook fires (non-blocking)
3. Hook extracts messages being compacted
4. Hook POSTs to POST /v1/snapshots
5. Compaction proceeds regardless of hook result
6. Next session: recent_activity surfaces the snapshot
```

**Configuration:**
- `LORE_PRECOMPACT=true|false` — enable/disable (default: true)
- No other config needed — uses existing Lore connection settings

### 6.2 Claude Code (MCP + CLAUDE.md)

**No hooks available.** Relies on agent following CLAUDE.md instructions.

Add to CLAUDE.md (via `lore setup claude-code`):
```markdown
## Memory (Lore)

- Call `recent_activity` at session start for continuity.
- Call `save_snapshot` when: (1) conversation is getting long, (2) you've made important
  decisions, (3) before ending a complex debugging/design session.
- Use `recall` for semantic search, `remember` to save individual facts.
```

**Limitation:** Agent compliance is voluntary. The MCP tool description is directive ("USE THIS when...") to maximize compliance. Not as reliable as OpenClaw's automatic hook.

### 6.3 Codex (MCP Only)

**Same MCP tool.** No config file to add instructions. Relies entirely on tool description.

**Mitigation:** The `save_snapshot` tool description explicitly says when to use it. The FastMCP server `instructions` field (already updated in E2) provides session-level guidance.

### 6.4 Cursor (MCP + .cursorrules)

**Same pattern as Claude Code.** Add to `.cursorrules` via `lore setup cursor`.

---

## 7. Open Questions

### OQ-1: What compaction event does OpenClaw expose?

**Context:** The hook depends on a `session:compacting` event (or equivalent). Need to verify OpenClaw's hook event system supports this.

**Fallback:** If no compaction event exists, use a `message:sent` hook with a token-count heuristic: "if estimated context > 80% of window, save snapshot." Less precise but functional.

### OQ-2: Should snapshots from the same session be consolidated?

**Context:** A long session might trigger multiple compaction events, creating multiple snapshots. This could clutter `recent_activity`.

**Recommendation:** Don't consolidate for v1. Each snapshot captures a different point in time — that's valuable. `recent_activity` already caps output, so multiple snapshots won't flood the context. Revisit if users report noise.

### OQ-3: Should `save_snapshot` be callable from the existing `remember` tool with `type="session_snapshot"`?

**Recommendation:** No. Separate tool. `save_snapshot` has different defaults (high importance, auto session_id, optional LLM extraction) that would clutter `remember`'s interface. A dedicated tool also gets a dedicated, directive description that tells agents _when_ to use it — critical for non-hook platforms.

---

# PART 2: E4 — Topic Notes / Auto-Summaries (Concept Hubs)

## 1. Overview & Problem Statement

### The Problem

As memories accumulate, recurring concepts emerge: projects, tools, people, architectural patterns. Lore's knowledge graph (v0.6.0 F1) tracks entities and relationships, but there's no user-facing view that says **"here's everything Lore knows about X."** Users can `recall` semantically, but that requires knowing what to ask. They can `graph_query` an entity, but that returns raw edges, not insight.

The knowledge is there. It's just not synthesized.

### The Solution

**Topic Notes** — auto-generated summaries for entities that appear across 3+ memories. Each topic note aggregates:
- **First mention**: When this concept first appeared
- **Key decisions**: What was decided about this topic
- **Related entities**: What connects to it in the knowledge graph
- **Timeline**: How understanding evolved over time
- **Memory references**: Links to the underlying memories

Available via CLI (`lore topics`), MCP tools, REST API, and the web UI (E1 sidebar integration).

### Why This Matters

- **Knowledge discovery**: Users find connections they didn't know existed
- **Onboarding**: New team members get instant context on any recurring concept
- **Decision history**: "What did we decide about auth?" → one command, full picture
- **Bridges graph and memory**: Makes the knowledge graph _useful_ to humans, not just algorithms

---

## 2. User Stories

### US-1: List Auto-Detected Topics

**As** a user,
**I want** to see what topics Lore has identified across my memories,
**So that** I can discover recurring concepts and explore them.

**Acceptance Criteria:**
- [ ] `lore topics` lists entities appearing in 3+ memories
- [ ] Each topic shows: name, entity type, memory count, first seen, last seen
- [ ] Sorted by memory count (most-referenced first)
- [ ] Filterable by entity type (`--type project`, `--type tool`, etc.)
- [ ] Threshold configurable (`--min-mentions 5`, default: 3)
- [ ] Returns empty list (not error) when no topics meet threshold

### US-2: View Topic Detail

**As** a user researching a concept,
**I want** a comprehensive summary of everything Lore knows about a topic,
**So that** I get full context without manually searching memories.

**Acceptance Criteria:**
- [ ] `lore topics <name>` shows full topic detail
- [ ] Includes: first mention date, latest mention date, total memory count
- [ ] Includes: list of related entities (from knowledge graph edges)
- [ ] Includes: linked memories (sorted by time, most recent first)
- [ ] Memory content shown in brief (first 100 chars) by default, `--detailed` for full
- [ ] Without LLM: chronological memory list with metadata
- [ ] With LLM: narrative summary of key points + chronological list

### US-3: MCP Topic Discovery

**As** an AI agent,
**I want** to query topics via MCP to get context on recurring concepts,
**So that** I can provide informed responses about well-known project concepts.

**Acceptance Criteria:**
- [ ] `topics` MCP tool lists topics (same as CLI)
- [ ] `topic_detail` MCP tool returns full topic summary
- [ ] Tool descriptions guide agents on when to use them
- [ ] Output is formatted for agent consumption (structured, not decorative)

### US-4: LLM-Enhanced Topic Summaries (Optional)

**As** a user with an LLM configured,
**I want** topic summaries to be narrative and insightful,
**So that** I get synthesized understanding, not just a list of memories.

**Acceptance Criteria:**
- [ ] With LLM: generates a 2-4 sentence narrative summary per topic
- [ ] Summary covers: what it is, key decisions about it, current state
- [ ] Summaries cached and regenerated when new memories reference the topic
- [ ] Falls back to structured listing if LLM fails
- [ ] `metadata.summary_method: "llm"|"structured"` on the cached summary

### US-5: Web UI Integration (E1 Sidebar)

**As** a web UI user,
**I want** topics visible as navigation in the graph visualization,
**So that** I can click a topic and see its connected memories and entities.

**Acceptance Criteria:**
- [ ] Topics appear as a sidebar/list in the E1 web UI
- [ ] Clicking a topic highlights its entity and connected nodes in the graph
- [ ] Topic detail panel shows summary + linked memories
- [ ] Topics list updates when the page loads (no manual refresh)

### US-6: Topic Regeneration on New Data

**As** a user who keeps adding memories,
**I want** topic summaries to stay current as new memories arrive,
**So that** topics reflect the latest knowledge.

**Acceptance Criteria:**
- [ ] When a new memory references an existing topic entity, the topic's cached summary is invalidated
- [ ] Next request for that topic regenerates the summary
- [ ] New entities crossing the 3-memory threshold automatically become topics
- [ ] Regeneration is lazy (on-demand), not eager (background job)
- [ ] Timestamp of last regeneration visible in topic metadata

---

## 3. Functional Requirements

### FR-1: Topic Detection Engine

Topics are entities from the knowledge graph that have `mention_count >= threshold` (default: 3).

```python
def list_topics(
    self,
    *,
    entity_type: Optional[str] = None,
    min_mentions: int = 3,
    limit: int = 50,
    project: Optional[str] = None,
) -> List[TopicSummary]:
```

**Implementation notes:**

- Query `store.list_entities()` filtered by `mention_count >= min_mentions`
- Already indexed: `entities` table has `mention_count` column, sorted by it
- Filter by `entity_type` if provided
- Project scoping: requires joining through `entity_mentions → memories` to filter by project. For v1, if `project` is specified, post-filter in application code (query all entities, then filter by checking if any linked memory belongs to that project). Optimize with SQL join in v2 if performance demands it.
- Return `TopicSummary` dataclass (see FR-6)

### FR-2: Topic Detail Builder

```python
def topic_detail(
    self,
    name: str,
    *,
    max_memories: int = 20,
    include_summary: bool = True,
) -> TopicDetail:
```

**Implementation notes:**

1. Resolve entity by name (case-insensitive, alias-aware via `store.get_entity_by_name()` / `store.get_entity_by_alias()`)
2. Get all `EntityMention` records for the entity via `store.get_entity_mentions_for_entity()`
3. Load the linked memories via `store.get()` for each mention's `memory_id`
4. Get related entities via `store.query_relationships()` for the entity
5. If `include_summary` and LLM available: generate narrative summary (see FR-4)
6. Assemble `TopicDetail` (see FR-6)

**Performance concern:** Loading N memories individually is N queries. For v1, acceptable (topics have 3-20 memories typically). For v2, add a `store.get_batch(ids)` method if needed.

### FR-3: MCP Tools

**Tool 1: `topics`**

```
Tool: topics
Description: "List auto-detected topics — recurring concepts that appear across
  multiple memories. USE THIS WHEN: you want to understand what major themes exist
  in the knowledge base, find topics to explore in depth, or get an overview of
  what Lore knows about. Topics are entities from the knowledge graph that appear
  in 3+ memories."

Parameters:
  entity_type: str? = None    # Filter: person, tool, project, concept, etc.
  min_mentions: int = 3       # Minimum memory references (1-100, clamped)
  limit: int = 20             # Max topics to return
  project: str? = None        # Filter to project

Returns: str                  # Formatted topic list
```

**Tool 2: `topic_detail`**

```
Tool: topic_detail
Description: "Get a comprehensive summary of a specific topic — everything Lore
  knows about a recurring concept. Includes first mention, key decisions, related
  entities, timeline, and linked memories. USE THIS WHEN: you need deep context
  on a concept, want to understand decision history, or are onboarding to a new
  area. Works without LLM (chronological listing) — enhanced with LLM (narrative
  summary)."

Parameters:
  name: str                   # Topic/entity name (case-insensitive, alias-aware)
  max_memories: int = 20      # Max linked memories to include
  format: str = "brief"       # brief | detailed

Returns: str                  # Formatted topic detail
```

### FR-4: LLM Summary Generation (Optional)

When LLM is available, generate a narrative summary for a topic:

```
System: You are summarizing everything known about a specific topic based on
memory entries. Write 2-4 sentences covering: what it is, key decisions made
about it, and its current state. Be factual — only state what the memories say.

Topic: {entity_name} ({entity_type})
Related entities: {related_entity_names}
Memories (chronological):
{memory_contents}
```

**Cache strategy:**
- Cache key: `topic_summary:{entity_id}`
- Stored in memory metadata or a dedicated cache (use existing `EntityCache` pattern from `graph/cache.py`)
- Invalidated when: a new `EntityMention` is created for this entity
- TTL: 1 hour (even without invalidation, summaries refresh periodically)
- Cache miss → regenerate on demand

### FR-5: REST Endpoints

**Endpoint 1: List Topics**

```
GET /v1/topics
  Query Parameters:
    entity_type: string? (filter by entity type)
    min_mentions: int = 3
    limit: int = 50
    project: string?

  Response: 200 OK
    {
      "topics": [
        {
          "entity_id": "...",
          "name": "Lore",
          "entity_type": "project",
          "mention_count": 42,
          "first_seen_at": "2026-01-15T...",
          "last_seen_at": "2026-03-14T...",
          "related_entity_count": 8
        }
      ],
      "total": 15,
      "threshold": 3
    }
```

**Endpoint 2: Topic Detail**

```
GET /v1/topics/:name
  Query Parameters:
    max_memories: int = 20
    format: string = "brief" (brief | detailed)

  Response: 200 OK
    {
      "entity": { id, name, entity_type, aliases, description, mention_count, first_seen_at, last_seen_at },
      "related_entities": [
        { "name": "...", "entity_type": "...", "relationship": "uses", "direction": "outgoing" }
      ],
      "memories": [
        { "id": "...", "content": "...", "type": "lesson", "created_at": "...", "tags": [...] }
      ],
      "summary": "Narrative summary..." | null,
      "summary_method": "llm" | "structured" | null,
      "summary_generated_at": "..." | null,
      "memory_count": 42
    }

  Errors:
    404: Topic/entity not found
    401: Unauthorized
```

### FR-6: New Data Types

```python
@dataclass
class TopicSummary:
    """A topic entry in the topics list."""
    entity_id: str
    name: str
    entity_type: str
    mention_count: int
    first_seen_at: str
    last_seen_at: str
    related_entity_count: int = 0

@dataclass
class TopicDetail:
    """Full detail for a single topic."""
    entity: Entity
    related_entities: List[RelatedEntity]
    memories: List[Memory]
    summary: Optional[str] = None
    summary_method: Optional[str] = None  # "llm" | "structured"
    summary_generated_at: Optional[str] = None
    memory_count: int = 0

@dataclass
class RelatedEntity:
    """An entity related to a topic via a knowledge graph edge."""
    name: str
    entity_type: str
    relationship: str  # rel_type from Relationship
    direction: str     # "outgoing" | "incoming"
```

### FR-7: CLI Commands

```bash
lore topics                          # List all topics (3+ mentions)
lore topics --type project           # Filter by entity type
lore topics --min-mentions 5         # Higher threshold
lore topics auth                     # Detail for "auth" topic
lore topics auth --format detailed   # Full memory content
```

### FR-8: Web UI Integration (E1)

Add to the E1 graph visualization:

1. **Topics sidebar**: List of topics as clickable items in a left sidebar
2. **Click behavior**: Clicking a topic centers the graph on that entity, highlights connected nodes
3. **Detail panel**: Shows topic summary + memory list in a right panel (same layout as clicking a node, but richer)
4. **API calls**: Sidebar loads from `GET /v1/topics`, detail from `GET /v1/topics/:name`

**Implementation note:** This is a frontend-only addition. The E1 web UI already has node-click detail panels and graph filtering. Topics add a curated entry point into the graph.

---

## 4. Non-Functional Requirements

### NFR-1: Performance

| Metric | Target | Rationale |
|--------|--------|-----------|
| `topics` list | <200ms (local), <500ms (remote) | Simple indexed query on `mention_count` |
| `topic_detail` (no LLM) | <500ms (local), <1s (remote) | Multiple queries: entity + mentions + memories + relationships |
| `topic_detail` (with LLM, cache miss) | <5s | LLM summary generation |
| `topic_detail` (with LLM, cache hit) | <500ms | Same as no-LLM (cached summary) |
| Web UI topics sidebar load | <1s | Single API call |

### NFR-2: Reliability

- **No knowledge graph, no topics**: If `LORE_KNOWLEDGE_GRAPH` is not enabled, `topics` returns a clear message: "Enable knowledge graph to use topics." Not an error — a guidance message.
- **Missing entity**: `topic_detail` for a non-existent name returns 404 with helpful message including close matches (fuzzy entity name search via alias lookup).
- **LLM failure**: Falls back to structured listing. Never fails entirely due to LLM.

### NFR-3: Data Freshness

- Topics list is always fresh (queries `mention_count` directly from the entity table)
- Topic summaries (LLM) are cached with 1-hour TTL + invalidation on new mention
- Topic detail memory list is always fresh (queries mentions + memories on demand)

### NFR-4: Backward Compatibility

- New endpoints only — no changes to existing endpoints
- New MCP tools only — no changes to existing tools
- New types (`TopicSummary`, `TopicDetail`, `RelatedEntity`) are additive
- Knowledge graph schema unchanged — topics are a read-only view over existing data

### NFR-5: Testing

- Unit tests: topic detection (threshold logic), detail builder, summary generation, caching
- Integration tests: MCP tools, REST endpoints, CLI commands
- Edge cases: entity with exactly 3 mentions, entity at threshold boundary, entity with no relationships, entity with 100+ memories
- Web UI: manual testing (topic sidebar renders, click-to-highlight works)

---

## 5. API Design

### 5.1 MCP Tools

```python
@mcp.tool(
    description=(
        "List auto-detected topics — recurring concepts across multiple memories. "
        "USE THIS WHEN: you want to know what major themes exist, find concepts "
        "to explore, or get an overview of knowledge areas. Topics are entities "
        "from the knowledge graph appearing in 3+ memories."
    ),
)
def topics(
    entity_type: Optional[str] = None,
    min_mentions: int = 3,
    limit: int = 20,
    project: Optional[str] = None,
) -> str:

@mcp.tool(
    description=(
        "Get everything Lore knows about a topic — first mention, key decisions, "
        "related entities, timeline, linked memories. USE THIS WHEN: you need deep "
        "context on a concept, want decision history, or are exploring a knowledge area. "
        "Works without LLM (chronological list) — enhanced with LLM (narrative summary)."
    ),
)
def topic_detail(
    name: str,
    max_memories: int = 20,
    format: str = "brief",
) -> str:
```

### 5.2 REST Endpoints

```
GET /v1/topics?entity_type=&min_mentions=3&limit=50&project=
GET /v1/topics/:name?max_memories=20&format=brief
```

### 5.3 SDK Methods

```python
class Lore:
    def list_topics(
        self,
        *,
        entity_type: Optional[str] = None,
        min_mentions: int = 3,
        limit: int = 50,
        project: Optional[str] = None,
    ) -> List[TopicSummary]:

    def topic_detail(
        self,
        name: str,
        *,
        max_memories: int = 20,
        include_summary: bool = True,
    ) -> TopicDetail:
```

### 5.4 CLI

```
lore topics [NAME] [OPTIONS]

Arguments:
  NAME              Topic name to show detail (optional — omit to list all)

Options:
  --type TEXT        Filter by entity type
  --min-mentions INT Minimum memory references (default: 3)
  --format TEXT      Output format: brief, detailed (default: brief)
  --limit INT        Max topics in list view (default: 50)
```

---

## 6. Integration Patterns

### 6.1 OpenClaw (Hooks + MCP)

**Auto-inject enhancement:** Consider adding topic mentions to the `lore-retrieve` auto-inject. When a recalled memory references a topic entity, append a one-liner: "Related topic: {name} ({mention_count} memories) — call topic_detail for more."

This is a lightweight pointer, not the full topic. It guides the agent to dig deeper when relevant.

**MCP tools:** Both `topics` and `topic_detail` are available for explicit agent queries.

### 6.2 Claude Code (MCP + CLAUDE.md)

Add to CLAUDE.md:
```markdown
## Memory (Lore)

- Call `topics` to see what recurring concepts Lore knows about.
- Call `topic_detail <name>` for comprehensive context on any concept.
```

### 6.3 Codex (MCP Only)

Tool descriptions are the only integration point. Both tool descriptions explicitly say when to use them.

### 6.4 Cursor (MCP + .cursorrules)

Same pattern as Claude Code. Add to `.cursorrules` via `lore setup cursor`.

---

## 7. Open Questions

### OQ-1: Should topics be limited to knowledge graph entities, or should we also detect topics from tags/types?

**Context:** Some users don't enable the knowledge graph. Tags and memory types also indicate recurring concepts.

**Recommendation:** Knowledge graph entities only for v1. The entity system already handles deduplication, aliases, and relationship tracking. Building a parallel topic detection system from tags would duplicate effort. If users want topics without the knowledge graph, that's a feature request for v2.

### OQ-2: Should topic summaries be stored as memories themselves?

**Context:** Storing summaries as memories would make them recallable via semantic search. "Tell me about our auth decisions" would match a topic summary memory.

**Recommendation:** No for v1. Topic summaries are a cached view, not a source of truth. Storing them as memories creates a meta-memory problem (summaries of summaries, circular references). Keep them in a lightweight cache. Revisit if users want to `recall` topic summaries directly.

### OQ-3: How should the web UI topics sidebar interact with the existing graph filter?

**Context:** E1 already has filters (project, type, date range). Adding a topics sidebar creates two entry points into the same graph.

**Recommendation:** Topics sidebar is a _curated_ filter. Clicking a topic is equivalent to filtering the graph to show that entity + its 1-hop neighbors. The sidebar and existing filters coexist — topics are a shortcut, not a replacement.

### OQ-4: Should we pre-compute topic lists or always compute on demand?

**Recommendation:** On-demand for v1. The query is fast (indexed `mention_count` column). Pre-computation adds cache invalidation complexity for minimal performance gain. The `list_entities` query with a `mention_count >=` filter is O(entities), and entities are typically <1000 even in heavy-use Lore instances.

---

## Appendix A: Implementation Order (Both Epics)

### E3 (Pre-Compaction Hook)

1. **Types**: Add `session_snapshot` to `VALID_MEMORY_TYPES`, configure decay
2. **SDK**: `Lore.save_snapshot()` method
3. **MCP tool**: `save_snapshot`
4. **REST endpoint**: `POST /v1/snapshots`
5. **CLI**: `lore snapshot save` subcommand
6. **LLM extraction**: Optional enhancement (prompt + extraction logic)
7. **OpenClaw hook**: `lore-precompact` handler
8. **Setup commands**: Update CLAUDE.md / .cursorrules protocol instructions
9. **Tests**: TDD each layer

### E4 (Topic Notes)

1. **Types**: `TopicSummary`, `TopicDetail`, `RelatedEntity` dataclasses
2. **SDK**: `Lore.list_topics()`, `Lore.topic_detail()`
3. **MCP tools**: `topics`, `topic_detail`
4. **REST endpoints**: `GET /v1/topics`, `GET /v1/topics/:name`
5. **CLI**: `lore topics` command
6. **LLM summaries**: Optional narrative generation + caching
7. **Web UI**: Topics sidebar in E1
8. **Setup commands**: Update CLAUDE.md / .cursorrules
9. **Tests**: TDD each layer

### Parallelization

E3 and E4 are independent — they can be developed in parallel. E3 is smaller (~15 stories) and should complete first. E4 depends on the knowledge graph being enabled but not on E3.

## Appendix B: Cross-Epic Dependencies

| Dependency | Direction | Notes |
|-----------|-----------|-------|
| E2 (Recent Activity) → E3 | E3 benefits from E2 | Session snapshots surface via `recent_activity` |
| E1 (Graph Viz) → E4 | E4 extends E1 | Topics sidebar in web UI |
| E4 → E6 (Approval UX) | E6 builds on E4 | Topic notes inform the approval flow |
| Knowledge Graph (F1) → E4 | E4 requires F1 | Topics are built on entities + mentions |
