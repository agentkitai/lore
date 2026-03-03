"""Lore SDK — cross-agent memory library.

DEPRECATED: This package has been renamed to 'openbrain'.
Use `from openbrain import OpenBrain` instead of `from lore import Lore`.
The 'lore' import path will continue to work for backward compatibility.
"""

import inspect as _inspect
import warnings as _warnings

from lore.exceptions import LessonNotFoundError
from lore.lore import Lore
from lore.prompt import as_prompt
from lore.types import Lesson, QueryResult

# Only warn external callers, not internal lore/openbrain/test imports
_caller = _inspect.stack()
if len(_caller) >= 2:
    _caller_file = _caller[1].filename
    _is_internal = any(p in _caller_file for p in ("/lore/", "/openbrain/", "/tests/"))
    if not _is_internal:
        _warnings.warn(
            "The 'lore' package has been renamed to 'openbrain'. "
            "Use `from openbrain import OpenBrain` instead of `from lore import Lore`. "
            "The 'lore' import path will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
del _caller


# Lazy import to avoid hard httpx dependency for local-only users
def __getattr__(name: str):
    if name == "LoreClient":
        from lore.client import LoreClient
        return LoreClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Lore", "LoreClient", "Lesson", "QueryResult", "LessonNotFoundError", "as_prompt"]
