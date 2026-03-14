"""Integration tests: remember() with classification -> recall() with filters -> correct results."""

from __future__ import annotations

import os

import pytest

from lore.lore import Lore
from lore.store.memory import MemoryStore


@pytest.fixture
def lore_classify(tmp_path):
    """Lore instance with classification enabled (rule-based, no LLM)."""
    db_path = str(tmp_path / "test.db")
    lore = Lore(store=MemoryStore(), classify=True, redact=False)
    yield lore
    lore.close()


@pytest.fixture
def lore_no_classify(tmp_path):
    """Lore instance with classification disabled (default)."""
    db_path = str(tmp_path / "test.db")
    lore = Lore(store=MemoryStore(), classify=False, redact=False)
    yield lore
    lore.close()


# ── remember() with classification ──────────────────────────────────


class TestRememberWithClassification:
    def test_classification_stored_in_metadata(self, lore_classify):
        mid = lore_classify.remember("I prefer vim over emacs")
        mem = lore_classify.get(mid)
        assert mem is not None
        cls = mem.metadata.get("classification")
        assert cls is not None
        assert cls["intent"] in ("preference", "instruction")
        assert cls["domain"] in ("personal", "technical")
        assert cls["emotion"] in ("neutral", "confident", "uncertain", "frustrated", "excited", "curious")
        assert "confidence" in cls

    def test_classification_disabled_no_metadata(self, lore_no_classify):
        mid = lore_no_classify.remember("I prefer vim over emacs")
        mem = lore_no_classify.get(mid)
        assert mem is not None
        if mem.metadata:
            assert "classification" not in mem.metadata

    def test_technical_question_classified(self, lore_classify):
        mid = lore_classify.remember("How do I deploy to staging?")
        mem = lore_classify.get(mid)
        cls = mem.metadata["classification"]
        assert cls["intent"] == "question"
        assert cls["domain"] == "technical"

    def test_low_confidence_marker(self, lore_classify):
        # With default threshold 0.5, rule-based fallback (0.3) should trigger low_confidence
        mid = lore_classify.remember("hello world")
        mem = lore_classify.get(mid)
        cls = mem.metadata["classification"]
        assert cls.get("low_confidence") is True

    def test_high_confidence_no_marker(self, tmp_path):
        # With matching patterns, confidence is 0.6 which is above 0.3 threshold
        lore = Lore(store=MemoryStore(),

            db_path=str(tmp_path / "test_high_conf.db"),
            classify=True, redact=False,
            classification_confidence_threshold=0.3,
        )
        mid = lore.remember("How do I deploy the code?")
        mem = lore.get(mid)
        cls = mem.metadata["classification"]
        assert "low_confidence" not in cls
        lore.close()

    def test_memory_stored_successfully(self, lore_classify):
        mid = lore_classify.remember("Test content for classification")
        mem = lore_classify.get(mid)
        assert mem is not None
        assert mem.content == "Test content for classification"

    def test_existing_metadata_preserved(self, lore_classify):
        mid = lore_classify.remember(
            "Deploy the service",
            metadata={"custom_key": "custom_value"},
        )
        mem = lore_classify.get(mid)
        assert mem.metadata["custom_key"] == "custom_value"
        assert "classification" in mem.metadata


# ── recall() with classification filters ────────────────────────────


class TestRecallWithFilters:
    def _populate(self, lore):
        lore.remember("How do I deploy to staging?")         # question, technical
        lore.remember("I prefer vim over emacs")             # preference, personal
        lore.remember("Revenue grew 20% this quarter")       # statement, business
        lore.remember("This keeps breaking every time")      # statement, ?, frustrated
        lore.remember("We decided to use Postgres")          # decision, technical

    def test_filter_by_intent(self, lore_classify):
        self._populate(lore_classify)
        results = lore_classify.recall("deploy", intent="question")
        assert len(results) >= 1
        for r in results:
            cls = r.memory.metadata["classification"]
            assert cls["intent"] == "question"

    def test_filter_by_domain(self, lore_classify):
        self._populate(lore_classify)
        results = lore_classify.recall("business", domain="business")
        for r in results:
            cls = r.memory.metadata["classification"]
            assert cls["domain"] == "business"

    def test_filter_by_emotion(self, lore_classify):
        self._populate(lore_classify)
        results = lore_classify.recall("breaking", emotion="frustrated")
        for r in results:
            cls = r.memory.metadata["classification"]
            assert cls["emotion"] == "frustrated"

    def test_multiple_filters(self, lore_classify):
        self._populate(lore_classify)
        results = lore_classify.recall("deploy", intent="question", domain="technical")
        for r in results:
            cls = r.memory.metadata["classification"]
            assert cls["intent"] == "question"
            assert cls["domain"] == "technical"

    def test_no_filters_returns_all(self, lore_classify):
        self._populate(lore_classify)
        results = lore_classify.recall("deploy")
        assert len(results) >= 1  # backward compatible

    def test_unclassified_excluded_with_filter(self, tmp_path):
        db_path = str(tmp_path / "mixed.db")
        # Store one without classification
        lore_off = Lore(store=MemoryStore(), classify=False, redact=False)
        lore_off.remember("I prefer dark mode")
        lore_off.close()
        # Store one with classification
        lore_on = Lore(store=MemoryStore(), classify=True, redact=False)
        lore_on.remember("I prefer light mode")
        # Filter — unclassified should be excluded
        results = lore_on.recall("prefer", intent="preference")
        for r in results:
            assert r.memory.metadata is not None
            assert "classification" in r.memory.metadata
        lore_on.close()

    def test_nonmatching_filter_excludes(self, lore_classify):
        lore_classify.remember("I prefer vim over emacs")
        results = lore_classify.recall("vim", intent="question")
        # The vim memory is a preference, not a question
        for r in results:
            cls = r.memory.metadata["classification"]
            assert cls["intent"] == "question"


# ── list_memories with classification filters ───────────────────────


class TestListMemoriesWithFilters:
    def test_filter_by_intent(self, lore_classify):
        lore_classify.remember("How do I deploy?")
        lore_classify.remember("The build is broken")
        results = lore_classify.list_memories(intent="question")
        assert len(results) >= 1
        for m in results:
            assert m.metadata["classification"]["intent"] == "question"

    def test_filter_by_domain(self, lore_classify):
        lore_classify.remember("Deploy the code")
        lore_classify.remember("I went for a walk")
        results = lore_classify.list_memories(domain="technical")
        for m in results:
            assert m.metadata["classification"]["domain"] == "technical"


# ── Lore.classify() standalone method ───────────────────────────────


class TestStandaloneClassify:
    def test_classify_without_storing(self, lore_no_classify):
        result = lore_no_classify.classify("Why does this break?")
        assert result.intent == "question"
        assert result.domain is not None
        assert result.emotion is not None

    def test_classify_works_even_when_disabled(self, lore_no_classify):
        result = lore_no_classify.classify("I prefer dark mode")
        assert result.intent in ("preference", "instruction")

    def test_classify_with_enabled(self, lore_classify):
        result = lore_classify.classify("Revenue grew 20%")
        assert result.domain == "business"


# ── Environment variable configuration ──────────────────────────────


class TestEnvConfiguration:
    def test_lore_classify_env(self, tmp_path):
        os.environ["LORE_CLASSIFY"] = "true"
        try:
            lore = Lore(store=MemoryStore(), redact=False)
            assert lore._classifier is not None
            lore.close()
        finally:
            del os.environ["LORE_CLASSIFY"]

    def test_lore_classify_env_false(self, tmp_path):
        os.environ["LORE_CLASSIFY"] = "false"
        try:
            lore = Lore(store=MemoryStore(), redact=False)
            assert lore._classifier is None
            lore.close()
        finally:
            del os.environ["LORE_CLASSIFY"]
