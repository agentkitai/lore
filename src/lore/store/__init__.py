"""Storage backends for Lore SDK."""

from lore.store.base import Store
from lore.store.memory import MemoryStore
from lore.store.sqlite import SqliteStore

__all__ = ["Store", "MemoryStore", "SqliteStore", "HttpStore"]


def __getattr__(name: str):
    if name == "HttpStore":
        from lore.store.http import HttpStore
        return HttpStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
