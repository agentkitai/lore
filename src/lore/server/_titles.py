"""Title generation for compact-result rendering (Phase 6D).

Pure, deterministic function over ``(content, meta)``: same input → same
title, no time dependence, no LLM. Lives outside ``routes/`` and
``services/`` so the MCP tool can import it without dragging the FastAPI
graph along.

Title precedence:

1. ``meta["title"]`` — Phase 6B observations carry a curated title.
2. First non-blank line of ``content``, truncated to 80 chars (with
   ellipsis if truncated).
3. ``"(untitled)"`` for an empty / whitespace-only memory.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol

_MAX_TITLE_LEN = 80


class _MemoryLike(Protocol):
    """Structural type covering ``StoredMemory`` (dataclass) and route DTOs."""

    content: str
    meta: Mapping[str, Any]


def memory_title(memory: _MemoryLike) -> str:
    """Return a deterministic short title for ``memory``.

    Prefers ``meta["title"]`` (set by Phase 6B observation captures),
    falls back to the first non-blank line of ``content`` truncated to
    80 chars, and finally ``"(untitled)"`` for empty content.
    """
    meta: Optional[Mapping[str, Any]] = getattr(memory, "meta", None)
    if meta:
        raw = meta.get("title")
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped:
                return stripped[:_MAX_TITLE_LEN]

    content = getattr(memory, "content", "") or ""
    # First non-blank line.
    line = ""
    for candidate in content.splitlines():
        stripped = candidate.strip()
        if stripped:
            line = stripped
            break

    if not line:
        return "(untitled)"
    if len(line) > _MAX_TITLE_LEN:
        return line[:_MAX_TITLE_LEN] + "…"
    return line
