"""PromptFormatter — formats RecallResult lists into LLM-ready prompt strings."""

from __future__ import annotations

from typing import List, Optional

from lore.prompt.templates import _OVERHEAD_CHARS, _WRAPPER_CHARS, FORMAT_REGISTRY
from lore.types import RecallResult


class PromptFormatter:
    """Formats RecallResult lists into LLM-ready prompt strings."""

    def format(
        self,
        query: str,
        results: List[RecallResult],
        *,
        format: str = "xml",
        max_tokens: Optional[int] = None,
        max_chars: Optional[int] = None,
        min_score: float = 0.0,
        include_metadata: bool = False,
    ) -> str:
        if format not in FORMAT_REGISTRY:
            valid = ", ".join(sorted(FORMAT_REGISTRY))
            raise ValueError(
                f"Unknown format {format!r}. Must be one of: {valid}"
            )

        # Filter by min_score
        filtered = [r for r in results if r.score >= min_score] if min_score > 0.0 else list(results)

        if not filtered:
            return ""

        # Budget enforcement
        included = self._apply_budget(filtered, format, include_metadata, max_tokens, max_chars)

        return FORMAT_REGISTRY[format](query, included, include_metadata)

    def _apply_budget(
        self,
        results: List[RecallResult],
        fmt: str,
        include_metadata: bool,
        max_tokens: Optional[int],
        max_chars: Optional[int],
    ) -> List[RecallResult]:
        budget = self._effective_budget(max_tokens, max_chars)
        if budget is None:
            return results

        overhead = _OVERHEAD_CHARS.get(fmt, 30)
        wrapper = _WRAPPER_CHARS.get(fmt, 40)

        included: List[RecallResult] = []
        running = wrapper
        for r in results:
            entry_cost = len(r.memory.content) + overhead
            if include_metadata:
                entry_cost += 40  # rough estimate for metadata fields
            if included and (running + entry_cost) > budget:
                break
            included.append(r)
            running += entry_cost

        return included

    @staticmethod
    def _effective_budget(
        max_tokens: Optional[int], max_chars: Optional[int]
    ) -> Optional[int]:
        token_budget = None
        if max_tokens is not None and max_tokens > 0:
            token_budget = max_tokens * 4

        char_budget = None
        if max_chars is not None and max_chars > 0:
            char_budget = max_chars

        if token_budget is not None and char_budget is not None:
            return min(token_budget, char_budget)
        return token_budget or char_budget
