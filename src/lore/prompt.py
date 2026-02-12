"""Prompt helper — formats lessons for system prompt injection."""

from __future__ import annotations

from typing import List

from lore.types import QueryResult

_HEADER = "## Relevant Lessons\n"


def as_prompt(lessons: List[QueryResult], max_tokens: int = 1000) -> str:
    """Format query results into a markdown string for system prompt injection.

    Args:
        lessons: List of QueryResult from lore.query().
        max_tokens: Approximate token budget (1 token ≈ 4 chars).

    Returns:
        Formatted markdown string, or empty string if no lessons.
    """
    if not lessons:
        return ""

    max_chars = max_tokens * 4

    # Sort by score descending (should already be sorted, but be safe)
    sorted_lessons = sorted(lessons, key=lambda r: r.score, reverse=True)

    parts: List[str] = [_HEADER]
    current_len = len(_HEADER)

    for result in sorted_lessons:
        lesson = result.lesson
        block = (
            f"**Problem:** {lesson.problem}\n"
            f"**Resolution:** {lesson.resolution}\n"
            f"**Confidence:** {lesson.confidence}\n"
        )
        block_len = len(block) + 1  # +1 for separator newline

        if current_len + block_len > max_chars:
            break

        parts.append(block)
        current_len += block_len

    # If no lessons fit, return empty
    if len(parts) == 1:
        return ""

    return "\n".join(parts)
