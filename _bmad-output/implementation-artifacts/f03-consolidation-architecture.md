# Architecture: F3 — Memory Consolidation / Auto-Summarization

**Version:** 1.0
**Author:** Architect Agent
**Date:** 2026-03-06
**PRD:** `_bmad-output/planning-artifacts/f03-consolidation-prd.md`
**Phase:** 4 — Operations Layer
**Depends on:** F4 (Memory Tiers — tier field), F5 (Importance Scoring — importance_score, access_count), F1 (Knowledge Graph — entity_mentions, relationships)
**Dependents:** None (terminal feature)

---

## 1. Overview

F3 adds a batch consolidation pipeline that identifies redundant or related memory clusters, merges or summarizes them via LLM, archives the originals, and updates the knowledge graph — all with full audit logging. It operates as an opt-in batch operation (manual trigger or scheduled) and never runs during `remember()` or `recall()`.

### Architecture Principles

1. **Batch-only** — Consolidation is never triggered on ingest. It runs on explicit trigger (MCP tool, CLI, schedule) to keep the hot path fast.
2. **Dry-run by default** — Every trigger defaults to `dry_run=True` to prevent accidental data changes.
3. **Graceful LLM degradation** — Without LLM configured, consolidation operates in dedup-only mode. Entity-based summarization groups are skipped.
4. **Atomic per-group** — Each consolidation group is processed independently. A failure in one group does not affect others. Partial progress is preserved.
5. **Soft delete, never destroy** — Originals are archived (`archived=True`, `consolidated_into=<id>`) and remain queryable for audit. No data is permanently removed.
6. **Pipeline pattern** — Follows the same pipeline composition pattern as F6 (enrichment pipeline) and F2 (extraction pipeline): a main engine class orchestrates discrete stages.

---

## 2. Data Model Changes

### 2.1 Memory Dataclass (`src/lore/types.py`)

Add two fields to the `Memory` dataclass:

```python
@dataclass
class Memory:
    # ... existing fields (through last_accessed_at) ...
    archived: bool = False
    consolidated_into: Optional[str] = None  # ID of the consolidated memory
```

**Invariants:**
- When `archived=True`, the memory is excluded from `recall()` and `list()` by default.
- `consolidated_into` is always `None` when `archived=False`.
- A memory with `consolidated_into` set always has `archived=True`.

### 2.2 New Types (`src/lore/types.py`)

```python
@dataclass
class ConsolidationLogEntry:
    """Audit record for a single consolidation action."""
    id: str                          # ULID
    consolidated_memory_id: str      # new memory created
    original_memory_ids: List[str]   # memories that were archived
    strategy: str                    # "deduplicate" | "summarize"
    model_used: Optional[str] = None # LLM model name, if any
    original_count: int = 0          # len(original_memory_ids)
    created_at: str = ""             # ISO 8601
    metadata: Optional[Dict[str, Any]] = None  # similarity scores, entity names, etc.


@dataclass
class ConsolidationResult:
    """Result of a consolidation run (dry-run or execute)."""
    groups_found: int = 0            # total groups identified
    memories_consolidated: int = 0   # originals archived
    memories_created: int = 0        # new consolidated memories
    duplicates_merged: int = 0       # near-duplicate pairs merged
    groups: List[Dict[str, Any]] = field(default_factory=list)  # per-group detail
    dry_run: bool = True


# Default retention policies: how old a memory must be to become a consolidation candidate.
DEFAULT_RETENTION_POLICIES: Dict[str, int] = {
    "working": 3600,      # 1 hour
    "short": 604800,      # 7 days
    "long": 2592000,      # 30 days
}

# Default consolidation config.
DEFAULT_CONSOLIDATION_CONFIG: Dict[str, Any] = {
    "retention_policies": dict(DEFAULT_RETENTION_POLICIES),
    "dedup_threshold": 0.95,
    "min_group_size": 3,
    "batch_size": 50,
    "max_groups_per_run": 100,
    "llm_model": None,
}
```

### 2.3 MemoryStats Update (`src/lore/types.py`)

Add consolidation fields to `MemoryStats`:

```python
@dataclass
class MemoryStats:
    # ... existing fields ...
    archived_count: int = 0
    consolidation_count: int = 0
    last_consolidation_at: Optional[str] = None
```

### 2.4 SQLite Schema Migration (`src/lore/store/sqlite.py`)

Follow the existing `_maybe_add_importance_columns()` pattern — a new `_maybe_add_consolidation_columns()` method:

```python
def _maybe_add_consolidation_columns(self) -> None:
    """Add archived and consolidated_into columns if missing."""
    cols = {
        row[1]
        for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
    }
    migrations = []
    if "archived" not in cols:
        migrations.append(
            "ALTER TABLE memories ADD COLUMN archived INTEGER DEFAULT 0"
        )
    if "consolidated_into" not in cols:
        migrations.append(
            "ALTER TABLE memories ADD COLUMN consolidated_into TEXT"
        )
    for sql in migrations:
        self._conn.execute(sql)
    if migrations:
        self._conn.executescript(
            "CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived);"
        )
        self._conn.commit()
```

Call from `__init__` after `_maybe_add_importance_columns()`, before `_conn.executescript(_SCHEMA)`.

### 2.5 New Table: consolidation_log (`src/lore/store/sqlite.py`)

```python
_CONSOLIDATION_LOG_SCHEMA = """\
CREATE TABLE IF NOT EXISTS consolidation_log (
    id                      TEXT PRIMARY KEY,
    consolidated_memory_id  TEXT NOT NULL,
    original_memory_ids     TEXT NOT NULL,       -- JSON array of IDs
    strategy                TEXT NOT NULL,        -- 'deduplicate' | 'summarize'
    model_used              TEXT,
    original_count          INTEGER NOT NULL,
    created_at              TEXT NOT NULL,
    metadata                TEXT                  -- JSON
);
CREATE INDEX IF NOT EXISTS idx_clog_memory
    ON consolidation_log(consolidated_memory_id);
CREATE INDEX IF NOT EXISTS idx_clog_created
    ON consolidation_log(created_at);
"""
```

Created via `_maybe_create_consolidation_log_table()`, called from `__init__` (same pattern as `_maybe_create_fact_tables()`).

### 2.6 In-Memory Store (`src/lore/store/memory.py`)

No schema changes needed. The `MemoryStore` stores `Memory` objects directly; new fields are carried by dataclass defaults. Consolidation log stored as `self._consolidation_log: List[ConsolidationLogEntry] = []`.

### 2.7 HTTP Store (`src/lore/store/http.py`)

Map `archived` and `consolidated_into` in JSON serialization/deserialization. Pass `include_archived` query parameter in API calls.

---

## 3. Store ABC Changes (`src/lore/store/base.py`)

### 3.1 Updated `list()` Signature

```python
def list(
    self,
    project: Optional[str] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    limit: Optional[int] = None,
    include_archived: bool = False,    # NEW
) -> List[Memory]:
```

When `include_archived=False` (default), filter out memories where `archived=True`. This is backward-compatible: existing callers see no change since no memories are archived until consolidation runs.

### 3.2 New Abstract Methods

Following the existing pattern of default no-op implementations (like fact/graph methods):

```python
# ------------------------------------------------------------------
# Consolidation log storage (default no-op implementations)
# ------------------------------------------------------------------

def save_consolidation_log(self, entry: ConsolidationLogEntry) -> None:
    """Save a consolidation log entry. No-op by default."""
    pass

def get_consolidation_log(
    self,
    limit: int = 50,
    project: Optional[str] = None,
) -> List[ConsolidationLogEntry]:
    """Get consolidation log entries. Returns empty list by default."""
    return []
```

No-op defaults allow custom store implementations to work without changes (same pattern as `save_fact`, `save_entity`, etc.).

---

## 4. ConsolidationEngine (`src/lore/consolidation.py`)

### 4.1 Module Layout

New file: `src/lore/consolidation.py`. Single module, no sub-package needed (the pipeline is self-contained, unlike graph which has multiple managers).

### 4.2 Class Design

```python
"""Memory consolidation pipeline — batch dedup + summarization."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import numpy as np
from ulid import ULID

from lore.embed.base import Embedder
from lore.llm.base import LLMProvider
from lore.store.base import Store
from lore.types import (
    ConsolidationLogEntry,
    ConsolidationResult,
    DEFAULT_CONSOLIDATION_CONFIG,
    DEFAULT_RETENTION_POLICIES,
    EntityMention,
    Memory,
)

logger = logging.getLogger(__name__)


class ConsolidationEngine:
    """Six-stage consolidation pipeline.

    Pipeline stages:
      1. IDENTIFY  — Find candidates by tier/age/importance
      2. GROUP     — Cluster by dedup (cosine sim) and entity (graph)
      3. SUMMARIZE — LLM condenses each group
      4. ARCHIVE   — Soft-delete originals
      5. RELINK    — Update graph edges
      6. LOG       — Write consolidation_log entry
    """

    def __init__(
        self,
        store: Store,
        embedder: Embedder,
        llm_provider: Optional[LLMProvider] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._llm = llm_provider
        cfg = dict(DEFAULT_CONSOLIDATION_CONFIG)
        if config:
            cfg.update(config)
        self._config = cfg

    async def consolidate(
        self,
        project: Optional[str] = None,
        tier: Optional[str] = None,
        strategy: str = "all",
        dry_run: bool = True,
    ) -> ConsolidationResult:
        """Run the full consolidation pipeline."""
        ...
```

### 4.3 Pipeline Stages — Detailed Design

#### Stage 1: Identify Candidates

```python
def _identify_candidates(
    self,
    project: Optional[str] = None,
    tier: Optional[str] = None,
) -> List[Memory]:
    """Find memories eligible for consolidation based on age and tier."""
    policies = self._config["retention_policies"]
    now = datetime.now(timezone.utc)
    candidates = []

    # Use store.list with include_archived=False (default) to skip already-archived
    all_memories = self._store.list(project=project, tier=tier)

    for memory in all_memories:
        if memory.archived:
            continue
        created = datetime.fromisoformat(memory.created_at)
        age_seconds = (now - created).total_seconds()
        threshold = policies.get(memory.tier, policies["long"])
        if age_seconds > threshold:
            candidates.append(memory)

    return candidates
```

**Batch processing:** When candidates exceed `batch_size`, process in chunks:

```python
batch_size = self._config["batch_size"]  # default 50
for i in range(0, len(candidates), batch_size):
    batch = candidates[i:i + batch_size]
    # Process batch through stages 2-6
```

#### Stage 2a: Deduplication Grouping

```python
def _find_duplicates(
    self,
    candidates: List[Memory],
) -> List[List[Memory]]:
    """Group near-duplicate memories by embedding cosine similarity."""
    threshold = self._config["dedup_threshold"]  # default 0.95
    groups: List[List[Memory]] = []
    used: Set[str] = set()

    # Deserialize embeddings once
    embeddings: Dict[str, np.ndarray] = {}
    for mem in candidates:
        if mem.embedding is not None:
            count = len(mem.embedding) // 4
            embeddings[mem.id] = np.array(
                struct.unpack(f"{count}f", mem.embedding), dtype=np.float32
            )

    for i, mem_a in enumerate(candidates):
        if mem_a.id in used or mem_a.id not in embeddings:
            continue
        vec_a = embeddings[mem_a.id]
        norm_a = np.linalg.norm(vec_a)
        if norm_a == 0:
            continue

        group = [mem_a]
        for j in range(i + 1, len(candidates)):
            mem_b = candidates[j]
            if mem_b.id in used or mem_b.id not in embeddings:
                continue
            vec_b = embeddings[mem_b.id]
            norm_b = np.linalg.norm(vec_b)
            if norm_b == 0:
                continue

            sim = float(np.dot(vec_a, vec_b) / (norm_a * norm_b))
            if sim > threshold:
                group.append(mem_b)
                used.add(mem_b.id)

        if len(group) > 1:
            groups.append(group)
            used.add(mem_a.id)

    return groups
```

**Cosine similarity** is computed via `np.dot(a, b) / (||a|| * ||b||)` — same numpy-based approach used in `Lore.recall()` (`src/lore/lore.py`). Embeddings are deserialized from the same `struct.pack` float32 format stored by `_serialize_embedding()`.

#### Stage 2b: Entity/Topic Grouping

```python
def _group_by_entity(
    self,
    candidates: List[Memory],
    already_grouped: Set[str],
) -> List[List[Memory]]:
    """Group memories sharing entities via graph entity_mentions table."""
    min_group_size = self._config["min_group_size"]  # default 3
    entity_to_memories: Dict[str, List[Memory]] = defaultdict(list)

    for memory in candidates:
        if memory.id in already_grouped:
            continue
        mentions = self._store.get_entity_mentions_for_memory(memory.id)
        for mention in mentions:
            entity_to_memories[mention.entity_id].append(memory)

    groups: List[List[Memory]] = []
    used: Set[str] = set()

    # Sort entities by mention count (descending) for deterministic grouping
    for entity_id, memories in sorted(
        entity_to_memories.items(),
        key=lambda kv: len(kv[1]),
        reverse=True,
    ):
        ungrouped = [m for m in memories if m.id not in used]
        if len(ungrouped) >= min_group_size:
            groups.append(ungrouped)
            used.update(m.id for m in ungrouped)

    return groups
```

**Key design decision:** Entity grouping uses `entity_mentions` (the junction table between entities and memories) rather than traversing the full graph. This is simpler, faster, and directly answers "which memories mention the same entity?" — exactly what we need for consolidation clustering.

The two grouping strategies are applied in order:
1. **Dedup first** — Identify near-duplicate pairs/clusters.
2. **Entity grouping second** — On remaining (non-deduped) candidates only.

This prevents dedup members from also appearing in entity groups.

#### Stage 3: LLM Summarization

```python
CONSOLIDATION_PROMPT = """You are a memory consolidation system. Given a group of related memories, create a single concise memory that preserves all important information.

Rules:
- Preserve all facts, lessons, and actionable insights
- Remove redundancy and repetition
- Use clear, direct language
- If memories contain contradictory information, note the contradiction
- Output only the consolidated memory content, nothing else

Memories to consolidate:
{memories}

Consolidated memory:"""


async def _summarize_group(
    self,
    memories: List[Memory],
    strategy: str,
) -> str:
    """Summarize a group of memories into consolidated content."""
    if strategy == "deduplicate" or self._llm is None:
        # Dedup mode: keep the memory with highest importance
        best = max(memories, key=lambda m: m.importance_score)
        return best.content

    # LLM summarization
    memories_text = "\n---\n".join(
        f"[{m.type}, importance: {m.importance_score:.2f}] {m.content}"
        for m in memories
    )
    prompt = CONSOLIDATION_PROMPT.format(memories=memories_text)
    try:
        return self._llm.complete(prompt, max_tokens=500)
    except Exception:
        logger.warning(
            "LLM summarization failed for group of %d memories, "
            "falling back to highest-importance content",
            len(memories),
            exc_info=True,
        )
        best = max(memories, key=lambda m: m.importance_score)
        return best.content
```

**Fail-safe:** If the LLM call fails (network error, rate limit, etc.), the engine falls back to picking the highest-importance memory's content — same as dedup mode. This ensures consolidation never fails due to LLM unavailability.

**Configurable model:** The `LLMProvider` is injected via the constructor. The Lore facade resolves the provider using the same `llm_provider`/`llm_model`/`llm_api_key` config pattern established by F6/F9.

#### Stage 4: Archive Originals

```python
def _archive_originals(
    self,
    originals: List[Memory],
    consolidated_memory_id: str,
) -> None:
    """Soft-delete original memories with reference to consolidated memory."""
    now = datetime.now(timezone.utc).isoformat()
    for memory in originals:
        memory.archived = True
        memory.consolidated_into = consolidated_memory_id
        memory.updated_at = now
        self._store.update(memory)
```

#### Stage 5: Relink Graph Edges

```python
def _relink_graph_edges(
    self,
    original_ids: List[str],
    consolidated_memory_id: str,
) -> int:
    """Update entity_mentions and relationships to point to consolidated memory."""
    updated = 0
    now = datetime.now(timezone.utc).isoformat()

    for original_id in original_ids:
        # Update entity_mentions: change memory_id from original to consolidated
        mentions = self._store.get_entity_mentions_for_memory(original_id)
        for mention in mentions:
            # Create new mention linking entity to consolidated memory
            # (existing unique index on entity_id, memory_id handles dedup)
            from lore.types import EntityMention
            new_mention = EntityMention(
                id=str(ULID()),
                entity_id=mention.entity_id,
                memory_id=consolidated_memory_id,
                mention_type=mention.mention_type,
                confidence=mention.confidence,
                created_at=now,
            )
            self._store.save_entity_mention(new_mention)  # INSERT OR IGNORE
            updated += 1

        # Update relationships that reference the original memory
        # Relationships use source_memory_id to track provenance
        rels = self._store.list_relationships(limit=1000)
        for rel in rels:
            if rel.source_memory_id == original_id:
                rel.source_memory_id = consolidated_memory_id
                rel.updated_at = now
                self._store.update_relationship(rel)
                updated += 1

    return updated
```

**Design decision:** Rather than deleting old entity_mentions and creating new ones, we create new mentions (INSERT OR IGNORE handles the unique constraint) and leave old ones in place. This preserves the audit trail and avoids cascading deletes.

**Optimization note:** The relationship update scans all relationships looking for `source_memory_id` matches. For stores with large relationship tables, a future optimization would add a `get_relationships_by_memory_id()` method. For the current scope (hundreds to low thousands of relationships), scanning is acceptable.

#### Stage 6: Log

```python
def _log_consolidation(
    self,
    consolidated_memory_id: str,
    original_ids: List[str],
    strategy: str,
    model_used: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> ConsolidationLogEntry:
    """Write a consolidation_log entry."""
    entry = ConsolidationLogEntry(
        id=str(ULID()),
        consolidated_memory_id=consolidated_memory_id,
        original_memory_ids=original_ids,
        strategy=strategy,
        model_used=model_used,
        original_count=len(original_ids),
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata=metadata,
    )
    self._store.save_consolidation_log(entry)
    return entry
```

### 4.4 Consolidated Memory Construction

```python
def _create_consolidated_memory(
    self,
    originals: List[Memory],
    content: str,
    strategy: str,
) -> Memory:
    """Build a new Memory from consolidated content and original metadata."""
    now = datetime.now(timezone.utc).isoformat()

    # Resolve type: most common type among originals
    type_counts = Counter(m.type for m in originals)
    resolved_type = type_counts.most_common(1)[0][0]

    # Merge tags: union of all tags, deduplicated
    merged_tags = list(set(tag for m in originals for tag in m.tags))

    # Compute embedding for the new consolidated content
    embedding_vec = self._embedder.embed(content)
    embedding_bytes = struct.pack(f"{len(embedding_vec)}f", *embedding_vec)

    memory = Memory(
        id=str(ULID()),
        content=content,
        type=resolved_type,
        tier="long",  # consolidated memories are always long-term
        tags=merged_tags,
        metadata={
            "consolidated_from": [m.id for m in originals],
            "consolidation_strategy": strategy,
            "original_count": len(originals),
        },
        source="consolidation",
        project=originals[0].project,
        embedding=embedding_bytes,
        created_at=now,
        updated_at=now,
        confidence=max(m.confidence for m in originals),
        importance_score=max(m.importance_score for m in originals),
        access_count=sum(m.access_count for m in originals),
        upvotes=sum(m.upvotes for m in originals),
        downvotes=sum(m.downvotes for m in originals),
    )
    return memory
```

**Importance inheritance:** `importance_score = max(originals)` — the consolidated memory is at least as important as the most important source. `access_count`, `upvotes`, `downvotes` are summed to preserve total engagement signal. `confidence = max(originals)` — the consolidated memory is at least as confident as the most confident source.

### 4.5 Full Pipeline Orchestration

```python
async def consolidate(
    self,
    project: Optional[str] = None,
    tier: Optional[str] = None,
    strategy: str = "all",
    dry_run: bool = True,
) -> ConsolidationResult:
    """Run the full consolidation pipeline."""
    result = ConsolidationResult(dry_run=dry_run)

    # Stage 1: Identify candidates
    candidates = self._identify_candidates(project=project, tier=tier)
    if not candidates:
        return result

    # Stage 2: Group
    all_groups: List[tuple[List[Memory], str]] = []  # (group, strategy_name)
    already_grouped: Set[str] = set()

    if strategy in ("deduplicate", "all"):
        dedup_groups = self._find_duplicates(candidates)
        for group in dedup_groups:
            all_groups.append((group, "deduplicate"))
            already_grouped.update(m.id for m in group)

    if strategy in ("summarize", "all") and self._llm is not None:
        entity_groups = self._group_by_entity(candidates, already_grouped)
        for group in entity_groups:
            all_groups.append((group, "summarize"))

    # Apply max_groups_per_run safety limit
    max_groups = self._config["max_groups_per_run"]
    all_groups = all_groups[:max_groups]
    result.groups_found = len(all_groups)

    if dry_run:
        # Build preview without modifying data
        for group, strat in all_groups:
            group_info = {
                "strategy": strat,
                "memory_count": len(group),
                "memory_ids": [m.id for m in group],
                "preview": group[0].content[:200] + "..." if len(group[0].content) > 200 else group[0].content,
            }
            if strat == "deduplicate" and len(group) >= 2:
                # Compute max pairwise similarity for display
                group_info["similarity"] = self._max_pairwise_similarity(group)
            if strat == "summarize":
                # Include entity names for context
                group_info["entities"] = self._get_shared_entities(group)
            result.groups.append(group_info)
            result.memories_consolidated += len(group)
            result.memories_created += 1
            if strat == "deduplicate":
                result.duplicates_merged += len(group) - 1
        return result

    # Execute consolidation
    for group, strat in all_groups:
        try:
            await self._process_group(group, strat, result)
        except Exception:
            logger.error(
                "Failed to consolidate group of %d memories (strategy=%s), skipping",
                len(group), strat, exc_info=True,
            )

    return result


async def _process_group(
    self,
    group: List[Memory],
    strategy: str,
    result: ConsolidationResult,
) -> None:
    """Process a single consolidation group through stages 3-6."""
    # Stage 3: Summarize
    content = await self._summarize_group(group, strategy)

    # Stage 4a: Create consolidated memory
    consolidated = self._create_consolidated_memory(group, content, strategy)
    self._store.save(consolidated)

    # Stage 4b: Archive originals
    self._archive_originals(group, consolidated.id)

    # Stage 5: Relink graph edges
    original_ids = [m.id for m in group]
    edges_updated = self._relink_graph_edges(original_ids, consolidated.id)

    # Stage 6: Log
    model_name = None
    if self._llm is not None and strategy == "summarize":
        model_name = getattr(self._llm, "model", None)
    self._log_consolidation(
        consolidated_memory_id=consolidated.id,
        original_ids=original_ids,
        strategy=strategy,
        model_used=model_name,
        metadata={"edges_updated": edges_updated},
    )

    # Update result counters
    result.memories_consolidated += len(group)
    result.memories_created += 1
    if strategy == "deduplicate":
        result.duplicates_merged += len(group) - 1
```

---

## 5. Recall Filtering

### 5.1 SqliteStore Changes

All memory queries must exclude archived memories by default.

**`list()` method:**
```python
def list(self, project=None, type=None, tier=None, limit=None,
         include_archived: bool = False) -> List[Memory]:
    query = "SELECT * FROM memories"
    conditions = []
    params = []
    if not include_archived:
        conditions.append("archived = 0")
    # ... existing filters ...
```

**Recall path in `Lore.recall()`:** The existing `recall()` method iterates all memories and computes cosine similarity. Add filtering:

```python
# In Lore.recall(), after loading all memories:
all_memories = self._store.list(project=project, tier=tier)
# Filter archived (already handled by store.list if include_archived=False)
```

Since `store.list()` already excludes archived by default, no change needed in `recall()` itself — it naturally excludes archived memories.

### 5.2 `_row_to_memory()` Update

Update `SqliteStore._row_to_memory()` to read the new columns:

```python
@staticmethod
def _row_to_memory(row: sqlite3.Row) -> Memory:
    # ... existing field extraction ...
    keys = row.keys()
    return Memory(
        # ... existing fields ...
        archived=bool(row["archived"]) if "archived" in keys else False,
        consolidated_into=row["consolidated_into"] if "consolidated_into" in keys else None,
    )
```

### 5.3 `save()` and `update()` Column Updates

Add `archived` and `consolidated_into` to the INSERT and UPDATE SQL in `save()` and `update()`.

---

## 6. Lore Facade Integration (`src/lore/lore.py`)

### 6.1 Constructor Changes

```python
class Lore:
    def __init__(
        self,
        # ... existing params ...
        consolidation_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        # ... existing init ...

        # Consolidation engine (always available, LLM optional)
        from lore.consolidation import ConsolidationEngine
        self._consolidation_engine = ConsolidationEngine(
            store=self._store,
            embedder=self._embedder,
            llm_provider=self._llm_provider if hasattr(self, '_llm_provider') else None,
            config=consolidation_config,
        )
```

The consolidation engine is always instantiated (it's lightweight — just stores references). The LLM provider is optional. If the user already configured an LLM provider for F2/F6/F9, the same provider is reused.

### 6.2 New Methods

```python
async def consolidate(
    self,
    project: Optional[str] = None,
    tier: Optional[str] = None,
    strategy: str = "all",
    dry_run: bool = True,
) -> ConsolidationResult:
    """Run the consolidation pipeline.

    Args:
        project: Consolidate only memories for this project.
        tier: Consolidate only memories in this tier.
        strategy: 'deduplicate', 'summarize', or 'all' (default).
        dry_run: If True (default), preview without making changes.

    Returns:
        ConsolidationResult with details of what was/would be consolidated.
    """
    return await self._consolidation_engine.consolidate(
        project=project or self.project,
        tier=tier,
        strategy=strategy,
        dry_run=dry_run,
    )

def get_consolidation_log(
    self,
    limit: int = 50,
    project: Optional[str] = None,
) -> List[ConsolidationLogEntry]:
    """Retrieve consolidation history."""
    return self._store.get_consolidation_log(limit=limit, project=project)
```

---

## 7. MCP Tool (`src/lore/mcp/server.py`)

```python
@mcp.tool(
    description=(
        "Trigger memory consolidation. Merges near-duplicate memories and "
        "summarizes related memory clusters into concise long-term memories. "
        "USE THIS WHEN: the memory store has grown large with redundant or "
        "overlapping memories. Defaults to dry-run mode (preview only)."
    ),
)
async def consolidate(
    project: Optional[str] = None,
    dry_run: bool = True,
    strategy: Optional[str] = None,
) -> str:
    """Consolidate memories."""
    lore = _get_lore()
    result = await lore.consolidate(
        project=project,
        strategy=strategy or "all",
        dry_run=dry_run,
    )
    return _format_consolidation_result(result)


def _format_consolidation_result(result: ConsolidationResult) -> str:
    """Format consolidation result for MCP output."""
    if result.dry_run:
        lines = [
            "Consolidation Preview (DRY RUN)",
            "================================",
            f"Groups found: {result.groups_found}",
            f"Would archive: {result.memories_consolidated} memories",
            f"Would create: {result.memories_created} consolidated memories",
            f"Duplicates to merge: {result.duplicates_merged}",
        ]
        if result.groups:
            lines.append("")
            for i, g in enumerate(result.groups, 1):
                lines.append(f"  Group {i} ({g['strategy']}): {g['memory_count']} memories")
                lines.append(f"    Preview: {g['preview']}")
        lines.append("")
        lines.append("Run with dry_run=false to execute.")
    else:
        lines = [
            "Consolidation Complete",
            "======================",
            f"Archived: {result.memories_consolidated} memories",
            f"Created: {result.memories_created} consolidated memories",
            f"Duplicates merged: {result.duplicates_merged}",
            "",
            "Details logged to consolidation_log table.",
        ]
    return "\n".join(lines)
```

---

## 8. CLI (`src/lore/cli.py`)

### 8.1 New Subcommand: `consolidate`

```python
def cmd_consolidate(args: argparse.Namespace) -> None:
    import asyncio
    lore = _get_lore(args.db)

    if getattr(args, "log", False):
        # Show consolidation history
        entries = lore.get_consolidation_log(
            limit=getattr(args, "limit", 10),
        )
        if not entries:
            print("No consolidation history found.")
        for entry in entries:
            print(f"[{entry.created_at}] {entry.strategy}: "
                  f"{entry.original_count} memories -> {entry.consolidated_memory_id}")
        lore.close()
        return

    dry_run = not getattr(args, "execute", False)
    result = asyncio.run(lore.consolidate(
        project=args.project,
        tier=getattr(args, "tier", None),
        strategy=getattr(args, "strategy", "all"),
        dry_run=dry_run,
    ))
    print(_format_consolidation_result(result))
    lore.close()
```

### 8.2 Argument Registration

Add to `build_parser()`:

```python
consolidate_parser = subparsers.add_parser("consolidate", help="Consolidate memories")
consolidate_parser.add_argument("--dry-run", action="store_true", default=True, help="Preview (default)")
consolidate_parser.add_argument("--execute", action="store_true", help="Execute consolidation")
consolidate_parser.add_argument("--project", help="Filter by project")
consolidate_parser.add_argument("--tier", help="Filter by tier")
consolidate_parser.add_argument("--strategy", choices=["deduplicate", "summarize", "all"], default="all")
consolidate_parser.add_argument("--log", action="store_true", help="Show consolidation history")
consolidate_parser.add_argument("--limit", type=int, default=10, help="Log entries to show")
consolidate_parser.add_argument("--db", help="Database path")
consolidate_parser.set_defaults(func=cmd_consolidate)
```

---

## 9. Scheduling (P1)

### 9.1 Design

Scheduled consolidation uses Python's `asyncio` with a simple periodic task — no external dependency (no APScheduler). This keeps the dependency footprint minimal.

```python
# src/lore/consolidation.py (addition to ConsolidationEngine)

import asyncio

class ConsolidationScheduler:
    """Simple periodic consolidation scheduler."""

    INTERVALS = {
        "hourly": 3600,
        "daily": 86400,
        "weekly": 604800,
    }

    def __init__(
        self,
        engine: ConsolidationEngine,
        schedule: str = "daily",
        project: Optional[str] = None,
    ) -> None:
        self._engine = engine
        self._interval = self.INTERVALS.get(schedule, 86400)
        self._project = project
        self._task: Optional[asyncio.Task] = None

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                result = await self._engine.consolidate(
                    project=self._project,
                    dry_run=False,
                )
                logger.info(
                    "Scheduled consolidation: archived=%d, created=%d",
                    result.memories_consolidated,
                    result.memories_created,
                )
            except Exception:
                logger.error("Scheduled consolidation failed", exc_info=True)

    def start(self) -> None:
        """Start the background consolidation loop."""
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run_loop())

    def stop(self) -> None:
        """Stop the background consolidation loop."""
        if self._task and not self._task.done():
            self._task.cancel()
```

The scheduler is only started when `consolidation_schedule` is set in the Lore constructor:

```python
# In Lore.__init__:
self._consolidation_scheduler = None
if consolidation_schedule:
    self._consolidation_scheduler = ConsolidationScheduler(
        engine=self._consolidation_engine,
        schedule=consolidation_schedule,
        project=self.project,
    )
    # Note: start() must be called within a running event loop.
    # The MCP server's event loop will trigger this.
```

---

## 10. Dry-Run Mode

Dry-run is the default for both MCP and CLI. When `dry_run=True`:

1. Stages 1 and 2 execute normally (identify candidates, group them).
2. Stage 3 (summarization) is **skipped** — no LLM calls are made.
3. Stages 4-6 (archive, relink, log) are **skipped** — no data changes.
4. The result includes per-group previews: memory count, first 200 chars of content, pairwise similarity scores (for dedup), shared entity names (for entity groups).

This allows users to inspect what would happen before committing.

---

## 11. Store Implementation Details

### 11.1 SqliteStore — Consolidation Log CRUD

```python
def save_consolidation_log(self, entry: ConsolidationLogEntry) -> None:
    self._conn.execute(
        """INSERT OR REPLACE INTO consolidation_log
           (id, consolidated_memory_id, original_memory_ids, strategy,
            model_used, original_count, created_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.id,
            entry.consolidated_memory_id,
            json.dumps(entry.original_memory_ids),
            entry.strategy,
            entry.model_used,
            entry.original_count,
            entry.created_at,
            json.dumps(entry.metadata) if entry.metadata else None,
        ),
    )
    self._conn.commit()

def get_consolidation_log(
    self,
    limit: int = 50,
    project: Optional[str] = None,
) -> List[ConsolidationLogEntry]:
    # Project filtering would require joining with memories table
    # For simplicity, return all entries sorted by created_at desc
    rows = self._conn.execute(
        "SELECT * FROM consolidation_log ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [self._row_to_consolidation_log(r) for r in rows]

@staticmethod
def _row_to_consolidation_log(row: sqlite3.Row) -> ConsolidationLogEntry:
    return ConsolidationLogEntry(
        id=row["id"],
        consolidated_memory_id=row["consolidated_memory_id"],
        original_memory_ids=json.loads(row["original_memory_ids"]),
        strategy=row["strategy"],
        model_used=row["model_used"],
        original_count=row["original_count"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else None,
    )
```

### 11.2 MemoryStore — In-Memory Implementation

```python
class MemoryStore(Store):
    def __init__(self):
        # ... existing ...
        self._consolidation_log: List[ConsolidationLogEntry] = []

    def list(self, project=None, type=None, tier=None, limit=None,
             include_archived: bool = False) -> List[Memory]:
        results = list(self._memories.values())
        if not include_archived:
            results = [m for m in results if not m.archived]
        # ... existing filters ...

    def save_consolidation_log(self, entry: ConsolidationLogEntry) -> None:
        self._consolidation_log.append(entry)

    def get_consolidation_log(self, limit=50, project=None) -> List[ConsolidationLogEntry]:
        entries = sorted(self._consolidation_log, key=lambda e: e.created_at, reverse=True)
        return entries[:limit]
```

---

## 12. File Changes Summary

| File | Change |
|------|--------|
| `src/lore/types.py` | Add `archived`, `consolidated_into` to `Memory`. Add `ConsolidationLogEntry`, `ConsolidationResult`, `DEFAULT_CONSOLIDATION_CONFIG`, `DEFAULT_RETENTION_POLICIES`. Update `MemoryStats`. |
| `src/lore/consolidation.py` | **NEW** — `ConsolidationEngine` (6-stage pipeline), `ConsolidationScheduler`. |
| `src/lore/lore.py` | Add `consolidation_config` param to constructor. Instantiate `ConsolidationEngine`. Add `consolidate()` and `get_consolidation_log()` methods. |
| `src/lore/store/base.py` | Add `include_archived` param to `list()`. Add `save_consolidation_log()`, `get_consolidation_log()` no-op defaults. |
| `src/lore/store/sqlite.py` | Add `_maybe_add_consolidation_columns()`, `_maybe_create_consolidation_log_table()`. Update `list()`, `save()`, `update()`, `_row_to_memory()`. Add consolidation log CRUD. Update `_SCHEMA` column list. |
| `src/lore/store/memory.py` | Add `include_archived` filter to `list()`. Add consolidation log in-memory storage. |
| `src/lore/store/http.py` | Map `archived`, `consolidated_into` fields. Pass `include_archived` in API calls. |
| `src/lore/mcp/server.py` | Add `consolidate` tool. |
| `src/lore/cli.py` | Add `consolidate` subcommand. |
| `tests/test_consolidation.py` | **NEW** — Unit tests for full pipeline. |
| `tests/test_consolidation_dedup.py` | **NEW** — Focused tests for deduplication. |
| `tests/test_consolidation_graph.py` | **NEW** — Tests for graph integration. |

---

## 13. Testing Strategy

### 13.1 Framework and Patterns

Tests use `pytest` with the existing parametrized fixture pattern (`@pytest.fixture(params=["memory", "sqlite"])`) to test against both in-memory and SQLite stores.

### 13.2 Test Files

#### `tests/test_consolidation.py` — Pipeline Integration

```
Test cases:
  - test_consolidate_dry_run_no_changes: Dry run identifies groups but makes no changes
  - test_consolidate_empty_store: No candidates, empty result
  - test_consolidate_fresh_memories_not_candidates: Memories younger than retention threshold skipped
  - test_consolidate_archived_memories_excluded: Already-archived memories not re-consolidated
  - test_consolidate_full_pipeline: End-to-end: create memories, consolidate, verify archives + new memory
  - test_consolidate_importance_inheritance: max(importance), sum(access_count, upvotes, downvotes)
  - test_consolidate_tags_merged: Union of tags from all originals
  - test_consolidate_type_resolved: Most common type wins
  - test_consolidate_tier_always_long: Consolidated memory always tier="long"
  - test_consolidate_source_is_consolidation: source="consolidation" on new memory
  - test_consolidate_metadata_tracks_originals: metadata contains consolidated_from, strategy, count
  - test_consolidate_batch_processing: >50 candidates processed in batches
  - test_consolidate_max_groups_limit: Safety limit on groups per run
  - test_consolidate_strategy_deduplicate_only: Only dedup groups, no entity groups
  - test_consolidate_strategy_summarize_only: Only entity groups (with LLM)
  - test_consolidate_no_llm_dedup_only: Without LLM, entity groups skipped
  - test_consolidate_llm_failure_fallback: LLM error falls back to highest-importance
  - test_consolidation_log_created: Each group writes a log entry
  - test_get_consolidation_log: Retrieve log entries, ordered by created_at desc
```

#### `tests/test_consolidation_dedup.py` — Deduplication Logic

```
Test cases:
  - test_find_duplicates_high_similarity: sim > 0.95 grouped
  - test_find_duplicates_below_threshold: sim < 0.95 not grouped
  - test_find_duplicates_exact_match: sim = 1.0 (identical embeddings)
  - test_find_duplicates_configurable_threshold: Custom threshold (e.g., 0.90)
  - test_find_duplicates_transitive_grouping: A~B and B~C -> [A,B,C] in one group
  - test_find_duplicates_no_embeddings: Memories without embeddings skipped
  - test_dedup_merge_keeps_highest_importance: Without LLM, highest importance content kept
  - test_dedup_archives_lower_importance: Lower importance originals archived
  - test_dedup_two_memories_minimal_group: Two duplicates form a valid group
```

#### `tests/test_consolidation_graph.py` — Graph Integration

```
Test cases:
  - test_group_by_entity_basic: 3+ memories sharing entity grouped
  - test_group_by_entity_min_group_size: <3 memories not grouped
  - test_group_by_entity_excludes_already_deduped: Deduped memories skipped
  - test_relink_entity_mentions: After consolidation, entity mentions point to new memory
  - test_relink_relationships: Relationships with source_memory_id updated
  - test_recall_after_consolidation: Recall returns consolidated memory, not archived originals
  - test_list_excludes_archived: list() default excludes archived
  - test_list_include_archived: list(include_archived=True) shows archived
  - test_graph_edges_integrity: All entity_mention memory_ids reference non-archived memories
```

### 13.3 Test Helpers

```python
# Shared fixture for creating memories with controlled embeddings
def make_memory(content: str, embedding: List[float], **kwargs) -> Memory:
    """Create a Memory with specified embedding for testing."""
    import struct
    return Memory(
        id=str(ULID()),
        content=content,
        embedding=struct.pack(f"{len(embedding)}f", *embedding),
        created_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        **kwargs,
    )

# Create near-duplicate pair with high cosine similarity
def make_duplicate_pair(base_embedding: List[float], noise: float = 0.01):
    """Return two embeddings with cosine similarity > 0.99."""
    import numpy as np
    base = np.array(base_embedding, dtype=np.float32)
    perturbed = base + np.random.uniform(-noise, noise, size=base.shape).astype(np.float32)
    return base.tolist(), perturbed.tolist()
```

### 13.4 Mock LLM for Testing

```python
class MockLLMProvider(LLMProvider):
    """Mock LLM that returns a fixed summary."""

    def __init__(self, response: str = "Consolidated summary."):
        self._response = response
        self.call_count = 0

    def complete(self, prompt: str, *, max_tokens: int = 200) -> str:
        self.call_count += 1
        return self._response
```

---

## 14. Edge Cases and Error Handling

| Case | Handling |
|------|---------|
| Memory has no embedding | Skipped during deduplication (cannot compute similarity). Included in entity grouping if it has entity mentions. |
| Memory has zero-norm embedding | Skipped during deduplication (division by zero in cosine similarity). |
| LLM returns empty string | Fall back to highest-importance content. |
| LLM returns excessively long response | `max_tokens=500` limit on the LLM call. |
| Concurrent consolidation runs | No locking mechanism. Two concurrent runs may produce redundant archives. Acceptable: the second run will find fewer candidates (already archived). |
| Consolidating already-consolidated memories | Consolidated memories have `source="consolidation"`. They are valid candidates for future consolidation (no special exclusion). If re-consolidated, the audit trail tracks the chain via `consolidated_from` metadata. |
| Single-memory groups | Dedup requires >=2 members. Entity grouping requires `min_group_size` (default 3). Single-memory "groups" are dropped. |
| Empty project scope | `project=None` consolidates across all projects. |
| Store doesn't support graph | `get_entity_mentions_for_memory()` returns `[]` by default (no-op in base Store). Entity grouping produces no groups — dedup-only mode. |

---

## 15. Performance Considerations

| Concern | Mitigation |
|---------|-----------|
| Loading all memories for candidate identification | `store.list()` with `tier` filter limits scope. Batch processing (default 50) controls memory. |
| O(n^2) pairwise similarity in dedup | Acceptable for batch sizes <= 50. For larger corpora, future optimization: approximate nearest neighbor (ANN) index. |
| Entity mention lookups per memory | One SQL query per memory (`get_entity_mentions_for_memory`). For 50 memories, that's 50 queries — fast against SQLite with indexed `memory_id`. |
| Embedding deserialization | Done once per batch, cached in a dict. Not re-deserialized per comparison. |
| LLM call latency | One call per entity-based group. Dedup groups don't use LLM. Groups are processed sequentially (could be parallelized in future). |

---

## 16. Backward Compatibility

| Concern | Mitigation |
|---------|-----------|
| `archived` field absent on existing memories | Defaults to `False`. SQLite `DEFAULT 0`. |
| `consolidated_into` field absent | Defaults to `None`. |
| `recall()` behavior change | No impact — no memories are archived until consolidation runs. |
| `list()` new parameter | Default `include_archived=False` preserves existing behavior. |
| `consolidation_log` table missing | Created on `__init__` (same pattern as fact/graph tables). |
| Store ABC new methods | No-op defaults. Custom stores continue to work without implementing consolidation methods. |
| Lore constructor new param | `consolidation_config=None` is optional. Existing code unaffected. |
