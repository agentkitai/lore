# PRD: F3 — Memory Consolidation / Auto-Summarization

**Feature:** F3 — Memory Consolidation / Auto-Summarization
**Version:** v0.6.0 ("Open Brain")
**Status:** Draft
**Author:** John (PM)
**Date:** 2026-03-06
**Phase:** 4 — Operations Layer
**Depends on:** F4 (Memory Tiers — tier-based retention policies), F5 (Importance Scoring — decay + importance thresholds), F1 (Knowledge Graph — entity/topic grouping for intelligent consolidation)
**Dependents:** None (terminal feature in dependency graph)

---

## 1. Problem Statement

As Lore accumulates memories over time, three problems emerge:

1. **Memory bloat.** Hundreds of similar or overlapping memories accumulate — "learned that pytest fixtures are scoped" appears in five slightly different wordings across sessions. Vector recall returns near-duplicates, wasting context budget and confusing LLMs.
2. **Stale clusters.** Groups of old episodic memories ("debugged auth issue on Tuesday", "found the root cause Wednesday", "deployed fix Thursday") remain as individual entries long after the useful insight has been extracted. The consolidated lesson ("auth-service had a token expiry race condition, fixed by adding clock skew tolerance") is more valuable than the play-by-play.
3. **No lifecycle management.** Working-tier and short-tier memories expire via TTL, but there's no mechanism to _compress_ knowledge before it ages out. Valuable patterns buried in short-term memories are lost when the TTL fires, rather than being distilled into long-term knowledge.

Competitive platforms handle this differently:
- **Mem0** performs deduplication on ingest but has no post-hoc consolidation.
- **Zep** uses "memory synthesis" to summarize conversation history, but only within a single session.
- **Cognee** has no explicit consolidation pipeline.

Lore's consolidation system will be a differentiator: a background pipeline that identifies related memory clusters, summarizes them via LLM, archives originals, and updates the knowledge graph — all with full auditability.

## 2. Goals

1. **Reduce memory bloat** — Detect and merge near-duplicate memories (cosine similarity > 0.95), eliminating redundancy in recall results.
2. **Compress episodic to semantic** — Summarize clusters of related episodic memories into concise semantic memories that capture the distilled insight.
3. **Configurable lifecycle** — Tier-based retention policies determine when memories become consolidation candidates (working: 1h, short: 7d, long: 30d).
4. **Graph-aware grouping** — Use knowledge graph entity/relationship data (F1) to intelligently group memories by topic and entity for consolidation.
5. **Full auditability** — Every consolidation action is logged with references to originals, which are soft-deleted (archived) rather than destroyed.
6. **Manual and automatic triggers** — Consolidation runs on a configurable schedule (daily/weekly) or can be triggered manually via MCP tool or CLI.

## 3. Non-Goals

- **Real-time consolidation on ingest** — Consolidation is a batch process, not triggered on every `remember()` call. Deduplication at ingest time is a future optimization.
- **Cross-project consolidation** — Memories are consolidated within a single project scope. Cross-project knowledge synthesis is out of scope.
- **Automatic tier promotion** — Consolidation creates new long-term memories from clusters, but does not auto-promote individual memories between tiers. That's a future F5 enhancement.
- **Consolidation UI/dashboard** — Beyond CLI output and log table. Visualization is out of scope.
- **Undo/rollback** — Originals are archived (soft-deleted) and can be manually restored, but there is no automated "undo consolidation" command.

## 4. Requirements

### 4.1 Must-Have (P0)

| ID | Requirement | Details |
|----|-------------|---------|
| R1 | **Consolidation pipeline** | Six-stage pipeline: (1) identify candidates by tier/age/importance, (2) group by topic/entity using graph, (3) LLM summarize each group, (4) archive originals with reference to consolidated memory, (5) update graph edges to point to consolidated memory, (6) log all actions |
| R2 | **Deduplication detection** | Detect near-duplicate memories with cosine similarity > 0.95. Configurable threshold. Duplicates are merged: keep the one with higher importance, archive the other with a reference. |
| R3 | **Consolidation log table** | New `consolidation_log` table recording: log ID, consolidated memory ID, original memory IDs, strategy used (merge/summarize/deduplicate), timestamp, LLM model used (if any), summary of what changed. |
| R4 | **Soft delete originals** | Archived memories get `archived = true` and `consolidated_into = <new_memory_id>`. They are excluded from recall results but remain queryable for audit. |
| R5 | **Importance preservation** | Consolidated memory inherits `importance_score = max(source_importance_scores)`. Access counts are summed. Upvotes/downvotes are summed. |
| R6 | **Graph integration** | After consolidation, update all entity-memory edges (`relationships.memory_id`) that pointed to archived originals to point to the consolidated memory. Entity mention counts are preserved. |
| R7 | **Configurable retention policies** | Per-tier retention thresholds that determine when memories become consolidation candidates. Defaults: working = 1h, short = 7d, long = 30d. Configurable via `Lore(consolidation_config=...)`. |
| R8 | **MCP tool: `consolidate`** | Trigger manual consolidation. Parameters: `project` (optional), `dry_run` (bool, default true), `strategy` (optional: "deduplicate", "summarize", "all"). Returns summary of what was/would be consolidated. |
| R9 | **CLI: `lore consolidate`** | `lore consolidate --dry-run` (preview), `lore consolidate --execute` (run). Optional flags: `--project`, `--tier`, `--strategy`. Dry-run is the default to prevent accidental data changes. |
| R10 | **LLM-powered summarization** | Use configurable LLM to summarize memory clusters into concise consolidated memories. LLM is optional — without it, consolidation falls back to deduplication-only mode (merge exact/near duplicates, no summarization). |
| R11 | **Schema changes** | Add `archived` (bool, default false) and `consolidated_into` (optional string) fields to Memory dataclass and all store implementations. |

### 4.2 Should-Have (P1)

| ID | Requirement | Details |
|----|-------------|---------|
| R12 | **Scheduled consolidation** | Background scheduler that runs consolidation on a configurable interval (daily/weekly). Uses Python's `sched` or `APScheduler`. Configuration via `Lore(consolidation_schedule="daily")`. |
| R13 | **Consolidation statistics** | `stats()` includes consolidation metrics: total consolidations run, memories archived, memories created, last consolidation timestamp. |
| R14 | **Batch processing** | Process consolidation in batches (default 50 memories per batch) to avoid memory pressure on large stores. |
| R15 | **Dry-run detail output** | Dry-run mode shows: candidate groups, proposed consolidated content preview (first 200 chars), number of originals per group, estimated reduction in memory count. |

### 4.3 Nice-to-Have (P2)

| ID | Requirement | Details |
|----|-------------|---------|
| R16 | **Consolidation quality score** | After consolidation, compute a quality metric: ratio of information preserved (embedding similarity between consolidated memory and centroid of originals). Log this score for monitoring. |
| R17 | **Restore archived memory** | CLI command `lore restore <memory_id>` to un-archive a memory and remove its `consolidated_into` reference. For manual correction. |
| R18 | **Consolidation hooks** | Event hooks (pre-consolidation, post-consolidation) for extensibility. |

## 5. Detailed Design

### 5.1 Consolidation Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                   Consolidation Pipeline                     │
│                                                             │
│  1. IDENTIFY    ──▶  Find candidates by tier/age/importance │
│  2. GROUP       ──▶  Cluster by entity/topic (graph + sim)  │
│  3. SUMMARIZE   ──▶  LLM condenses each group              │
│  4. ARCHIVE     ──▶  Soft-delete originals                  │
│  5. RELINK      ──▶  Update graph edges                     │
│  6. LOG         ──▶  Write consolidation_log entry          │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Stage 1: Identify Candidates

```python
def identify_candidates(
    store: Store,
    tier: Optional[str] = None,
    project: Optional[str] = None,
    retention_policies: Dict[str, int] = None,
) -> List[Memory]:
    """Find memories eligible for consolidation based on age and tier."""
    policies = retention_policies or DEFAULT_RETENTION_POLICIES
    candidates = []

    for memory in store.list(project=project, tier=tier):
        if memory.archived:
            continue
        age_seconds = (now() - parse(memory.created_at)).total_seconds()
        tier_threshold = policies.get(memory.tier, policies["long"])
        if age_seconds > tier_threshold:
            candidates.append(memory)

    return candidates
```

Default retention policies (seconds):
```python
DEFAULT_RETENTION_POLICIES = {
    "working": 3600,      # 1 hour
    "short": 604800,      # 7 days
    "long": 2592000,      # 30 days
}
```

Note: These retention thresholds determine when a memory _becomes eligible_ for consolidation, not when it is deleted. A 31-day-old long-tier memory is a _candidate_ — it may be grouped and summarized, or left alone if it has no related memories to consolidate with.

### 5.3 Stage 2: Group by Topic/Entity

Two grouping strategies, applied in order:

**Strategy A: Deduplication (cosine similarity > threshold)**
```python
def find_duplicates(candidates: List[Memory], threshold: float = 0.95) -> List[List[Memory]]:
    """Group near-duplicate memories by embedding similarity."""
    groups = []
    used = set()

    for i, mem_a in enumerate(candidates):
        if mem_a.id in used:
            continue
        group = [mem_a]
        for j, mem_b in enumerate(candidates[i+1:], i+1):
            if mem_b.id in used:
                continue
            sim = cosine_similarity(mem_a.embedding, mem_b.embedding)
            if sim > threshold:
                group.append(mem_b)
                used.add(mem_b.id)
        if len(group) > 1:
            groups.append(group)
            used.add(mem_a.id)

    return groups
```

**Strategy B: Entity/topic clustering (using knowledge graph)**
```python
def group_by_entity(
    candidates: List[Memory],
    graph_store: GraphStore,
    min_group_size: int = 3,
) -> List[List[Memory]]:
    """Group memories that share entities via graph relationships."""
    # For each candidate, find its connected entities
    entity_to_memories: Dict[str, List[Memory]] = defaultdict(list)

    for memory in candidates:
        entities = graph_store.get_entities_for_memory(memory.id)
        for entity in entities:
            entity_to_memories[entity.id].append(memory)

    # Groups: memories sharing the same entity, with >= min_group_size members
    groups = []
    used = set()
    for entity_id, memories in entity_to_memories.items():
        # Filter out already-grouped memories
        ungrouped = [m for m in memories if m.id not in used]
        if len(ungrouped) >= min_group_size:
            groups.append(ungrouped)
            used.update(m.id for m in ungrouped)

    return groups
```

Candidates not placed into any group are left untouched.

### 5.4 Stage 3: LLM Summarization

```python
CONSOLIDATION_PROMPT = """You are a memory consolidation system. Given a group of related memories,
create a single concise memory that preserves all important information.

Rules:
- Preserve all facts, lessons, and actionable insights
- Remove redundancy and repetition
- Use clear, direct language
- If memories contain contradictory information, note the contradiction
- Output only the consolidated memory content, nothing else

Memories to consolidate:
{memories}

Consolidated memory:"""

async def summarize_group(
    memories: List[Memory],
    llm_client: Optional[LLMClient],
) -> str:
    """LLM-summarize a group of memories into one consolidated content."""
    if llm_client is None:
        # Fallback: pick the memory with highest importance
        best = max(memories, key=lambda m: m.importance_score)
        return best.content

    memories_text = "\n---\n".join(
        f"[{m.type}, importance: {m.importance_score:.2f}] {m.content}"
        for m in memories
    )
    prompt = CONSOLIDATION_PROMPT.format(memories=memories_text)
    return await llm_client.complete(prompt)
```

When no LLM is configured, deduplication groups simply keep the highest-importance member. Entity-based groups are skipped (they require LLM summarization to be meaningful).

### 5.5 Stage 4: Archive Originals

```python
def archive_originals(
    store: Store,
    originals: List[Memory],
    consolidated_memory_id: str,
) -> None:
    """Soft-delete original memories with reference to consolidated memory."""
    for memory in originals:
        memory.archived = True
        memory.consolidated_into = consolidated_memory_id
        memory.updated_at = now_iso()
        store.update(memory)
```

Archived memories:
- Are excluded from `recall()` results (filter: `WHERE archived = false`)
- Are excluded from `list_memories()` results by default (add `--include-archived` flag to see them)
- Remain in the database for audit and potential restoration
- Retain their original embeddings and metadata

### 5.6 Stage 5: Update Graph Edges

```python
def relink_graph_edges(
    graph_store: GraphStore,
    original_ids: List[str],
    consolidated_memory_id: str,
) -> int:
    """Update graph relationships to point to consolidated memory."""
    updated = 0
    for original_id in original_ids:
        relationships = graph_store.get_relationships_for_memory(original_id)
        for rel in relationships:
            rel.memory_id = consolidated_memory_id
            graph_store.update_relationship(rel)
            updated += 1
    return updated
```

Entity mention counts are not modified — the entity was mentioned in the originals, and the consolidated memory represents that same knowledge.

### 5.7 Stage 6: Consolidation Log

```python
@dataclass
class ConsolidationLogEntry:
    id: str                          # UUID
    consolidated_memory_id: str      # new memory created
    original_memory_ids: List[str]   # memories that were archived
    strategy: str                    # "deduplicate" | "summarize"
    model_used: Optional[str]        # LLM model name, if any
    original_count: int              # number of originals
    created_at: str                  # ISO timestamp
    metadata: Optional[Dict] = None  # additional context (similarity scores, entity names, etc.)
```

### 5.8 Consolidated Memory Construction

```python
def create_consolidated_memory(
    originals: List[Memory],
    consolidated_content: str,
    strategy: str,
) -> Memory:
    """Build a new Memory from consolidated content and original metadata."""
    return Memory(
        id=generate_id(),
        content=consolidated_content,
        type=_resolve_type(originals),          # most common type among originals
        tier="long",                             # consolidated memories are always long-term
        tags=_merge_tags(originals),             # union of all tags
        metadata={
            "consolidated_from": [m.id for m in originals],
            "consolidation_strategy": strategy,
            "original_count": len(originals),
        },
        source="consolidation",
        project=originals[0].project,            # all originals share project
        importance_score=max(m.importance_score for m in originals),
        access_count=sum(m.access_count for m in originals),
        upvotes=sum(m.upvotes for m in originals),
        downvotes=sum(m.downvotes for m in originals),
        confidence=max(m.confidence for m in originals),
    )

def _resolve_type(memories: List[Memory]) -> str:
    """Return the most common type among originals."""
    counts = Counter(m.type for m in memories)
    return counts.most_common(1)[0][0]

def _merge_tags(memories: List[Memory]) -> List[str]:
    """Union of all tags, deduplicated."""
    return list(set(tag for m in memories for tag in m.tags))
```

## 6. Data Model Changes

### 6.1 Memory Dataclass (types.py)

Add two fields:

```python
@dataclass
class Memory:
    # ... existing fields ...
    archived: bool = False
    consolidated_into: Optional[str] = None  # ID of the consolidated memory
```

### 6.2 New Table: consolidation_log

**SQLite:**
```sql
CREATE TABLE IF NOT EXISTS consolidation_log (
    id TEXT PRIMARY KEY,
    consolidated_memory_id TEXT NOT NULL,
    original_memory_ids TEXT NOT NULL,       -- JSON array of IDs
    strategy TEXT NOT NULL,                  -- 'deduplicate' | 'summarize'
    model_used TEXT,
    original_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    metadata TEXT                            -- JSON
);

CREATE INDEX IF NOT EXISTS idx_consolidation_log_memory
    ON consolidation_log(consolidated_memory_id);
CREATE INDEX IF NOT EXISTS idx_consolidation_log_created
    ON consolidation_log(created_at);
```

**Postgres:**
```sql
CREATE TABLE IF NOT EXISTS consolidation_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consolidated_memory_id UUID NOT NULL REFERENCES memories(id),
    original_memory_ids UUID[] NOT NULL,
    strategy VARCHAR(20) NOT NULL,
    model_used VARCHAR(100),
    original_count INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    metadata JSONB
);
```

### 6.3 Schema Migration for Memory Table

**SQLite:**
```sql
ALTER TABLE memories ADD COLUMN archived INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN consolidated_into TEXT;
CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived);
```

**Postgres:**
```sql
ALTER TABLE memories ADD COLUMN archived BOOLEAN DEFAULT FALSE;
ALTER TABLE memories ADD COLUMN consolidated_into UUID REFERENCES memories(id);
CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived);
```

### 6.4 MemoryStats Updates

```python
@dataclass
class MemoryStats:
    # ... existing ...
    archived_count: int = 0
    consolidation_count: int = 0           # total consolidation runs
    last_consolidation_at: Optional[str] = None
```

## 7. API / Interface Changes

### 7.1 Lore Facade

```python
class Lore:
    def __init__(
        self,
        # ... existing params ...
        consolidation_config: Optional[Dict] = None,
        consolidation_schedule: Optional[str] = None,  # "daily", "weekly", None
    ):
        ...

    async def consolidate(
        self,
        project: Optional[str] = None,
        tier: Optional[str] = None,
        strategy: str = "all",           # "deduplicate", "summarize", "all"
        dry_run: bool = True,
    ) -> ConsolidationResult:
        """Run the consolidation pipeline."""
        ...

    def get_consolidation_log(
        self,
        limit: int = 50,
        project: Optional[str] = None,
    ) -> List[ConsolidationLogEntry]:
        """Retrieve consolidation history."""
        ...
```

```python
@dataclass
class ConsolidationResult:
    groups_found: int                    # number of groups identified
    memories_consolidated: int           # number of originals archived
    memories_created: int                # number of new consolidated memories
    duplicates_merged: int               # near-duplicate pairs merged
    groups: List[Dict]                   # details per group (for dry-run output)
    dry_run: bool
```

### 7.2 Consolidation Configuration

```python
DEFAULT_CONSOLIDATION_CONFIG = {
    "retention_policies": {
        "working": 3600,       # 1 hour
        "short": 604800,       # 7 days
        "long": 2592000,       # 30 days
    },
    "dedup_threshold": 0.95,   # cosine similarity threshold for duplicates
    "min_group_size": 3,       # minimum memories to form a summarization group
    "batch_size": 50,          # memories processed per batch
    "max_groups_per_run": 100, # safety limit
    "llm_model": None,        # None = no LLM, dedup-only mode
}
```

### 7.3 MCP Tool: consolidate

```python
@mcp.tool()
async def consolidate(
    project: Optional[str] = None,
    dry_run: bool = True,
    strategy: Optional[str] = None,
) -> str:
    """Trigger memory consolidation. Merges near-duplicate memories and
    summarizes related memory clusters into concise long-term memories.

    Args:
        project: Consolidate memories for a specific project only.
        dry_run: If true (default), preview what would be consolidated without
                 making changes. Set to false to execute.
        strategy: 'deduplicate' (merge near-duplicates only),
                  'summarize' (LLM-summarize related clusters),
                  or 'all' (both, default).

    Returns:
        Summary of consolidation results including groups found,
        memories affected, and new memories created.
    """
```

Output format (dry-run):
```
Consolidation Preview (DRY RUN)
================================
Strategy: all
Candidates found: 47

Duplicate Groups (3 found):
  Group 1: 2 memories (similarity: 0.97)
    - [abc123] "pytest fixtures are session-scoped by..."
    - [def456] "pytest fixtures can be scoped to session..."
    Action: Merge (keep abc123, archive def456)

  Group 2: 3 memories (similarity: 0.96)
    ...

Topic Groups (2 found):
  Group 1: 4 memories (entity: "auth-service")
    - [ghi789] "debugged auth token expiry..."
    - [jkl012] "root cause was clock skew..."
    - [mno345] "deployed fix with tolerance..."
    - [pqr678] "auth-service now handles skew..."
    Action: Summarize into 1 memory

Summary: Would archive 8 memories, create 4 consolidated memories.
Run with dry_run=false to execute.
```

Output format (execute):
```
Consolidation Complete
======================
Archived: 8 memories
Created: 4 consolidated memories
Duplicates merged: 3
Topic groups summarized: 2
Graph edges updated: 12

Details logged to consolidation_log table.
```

### 7.4 CLI: lore consolidate

```
$ lore consolidate --dry-run
$ lore consolidate --execute
$ lore consolidate --execute --project myapp
$ lore consolidate --execute --strategy deduplicate
$ lore consolidate --execute --tier short
$ lore consolidate --log               # show recent consolidation log
$ lore consolidate --log --limit 20
```

Arguments:
- `--dry-run` (default): Preview consolidation without changes.
- `--execute`: Run consolidation and apply changes.
- `--project`: Filter to a specific project.
- `--tier`: Filter to a specific tier.
- `--strategy`: "deduplicate", "summarize", or "all" (default).
- `--log`: Show consolidation history instead of running consolidation.
- `--limit`: Number of log entries to show (default 10).

### 7.5 Store ABC Changes

```python
class Store(ABC):
    # Existing methods updated:

    def list(
        self,
        project=None, type=None, tier=None, limit=None,
        include_archived: bool = False,    # NEW — default excludes archived
    ) -> List[Memory]: ...

    # New methods:
    @abstractmethod
    def save_consolidation_log(self, entry: ConsolidationLogEntry) -> None: ...

    @abstractmethod
    def get_consolidation_log(
        self, limit: int = 50, project: Optional[str] = None,
    ) -> List[ConsolidationLogEntry]: ...
```

### 7.6 Recall Filtering

`recall()` must exclude archived memories from results. This is implemented as a filter in the store's search method:

```python
# In recall/search methods:
results = [r for r in results if not r.memory.archived]
```

For SQLite, add `WHERE archived = 0` to the search query. For Postgres, add `WHERE archived = FALSE`.

## 8. File Changes

| File | Change |
|------|--------|
| `src/lore/types.py` | Add `archived`, `consolidated_into` to `Memory`. Add `ConsolidationLogEntry`, `ConsolidationResult`, `DEFAULT_CONSOLIDATION_CONFIG`, `DEFAULT_RETENTION_POLICIES`. Update `MemoryStats`. |
| `src/lore/lore.py` | Add `consolidate()`, `get_consolidation_log()` methods. Add consolidation config to constructor. Update `recall()` to filter archived memories. |
| `src/lore/consolidation.py` | **NEW** — Pipeline implementation: `identify_candidates()`, `find_duplicates()`, `group_by_entity()`, `summarize_group()`, `archive_originals()`, `relink_graph_edges()`, `create_consolidated_memory()`. |
| `src/lore/store/base.py` | Add `include_archived` param to `list()`. Add `save_consolidation_log()`, `get_consolidation_log()` abstract methods. |
| `src/lore/store/sqlite.py` | Schema migration for `archived`, `consolidated_into` columns. Create `consolidation_log` table. Implement log methods. Update `list()` and search to filter archived. |
| `src/lore/store/memory.py` | Handle `archived` field in filtering. Implement consolidation log in-memory. |
| `src/lore/store/http.py` | Map `archived`, `consolidated_into` fields. Pass `include_archived` in API calls. |
| `src/lore/mcp/server.py` | Add `consolidate` tool. |
| `src/lore/cli.py` | Add `consolidate` subcommand with `--dry-run`, `--execute`, `--project`, `--tier`, `--strategy`, `--log` flags. |
| `tests/test_consolidation.py` | **NEW** — Unit tests for full pipeline. |
| `tests/test_consolidation_dedup.py` | **NEW** — Focused tests for deduplication logic. |
| `tests/test_consolidation_graph.py` | **NEW** — Tests for graph integration during consolidation. |

## 9. Backward Compatibility

| Concern | Mitigation |
|---------|-----------|
| `archived` field absent on existing memories | Default `False` — existing memories are active. SQLite `DEFAULT 0`. |
| `consolidated_into` field absent | Default `None` — no reference. |
| `recall()` behavior change (filtering archived) | No impact — no memories are archived until consolidation runs. |
| `list()` behavior change (`include_archived=False`) | Default excludes archived. Existing calls see no change since no memories are archived initially. |
| `consolidation_log` table doesn't exist | Created on first access via migration (same pattern as other schema migrations). |
| Store ABC new methods | Added with concrete default implementations that raise `NotImplementedError`, allowing gradual adoption by custom store implementations. |

## 10. Acceptance Criteria

### Must-Have

1. **AC1:** Running `consolidate(dry_run=True)` identifies candidate groups without modifying any data.
2. **AC2:** Running `consolidate(dry_run=False)` archives original memories (sets `archived=True`, `consolidated_into=<id>`) and creates new consolidated memories.
3. **AC3:** Two memories with cosine similarity > 0.95 are detected as duplicates and merged — the lower-importance one is archived.
4. **AC4:** A group of 3+ memories sharing an entity (via knowledge graph) are summarized into one consolidated memory when an LLM is configured.
5. **AC5:** Consolidated memory has `importance_score = max(originals)`, `access_count = sum(originals)`, `upvotes = sum(originals)`, `tier = "long"`.
6. **AC6:** Graph edges (relationships) that pointed to archived originals now point to the consolidated memory.
7. **AC7:** `consolidation_log` table contains an entry for each consolidation action with original IDs, strategy, and timestamp.
8. **AC8:** `recall()` never returns archived memories.
9. **AC9:** `list_memories()` excludes archived memories by default; `include_archived=True` shows them.
10. **AC10:** MCP `consolidate` tool works with `dry_run=true` and `dry_run=false`.
11. **AC11:** CLI `lore consolidate --dry-run` shows preview; `--execute` runs consolidation.
12. **AC12:** Without LLM configured, consolidation runs in dedup-only mode (no entity-based summarization).
13. **AC13:** Retention policies are configurable per tier: working=1h, short=7d, long=30d defaults.
14. **AC14:** Existing tests continue to pass (archived field defaults to False, no behavioral change for non-consolidated memories).

### Should-Have

15. **AC15:** Scheduled consolidation runs automatically at configured intervals (daily/weekly).
16. **AC16:** `stats()` includes `archived_count`, `consolidation_count`, and `last_consolidation_at`.
17. **AC17:** CLI `lore consolidate --log` displays recent consolidation history.
18. **AC18:** Consolidation processes in batches (configurable batch size, default 50).

## 11. Success Metrics

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Dedup accuracy | Near-duplicate memories (sim > 0.95) are consistently detected | Test: create 5 pairs of near-duplicates, verify all 5 are detected |
| Consolidation reduction | 40%+ reduction in memory count for stores with significant duplication | Test: create 20 related memories, consolidate, verify < 12 remain active |
| Recall quality improvement | No archived memories appear in recall results | Test: consolidate a group, recall with query matching originals, verify only consolidated memory returned |
| Importance preservation | Consolidated memory importance >= max(original importances) | Test: verify score after consolidation |
| Graph integrity | All graph edges updated after consolidation | Test: query entity relationships post-consolidation, verify memory_id references are valid |
| Audit completeness | Every consolidation action has a log entry | Test: run consolidation, verify log entry count matches groups processed |
| Performance | Consolidation of 100 memories completes in < 30s (excluding LLM time) | Benchmark: synthetic dataset, measure wall time |
| No-LLM mode | Dedup-only consolidation works without any LLM configuration | Test: run consolidation without LLM config, verify dedup works and summarization is skipped |

## 12. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| LLM summarization loses important details | High — information loss is irreversible | Originals are soft-deleted, not destroyed. Quality score (P2) can detect poor summaries. Prompt explicitly instructs preservation of all facts. |
| Aggressive dedup threshold merges distinct memories | Medium — different-but-similar memories merged incorrectly | Default threshold 0.95 is conservative. Dry-run mode allows preview. |
| Graph edge relinking creates inconsistencies | Medium — broken entity relationships | Run in transaction. Validate post-consolidation that all memory_id references in relationships table point to non-archived memories. |
| Consolidation on large stores is slow | Low — batch processing controls memory use | Batch size configurable. Max groups per run capped. Schedule during off-hours. |
| LLM API failures mid-consolidation | Medium — partial consolidation state | Process groups independently. Each group is its own transaction. Failed groups are skipped and logged. |
| Circular consolidation (consolidating already-consolidated memories) | Low — consolidated memories could be re-consolidated | Archived memories are excluded from candidates. Consolidated memories have `source="consolidation"` — optionally exclude from future consolidation runs. |

## 13. Out of Scope

- **Real-time deduplication on ingest** — Future optimization. Current design is batch-only.
- **Cross-project consolidation** — Memories are scoped to a single project.
- **Consolidation undo/rollback** — Originals are preserved via soft delete; manual restoration is possible but no automated rollback.
- **Consolidation visualization** — No web UI or graph visualization of consolidation clusters.
- **Server-side consolidation API** — Consolidation runs in the SDK. Server stores the results. A server-side consolidation endpoint is a future consideration.
- **Streaming consolidation** — No incremental/streaming mode. Batch processing only.
