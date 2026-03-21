"""Plugin base class and metadata."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class PluginMeta:
    """Plugin metadata."""
    name: str
    version: str
    description: str = ""
    lore_sdk_version: str = ">=1.0.0"
    priority: int = 100  # lower = runs first


class LorePlugin(ABC):
    """Abstract base class for Lore plugins.

    Subclass and implement any hooks you need. Unimplemented hooks
    are no-ops that pass data through unchanged.
    """

    meta: PluginMeta

    def on_remember(self, memory: Any) -> Any:
        """Called after a memory is saved. Return the (possibly modified) memory."""
        return memory

    def on_recall(self, query: str, results: List[Any]) -> List[Any]:
        """Called after recall results are scored. Return modified results."""
        return results

    def on_enrich(self, memory: Any, enrichment: Dict[str, Any]) -> Dict[str, Any]:
        """Called after enrichment. Return modified enrichment dict."""
        return enrichment

    def on_extract(self, memory: Any, facts: List[Any]) -> List[Any]:
        """Called after fact extraction. Return modified facts."""
        return facts

    def on_score(self, memory: Any, score: float) -> float:
        """Called during scoring. Return modified score."""
        return score

    def cleanup(self) -> None:
        """Called when the plugin is unloaded or the system shuts down."""
        pass
