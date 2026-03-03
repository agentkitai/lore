"""Open Brain — universal AI memory layer. MCP-native. Self-hosted."""

__version__ = "0.3.0"

from openbrain.types import Memory, SearchResult, StoreStats


# Lazy imports to avoid hard dependencies for local-only users
def __getattr__(name: str):
    if name == "OpenBrain":
        from lore.lore import Lore
        return Lore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Memory", "SearchResult", "StoreStats", "OpenBrain"]
