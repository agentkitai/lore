"""Tests for individual recommendation signal extractors (F9)."""

from __future__ import annotations

import struct

import numpy as np
import pytest


class TestContextSimilarity:
    def _make_embedding(self, vec):
        return struct.pack(f"{len(vec)}f", *vec)

    def test_identical_vectors(self):
        from lore.recommend.signals import context_similarity
        vec = [1.0, 0.0, 0.0]
        emb = self._make_embedding(vec)
        score, _ = context_similarity(vec, emb)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_orthogonal_vectors(self):
        from lore.recommend.signals import context_similarity
        vec1 = [1.0, 0.0, 0.0]
        vec2 = [0.0, 1.0, 0.0]
        emb = self._make_embedding(vec2)
        score, _ = context_similarity(vec1, emb)
        assert score == pytest.approx(0.0, abs=0.01)

    def test_zero_vector_returns_zero(self):
        from lore.recommend.signals import context_similarity
        vec = [0.0, 0.0, 0.0]
        emb = self._make_embedding(vec)
        score, _ = context_similarity([1.0, 0.0, 0.0], emb)
        assert score == 0.0


class TestEntityOverlap:
    def test_case_insensitive(self):
        from lore.recommend.signals import entity_overlap
        score, _ = entity_overlap(["Python"], ["python"])
        assert score == 1.0

    def test_partial_overlap(self):
        from lore.recommend.signals import entity_overlap
        score, explanation = entity_overlap(
            ["python", "docker"],
            ["python", "rust"],
        )
        assert score == 0.5
        assert "python" in explanation.lower()


class TestTemporalPattern:
    def test_same_hour(self):
        from lore.recommend.signals import temporal_pattern
        score, _ = temporal_pattern("2024-06-15T14:00:00+00:00", current_hour=14)
        assert score > 0.0

    def test_midnight_vs_noon(self):
        from lore.recommend.signals import temporal_pattern
        score, _ = temporal_pattern("2024-06-15T00:00:00+00:00", current_hour=12)
        assert score == 0.0

    def test_invalid_timestamp(self):
        from lore.recommend.signals import temporal_pattern
        score, _ = temporal_pattern("invalid", current_hour=12)
        assert score == 0.0


class TestAccessPattern:
    def test_zero_access(self):
        from lore.recommend.signals import access_pattern
        score, _ = access_pattern(0, None)
        assert score == 0.0

    def test_moderate_access(self):
        from lore.recommend.signals import access_pattern
        score, _ = access_pattern(10, "2024-01-01T00:00:00")
        assert 0.0 < score <= 0.5

    def test_high_access_capped(self):
        from lore.recommend.signals import access_pattern
        score, _ = access_pattern(10000, "2024-01-01T00:00:00")
        assert score <= 0.5
