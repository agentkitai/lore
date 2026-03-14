# E2: Recent Activity Summary — PRD

**Epic:** E2 — Session Context
**Version:** v0.10.0
**Author:** John (PM)
**Date:** 2026-03-14
**Status:** Draft

---

## 1. Overview & Problem Statement

### The Problem

Lore's auto-inject retrieval (via hooks) and `recall` (via MCP) are both **semantically driven** — they surface memories that match the current query. This creates a blind spot: **recent context that doesn't semantically match gets lost**.

Real scenario: Yesterday you made three architecture decisions, filed a bug, and changed the deploy strategy. Today you ask about testing. None of yesterday's context surfaces because "testing" doesn't semantically match "deploy strategy" or "architecture decision." You start your session cold, missing critical context that happened hours ago.

This is the **"cold start with wrong context" problem**. Semantic relevance ≠ temporal relevance. Both matter.

### The Solution

A dedicated "recent activity" capability that retrieves the last N hours of memories **regardless of semantic relevance**, groups them by project/topic, and presents a concise summary. Available at session start via:

- **Auto-inject** (OpenClaw hook enhancement) — zero agent effort
- **MCP tool** (all platforms) — agent calls it on session start
- **REST endpoint** (programmatic access) — hooks and integrations call it directly

### Why This Matters

- **Continuity**: Sessions feel like picking up where you left off, not starting fresh
- **Decision visibility**: Recent decisions surface even when the current task is unrelated
- **Low effort, high impact**: Rated "Small" effort in the product brief. First epic to ship in v0.10.0

---

## 2. User Stories

### US-1: Session Start Context (Core)

**As** an AI agent starting a new session,
**I want** a summary of what happened in the last 24 hours,
**So that** I have continuity with prior work without needing to search.

**Acceptance Criteria:**
- [ ] Calling `recent_activity` with no parameters returns last 24h of memories
- [ ] Memories are grouped by project
- [ ] Within each project group, memories are sorted by time (newest first)
- [ ] Output includes memory type, content summary, and timestamp
- [ ] Returns empty result (not error) when no recent memories exist
- [ ] Response completes in <200ms for up to 500 recent memories (local store)

### US-2: Custom Time Window

**As** a developer resuming work after a long weekend,
**I want** to see activity from the last 72 hours,
**So that** I catch everything since I last worked.

**Acceptance Criteria:**
- [ ] `hours` parameter controls the lookback window (default: 24)
- [ ] Minimum: 1 hour. Maximum: 168 hours (7 days)
- [ ] Values outside range are clamped, not rejected (fail-friendly)

### US-3: Project Scoping

**As** a developer working across multiple projects,
**I want** recent activity filtered to my current project,
**So that** I don't see noise from unrelated work.

**Acceptance Criteria:**
- [ ] `project` parameter filters to a specific project
- [ ] When omitted, shows all projects (grouped)
- [ ] Project scoping respects the `LORE_PROJECT` env var as default when no explicit parameter is provided

### US-4: Format Control

**As** an integration developer building a hook,
**I want** control over the output format and verbosity,
**So that** I can fit the summary into my platform's context constraints.

**Acceptance Criteria:**
- [ ] `format` parameter supports: `brief` (default), `detailed`, `structured`
  - `brief`: One-line per memory, grouped by project. Target: <500 tokens for a typical day
  - `detailed`: Full content per memory with metadata. No token target
  - `structured`: JSON output with typed fields (for programmatic consumption)
- [ ] `max_memories` parameter caps total memories returned (default: 50)

### US-5: OpenClaw Auto-Inject

**As** an OpenClaw user,
**I want** recent activity automatically included in my session context,
**So that** I never start a session without continuity.

**Acceptance Criteria:**
- [ ] The `lore-retrieve` hook includes a "Recent Activity" section alongside semantic results
- [ ] Recent activity block is visually separated from semantic results (different header)
- [ ] Recent activity is capped at ~300 tokens to avoid bloating context
- [ ] Can be disabled via env var `LORE_RECENT_ACTIVITY=false`

### US-6: LLM-Optional Operation

**As** a user without an LLM API key configured,
**I want** recent activity to work with structured grouping only,
**So that** I get value without paying for LLM calls.

**Acceptance Criteria:**
- [ ] Without LLM: memories listed chronologically within project groups, content truncated to first 100 chars
- [ ] With LLM: memories summarized into concise key points per project group
- [ ] The tool description clearly states that LLM is optional and what the difference is
- [ ] No errors or degraded UX when LLM is unavailable — just a different (still useful) format

### US-7: Cross-Platform MCP Tool

**As** a Codex/Cursor/Claude Code user,
**I want** an MCP tool I can call at session start,
**So that** I get the same continuity that OpenClaw users get automatically.

**Acceptance Criteria:**
- [ ] `recent_activity` MCP tool is registered and discoverable
- [ ] Tool description explicitly instructs the agent to call it at session start
- [ ] Works identically across all platforms (same parameters, same output)

---

## 3. Functional Requirements

### FR-1: MCP Tool — `recent_activity`

```
Tool: recent_activity
Description: "Get a summary of recent memory activity. Call this at the start of
  every session to maintain continuity with prior work. Returns memories from the
  last N hours grouped by project, regardless of semantic relevance. Works without
  LLM (structured listing) — enhanced with LLM (concise summary)."

Parameters:
  hours: int = 24          # Lookback window (1–168, clamped)
  project: str? = None     # Filter to project (default: all, grouped)
  format: str = "brief"    # brief | detailed | structured
  max_memories: int = 50   # Cap on total memories (1–200, clamped)

Returns: str               # Formatted summary
```

**Implementation notes:**

- Query the store for memories with `created_at >= now() - {hours}h`
- Sort by `created_at DESC` within each project group
- No embedding computation needed — this is a pure time-range query
- Respect project scoping from `LORE_PROJECT` env var as default
- Exclude expired memories (same filter as `list_memories`)
- Include all tiers (working, short, long) — working-tier memories are especially relevant for recent activity since they represent scratch context

### FR-2: REST Endpoint — `GET /v1/recent`

```
GET /v1/recent
  ?hours=24
  &project=my-project
  &format=brief|detailed|structured
  &max_memories=50

Response (structured format):
{
  "groups": [
    {
      "project": "lore",
      "memories": [
        {
          "id": "...",
          "content": "...",
          "type": "lesson",
          "tier": "long",
          "created_at": "2026-03-14T10:30:00Z",
          "tags": ["architecture"],
          "importance_score": 0.85
        }
      ],
      "count": 3
    }
  ],
  "total_count": 12,
  "hours": 24,
  "generated_at": "2026-03-14T14:00:00Z",
  "has_llm_summary": false,
  "query_time_ms": 15.2
}

Response (brief/detailed format):
{
  "formatted": "## Recent Activity (last 24h)\n\n### lore\n- ...",
  "total_count": 12,
  "hours": 24,
  "generated_at": "2026-03-14T14:00:00Z",
  "has_llm_summary": false,
  "query_time_ms": 15.2
}
```

**Implementation notes:**

- Reuse existing auth middleware (`AuthContext`, `get_auth_context`)
- Server-side query: `SELECT * FROM memories WHERE org_id = $1 AND created_at >= now() - interval '{hours} hours' AND (expires_at IS NULL OR expires_at > now()) ORDER BY created_at DESC LIMIT {max_memories}`
- Group in application code by `project` field
- For `brief` format: truncate content to 100 chars, one line per memory
- For `detailed` format: full content with metadata
- For `structured` format: return JSON with typed fields
- Record analytics event (same pattern as `/v1/retrieve`)

### FR-3: OpenClaw Hook Enhancement

Modify the existing `lore-retrieve` hook handler to also fetch recent activity:

```typescript
// In handler.ts, add alongside the semantic retrieval:

const recentRes = await fetch(
  `http://localhost:8765/v1/recent?hours=24&format=brief&max_memories=10`,
  {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: AbortSignal.timeout(2000),
  }
);
const recentData = await recentRes.json();
if (recentData.total_count > 0) {
  ctx.addContext(`📋 Recent Activity (last 24h):\n${recentData.formatted}`);
}
```

**Design decisions:**
- Separate context block from semantic memories (`📋` vs `🧠`)
- Capped at 10 memories / ~300 tokens to avoid context bloat
- Uses the same timeout and fail-open pattern as semantic retrieval
- Disabled via `LORE_RECENT_ACTIVITY=false` env var

### FR-4: Lore SDK Method

Add `recent_activity()` to the `Lore` class in `lore.py`:

```python
def recent_activity(
    self,
    *,
    hours: int = 24,
    project: Optional[str] = None,
    format: str = "brief",
    max_memories: int = 50,
) -> Dict[str, Any]:
    """Get recent memories grouped by project.

    Returns dict with 'groups' (list of project groups with memories),
    'total_count', 'hours', 'has_llm_summary'.
    """
```

**Implementation notes:**
- Uses `store.list()` with a time filter (add `since` parameter to Store ABC if not present)
- Groups results by `memory.project` (default project = "default")
- If LLM is available and `format != "structured"`, summarize each group
- Returns structured dict regardless of format (formatting happens in MCP tool / REST endpoint)

### FR-5: Store Layer — Time-Range Query

The Store ABC needs a time-range query capability. Check if `list()` already supports time filtering; if not, add a `since` parameter:

```python
# In Store ABC (store/base.py):
def list(
    self,
    *,
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    limit: Optional[int] = None,
    since: Optional[str] = None,  # ISO 8601 datetime, inclusive
) -> List[Memory]:
```

Both `SqliteStore` and `HttpStore` must implement this. The Postgres server already has `created_at` indexed, so the query is efficient.

### FR-6: LLM Summary (Optional Enhancement)

When an LLM provider is configured and `format` is `brief`:

- Group memories by project
- For each group, send memories to LLM with prompt: "Summarize these recent activities into 2-3 bullet points focusing on key decisions, changes, and open items"
- Cache the summary for 15 minutes (invalidated when new memories arrive)
- Fall back to structured listing if LLM call fails

**Opinionated decision:** LLM summaries are cached per (project, hours) key. We don't re-summarize on every call — that's wasteful and slow. The cache invalidation on new memory arrival ensures freshness without redundant LLM calls.

### FR-7: CLI Command

```bash
lore recent                      # Last 24h, all projects, brief format
lore recent --hours 72           # Last 3 days
lore recent --project my-proj    # Specific project
lore recent --format detailed    # Full content
```

Uses the same `Lore.recent_activity()` method. Formatting matches the terminal (no XML/markdown, just clean text output).

---

## 4. Non-Functional Requirements

### NFR-1: Performance

| Metric | Target | Rationale |
|--------|--------|-----------|
| MCP tool response time | <200ms (local), <500ms (remote, no LLM) | Must be fast enough for session-start injection without noticeable delay |
| REST endpoint response time | <100ms (no LLM), <3s (with LLM summary) | Hook timeout is 2s; LLM summary can be slower since it's optional |
| Memory overhead | <5MB for 500 recent memories | Don't load embeddings — only content + metadata |

### NFR-2: Reliability

- **Fail-open**: If the recent activity query fails, return empty result, not error. The session must start regardless.
- **No embeddings needed**: This is a time-range query, not a semantic search. No embedding computation, no ONNX model loading.
- **Graceful degradation**: LLM unavailable → structured listing. Store unavailable → empty result.

### NFR-3: Token Budget

The `brief` format must target **<500 tokens** for a typical day (10-30 memories). This means:
- Content truncated to first 100 characters
- One line per memory
- Group headers are minimal ("### project-name (5 memories)")
- No metadata in brief mode (no tags, no IDs, no scores)

This is critical for auto-inject (OpenClaw). Context windows are precious.

### NFR-4: Backward Compatibility

- No changes to existing MCP tool signatures
- No changes to existing REST endpoint behavior
- New `since` parameter on `Store.list()` must have a default of `None` (no-op)
- OpenClaw hook enhancement is additive (semantic retrieval still works as before)

### NFR-5: Testing

- Unit tests for grouping logic, format rendering, time-range filtering
- Integration tests for MCP tool, REST endpoint, CLI command
- Edge cases: zero memories, single project, 200+ memories, memories with no project set
- Performance test: 500 memories in <200ms (local store)

---

## 5. API Design

### 5.1 MCP Tool

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

**Tool description design:** The description explicitly says "CALL THIS AT THE START OF EVERY SESSION." This is intentional — MCP tool descriptions are the only way to guide agent behavior on platforms without hooks. The instruction must be unambiguous.

### 5.2 REST Endpoint

```
GET /v1/recent
  Query Parameters:
    hours: int (default: 24, range: 1-168)
    project: string (optional)
    format: string (default: "brief", enum: brief|detailed|structured)
    max_memories: int (default: 50, range: 1-200)

  Headers:
    Authorization: Bearer <api_key>

  Response: 200 OK
    Content-Type: application/json
    Body: RecentActivityResponse (see FR-2)

  Errors:
    401: Missing or invalid API key
    422: Invalid format value
```

### 5.3 SDK Method

```python
class Lore:
    def recent_activity(
        self,
        *,
        hours: int = 24,
        project: Optional[str] = None,
        format: str = "brief",
        max_memories: int = 50,
    ) -> RecentActivityResult:
        """Get recent memories grouped by project."""
```

### 5.4 CLI

```
lore recent [OPTIONS]

Options:
  --hours INT       Lookback window in hours (default: 24)
  --project TEXT    Filter to specific project
  --format TEXT     Output format: brief, detailed (default: brief)
```

---

## 6. Integration Patterns

### 6.1 OpenClaw (Hooks + MCP)

**Auto-inject (recommended):** Enhance the existing `lore-retrieve` hook to call `GET /v1/recent` alongside `GET /v1/retrieve`. The recent activity block is injected as a separate context section before the semantic memories.

```
Context injection order:
1. 📋 Recent Activity (last 24h) — from /v1/recent
2. 🧠 Relevant Memories — from /v1/retrieve (semantic)
```

**MCP fallback:** The `recent_activity` MCP tool is also available for explicit agent calls.

**Configuration:**
- `LORE_RECENT_ACTIVITY=true|false` — enable/disable auto-inject of recent activity (default: true)
- `LORE_RECENT_HOURS=24` — override default lookback window

### 6.2 Claude Code (MCP + CLAUDE.md)

**No hooks available.** Claude Code relies on MCP tools + CLAUDE.md instructions.

**Setup:** Add to the project's `CLAUDE.md`:

```markdown
## Memory (Lore)

At the start of each session, call `recent_activity` to load context from recent work.
Use `recall` for semantic search when you need specific knowledge.
Use `remember` to save important decisions, lessons, and context.
```

**`lore setup claude-code`** should auto-append this to CLAUDE.md (extend existing setup command).

### 6.3 Codex (MCP)

**No hooks, no config file.** Codex relies purely on MCP tool descriptions.

**Integration:** The MCP tool description ("CALL THIS AT THE START OF EVERY SESSION") is the only integration point. This is inherently weaker than OpenClaw's auto-inject — the agent may or may not call it.

**Mitigation:** Make the tool description as directive as possible. Consider adding `recent_activity` to the MCP server's `instructions` field so it appears in the server-level prompt.

Update the FastMCP `instructions` to:

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

### 6.4 Cursor (MCP + .cursorrules)

**MCP tools + `.cursorrules` instructions.** Same pattern as Claude Code.

**Setup:** Add to `.cursorrules`:

```
## Memory (Lore)

At the start of each session, call the recent_activity tool from Lore to load recent context.
```

**`lore setup cursor`** should auto-append this (extend existing setup command).

---

## 7. Success Metrics

### Adoption Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| `recent_activity` calls per day (across all users) | >10/day within 2 weeks of launch | Analytics event on `/v1/recent` |
| % of sessions that call `recent_activity` | >50% of OpenClaw sessions (auto-inject), >20% of MCP-only sessions | Correlate with session start events |
| Time from session start to first `recent_activity` call | <5 seconds (median) | Timestamp delta in analytics |

### Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Response time p95 (no LLM) | <200ms | Server-side latency tracking |
| Response time p95 (with LLM) | <3s | Server-side latency tracking |
| Token count for `brief` format (p95) | <500 tokens | Count output characters / 4 |
| Error rate | <0.1% | 5xx responses / total requests |

### Impact Metrics (qualitative)

- Do users report better session continuity? (track via feedback/GitHub issues)
- Does `recent_activity` reduce redundant `recall` queries at session start?
- Does auto-inject in OpenClaw feel natural or noisy? (monitor for `LORE_RECENT_ACTIVITY=false` opt-outs)

---

## 8. Open Questions

### OQ-1: Should `recent_activity` deduplicate with semantic retrieval?

**Context:** In OpenClaw, both semantic retrieval and recent activity run. A memory could appear in both blocks.

**Recommendation:** Don't deduplicate. The overlap is usually small, and deduplication adds complexity and a dependency between the two calls. If users report noise, revisit.

### OQ-2: What happens when there are 200+ memories in 24 hours?

**Context:** Heavy users or batch ingestion could produce hundreds of memories per day. The `max_memories` cap (default 50) handles this, but `brief` format at 50 memories is still ~250 tokens.

**Recommendation:** Default `max_memories=50` is fine. For the OpenClaw hook, hardcode `max_memories=10` to keep auto-inject tight. Power users can call the MCP tool directly with higher limits.

### OQ-3: Should we include `working` tier memories?

**Context:** Working-tier memories auto-expire in 1 hour. They represent scratch context from the last session. Including them in recent activity could surface context that was intentionally ephemeral.

**Recommendation:** Include them. Working-tier memories are *especially* relevant for recent activity — they represent the "scratchpad" from the last session. If they've expired, they won't appear anyway. If they're still alive, they're by definition recent and relevant.

### OQ-4: Should the `lore-retrieve` hook make two HTTP calls or one combined call?

**Context:** Adding recent activity to the OpenClaw hook means either two parallel HTTP calls (`/v1/retrieve` + `/v1/recent`) or a new combined endpoint.

**Recommendation:** Two parallel calls. Keep endpoints single-purpose. The hook already has a 2-second timeout; two parallel calls within that budget is fine. A combined endpoint adds complexity for marginal latency savings.

### OQ-5: How should LLM summary caching work across projects?

**Context:** Cache key is `(project, hours)`. If a user switches projects mid-session, they get a different cache entry. But if they add memories, the cache for that project should invalidate.

**Recommendation:** Simple TTL cache (15 minutes) with no explicit invalidation for v1. The summary is a convenience, not a source of truth. Users can call with `format=detailed` to bypass the cache and see raw memories. Revisit if users report stale summaries.

### OQ-6: Should there be a `since` timestamp instead of/alongside `hours`?

**Context:** `hours` is relative to "now." A `since` ISO 8601 timestamp would allow "show me everything since 2pm yesterday" use cases.

**Recommendation:** Not for v1. `hours` is simpler, covers 90% of use cases, and avoids timezone confusion. Add `since` as a follow-up if users request it.

### OQ-7: Integration with E3 (Pre-Compaction)?

**Context:** E3 saves session snapshots before compaction. Recent activity should surface these snapshots prominently since they represent the most critical recent context.

**Recommendation:** No special handling for v1. Session snapshots are memories with `type=session_snapshot`. They'll appear in recent activity naturally. E3 can add priority weighting later if needed.

---

## Appendix A: Implementation Order

1. **Store layer**: Add `since` parameter to `Store.list()` — both SQLite and HttpStore
2. **SDK method**: `Lore.recent_activity()` — grouping + formatting logic
3. **MCP tool**: `recent_activity` — wraps SDK method
4. **REST endpoint**: `GET /v1/recent` — server-side implementation
5. **CLI command**: `lore recent`
6. **OpenClaw hook**: Enhance `lore-retrieve` handler
7. **Setup commands**: Update `lore setup claude-code`, `lore setup cursor`
8. **LLM summary**: Optional enhancement, can ship after the core
9. **Tests**: Throughout — TDD each layer

## Appendix B: Token Budget Analysis

Typical day: 15 memories across 2 projects.

**Brief format (no LLM):**
```
## Recent Activity (last 24h)

### lore (8)
- [14:30] lesson: Discovered that ONNX model loading takes 2s on first...
- [13:15] general: Architecture decision: use FastMCP for all new tools...
- [11:00] code: Fixed race condition in consolidation by adding lock...
(5 more)

### my-app (7)
- [16:00] general: Deployed v2.3.1 to staging, all tests passing...
- [14:45] lesson: Redis connection pool must be warmed before load test...
(5 more)
```

**Estimated tokens:** ~250 (well within 500 target)

**Brief format (with LLM):**
```
## Recent Activity (last 24h)

### lore
- Fixed ONNX loading perf and consolidation race condition
- Decided to use FastMCP for all new MCP tools
- 3 other memories (debug, code patterns)

### my-app
- Deployed v2.3.1 to staging successfully
- Learned Redis pool must be warmed before load tests
- 2 other memories (config, deploy)
```

**Estimated tokens:** ~120 (excellent for auto-inject)
