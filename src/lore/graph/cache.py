"""Entity cache for fast query matching."""

from __future__ import annotations

import time
from typing import List, Optional

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
