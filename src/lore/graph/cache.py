"""Entity cache for fast query matching."""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from lore.store.base import Store
from lore.types import Entity


class EntityCache:
    """In-memory cache of entity names for fast query matching."""

    def __init__(self, store: Store, ttl_seconds: int = 300) -> None:
        self.store = store
        self.ttl = ttl_seconds
        self._cache: Optional[List[Entity]] = None
        self._cached_at: float = 0

    def get_all(self) -> List[Entity]:
        now = time.time()
        if self._cache is None or (now - self._cached_at) > self.ttl:
            self._cache = self.store.list_entities()
            self._cached_at = now
        return self._cache

    def invalidate(self) -> None:
        self._cache = None


class TopicSummaryCache:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl = ttl_seconds
        self._cache: Dict[str, Tuple[str, str, float]] = {}

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


def find_query_entities(query: str, cache: EntityCache) -> List[Entity]:
    """Find entities mentioned in a recall query via substring matching."""
    query_lower = query.lower()
    all_entities = cache.get_all()

    matches = []
    for entity in all_entities:
        if entity.name in query_lower:
            matches.append(entity)
            continue
        for alias in entity.aliases:
            if alias in query_lower:
                matches.append(entity)
                break

    return matches
