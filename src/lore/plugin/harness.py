"""Test harness for Lore plugins."""

from __future__ import annotations

from typing import Any, Dict, List

from lore.plugin.base import LorePlugin
from lore.store.memory import MemoryStore
from lore.types import Memory


class PluginTestHarness:
    """Provides a mock Lore environment for testing plugins."""

    def __init__(self, plugin: LorePlugin) -> None:
        self.plugin = plugin
        self.store = MemoryStore()
        self.memories: List[Memory] = []

    def add_test_memory(self, content: str, **kwargs) -> Memory:
        from ulid import ULID
        from datetime import datetime, timezone

        memory = Memory(
            id=str(ULID()),
            content=content,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            **kwargs,
        )
        self.store.save(memory)
        self.memories.append(memory)
        return memory

    def test_on_remember(self, memory: Memory) -> Memory:
        return self.plugin.on_remember(memory)

    def test_on_recall(self, query: str, results: list) -> list:
        return self.plugin.on_recall(query, results)

    def test_on_score(self, memory: Memory, score: float) -> float:
        return self.plugin.on_score(memory, score)

    def run_all_hooks(self) -> Dict[str, Any]:
        """Run all hooks with test data and return results."""
        results: Dict[str, Any] = {}

        if self.memories:
            mem = self.memories[0]
            results["on_remember"] = self.test_on_remember(mem)
            results["on_recall"] = self.test_on_recall("test query", [mem])
            results["on_score"] = self.test_on_score(mem, 0.85)

        return results
