"""Lore — universal AI memory layer. MCP-native. Self-hosted."""

__version__ = "0.4.0"

from lore.lore import Lore
from lore.types import Memory, SearchResult, StoreStats

__all__ = [
    "Lore",
    "Memory",
    "SearchResult",
    "StoreStats",
]
