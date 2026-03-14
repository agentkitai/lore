# E2: Recent Activity Summary — Technical Architecture

**Epic:** E2 — Session Context
**Version:** v0.10.0
**Author:** Winston (Solutions Architect)
**Date:** 2026-03-14
**Status:** Draft

---

## 1. Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Surface Layer                                │
│                                                                     │
│  ┌──────────────┐   ┌──────────────────┐   ┌───────────────────┐   │
│  │  MCP Tool     │   │  REST Endpoint   │   │   CLI Command     │   │
│  │ recent_       │   │ GET /v1/recent   │   │  lore recent      │   │
│  │ activity()    │   │                  │   │                   │   │
│  └──────┬───────┘   └────────┬─────────┘   └────────┬──────────┘   │
│         │                    │                       │              │
└─────────┼────────────────────┼───────────────────────┼──────────────┘
          │                    │                       │
          ▼                    ▼                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       SDK Layer (lore.py)                            │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Lore.recent_activity(hours, project, format, max_memories)  │   │
│  │                                                              │   │
│  │  1. Clamp parameters                                        │   │
│  │  2. Compute `since` cutoff (now - hours)                    │   │
│  │  3. Call store.list(since=..., project=..., limit=...)      │   │
│  │  4. Filter expired                                          │   │
│  │  5. Group by project                                        │   │
│  │  6. Sort within groups (created_at DESC)                    │   │
│  │  7. (Optional) LLM summarize per group                     │   │
│  │  8. Return RecentActivityResult                             │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         │                                           │
└─────────────────────────┼───────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Store Layer                                    │
│                                                                     │
│  ┌──────────────────┐            ┌──────────────────┐              │
│  │  SqliteStore      │            │  HttpStore        │              │
│  │  .list(since=...) │            │  .list(since=...) │              │
│  │                   │            │                   │              │
│  │  WHERE created_at │            │  ?since=ISO8601   │              │
│  │  >= ? AND ...     │            │  → server-side    │              │
│  └──────────────────┘            └──────────────────┘              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     Formatting Layer (NEW)                           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  src/lore/recent.py                                          │   │
│  │                                                              │   │
│  │  RecentActivityResult (dataclass)                            │   │
│  │  format_brief(groups) -> str                                 │   │
│  │  format_detailed(groups) -> str                              │   │
│  │  format_structured(groups) -> Dict                           │   │
│  │  group_memories_by_project(memories) -> List[ProjectGroup]   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### New Files

| File | Purpose |
|------|---------|
| `src/lore/recent.py` | Grouping logic, formatting (brief/detailed/structured), `RecentActivityResult` dataclass |
| `src/lore/server/routes/recent.py` | REST endpoint `GET /v1/recent` |
| `tests/test_recent.py` | Unit tests for grouping + formatting |
| `tests/test_recent_integration.py` | Integration tests for MCP tool, REST, CLI |

### Modified Files

| File | Change |
|------|--------|
| `src/lore/types.py` | Add `RecentActivityResult`, `ProjectGroup` dataclasses |
| `src/lore/store/base.py` | Add `since: Optional[str] = None` parameter to `list()` |
| `src/lore/store/sqlite.py` | Implement `since` filter in `list()` |
| `src/lore/store/http.py` | Pass `since` as query param in `list()` |
| `src/lore/lore.py` | Add `recent_activity()` method |
| `src/lore/mcp/server.py` | Add `recent_activity` MCP tool, update `instructions` |
| `src/lore/cli.py` | Add `recent` subcommand |
| `src/lore/server/app.py` | Include `recent_router` |

---

## 2. Data Model Changes

### 2.1 New Dataclasses (`types.py`)

```python
@dataclass
class ProjectGroup:
    """A group of memories belonging to one project."""
    project: str
    memories: List[Memory]
    count: int
    summary: Optional[str] = None  # LLM-generated summary, if available


@dataclass
class RecentActivityResult:
    """Result of a recent_activity query."""
    groups: List[ProjectGroup]
    total_count: int
    hours: int
    has_llm_summary: bool = False
    query_time_ms: float = 0.0
    generated_at: str = ""
```

### 2.2 Store ABC Change (`store/base.py`)

Add `since` parameter to the existing `list()` signature:

```python
@abstractmethod
def list(
    self,
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    limit: Optional[int] = None,
    include_archived: bool = False,
    since: Optional[str] = None,  # NEW — ISO 8601 datetime, inclusive
) -> List[Memory]:
```

Default `None` preserves backward compatibility — all existing callers are unaffected.

### 2.3 No New Tables or Indexes

The existing `idx_memories_created` index on `memories(created_at)` in SQLite is sufficient for the `WHERE created_at >= ?` filter. The Postgres server already has `created_at` indexed.

No schema migrations needed.

---

## 3. Database Changes

### 3.1 SQLite Store (`sqlite.py`)

Add `since` condition to `list()`:

```python
def list(
    self,
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    limit: Optional[int] = None,
    include_archived: bool = False,
    since: Optional[str] = None,
) -> List[Memory]:
    query = "SELECT * FROM memories"
    params: List[Any] = []
    conditions: List[str] = []
    if not include_archived:
        conditions.append("archived = 0")
    if project is not None:
        conditions.append("project = ?")
        params.append(project)
    if type is not None:
        conditions.append("type = ?")
        params.append(type)
    if tier is not None:
        conditions.append("tier = ?")
        params.append(tier)
    if since is not None:                      # NEW
        conditions.append("created_at >= ?")   # NEW
        params.append(since)                   # NEW
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = self._conn.execute(query, params).fetchall()
    return [self._row_to_memory(r) for r in rows]
```

**Query plan:** Uses `idx_memories_created` for the range scan. For `since` + `project`, SQLite will pick whichever index is more selective. Given typical usage (filtering last 24h from a small-to-moderate DB), this is efficient without a composite index.

**Embeddings excluded from result set:** The `list()` query returns `SELECT *` which includes the `embedding` BLOB column. For recent_activity, embeddings are not needed. However, adding a projection optimization is deferred — the PRD states <5MB for 500 memories is acceptable, and the Memory dataclass requires all fields. The `embedding` field will simply be unused by the formatting layer.

### 3.2 HttpStore (`http.py`)

Pass `since` as a query parameter:

```python
def list(
    self,
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    limit: Optional[int] = None,
    include_archived: bool = False,
    since: Optional[str] = None,
) -> List[Memory]:
    params: Dict[str, Any] = {}
    if project is not None:
        params["project"] = project
    if limit is not None:
        params["limit"] = limit
    if include_archived:
        params["include_archived"] = "true"
    if since is not None:                 # NEW
        params["since"] = since           # NEW

    resp = self._request("GET", "/v1/lessons", params=params)
    # ... rest unchanged ...
```

The Postgres backend's `/v1/lessons` endpoint needs to accept `since` and add `WHERE created_at >= $N` to its query. This is a server-side change in `routes/lessons.py`.

### 3.3 Postgres Server (`routes/recent.py` — NEW)

Direct query for the `GET /v1/recent` endpoint:

```sql
SELECT id, content, COALESCE(meta->>'type', 'general') AS type,
       COALESCE(meta->>'tier', 'long') AS tier,
       source, project, tags, created_at,
       importance_score
FROM memories
WHERE org_id = $1
  AND created_at >= $2
  AND (expires_at IS NULL OR expires_at > now())
ORDER BY created_at DESC
LIMIT $3
```

Parameters: `$1 = auth.org_id`, `$2 = now() - interval '{hours} hours'`, `$3 = max_memories`.

**Note:** This query intentionally does NOT load `embedding` — the `SELECT` is explicit (no `SELECT *`). This saves bandwidth and memory for large result sets.

---

## 4. API Contracts

### 4.1 MCP Tool — `recent_activity`

```python
@mcp.tool(
    description=(
        "Get a summary of recent memory activity across projects. "
        "CALL THIS AT THE START OF EVERY SESSION to maintain continuity "
        "with prior work. Returns the last N hours of memories grouped "
        "by project, regardless of semantic relevance to your current task. "
        "This catches recent decisions, changes, and context that semantic "
        "search would miss. Works without LLM (structured listing) — "
        "enhanced with LLM (concise summary of key points)."
    ),
)
def recent_activity(
    hours: int = 24,
    project: Optional[str] = None,
    format: str = "brief",
    max_memories: int = 50,
) -> str:
```

**Parameters:**

| Param | Type | Default | Validation | Description |
|-------|------|---------|------------|-------------|
| `hours` | int | 24 | Clamped to [1, 168] | Lookback window |
| `project` | str? | None | Falls back to `LORE_PROJECT` | Filter to project |
| `format` | str | "brief" | `brief`, `detailed`, `structured` | Output format |
| `max_memories` | int | 50 | Clamped to [1, 200] | Max memories returned |

**Return:** Formatted string (all formats return `str` from MCP — `structured` returns JSON string).

**Implementation in `mcp/server.py`:**

```python
def recent_activity(
    hours: int = 24,
    project: Optional[str] = None,
    format: str = "brief",
    max_memories: int = 50,
) -> str:
    try:
        lore = _get_lore()
        result = lore.recent_activity(
            hours=hours,
            project=project,
            format=format,
            max_memories=max_memories,
        )
        if format == "structured":
            import json
            return json.dumps(_result_to_dict(result), indent=2)
        return format_recent_activity(result, format)
    except Exception as e:
        return f"Failed to get recent activity: {e}"
```

### 4.2 REST Endpoint — `GET /v1/recent`

```
GET /v1/recent
  Query Parameters:
    hours: int       (default: 24, clamped: 1-168)
    project: string  (optional, overridden by auth key scope)
    format: string   (default: "brief", enum: brief|detailed|structured)
    max_memories: int (default: 50, clamped: 1-200)

  Headers:
    Authorization: Bearer <api_key>

  Response 200 OK (format=structured):
    Content-Type: application/json
    {
      "groups": [
        {
          "project": "lore",
          "memories": [
            {
              "id": "01JQ...",
              "content": "Architecture decision: use FastMCP...",
              "type": "lesson",
              "tier": "long",
              "created_at": "2026-03-14T10:30:00Z",
              "tags": ["architecture"],
              "importance_score": 0.85
            }
          ],
          "count": 3,
          "summary": null
        }
      ],
      "total_count": 12,
      "hours": 24,
      "generated_at": "2026-03-14T14:00:00Z",
      "has_llm_summary": false,
      "query_time_ms": 15.2
    }

  Response 200 OK (format=brief|detailed):
    Content-Type: application/json
    {
      "formatted": "## Recent Activity (last 24h)\n\n### lore (3)\n- ...",
      "total_count": 12,
      "hours": 24,
      "generated_at": "2026-03-14T14:00:00Z",
      "has_llm_summary": false,
      "query_time_ms": 15.2
    }

  Errors:
    401: Missing or invalid API key
    422: Invalid format value (not brief|detailed|structured)
```

**Pydantic models (in `server/routes/recent.py`):**

```python
class RecentMemoryItem(BaseModel):
    id: str
    content: str
    type: str
    tier: str
    created_at: str
    tags: List[str] = []
    importance_score: float = 1.0

class RecentProjectGroup(BaseModel):
    project: str
    memories: List[RecentMemoryItem]
    count: int
    summary: Optional[str] = None

class RecentActivityStructuredResponse(BaseModel):
    groups: List[RecentProjectGroup]
    total_count: int
    hours: int
    generated_at: str
    has_llm_summary: bool = False
    query_time_ms: float

class RecentActivityFormattedResponse(BaseModel):
    formatted: str
    total_count: int
    hours: int
    generated_at: str
    has_llm_summary: bool = False
    query_time_ms: float
```

### 4.3 CLI Command — `lore recent`

```
lore recent [OPTIONS]

Options:
  --hours INT       Lookback window in hours (default: 24)
  --project TEXT    Filter to specific project
  --format TEXT     Output format: brief, detailed (default: brief)
  --db TEXT         Database path (default: ~/.lore/memories.db)
```

Output is plain text, no markdown headers. Example:

```
Recent Activity (last 24h)

lore (3 memories)
  [14:30] lesson: Discovered that ONNX model loading takes 2s on first...
  [13:15] general: Architecture decision: use FastMCP for all new tools...
  [11:00] code: Fixed race condition in consolidation by adding lock...

my-app (2 memories)
  [16:00] general: Deployed v2.3.1 to staging, all tests passing...
  [14:45] lesson: Redis connection pool must be warmed before load test...
```

### 4.4 SDK Method — `Lore.recent_activity()`

```python
def recent_activity(
    self,
    *,
    hours: int = 24,
    project: Optional[str] = None,
    format: str = "brief",
    max_memories: int = 50,
) -> RecentActivityResult:
```

This is the central method. All surfaces (MCP, REST, CLI) call it. Formatting is done downstream by each surface.

---

## 5. Data Flow

### 5.1 MCP Tool Request Flow

```
Agent calls recent_activity(hours=24, format="brief")
  │
  ▼
mcp/server.py :: recent_activity()
  │ Resolve default project from LORE_PROJECT env
  ▼
lore.py :: Lore.recent_activity()
  │
  ├─ 1. Clamp hours to [1, 168], max_memories to [1, 200]
  ├─ 2. Compute since = (now_utc - timedelta(hours=hours)).isoformat()
  ├─ 3. Call self._store.list(project=project, since=since, limit=max_memories)
  ├─ 4. Filter out expired memories (expires_at < now)
  ├─ 5. Call group_memories_by_project(memories)
  ├─ 6. Build RecentActivityResult
  │     └─ If LLM available and format != "structured":
  │        └─ Summarize each group (see §5.3)
  └─ 7. Return RecentActivityResult
  │
  ▼
mcp/server.py :: format output
  │ format_recent_activity(result, "brief") → str
  ▼
Return formatted string to agent
```

### 5.2 REST Endpoint Request Flow

```
GET /v1/recent?hours=24&format=brief
  │
  ▼
server/routes/recent.py :: recent()
  │
  ├─ 1. Parse + validate query params (clamp hours, max_memories)
  ├─ 2. Resolve auth context (org_id, project scope)
  ├─ 3. Compute cutoff timestamp: now() - interval '{hours} hours'
  ├─ 4. Execute SQL query directly (no SDK layer for server)
  │     SELECT id, content, ... FROM memories
  │     WHERE org_id = $1 AND created_at >= $2 AND (expires_at IS NULL OR ...)
  │     ORDER BY created_at DESC LIMIT $3
  ├─ 5. Group rows by project in application code
  ├─ 6. Format response based on format param
  ├─ 7. Record analytics event (fire-and-forget, same pattern as /v1/retrieve)
  └─ 8. Return JSON response
```

### 5.3 LLM Summary Flow (Optional)

```
RecentActivityResult with groups
  │
  ├─ Check: self._enrichment_enabled and format != "structured"
  │   ├─ False → return as-is (structured listing)
  │   └─ True ↓
  │
  ├─ For each ProjectGroup:
  │   ├─ Concatenate memory contents (truncated to 2000 chars total)
  │   ├─ Call LLM with prompt:
  │   │   "Summarize these recent activities into 2-3 bullet points
  │   │    focusing on key decisions, changes, and open items."
  │   ├─ Set group.summary = LLM response
  │   └─ On LLM failure → leave group.summary = None (graceful fallback)
  │
  └─ Set result.has_llm_summary = True (if any group was summarized)
```

---

## 6. LLM-Optional Design

### Without LLM (default path)

All functionality works. Memories are listed chronologically within project groups:

1. **Brief format:** One line per memory — `[HH:MM] type: content[:100]...`
2. **Detailed format:** Full content + metadata per memory
3. **Structured format:** JSON with typed fields, no summarization

### With LLM (enrichment enabled)

When `LORE_ENRICHMENT_ENABLED=true` and an LLM provider is configured:

1. **Brief format:** Each project group gets a 2-3 bullet point summary replacing the raw listing
2. **Detailed format:** Summary header per group + full content below
3. **Structured format:** `summary` field populated in each `ProjectGroup`

### Decision: Where LLM summarization lives

The LLM call happens inside `Lore.recent_activity()`, NOT in the formatting layer. This keeps the SDK as the single source of truth for LLM integration. The formatting layer simply checks `group.summary is not None` to decide whether to render the summary or the raw listing.

### Fail-open behavior

| Failure | Behavior |
|---------|----------|
| LLM unavailable | Fall back to structured listing (no error) |
| LLM timeout | Fall back to structured listing |
| LLM returns garbage | Use raw content (validate LLM output is non-empty) |
| Store unavailable | Return empty result (groups=[], total_count=0) |

---

## 7. Formatting Module (`src/lore/recent.py`)

This new module contains all grouping and formatting logic, keeping it testable in isolation from the Lore class.

```python
"""Recent activity grouping and formatting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lore.types import Memory, ProjectGroup, RecentActivityResult


def group_memories_by_project(memories: List[Memory]) -> List[ProjectGroup]:
    """Group memories by project, sorted by newest first within each group.

    Groups are sorted by the most recent memory in each group.
    Memories with project=None are grouped under "default".
    """
    groups: Dict[str, List[Memory]] = {}
    for m in memories:
        key = m.project or "default"
        groups.setdefault(key, []).append(m)

    result = []
    for project, mems in groups.items():
        # Already sorted by created_at DESC from store query, but ensure
        mems.sort(key=lambda m: m.created_at, reverse=True)
        result.append(ProjectGroup(
            project=project,
            memories=mems,
            count=len(mems),
        ))

    # Sort groups by most recent memory
    result.sort(key=lambda g: g.memories[0].created_at, reverse=True)
    return result


def format_brief(result: RecentActivityResult) -> str:
    """Format as brief one-liner-per-memory output."""
    if not result.groups:
        return f"No recent activity in the last {result.hours}h."

    lines = [f"## Recent Activity (last {result.hours}h)\n"]
    for group in result.groups:
        lines.append(f"### {group.project} ({group.count})")
        if group.summary:
            lines.append(group.summary)
        else:
            for m in group.memories:
                ts = _format_time(m.created_at)
                content = m.content[:100]
                if len(m.content) > 100:
                    content += "..."
                lines.append(f"- [{ts}] {m.type}: {content}")
        lines.append("")
    return "\n".join(lines)


def format_detailed(result: RecentActivityResult) -> str:
    """Format with full content and metadata."""
    if not result.groups:
        return f"No recent activity in the last {result.hours}h."

    lines = [f"## Recent Activity (last {result.hours}h)\n"]
    for group in result.groups:
        lines.append(f"### {group.project} ({group.count})")
        if group.summary:
            lines.append(f"**Summary:** {group.summary}\n")
        for m in group.memories:
            ts = _format_time(m.created_at)
            lines.append(f"**[{ts}] {m.type}** (tier: {m.tier}, importance: {m.importance_score:.2f})")
            lines.append(m.content)
            if m.tags:
                lines.append(f"Tags: {', '.join(m.tags)}")
            lines.append("")
    return "\n".join(lines)


def format_structured(result: RecentActivityResult) -> Dict[str, Any]:
    """Return structured dict for JSON serialization."""
    return {
        "groups": [
            {
                "project": g.project,
                "memories": [
                    {
                        "id": m.id,
                        "content": m.content,
                        "type": m.type,
                        "tier": m.tier,
                        "created_at": m.created_at,
                        "tags": m.tags,
                        "importance_score": m.importance_score,
                    }
                    for m in g.memories
                ],
                "count": g.count,
                "summary": g.summary,
            }
            for g in result.groups
        ],
        "total_count": result.total_count,
        "hours": result.hours,
        "generated_at": result.generated_at,
        "has_llm_summary": result.has_llm_summary,
        "query_time_ms": result.query_time_ms,
    }


def format_cli(result: RecentActivityResult) -> str:
    """Format for terminal output (no markdown, clean text)."""
    if not result.groups:
        return f"No recent activity in the last {result.hours}h."

    lines = [f"Recent Activity (last {result.hours}h)\n"]
    for group in result.groups:
        lines.append(f"{group.project} ({group.count} memories)")
        if group.summary:
            lines.append(f"  {group.summary}")
        else:
            for m in group.memories:
                ts = _format_time(m.created_at)
                content = m.content[:100]
                if len(m.content) > 100:
                    content += "..."
                lines.append(f"  [{ts}] {m.type}: {content}")
        lines.append("")
    return "\n".join(lines)


def _format_time(iso_str: str) -> str:
    """Extract HH:MM from ISO 8601 timestamp."""
    if not iso_str or len(iso_str) < 16:
        return "??:??"
    return iso_str[11:16]
```

---

## 8. Integration Points

### 8.1 OpenClaw Hook Enhancement

The existing `lore-retrieve` hook handler makes a single call to `GET /v1/retrieve`. Enhance it to make two parallel calls:

```typescript
// handler.ts — inside the retrieve handler
const [semanticRes, recentRes] = await Promise.allSettled([
  fetch(`${baseUrl}/v1/retrieve?query=${encodeURIComponent(query)}&format=xml`, {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: AbortSignal.timeout(2000),
  }),
  fetch(`${baseUrl}/v1/recent?hours=24&format=brief&max_memories=10`, {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: AbortSignal.timeout(2000),
  }),
]);

// Inject recent activity first (if available and enabled)
if (
  process.env.LORE_RECENT_ACTIVITY !== "false" &&
  recentRes.status === "fulfilled" &&
  recentRes.value.ok
) {
  const recentData = await recentRes.value.json();
  if (recentData.total_count > 0) {
    ctx.addContext(`📋 Recent Activity (last 24h):\n${recentData.formatted}`);
  }
}

// Then inject semantic results (existing behavior)
if (semanticRes.status === "fulfilled" && semanticRes.value.ok) {
  // ... existing code ...
}
```

**Key design decisions:**
- Two parallel HTTP calls, not a combined endpoint — keeps endpoints single-purpose
- Recent activity block injected BEFORE semantic results
- `max_memories=10` hardcoded for hook — keeps auto-inject tight (~300 tokens)
- `LORE_RECENT_ACTIVITY=false` env var disables the recent activity block
- Fail-open: if recent call fails, semantic results still work

### 8.2 MCP Server Instructions Update

```python
mcp = FastMCP(
    name="lore",
    instructions=(
        "Lore is a cross-agent memory system. "
        "IMPORTANT: Call recent_activity at the start of every session "
        "for continuity with prior work. Use recall for semantic search. "
        "Use remember to save knowledge worth preserving."
    ),
)
```

### 8.3 CLAUDE.md Addition

The `lore setup claude-code` command should append:

```markdown
## Memory (Lore)

At the start of each session, call `recent_activity` to load context from recent work.
Use `recall` for semantic search when you need specific knowledge.
Use `remember` to save important decisions, lessons, and context.
```

### 8.4 .cursorrules Addition

The `lore setup cursor` command should append:

```
## Memory (Lore)

At the start of each session, call the recent_activity tool from Lore to load recent context.
```

---

## 9. Caching Strategy

### V1: No caching

For the initial release, no caching is implemented. Rationale:
- The time-range query is fast (indexed `created_at` scan, no embedding computation)
- Target latency <200ms is achievable without caching for up to 500 memories
- Caching introduces invalidation complexity (new memories must bust the cache)
- The LLM summary path is inherently slow (~2-3s) and caching LLM output is a v2 concern

### V2 (future): LLM Summary Cache

If LLM summaries are added:
- Cache key: `(org_id, project, hours_bucket)` where `hours_bucket` rounds hours to nearest common value (1, 6, 12, 24, 48, 72, 168)
- TTL: 15 minutes
- Invalidation: simple TTL expiry, no explicit invalidation
- Storage: in-memory dict (process-local) — sufficient for single-process MCP server
- The REST endpoint (multi-process) would use a different strategy (Redis or skip caching)

---

## 10. Performance Considerations

### 10.1 Query Efficiency

| Store | Query | Index Used | Expected Latency |
|-------|-------|------------|------------------|
| SQLite | `WHERE created_at >= ? AND archived = 0 LIMIT N` | `idx_memories_created` | <10ms for 500 results |
| Postgres | `WHERE org_id = $1 AND created_at >= $2 LIMIT $3` | `idx_memories_created` + org_id partition | <20ms for 500 results |
| HttpStore | HTTP GET with `since` param → Postgres query | Same as Postgres + network | <100ms (local network) |

### 10.2 Memory Overhead

- 500 Memory objects ≈ 2-3MB (content + metadata, no embeddings in REST path)
- SQLite `SELECT *` includes embeddings (~1.5KB each) = additional ~750KB for 500 memories
  - Acceptable per NFR-1 (<5MB target)
  - Optimization opportunity: add a `columns` parameter to `Store.list()` later

### 10.3 Grouping Cost

`group_memories_by_project()` is O(n) — single pass to bucket, then sort groups by most-recent. For n=500, this is <1ms.

### 10.4 Formatting Cost

`format_brief()` with string concatenation for 500 memories: <5ms. Content truncation to 100 chars prevents oversized strings.

### 10.5 LLM Path

| Step | Expected Latency |
|------|------------------|
| Store query | <20ms |
| Grouping + prep | <5ms |
| LLM call (per group) | 1-3s |
| Total (3 groups) | 3-9s |

This exceeds the 200ms target, which is why LLM is optional and the `structured` format bypasses it entirely. The REST endpoint returns `has_llm_summary: true/false` so clients know what they got.

### 10.6 Token Budget Compliance

Brief format with 50 memories across 3 projects:
- Header: `## Recent Activity (last 24h)\n\n` = ~8 tokens
- Per group header: `### project-name (N)\n` = ~6 tokens × 3 = ~18 tokens
- Per memory line: `- [HH:MM] type: content[:100]...` ≈ ~30 tokens × 50 = ~1500 tokens

This exceeds the 500-token target. To comply, the `brief` format should cap displayed memories per group. Design:
- Show first 3 memories per group
- Add `(N more)` line for overflow
- With 3 groups × 3 memories + overflow lines ≈ ~300 tokens ✓

For OpenClaw auto-inject, `max_memories=10` further constrains this to ~250 tokens ✓.

---

## 11. Testing Strategy

### 11.1 Unit Tests (`tests/test_recent.py`)

| Test | Description |
|------|-------------|
| `test_group_empty_list` | Empty memories → empty groups |
| `test_group_single_project` | All memories same project → one group |
| `test_group_multiple_projects` | Memories across projects → correct grouping |
| `test_group_null_project` | `project=None` → grouped under "default" |
| `test_group_sorted_by_newest` | Groups ordered by most recent memory |
| `test_format_brief_no_memories` | Returns "No recent activity" message |
| `test_format_brief_basic` | Correct brief format with truncation |
| `test_format_brief_with_summary` | LLM summary replaces raw listing |
| `test_format_brief_overflow` | >3 memories per group shows "(N more)" |
| `test_format_detailed_metadata` | Includes tier, importance, tags |
| `test_format_structured_json` | Returns valid dict with all fields |
| `test_format_cli_no_markdown` | No `##` or `**` in CLI output |
| `test_format_time_valid` | Extracts HH:MM from ISO timestamp |
| `test_format_time_invalid` | Returns "??:??" for malformed timestamps |

### 11.2 SDK Tests (`tests/test_lore_recent.py`)

| Test | Description |
|------|-------------|
| `test_recent_activity_default_params` | 24h window, all projects, brief format |
| `test_recent_activity_custom_hours` | Custom lookback window |
| `test_recent_activity_hours_clamped_low` | hours=0 → clamped to 1 |
| `test_recent_activity_hours_clamped_high` | hours=500 → clamped to 168 |
| `test_recent_activity_project_filter` | Only returns memories for specified project |
| `test_recent_activity_project_env_fallback` | Uses LORE_PROJECT when project=None |
| `test_recent_activity_max_memories` | Respects limit |
| `test_recent_activity_excludes_expired` | Expired memories not returned |
| `test_recent_activity_includes_all_tiers` | working, short, and long tiers included |
| `test_recent_activity_empty_result` | No recent memories → empty groups, no error |
| `test_recent_activity_format_structured` | Returns all fields in structured format |

### 11.3 Store Tests (`tests/test_store_since.py`)

| Test | Description |
|------|-------------|
| `test_sqlite_list_since_filter` | Only returns memories created after `since` |
| `test_sqlite_list_since_none` | `since=None` returns all (backward compat) |
| `test_sqlite_list_since_with_project` | Combined `since` + `project` filter |
| `test_sqlite_list_since_with_limit` | Combined `since` + `limit` |
| `test_http_list_passes_since_param` | HttpStore passes `since` as query param |
| `test_memory_store_list_since` | In-memory store supports `since` (for test infra) |

### 11.4 Integration Tests (`tests/test_recent_integration.py`)

| Test | Description |
|------|-------------|
| `test_mcp_recent_activity_tool_exists` | Tool is registered and discoverable |
| `test_mcp_recent_activity_returns_string` | MCP tool returns formatted string |
| `test_mcp_recent_activity_structured` | format=structured returns valid JSON string |
| `test_cli_recent_command` | `lore recent` runs without error |
| `test_cli_recent_with_hours` | `lore recent --hours 72` uses correct window |
| `test_rest_recent_endpoint` | GET /v1/recent returns 200 with valid response |
| `test_rest_recent_auth_required` | GET /v1/recent without auth → 401 |
| `test_rest_recent_invalid_format` | format=invalid → 422 |

### 11.5 Performance Tests

| Test | Description |
|------|-------------|
| `test_recent_500_memories_under_200ms` | Insert 500 memories, call recent_activity, assert <200ms |

---

## 12. Implementation Order

Numbered for dependency tracking. Each step should be TDD'd.

| Step | Component | Files | Dependencies |
|------|-----------|-------|--------------|
| 1 | Data types | `types.py` | None |
| 2 | Store ABC + SQLite | `store/base.py`, `store/sqlite.py` | Step 1 |
| 3 | Store MemoryStore (test infra) | `store/memory.py` | Step 2 |
| 4 | HttpStore | `store/http.py` | Step 2 |
| 5 | Formatting module | `recent.py` | Step 1 |
| 6 | SDK method | `lore.py` | Steps 2, 5 |
| 7 | MCP tool | `mcp/server.py` | Step 6 |
| 8 | REST endpoint | `server/routes/recent.py`, `server/app.py` | Step 1 |
| 9 | CLI command | `cli.py` | Step 6 |
| 10 | OpenClaw hook | (external repo) | Step 8 |
| 11 | Setup commands | `cli.py` (setup subcommand) | Step 7 |
| 12 | LLM summary | `lore.py`, `recent.py` | Step 6 |
| 13 | Performance tests | `tests/` | Steps 2, 6 |

Steps 7, 8, 9 can be parallelized after Step 6 is complete.

---

## 13. Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Token budget exceeded in brief format | Context bloat in auto-inject | Medium | Cap at 3 memories per group + `(N more)` overflow |
| SQLite `SELECT *` loads embeddings unnecessarily | Higher memory for large result sets | Low | Acceptable for v1 (<5MB for 500), optimize later |
| HttpStore server doesn't support `since` param | Breaks remote store path | High | Must update `/v1/lessons` endpoint simultaneously |
| LLM summary latency exceeds hook timeout | Auto-inject misses summary | Medium | LLM summary is never used in hook path (brief format, no LLM) |
| Agents ignore tool description instruction | Low adoption on MCP-only platforms | High | Mitigate with `instructions` field + CLAUDE.md/cursorrules setup |

---

## Appendix A: Server-Side Route Implementation

```python
# src/lore/server/routes/recent.py

"""Recent activity endpoint — GET /v1/recent."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lore.server.auth import AuthContext, get_auth_context
from lore.server.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["recent"])

VALID_FORMATS = {"brief", "detailed", "structured"}


class RecentMemoryItem(BaseModel):
    id: str
    content: str
    type: str
    tier: str
    created_at: str
    tags: List[str] = []
    importance_score: float = 1.0


class RecentProjectGroup(BaseModel):
    project: str
    memories: List[RecentMemoryItem]
    count: int


class RecentActivityResponse(BaseModel):
    groups: Optional[List[RecentProjectGroup]] = None
    formatted: Optional[str] = None
    total_count: int
    hours: int
    generated_at: str
    has_llm_summary: bool = False
    query_time_ms: float


@router.get("/recent", response_model=RecentActivityResponse)
async def recent_activity(
    hours: int = Query(24, ge=1, le=168, description="Lookback window in hours"),
    project: Optional[str] = Query(None, description="Filter by project"),
    format: str = Query("brief", description="Output format: brief, detailed, structured"),
    max_memories: int = Query(50, ge=1, le=200, description="Max memories to return"),
    auth: AuthContext = Depends(get_auth_context),
) -> RecentActivityResponse:
    """Get recent memory activity grouped by project."""
    start = time.monotonic()

    if format not in VALID_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid format '{format}'. Must be one of: {', '.join(sorted(VALID_FORMATS))}",
        )

    # Compute cutoff
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # Build query
    effective_project = project
    if auth.project is not None:
        effective_project = auth.project

    where_parts = ["org_id = $1", "created_at >= $2", "(expires_at IS NULL OR expires_at > now())"]
    params: list = [auth.org_id, cutoff]

    if effective_project is not None:
        params.append(effective_project)
        where_parts.append(f"project = ${len(params)}")

    params.append(max_memories)
    limit_idx = len(params)

    sql = f"""
        SELECT id, content,
               COALESCE(meta->>'type', 'general') AS type,
               COALESCE(meta->>'tier', 'long') AS tier,
               source, project, tags, created_at, importance_score
        FROM memories
        WHERE {' AND '.join(where_parts)}
        ORDER BY created_at DESC
        LIMIT ${limit_idx}
    """

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    # Group by project
    groups_dict: dict[str, list] = {}
    for r in rows:
        rd = dict(r)
        proj = rd.get("project") or "default"
        tags = rd.get("tags") or []
        if isinstance(tags, str):
            tags = json.loads(tags)
        created_at = rd.get("created_at")
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()

        item = RecentMemoryItem(
            id=rd["id"],
            content=rd["content"],
            type=rd.get("type", "general"),
            tier=rd.get("tier", "long"),
            created_at=str(created_at or ""),
            tags=tags,
            importance_score=rd.get("importance_score", 1.0) or 1.0,
        )
        groups_dict.setdefault(proj, []).append(item)

    groups = [
        RecentProjectGroup(project=p, memories=mems, count=len(mems))
        for p, mems in groups_dict.items()
    ]
    groups.sort(key=lambda g: g.memories[0].created_at, reverse=True)

    total_count = sum(g.count for g in groups)
    elapsed_ms = round((time.monotonic() - start) * 1000, 2)
    generated_at = datetime.now(timezone.utc).isoformat()

    if format == "structured":
        return RecentActivityResponse(
            groups=groups,
            total_count=total_count,
            hours=hours,
            generated_at=generated_at,
            query_time_ms=elapsed_ms,
        )

    # Format as text
    formatted = _format_text(groups, hours, format)
    return RecentActivityResponse(
        formatted=formatted,
        total_count=total_count,
        hours=hours,
        generated_at=generated_at,
        query_time_ms=elapsed_ms,
    )


def _format_text(groups: List[RecentProjectGroup], hours: int, fmt: str) -> str:
    """Format groups as brief or detailed text."""
    if not groups:
        return f"No recent activity in the last {hours}h."

    lines = [f"## Recent Activity (last {hours}h)\n"]
    for group in groups:
        lines.append(f"### {group.project} ({group.count})")
        for m in group.memories:
            ts = m.created_at[11:16] if len(m.created_at) >= 16 else "??:??"
            if fmt == "detailed":
                lines.append(f"**[{ts}] {m.type}** (tier: {m.tier}, importance: {m.importance_score:.2f})")
                lines.append(m.content)
                if m.tags:
                    lines.append(f"Tags: {', '.join(m.tags)}")
                lines.append("")
            else:
                content = m.content[:100]
                if len(m.content) > 100:
                    content += "..."
                lines.append(f"- [{ts}] {m.type}: {content}")
        lines.append("")
    return "\n".join(lines)
```

---

## Appendix B: Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_PROJECT` | None | Default project scope (existing) |
| `LORE_RECENT_ACTIVITY` | `true` | Enable/disable recent activity in OpenClaw auto-inject |
| `LORE_RECENT_HOURS` | `24` | Override default lookback window for auto-inject |
| `LORE_ENRICHMENT_ENABLED` | `false` | Enable LLM enrichment (existing) — also enables LLM summaries for recent_activity |
