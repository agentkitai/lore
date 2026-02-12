"""Tests for Story 6: Prompt Helper."""

from __future__ import annotations

from typing import List

import numpy as np
import pytest

from lore import Lore, QueryResult, as_prompt
from lore.store.memory import MemoryStore
from lore.types import Lesson

_DIM = 384


def _fake_embed(text: str) -> List[float]:
    rng = np.random.RandomState(abs(hash(text)) % (2**31))
    vec = rng.randn(_DIM).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _make_lore() -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_fake_embed)


def _make_result(problem: str, resolution: str, confidence: float, score: float) -> QueryResult:
    return QueryResult(
        lesson=Lesson(
            id="test",
            problem=problem,
            resolution=resolution,
            confidence=confidence,
        ),
        score=score,
    )


class TestAsPrompt:
    def test_empty_lessons_returns_empty(self) -> None:
        assert as_prompt([]) == ""

    def test_single_lesson_formatted(self) -> None:
        results = [_make_result("p1", "r1", 0.9, 0.8)]
        text = as_prompt(results)
        assert "## Relevant Lessons" in text
        assert "**Problem:** p1" in text
        assert "**Resolution:** r1" in text
        assert "**Confidence:** 0.9" in text

    def test_ordered_by_score(self) -> None:
        results = [
            _make_result("low", "r", 0.5, 0.3),
            _make_result("high", "r", 0.9, 0.9),
        ]
        text = as_prompt(results)
        high_pos = text.index("high")
        low_pos = text.index("low")
        assert high_pos < low_pos

    def test_max_tokens_truncation(self) -> None:
        results = [
            _make_result(f"problem {i}", f"resolution {i}", 0.5, 1.0 - i * 0.1)
            for i in range(20)
        ]
        text = as_prompt(results, max_tokens=50)  # ~200 chars
        # Should include only complete lessons that fit
        assert len(text) <= 200 + 100  # some slack for header
        # No partial lessons — each lesson present should have all 3 fields
        if "**Problem:**" in text:
            count_p = text.count("**Problem:**")
            count_r = text.count("**Resolution:**")
            count_c = text.count("**Confidence:**")
            assert count_p == count_r == count_c

    def test_max_tokens_no_lessons_fit(self) -> None:
        results = [_make_result("a" * 200, "b" * 200, 0.5, 0.9)]
        text = as_prompt(results, max_tokens=10)  # ~40 chars
        assert text == ""

    def test_lore_as_prompt_method(self) -> None:
        lore = _make_lore()
        lore.publish(problem="test problem", resolution="test resolution")
        results = lore.query("test")
        text = lore.as_prompt(results)
        assert "## Relevant Lessons" in text

    def test_clean_markdown(self) -> None:
        results = [_make_result("p1", "r1", 0.9, 0.8)]
        text = as_prompt(results)
        # Should be valid markdown without weird artifacts
        lines = text.strip().split("\n")
        assert len(lines) >= 4  # header + 3 fields minimum
