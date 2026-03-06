"""Adapter registry — maps adapter names to classes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .raw import RawAdapter

if TYPE_CHECKING:
    from .base import SourceAdapter

# Registry: adapter_name -> class
ADAPTERS: dict = {
    "raw": RawAdapter,
}

# Lazy imports for adapters with optional dependencies
_LAZY_ADAPTERS = {
    "slack": (".slack", "SlackAdapter"),
    "telegram": (".telegram", "TelegramAdapter"),
    "git": (".git", "GitAdapter"),
}


def get_adapter(name: str, **kwargs: object) -> "SourceAdapter":
    """Look up adapter by name. Raises ValueError for unknown adapters."""
    cls = ADAPTERS.get(name)
    if cls is None:
        lazy = _LAZY_ADAPTERS.get(name)
        if lazy is not None:
            import importlib
            mod = importlib.import_module(lazy[0], package=__package__)
            cls = getattr(mod, lazy[1])
            ADAPTERS[name] = cls  # cache for next call
    if cls is None:
        raise ValueError(f"Unknown source adapter: {name}")
    return cls(**kwargs)
