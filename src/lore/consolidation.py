"""Memory consolidation pipeline — batch dedup + summarization."""

from __future__ import annotations

import logging
import struct
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
    EntityMention,
    Memory,
)

logger = logging.getLogger(__name__)

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

    # ------------------------------------------------------------------
    # Stage 1: Identify Candidates
    # ------------------------------------------------------------------

    def _identify_candidates(
        self,
        project: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> List[Memory]:
        """Find memories eligible for consolidation based on age and tier."""
        policies = self._config["retention_policies"]
        now = datetime.now(timezone.utc)
        candidates = []

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

    # ------------------------------------------------------------------
    # Stage 2a: Deduplication Grouping
    # ------------------------------------------------------------------

    def _find_duplicates(
        self,
        candidates: List[Memory],
    ) -> List[List[Memory]]:
        """Group near-duplicate memories by embedding cosine similarity."""
        threshold = self._config["dedup_threshold"]
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
            norm_a = float(np.linalg.norm(vec_a))
            if norm_a == 0:
                continue

            group = [mem_a]
            for j in range(i + 1, len(candidates)):
                mem_b = candidates[j]
                if mem_b.id in used or mem_b.id not in embeddings:
                    continue
                vec_b = embeddings[mem_b.id]
                norm_b = float(np.linalg.norm(vec_b))
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

    # ------------------------------------------------------------------
    # Stage 2b: Entity/Topic Grouping
    # ------------------------------------------------------------------

    def _group_by_entity(
        self,
        candidates: List[Memory],
        already_grouped: Set[str],
    ) -> List[List[Memory]]:
        """Group memories sharing entities via graph entity_mentions table."""
        min_group_size = self._config["min_group_size"]
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

    # ------------------------------------------------------------------
    # Stage 3: LLM Summarization
    # ------------------------------------------------------------------

    def _summarize_group(
        self,
        memories: List[Memory],
        strategy: str,
    ) -> str:
        """Summarize a group of memories into consolidated content."""
        if strategy == "deduplicate" or self._llm is None:
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

    # ------------------------------------------------------------------
    # Stage 3b: Create Consolidated Memory
    # ------------------------------------------------------------------

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
        merged_tags = sorted(set(tag for m in originals for tag in m.tags))

        # Compute embedding for the new consolidated content
        embedding_vec = self._embedder.embed(content)
        embedding_bytes = struct.pack(f"{len(embedding_vec)}f", *embedding_vec)

        return Memory(
            id=str(ULID()),
            content=content,
            type=resolved_type,
            tier="long",
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

    # ------------------------------------------------------------------
    # Stage 4: Archive Originals
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Stage 5: Relink Graph Edges
    # ------------------------------------------------------------------

    def _relink_graph_edges(
        self,
        original_ids: List[str],
        consolidated_memory_id: str,
    ) -> int:
        """Update entity_mentions and relationships to point to consolidated memory."""
        updated = 0
        now = datetime.now(timezone.utc).isoformat()

        for original_id in original_ids:
            mentions = self._store.get_entity_mentions_for_memory(original_id)
            for mention in mentions:
                new_mention = EntityMention(
                    id=str(ULID()),
                    entity_id=mention.entity_id,
                    memory_id=consolidated_memory_id,
                    mention_type=mention.mention_type,
                    confidence=mention.confidence,
                    created_at=now,
                )
                self._store.save_entity_mention(new_mention)
                updated += 1

            # Update relationships that reference the original memory
            rels = self._store.list_relationships(limit=1000)
            for rel in rels:
                if rel.source_memory_id == original_id:
                    rel.source_memory_id = consolidated_memory_id
                    rel.updated_at = now
                    self._store.update_relationship(rel)
                    updated += 1

        return updated

    # ------------------------------------------------------------------
    # Stage 6: Log
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Dry-run helpers
    # ------------------------------------------------------------------

    def _max_pairwise_similarity(self, group: List[Memory]) -> float:
        """Compute max pairwise cosine similarity within a group."""
        embeddings = []
        for mem in group:
            if mem.embedding is not None:
                count = len(mem.embedding) // 4
                embeddings.append(
                    np.array(struct.unpack(f"{count}f", mem.embedding), dtype=np.float32)
                )

        if len(embeddings) < 2:
            return 0.0

        max_sim = 0.0
        for i in range(len(embeddings)):
            norm_i = float(np.linalg.norm(embeddings[i]))
            if norm_i == 0:
                continue
            for j in range(i + 1, len(embeddings)):
                norm_j = float(np.linalg.norm(embeddings[j]))
                if norm_j == 0:
                    continue
                sim = float(np.dot(embeddings[i], embeddings[j]) / (norm_i * norm_j))
                max_sim = max(max_sim, sim)
        return round(max_sim, 4)

    def _get_shared_entities(self, group: List[Memory]) -> List[str]:
        """Get entity names shared by memories in a group."""
        entity_ids: Set[str] = set()
        for mem in group:
            mentions = self._store.get_entity_mentions_for_memory(mem.id)
            entity_ids.update(m.entity_id for m in mentions)

        names = []
        for eid in entity_ids:
            entity = self._store.get_entity(eid)
            if entity:
                names.append(entity.name)
        return names

    # ------------------------------------------------------------------
    # Full Pipeline Orchestration
    # ------------------------------------------------------------------

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
        all_groups: List[tuple] = []  # (group, strategy_name)
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
                group_info: Dict[str, Any] = {
                    "strategy": strat,
                    "memory_count": len(group),
                    "memory_ids": [m.id for m in group],
                    "preview": (
                        group[0].content[:200] + "..."
                        if len(group[0].content) > 200
                        else group[0].content
                    ),
                }
                if strat == "deduplicate" and len(group) >= 2:
                    group_info["similarity"] = self._max_pairwise_similarity(group)
                if strat == "summarize":
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
        content = self._summarize_group(group, strategy)

        # Stage 3b: Create consolidated memory
        consolidated = self._create_consolidated_memory(group, content, strategy)
        self._store.save(consolidated)

        # Stage 4: Archive originals
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


# ------------------------------------------------------------------
# Scheduled Consolidation
# ------------------------------------------------------------------

_SCHEDULE_INTERVALS: Dict[str, int] = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
}


class ConsolidationScheduler:
    """Background scheduler for periodic consolidation runs."""

    def __init__(
        self,
        engine: ConsolidationEngine,
        interval: str = "daily",
    ) -> None:
        self._engine = engine
        if interval not in _SCHEDULE_INTERVALS:
            raise ValueError(
                f"Invalid schedule interval: {interval!r}. "
                f"Must be one of {list(_SCHEDULE_INTERVALS.keys())}"
            )
        self._interval_seconds = _SCHEDULE_INTERVALS[interval]
        self._task: Any = None

    async def _run_loop(self) -> None:
        import asyncio

        while True:
            try:
                result = await self._engine.consolidate(dry_run=False)
                logger.info(
                    "Scheduled consolidation: %d groups, %d archived, %d created",
                    result.groups_found,
                    result.memories_consolidated,
                    result.memories_created,
                )
            except Exception:
                logger.error("Scheduled consolidation failed", exc_info=True)
            await asyncio.sleep(self._interval_seconds)

    def start(self) -> None:
        """Start the background consolidation task."""
        import asyncio

        self._task = asyncio.ensure_future(self._run_loop())

    def stop(self) -> None:
        """Stop the background consolidation task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
