"""Format functions for prompt export.

Each function takes (query, results, include_metadata) and returns a formatted string.
Budget enforcement happens upstream in PromptFormatter — these functions format ALL
provided results.
"""

from __future__ import annotations

from typing import Callable, Dict, List
from xml.sax.saxutils import escape, quoteattr

from lore.types import RecallResult

FormatFn = Callable[[str, List[RecallResult], bool], str]

# Per-entry overhead estimates (chars) for budget calculation.
_OVERHEAD_CHARS: Dict[str, int] = {
    "xml": 60,
    "markdown": 25,
    "chatml": 20,
    "raw": 2,
}

# Wrapper/envelope overhead (chars) for budget calculation.
_WRAPPER_CHARS: Dict[str, int] = {
    "xml": 80,
    "markdown": 40,
    "chatml": 60,
    "raw": 30,
}


def format_xml(
    query: str, results: List[RecallResult], include_metadata: bool
) -> str:
    if not results:
        return ""
    parts = [f"<memories query={quoteattr(query)}>"]
    for r in results:
        attrs = f"type={quoteattr(r.memory.type)} score={quoteattr(f'{r.score:.2f}')}"
        if include_metadata:
            tags_str = ",".join(r.memory.tags) if r.memory.tags else ""
            attrs += f" tags={quoteattr(tags_str)}"
            attrs += f" id={quoteattr(r.memory.id)}"
            attrs += f" created={quoteattr(r.memory.created_at)}"
        parts.append(f"<memory {attrs}>{escape(r.memory.content)}</memory>")
    parts.append("</memories>")
    return "\n".join(parts)


def format_chatml(
    query: str, results: List[RecallResult], include_metadata: bool
) -> str:
    if not results:
        return ""
    lines = ["<|im_start|>system", f"Relevant memories for: {query}", ""]
    for r in results:
        prefix = f"[{r.memory.type}, {r.score:.2f}]"
        if include_metadata:
            tags_str = ",".join(r.memory.tags) if r.memory.tags else ""
            prefix = f"[{r.memory.type}, {r.score:.2f}, tags={tags_str}, id={r.memory.id}]"
        lines.append(f"{prefix} {r.memory.content}")
    lines.append("<|im_end|>")
    return "\n".join(lines)


def format_markdown(
    query: str, results: List[RecallResult], include_metadata: bool
) -> str:
    if not results:
        return ""
    lines = [f"## Relevant Memories: {query}", ""]
    for r in results:
        label = f"[{r.memory.type}, {r.score:.2f}]"
        if include_metadata:
            tags_str = ",".join(r.memory.tags) if r.memory.tags else ""
            label = f"[{r.memory.type}, {r.score:.2f}, tags={tags_str}, id={r.memory.id}]"
        lines.append(f"- **{label}** {r.memory.content}")
    return "\n".join(lines)


def format_raw(
    query: str, results: List[RecallResult], include_metadata: bool
) -> str:
    if not results:
        return ""
    lines = [f"Relevant memories for: {query}", ""]
    for r in results:
        if include_metadata:
            tags_str = ",".join(r.memory.tags) if r.memory.tags else ""
            lines.append(
                f"[{r.memory.type}, {r.score:.2f}, tags={tags_str}, id={r.memory.id}]"
            )
        lines.append(r.memory.content)
        lines.append("")
    # Remove trailing blank line
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


FORMAT_REGISTRY: Dict[str, FormatFn] = {
    "xml": format_xml,
    "chatml": format_chatml,
    "markdown": format_markdown,
    "raw": format_raw,
}
