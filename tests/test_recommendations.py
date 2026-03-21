"""Tests for Proactive Recommendations (F9)."""

from __future__ import annotations

import pytest


class TestSignals:
    def test_entity_overlap_no_overlap(self):
        from lore.recommend.signals import entity_overlap
        score, explanation = entity_overlap(["python", "docker"], ["rust", "go"])
        assert score == 0.0

    def test_entity_overlap_full_overlap(self):
        from lore.recommend.signals import entity_overlap
        score, explanation = entity_overlap(["python"], ["Python", "Docker"])
        assert score == 1.0

    def test_entity_overlap_partial(self):
        from lore.recommend.signals import entity_overlap
        score, explanation = entity_overlap(
            ["python", "docker", "postgres"],
            ["python", "redis"],
        )
        assert 0.0 < score < 1.0

    def test_entity_overlap_empty_inputs(self):
        from lore.recommend.signals import entity_overlap
        score, _ = entity_overlap([], ["python"])
        assert score == 0.0
        score, _ = entity_overlap(["python"], [])
        assert score == 0.0

    def test_temporal_pattern_same_hour(self):
        from datetime import datetime, timezone

        from lore.recommend.signals import temporal_pattern
        now = datetime.now(timezone.utc)
        score, _ = temporal_pattern(now.isoformat(), current_hour=now.hour)
        assert score > 0.0

    def test_temporal_pattern_opposite_hour(self):
        from lore.recommend.signals import temporal_pattern
        score, _ = temporal_pattern("2024-01-01T03:00:00+00:00", current_hour=15)
        assert score == 0.0

    def test_access_pattern_never_accessed(self):
        from lore.recommend.signals import access_pattern
        score, _ = access_pattern(0, None)
        assert score == 0.0

    def test_access_pattern_high_count(self):
        from lore.recommend.signals import access_pattern
        score, explanation = access_pattern(50, "2024-01-01T00:00:00")
        assert score > 0.0
        assert "50" in explanation


class TestExplainer:
    def test_explain_with_signals(self):
        from lore.recommend.explainer import explain
        from lore.recommend.types import RecommendationSignal

        signals = [
            RecommendationSignal("context", 0.9, 0.4, "High context match"),
            RecommendationSignal("entity", 0.5, 0.25, "Shared entities: docker"),
            RecommendationSignal("temporal", 0.1, 0.1, "Similar time"),
        ]
        result = explain(signals)
        assert "Suggested because:" in result
        assert "High context match" in result

    def test_explain_empty_signals(self):
        from lore.recommend.explainer import explain
        result = explain([])
        assert "No strong signals" in result

    def test_explain_zero_scores(self):
        from lore.recommend.explainer import explain
        from lore.recommend.types import RecommendationSignal

        signals = [
            RecommendationSignal("test", 0.0, 0.5, "Nothing"),
        ]
        result = explain(signals)
        assert "Weak match" in result


class TestFeedbackRecorder:
    def test_record_positive(self):
        from lore.recommend.feedback import FeedbackRecorder
        recorder = FeedbackRecorder()
        recorder.record("mem-1", "positive", "user-1")
        adj = recorder.get_weight_adjustment("user-1", "manual")
        assert adj > 0

    def test_record_negative(self):
        from lore.recommend.feedback import FeedbackRecorder
        recorder = FeedbackRecorder()
        recorder.record("mem-1", "negative", "user-1")
        adj = recorder.get_weight_adjustment("user-1", "manual")
        assert adj < 0

    def test_invalid_feedback_raises(self):
        from lore.recommend.feedback import FeedbackRecorder
        recorder = FeedbackRecorder()
        with pytest.raises(ValueError):
            recorder.record("mem-1", "invalid", "user-1")

    def test_weight_clamping(self):
        from lore.recommend.feedback import FeedbackRecorder
        recorder = FeedbackRecorder()
        for _ in range(100):
            recorder.record("mem-1", "positive", "user-1")
        adj = recorder.get_weight_adjustment("user-1", "manual")
        assert adj <= 0.5


class TestRecommendationTypes:
    def test_recommendation_dataclass(self):
        from lore.recommend.types import Recommendation
        rec = Recommendation(
            memory_id="test",
            content_preview="Docker setup...",
            score=0.85,
            explanation="High relevance",
        )
        assert rec.score == 0.85
        assert rec.signals == []

    def test_signal_dataclass(self):
        from lore.recommend.types import RecommendationSignal
        sig = RecommendationSignal(
            name="context_similarity",
            score=0.9,
            weight=0.4,
            explanation="High similarity",
        )
        assert sig.score * sig.weight == pytest.approx(0.36)
