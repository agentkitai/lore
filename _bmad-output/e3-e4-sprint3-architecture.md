# Sprint 3: E3 + E4 — Technical Architecture

**Version:** v0.12.0
**Author:** Winston (Solutions Architect)
**Date:** 2026-03-14

---

# PART 1: E3 — Pre-Compaction Hook (Context Rescue)

## 1. Architecture Overview

E3 adds a single new memory type (`session_snapshot`) and a thin save path through the existing `remember` pipeline. No new tables, no new store methods — snapshots are regular `Memory` objects with high importance and a dedicated type.

```
┌──────────────────────────────────────────────────────────┐
│                    Entry Points                          │
│                                                          │
│  OpenClaw Hook ──► REST POST /v1/snapshots               │
│  MCP Tool ───────► save_snapshot() ──┐                   │
│  CLI ────────────► lore snapshot save │                   │
│                                      ▼                   │
│                            Lore.save_snapshot()          │
│                                      │                   │
│                     ┌────────────────┼──────────────┐    │
│                     │ LLM available  │ No LLM       │    │
│                     │ & content>500  │              │    │
│                     ▼                ▼              │    │
│              Extract key       Save raw content     │    │
│              points via LLM                         │    │
│                     │                │              │    │
│                     └───────┬────────┘              │    │
│                             ▼                       │    │
│                     self.remember(                   │    │
│                       type="session_snapshot",       │    │
│                       tier="long",                   │    │
│                       importance_score=0.95          │    │
│                     )                                │    │
│                             │                       │    │
│                             ▼                       │    │
│                    Existing pipeline:                │    │
│                    embed → redact → store            │    │
└──────────────────────────────────────────────────────────┘
```

**Key insight:** No new storage infrastructure. Snapshots flow through the same `remember()` pipeline (embedding, redaction, storage) and surface naturally via `recent_activity` since they're recent, high-importance memories.

## 2. Data Model Changes

### 2.1 New Memory Type: `session_snapshot`

**File:** `src/lore/types.py`

Add `"session_snapshot"` to `VALID_MEMORY_TYPES`:

```python
VALID_MEMORY_TYPES = frozenset(
    list(DECAY_HALF_LIVES.keys())
    + [
        "general",
        "fact",
        "preference",
        "debug",
        "pattern",
        "session_snapshot",   # ← NEW
    ]
)
```

### 2.2 Decay Configuration

Add to `TIER_DECAY_HALF_LIVES["long"]`:

```python
"long": {
    ...
    "session_snapshot": 7,  # 7-day half-life: recent sessions valuable, old ones fade
}
```

Also add to `"working"` and `"short"` tiers for completeness (though snapshots default to `"long"`):

```python
"working": { ..., "session_snapshot": 0.5 },
"short":   { ..., "session_snapshot": 3 },
```

### 2.3 Snapshot Memory Shape

A session snapshot is a standard `Memory` with these defaults:

| Field | Value | Rationale |
|-------|-------|-----------|
| `type` | `"session_snapshot"` | Distinct type for filtering |
| `tier` | `"long"` | Survive beyond working/short decay |
| `importance_score` | `0.95` | Near-max; always surfaces in recall/recent_activity |
| `metadata.session_id` | UUID hex (12 chars) | Groups snapshots by session |
| `metadata.title` | Auto or user-provided | Human-readable label |
| `metadata.extraction_method` | `"raw"` or `"llm"` | Tracks how content was processed |
| `tags` | `["session_snapshot", session_id]` + user tags | Enables type+session filtering |

No schema changes needed — all fields exist on `Memory` already.

## 3. SDK Layer

### 3.1 `Lore.save_snapshot()` Method

**File:** `src/lore/lore.py`

```python
def save_snapshot(
    self,
    content: str,
    *,
    title: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Memory:
```

**Implementation flow:**

1. Validate `content` is non-empty
2. Generate `session_id` = `uuid4().hex[:12]` if not provided
3. Generate `title` = `content[:80].strip()` if not provided
4. **LLM extraction** (optional):
   - Guard: `self._enrichment_enabled and len(content) > 500`
   - Call `self._enrichment_pipeline.client.chat(...)` with extraction prompt (see §3.2)
   - On success: extracted output → `content`, original → `context`
   - On failure: log warning, fall back to raw content
5. Assemble tags: `["session_snapshot", session_id] + (tags or [])`
6. Assemble metadata: `{"session_id": session_id, "title": title, "extraction_method": method}`
7. Call `self.remember(content=..., type="session_snapshot", tier="long", context=..., tags=..., metadata=..., confidence=1.0)`
8. Post-save: set `importance_score = 0.95` via `self._store.update(memory)` (since `remember()` computes importance from confidence/votes, we override)
9. Return the saved `Memory`

**Why override importance post-save:** The existing `compute_importance()` in `remember()` calculates from confidence/votes/access_count. Snapshots need a fixed high score to guarantee surfacing. Overriding after save is simpler than adding a parameter to `remember()`.

### 3.2 LLM Extraction Prompt

```python
_SNAPSHOT_EXTRACTION_PROMPT = """You are extracting key information from a conversation session that is about to be compacted. Extract ONLY what would be critical to know in a future session. Be concise.

Extract:
1. Key decisions made (with rationale)
2. Current task state (what's in progress, what's blocked)
3. Action items or next steps
4. Important context that wouldn't be obvious from code alone

Format as a bulleted list. Omit categories with nothing to report. Max 300 words."""
```

Use the existing `self._enrichment_pipeline.client.chat()` path. No new LLM client needed.

## 4. MCP Tool

**File:** `src/lore/mcp/server.py`

```python
@mcp.tool(
    description=(
        "Save a session snapshot to preserve important context before it's lost. "
        "USE THIS when your conversation is getting long, when you've made "
        "important decisions, or before ending a complex session. The snapshot "
        "will surface in the next session's recent_activity so you pick up "
        "where you left off."
    ),
)
def save_snapshot(
    content: str,
    title: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> str:
    lore = _get_lore()
    memory = lore.save_snapshot(
        content, title=title, session_id=session_id, tags=tags
    )
    meta = memory.metadata or {}
    return (
        f"Snapshot saved (id={memory.id}, session={meta.get('session_id', '?')}, "
        f"method={meta.get('extraction_method', 'raw')}). "
        f"It will surface in the next session's recent_activity."
    )
```

Follows the exact pattern of existing MCP tools: thin wrapper calling SDK, returning formatted string.

## 5. REST Endpoint

**File:** `src/lore/server/routes/snapshots.py` (new file)

```python
router = APIRouter(prefix="/v1/snapshots", tags=["snapshots"])

@router.post("", status_code=201)
async def create_snapshot(body: SnapshotCreateRequest, auth: AuthContext = Depends(require_role("writer", "admin"))) -> SnapshotCreateResponse:
```

**Request/Response models** in `src/lore/server/models.py`:

```python
class SnapshotCreateRequest(BaseModel):
    content: str
    title: Optional[str] = None
    session_id: Optional[str] = None
    tags: Optional[List[str]] = None
    project: Optional[str] = None

class SnapshotCreateResponse(BaseModel):
    id: str
    session_id: str
    title: str
    extraction_method: str
    created_at: str
```

**Implementation:** The REST endpoint creates the memory directly in Postgres (same pattern as `create_memory` in `memories.py`) with the snapshot defaults. It does NOT go through the SDK — the server has its own store layer. The endpoint:

1. Generates session_id/title if not provided
2. Creates memory with `type="session_snapshot"`, `tier="long"`, high importance
3. Fires enrichment (LLM extraction) as background task if enabled
4. Returns 201 with snapshot metadata

**Register router** in `src/lore/server/app.py` alongside existing routers.

## 6. CLI Integration

**File:** `src/lore/cli.py`

Add `snapshot save` subcommand under existing `snapshot` group:

```python
@snapshot_group.command("save")
@click.argument("content")
@click.option("--title", default=None)
@click.option("--session-id", default=None)
def snapshot_save(content, title, session_id):
    lore = _get_lore()
    memory = lore.save_snapshot(content, title=title, session_id=session_id)
    click.echo(f"Snapshot saved: {memory.id}")
```

The existing `lore snapshot` (export) command is unchanged. `lore snapshot save` is a new subcommand.

## 7. OpenClaw Hook

**File:** `hooks/lore-precompact.ts` (new, in hooks directory)

```
Event: session:compacting
Blocking: false
Timeout: 3000ms

Flow:
1. Receive compaction event with messages payload
2. Concatenate message contents (cap at 4000 chars)
3. POST to /v1/snapshots with content + session_id from event
4. Log result, never throw
```

The hook uses the REST endpoint, not the MCP tool — hooks are TypeScript processes, not MCP clients.

**Fallback:** If OpenClaw doesn't expose `session:compacting`, use `message:sent` with a heuristic: fire when estimated context > 80% of window.

## 8. Surfacing in Next Session

**No code changes needed.** Session snapshots surface automatically because:

1. `recent_activity()` queries recent memories by `created_at` — snapshots are recent
2. `importance_score = 0.95` means they rank at the top of any importance-weighted query
3. The `type = "session_snapshot"` tag allows distinct formatting in output

The only addition: in the `recent_activity` output formatter, detect `type == "session_snapshot"` and prefix with `[Session Snapshot]` for visual distinction.

## 9. File Change Summary

| File | Change | Size |
|------|--------|------|
| `src/lore/types.py` | Add `session_snapshot` to types + decay config | ~5 lines |
| `src/lore/lore.py` | Add `save_snapshot()` method + extraction prompt | ~60 lines |
| `src/lore/mcp/server.py` | Add `save_snapshot` MCP tool | ~20 lines |
| `src/lore/server/routes/snapshots.py` | New file: REST endpoint | ~60 lines |
| `src/lore/server/models.py` | Add request/response models | ~15 lines |
| `src/lore/server/app.py` | Register snapshots router | ~2 lines |
| `src/lore/cli.py` | Add `snapshot save` subcommand | ~15 lines |
| `src/lore/recent.py` | Label snapshots in output formatter | ~5 lines |
| `hooks/lore-precompact.ts` | New file: OpenClaw hook | ~40 lines |

**Total: ~220 lines of production code across 9 files.**

---

# PART 2: E4 — Topic Notes (Auto-Summaries / Concept Hubs)

## 1. Architecture Overview

E4 is a **read-only aggregation layer** over the existing knowledge graph. No new tables — it queries `entities`, `entity_mentions`, `relationships`, and `memories` to build topic views on demand.

```
┌──────────────────────────────────────────────────────────┐
│                    Entry Points                          │
│                                                          │
│  CLI ────────────► lore topics [name]                    │
│  MCP Tool ───────► topics() / topic_detail()             │
│  REST ───────────► GET /v1/topics[/:name]                │
│  Web UI ─────────► Sidebar → REST API                    │
│                         │                                │
│                         ▼                                │
│               Lore.list_topics()                         │
│               Lore.topic_detail()                        │
│                         │                                │
│              ┌──────────┴──────────┐                     │
│              ▼                     ▼                     │
│    store.list_entities()    store.get_entity_by_name()   │
│    (mention_count >= N)     store.get_entity_mentions_   │
│              │              for_entity()                  │
│              │              store.query_relationships()   │
│              │              store.get() × N               │
│              │                     │                      │
│              │              ┌──────┴──────┐               │
│              │              ▼             ▼               │
│              │        LLM summary   Structured list      │
│              │        (cached)      (fallback)            │
│              │              │             │               │
│              ▼              └──────┬──────┘               │
│         TopicSummary[]       TopicDetail                  │
└──────────────────────────────────────────────────────────┘
```

**Key insight:** Topics are a pure query/aggregation feature. The knowledge graph already tracks entities with `mention_count`, aliases, and relationships. E4 just provides a user-facing view.

## 2. Data Model

### 2.1 New Dataclasses

**File:** `src/lore/types.py`

```python
@dataclass
class TopicSummary:
    """A topic in the list view."""
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
    related_entities: List["RelatedEntity"]
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
    relationship: str
    direction: str  # "outgoing" | "incoming"
```

These are output-only types — no persistence changes.

### 2.2 Topic Summary Cache

**File:** `src/lore/graph/cache.py` (extend existing `EntityCache`)

Add a `TopicSummaryCache` alongside `EntityCache`:

```python
class TopicSummaryCache:
    """In-memory cache for LLM-generated topic summaries."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl = ttl_seconds
        self._cache: Dict[str, Tuple[str, str, float]] = {}
        # key: entity_id → value: (summary_text, method, cached_at_timestamp)

    def get(self, entity_id: str) -> Optional[Tuple[str, str]]:
        entry = self._cache.get(entity_id)
        if entry is None:
            return None
        text, method, cached_at = entry
        if time.time() - cached_at > self.ttl:
            del self._cache[entity_id]
            return None
        return text, method

    def set(self, entity_id: str, summary: str, method: str) -> None:
        self._cache[entity_id] = (summary, method, time.time())

    def invalidate(self, entity_id: str) -> None:
        self._cache.pop(entity_id, None)
```

**Why in-memory, not persistent:** Topic summaries are cheap to regenerate (one LLM call), change frequently as new memories arrive, and are only needed during active sessions. A persistent cache adds complexity for minimal benefit.

**Invalidation trigger:** When `EntityManager.ingest_from_enrichment()` or `ingest_from_fact()` updates an entity's `mention_count`, call `topic_summary_cache.invalidate(entity_id)`. This is a single-line addition to two existing methods.

## 3. SDK Layer

### 3.1 `Lore.list_topics()` Method

**File:** `src/lore/lore.py`

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

**Implementation flow:**

1. Guard: if knowledge graph not enabled, return empty list (caller shows guidance message)
2. Call `self._store.list_entities(entity_type=entity_type, limit=0)` to get all entities
3. Filter: `mention_count >= min_mentions`
4. **Project filter** (if specified): For each candidate entity, check if any of its mentions link to a memory in the target project. Post-filter in Python for v1. This is acceptable because entities are typically <1000 and the filter short-circuits on first match.
5. Sort by `mention_count` descending
6. For each entity, count related entities via `self._store.query_relationships([entity.id])`
7. Truncate to `limit`
8. Return `TopicSummary` list

**Performance note:** Step 6 is N queries for N topics. For v1, cap at `limit=50` and this is fine. For v2, add a batch relationship count query to the store.

### 3.2 `Lore.topic_detail()` Method

**File:** `src/lore/lore.py`

```python
def topic_detail(
    self,
    name: str,
    *,
    max_memories: int = 20,
    include_summary: bool = True,
) -> Optional[TopicDetail]:
```

**Implementation flow:**

1. Resolve entity: `self._store.get_entity_by_name(name.lower())` → if None, try `get_entity_by_alias(name.lower())` → if None, return None
2. Get mentions: `self._store.get_entity_mentions_for_entity(entity.id)`
3. Load memories: `self._store.get(mention.memory_id)` for each mention, cap at `max_memories`, sort by `created_at` descending
4. Get relationships: `self._store.query_relationships([entity.id])` → resolve target/source entity names → build `RelatedEntity` list
5. **Summary generation** (if `include_summary`):
   a. Check `self._topic_summary_cache.get(entity.id)`
   b. Cache hit → use cached summary
   c. Cache miss + LLM available → generate via prompt (see §3.3), cache result
   d. Cache miss + no LLM → `summary_method = "structured"`, no narrative summary
6. Assemble and return `TopicDetail`

### 3.3 LLM Summary Prompt

```python
_TOPIC_SUMMARY_PROMPT = """You are summarizing everything known about a specific topic based on memory entries. Write 2-4 sentences covering: what it is, key decisions made about it, and its current state. Be factual — only state what the memories say.

Topic: {entity_name} ({entity_type})
Related entities: {related_names}
Memories (chronological):
{memory_contents}"""
```

Uses existing enrichment LLM client. Memory contents are truncated to fit within token budget (~3000 chars of memory text).

### 3.4 Initialization

In `Lore.__init__()`, create the topic summary cache:

```python
self._topic_summary_cache = TopicSummaryCache(ttl_seconds=3600)
```

Wire invalidation into the entity manager by passing the cache reference. When `EntityManager.ingest_from_enrichment()` or `ingest_from_fact()` increments `mention_count`, call `cache.invalidate(entity_id)`.

## 4. MCP Tools

**File:** `src/lore/mcp/server.py`

### 4.1 `topics` Tool

```python
@mcp.tool(
    description=(
        "List auto-detected topics — recurring concepts across multiple memories. "
        "USE THIS WHEN: you want to know what major themes exist, find concepts "
        "to explore, or get an overview of knowledge areas."
    ),
)
def topics(
    entity_type: Optional[str] = None,
    min_mentions: int = 3,
    limit: int = 20,
    project: Optional[str] = None,
) -> str:
    lore = _get_lore()
    if not lore._knowledge_graph_enabled:
        return "Topics require the knowledge graph. Set LORE_KNOWLEDGE_GRAPH=true to enable."
    results = lore.list_topics(
        entity_type=entity_type, min_mentions=min_mentions,
        limit=limit, project=project or _project(),
    )
    if not results:
        return "No topics found meeting the threshold."
    lines = [f"Topics ({len(results)} found, threshold: {min_mentions}+ mentions):\n"]
    for t in results:
        lines.append(f"- {t.name} ({t.entity_type}) — {t.mention_count} memories, "
                      f"{t.related_entity_count} related entities")
    return "\n".join(lines)
```

### 4.2 `topic_detail` Tool

```python
@mcp.tool(
    description=(
        "Get everything Lore knows about a topic — first mention, key decisions, "
        "related entities, timeline, linked memories. USE THIS WHEN: you need deep "
        "context on a concept or want decision history."
    ),
)
def topic_detail(
    name: str,
    max_memories: int = 20,
    format: str = "brief",
) -> str:
    lore = _get_lore()
    detail = lore.topic_detail(name, max_memories=max_memories)
    if detail is None:
        return f"No topic found matching '{name}'."
    # Format output (brief vs detailed controls memory content length)
    ...
```

Output format follows existing MCP tool patterns: structured text, not JSON.

## 5. REST Endpoints

**File:** `src/lore/server/routes/topics.py` (new file)

```python
router = APIRouter(prefix="/v1/topics", tags=["topics"])
```

### 5.1 List Topics

```python
@router.get("")
async def list_topics(
    entity_type: Optional[str] = Query(None),
    min_mentions: int = Query(3, ge=1, le=100),
    limit: int = Query(50, ge=1, le=200),
    project: Optional[str] = Query(None),
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
```

**Implementation:** Queries the `entities` table directly in Postgres:

```sql
SELECT e.id, e.name, e.entity_type, e.mention_count,
       e.first_seen_at, e.last_seen_at,
       (SELECT COUNT(*) FROM relationships r
        WHERE r.source_entity_id = e.id OR r.target_entity_id = e.id) as related_count
FROM entities e
WHERE e.mention_count >= $1
  AND ($2::text IS NULL OR e.entity_type = $2)
ORDER BY e.mention_count DESC
LIMIT $3
```

Project filtering in the server requires a join through `entity_mentions → memories` to check `org_id`. For v1, post-filter in application code.

### 5.2 Topic Detail

```python
@router.get("/{name}")
async def get_topic_detail(
    name: str,
    max_memories: int = Query(20, ge=1, le=100),
    format: str = Query("brief"),
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
```

**Implementation:** Multi-query pattern:

1. Resolve entity by name (case-insensitive `ILIKE` or alias lookup)
2. Get mentions → join to memories table → return memories with content
3. Get relationships → resolve entity names
4. Optionally generate LLM summary (background task, cache in-memory)
5. Return assembled JSON response per PRD spec

## 6. CLI Integration

**File:** `src/lore/cli.py`

```python
@cli.command("topics")
@click.argument("name", required=False)
@click.option("--type", "entity_type", default=None)
@click.option("--min-mentions", default=3, type=int)
@click.option("--format", "fmt", default="brief", type=click.Choice(["brief", "detailed"]))
@click.option("--limit", default=50, type=int)
def topics_cmd(name, entity_type, min_mentions, fmt, limit):
    lore = _get_lore()
    if name:
        detail = lore.topic_detail(name, max_memories=20, include_summary=True)
        # render detail
    else:
        results = lore.list_topics(entity_type=entity_type, min_mentions=min_mentions, limit=limit)
        # render list
```

Single command with optional `NAME` argument — no subcommand needed.

## 7. Web UI Integration (E1 Sidebar)

### 7.1 Frontend Changes

**Files:** `src/lore/server/routes/ui.py` (static assets), frontend JS

Add a topics sidebar to the existing E1 graph visualization page:

```
┌──────────────────────────────────────────────────────┐
│  Topics        │              Graph                  │
│  ────────────  │                                     │
│  ● Lore (42)   │         [D3 force graph]            │
│  ● Auth (18)   │                                     │
│  ● Postgres(12)│                                     │
│  ● React (8)   │                                     │
│  ● Docker (5)  │                                     │
│                │                                     │
│                │                                     │
│                ├──────────────────────────────────────│
│                │  Topic: Auth                         │
│                │  Type: concept | 18 memories         │
│                │  Related: OAuth, JWT, Middleware      │
│                │  Summary: Auth handles user...       │
│                │  ─────────────────────────────────── │
│                │  Memories:                           │
│                │  • 2026-03-12: Decided to use...     │
│                │  • 2026-03-10: Investigated...       │
└──────────────────────────────────────────────────────┘
```

**API calls:**
- Sidebar load: `GET /v1/topics?limit=20` on page load
- Topic click: `GET /v1/topics/{name}` → render detail panel + highlight entity node in graph

**Graph interaction:** Clicking a topic in the sidebar calls the existing `centerOnNode(entityId)` function from E1's graph code, then highlights 1-hop neighbors.

### 7.2 No Backend Changes for Web UI

The REST endpoints (§5) serve both the API and the web UI. The web UI is purely a frontend addition.

## 8. Cache Invalidation Flow

```
New memory saved
     │
     ▼
EntityManager.ingest_from_enrichment()
     │
     ├──► entity.mention_count += 1
     │
     ├──► topic_summary_cache.invalidate(entity.id)
     │
     └──► (entity may now cross threshold → becomes a topic)

Next topic_detail(name) request
     │
     ├──► cache miss → regenerate LLM summary
     │
     └──► cache with new data
```

**Lazy regeneration:** Summaries are only regenerated when requested after invalidation. No background jobs, no eager recomputation.

## 9. Dependency: Knowledge Graph Required

Topics require `LORE_KNOWLEDGE_GRAPH=true`. When disabled:

- `list_topics()` returns `[]`
- `topic_detail()` returns `None`
- MCP tools return guidance: "Enable knowledge graph to use topics."
- REST endpoints return `200` with empty results (not 500)
- CLI shows: "Topics require the knowledge graph. Run `lore config set knowledge_graph true`."

## 10. File Change Summary

| File | Change | Size |
|------|--------|------|
| `src/lore/types.py` | Add `TopicSummary`, `TopicDetail`, `RelatedEntity` dataclasses | ~30 lines |
| `src/lore/lore.py` | Add `list_topics()`, `topic_detail()`, init cache | ~100 lines |
| `src/lore/graph/cache.py` | Add `TopicSummaryCache` class | ~30 lines |
| `src/lore/graph/entities.py` | Add cache invalidation calls in ingest methods | ~4 lines |
| `src/lore/mcp/server.py` | Add `topics`, `topic_detail` MCP tools | ~50 lines |
| `src/lore/server/routes/topics.py` | New file: REST endpoints | ~100 lines |
| `src/lore/server/app.py` | Register topics router | ~2 lines |
| `src/lore/cli.py` | Add `topics` command | ~40 lines |
| `src/lore/server/routes/ui.py` | Topics sidebar JS/HTML | ~80 lines |

**Total: ~440 lines of production code across 9 files.**

---

# Cross-Epic Notes

## Implementation Order

E3 and E4 are fully independent — develop in parallel.

**E3 order:** types → SDK method → MCP tool → REST endpoint → CLI → LLM extraction → hook → setup commands

**E4 order:** types → cache → SDK methods → MCP tools → REST endpoints → CLI → LLM summaries → web UI sidebar

## Shared Patterns

Both epics follow the established Lore pattern:
1. Types in `types.py`
2. Logic in `lore.py` (SDK facade)
3. MCP tools in `mcp/server.py` (thin wrappers)
4. REST in `server/routes/` (FastAPI routers)
5. CLI in `cli.py` (Click commands)

## Testing Strategy

Both epics use TDD at each layer:
- **Unit:** SDK methods with `MemoryStore` (in-memory)
- **Integration:** MCP tools via FastMCP test client, REST via `httpx.AsyncClient`
- **Edge cases:** Empty content (E3), threshold boundaries (E4), LLM failures (both)
- **No mocks for store:** Use `MemoryStore` directly — it implements the full `Store` ABC
