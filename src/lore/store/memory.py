"""In-memory store implementation for testing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lore.store.base import Store
from lore.types import ConflictEntry, Fact, Memory


class MemoryStore(Store):
    """In-memory store backed by a dict. Useful for testing."""

    def __init__(self) -> None:
        self._memories: Dict[str, Memory] = {}
        self._facts: Dict[str, Fact] = {}
        self._conflict_log: List[ConflictEntry] = []

    def save(self, memory: Memory) -> None:
        self._memories[memory.id] = memory

    def get(self, memory_id: str) -> Optional[Memory]:
        return self._memories.get(memory_id)

    def list(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Memory]:
        memories = list(self._memories.values())
        if project is not None:
            memories = [m for m in memories if m.project == project]
        if type is not None:
            memories = [m for m in memories if m.type == type]
        if tier is not None:
            memories = [m for m in memories if m.tier == tier]
        memories.sort(key=lambda m: m.created_at, reverse=True)
        if limit is not None:
            memories = memories[:limit]
        return memories

    def update(self, memory: Memory) -> bool:
        if memory.id not in self._memories:
            return False
        self._memories[memory.id] = memory
        return True

    def delete(self, memory_id: str) -> bool:
        existed = self._memories.pop(memory_id, None) is not None
        if existed:
            # Cascade: remove facts for this memory
            to_remove = [fid for fid, f in self._facts.items() if f.memory_id == memory_id]
            for fid in to_remove:
                del self._facts[fid]
        return existed

    def count(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> int:
        memories = list(self._memories.values())
        if project is not None:
            memories = [m for m in memories if m.project == project]
        if type is not None:
            memories = [m for m in memories if m.type == type]
        if tier is not None:
            memories = [m for m in memories if m.tier == tier]
        return len(memories)

    def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        expired_ids = [
            mid for mid, m in self._memories.items()
            if m.expires_at is not None
            and datetime.fromisoformat(m.expires_at) < now
        ]
        for mid in expired_ids:
            del self._memories[mid]
        return len(expired_ids)

    # ------------------------------------------------------------------
    # Fact + conflict CRUD
    # ------------------------------------------------------------------

    def save_fact(self, fact: Fact) -> None:
        self._facts[fact.id] = fact

    def get_facts(self, memory_id: str) -> List[Fact]:
        facts = [f for f in self._facts.values() if f.memory_id == memory_id]
        facts.sort(key=lambda f: f.extracted_at)
        return facts

    def get_active_facts(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        limit: int = 50,
    ) -> List[Fact]:
        facts = [f for f in self._facts.values() if f.invalidated_by is None]
        if subject is not None:
            norm_subject = subject.strip().lower()
            facts = [f for f in facts if f.subject == norm_subject]
        if predicate is not None:
            norm_predicate = predicate.strip().lower()
            facts = [f for f in facts if f.predicate == norm_predicate]
        facts.sort(key=lambda f: f.extracted_at, reverse=True)
        return facts[:limit]

    def invalidate_fact(self, fact_id: str, invalidated_by: str) -> None:
        fact = self._facts.get(fact_id)
        if fact is not None and fact.invalidated_by is None:
            fact.invalidated_by = invalidated_by
            fact.invalidated_at = datetime.now(timezone.utc).isoformat()

    def save_conflict(self, entry: ConflictEntry) -> None:
        self._conflict_log.append(entry)

    def list_conflicts(
        self,
        resolution: Optional[str] = None,
        limit: int = 20,
    ) -> List[ConflictEntry]:
        entries = list(self._conflict_log)
        if resolution is not None:
            entries = [e for e in entries if e.resolution == resolution]
        entries.sort(key=lambda e: e.resolved_at, reverse=True)
        return entries[:limit]
