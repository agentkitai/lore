"""In-memory store implementation for testing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from lore.store.base import Store
from lore.types import Memory


class MemoryStore(Store):
    """In-memory store backed by a dict. Useful for testing."""

    def __init__(self) -> None:
        self._memories: Dict[str, Memory] = {}

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
        return self._memories.pop(memory_id, None) is not None

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
