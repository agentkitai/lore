"""Integration tests for enrichment in remember/recall/batch flows."""

from __future__ import annotations

import json
import struct
from unittest.mock import MagicMock, patch

import pytest

from lore.store.memory import MemoryStore
from lore.types import Memory

MOCK_ENRICHMENT_RESPONSE = json.dumps({
    "topics": ["deployment", "kubernetes"],
    "sentiment": {"label": "negative", "score": -0.5},
    "entities": [{"name": "Kubernetes", "type": "tool"}],
    "categories": ["infrastructure"],
})


def _stub_embed(text):
    """Deterministic embedding for tests."""
    return [0.1] * 384


def _make_lore(enrichment=False, store=None, **kwargs):
    """Create a Lore instance with mocked embedding and optional enrichment."""
    from lore import Lore
    return Lore(

        store=store or MemoryStore(),
        embedding_fn=_stub_embed,
        redact=False,
        enrichment=enrichment,
        **kwargs,
    )


def _make_enriched_memory(
    id, content, topics=None, sentiment_label="neutral", sentiment_score=0.0,
    entities=None, categories=None,
):
    """Create a Memory with enrichment metadata."""
    return Memory(
        id=id,
        content=content,
        embedding=struct.pack("384f", *([0.1] * 384)),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        metadata={
            "enrichment": {
                "topics": topics or [],
                "sentiment": {"label": sentiment_label, "score": sentiment_score},
                "entities": entities or [],
                "categories": categories or [],
                "enriched_at": "2026-01-01T00:00:00+00:00",
                "enrichment_model": "gpt-4o-mini",
            }
        },
    )


def _make_unenriched_memory(id, content):
    """Create a Memory without enrichment metadata."""
    return Memory(
        id=id,
        content=content,
        embedding=struct.pack("384f", *([0.1] * 384)),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------
# Story 4: remember() integration
# ---------------------------------------------------------------

class TestRememberWithEnrichment:
    @patch("lore.enrichment.llm.litellm", create=True)
    def test_remember_with_enrichment_success(self, mock_litellm):
        """Enrichment data is stored in metadata."""
        import sys
        sys.modules["litellm"] = mock_litellm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = MOCK_ENRICHMENT_RESPONSE
        mock_litellm.completion.return_value = mock_response

        store = MemoryStore()
        lore = _make_lore(enrichment=True, store=store, enrichment_model="gpt-4o-mini")

        # Need to mock check_api_key for the pipeline's LLM client
        lore._enrichment_pipeline.llm._warned_no_key = False
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            mid = lore.remember("The K8s deployment failed on AWS")

        mem = store.get(mid)
        assert mem is not None
        assert "enrichment" in mem.metadata
        assert mem.metadata["enrichment"]["topics"] == ["deployment", "kubernetes"]
        del sys.modules["litellm"]

    @patch("lore.enrichment.llm.litellm", create=True)
    def test_remember_enrichment_failure_still_saves(self, mock_litellm):
        """Memory is saved even when enrichment fails."""
        import sys
        sys.modules["litellm"] = mock_litellm
        mock_litellm.completion.side_effect = Exception("API error")

        store = MemoryStore()
        lore = _make_lore(enrichment=True, store=store)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            mid = lore.remember("some content")

        mem = store.get(mid)
        assert mem is not None
        # No enrichment key since it failed
        assert mem.metadata is None or "enrichment" not in (mem.metadata or {})
        del sys.modules["litellm"]

    def test_remember_enrichment_disabled(self):
        """No LLM call when enrichment is disabled."""
        store = MemoryStore()
        lore = _make_lore(enrichment=False, store=store)

        mid = lore.remember("some content")
        mem = store.get(mid)
        assert mem is not None
        assert mem.metadata is None or "enrichment" not in (mem.metadata or {})

    @patch("lore.enrichment.llm.litellm", create=True)
    def test_remember_enrichment_no_api_key(self, mock_litellm):
        """Enrichment skipped gracefully when no API key."""
        import sys
        sys.modules["litellm"] = mock_litellm

        store = MemoryStore()
        lore = _make_lore(enrichment=True, store=store)

        # No API key set
        with patch.dict("os.environ", {}, clear=True):
            mid = lore.remember("some content")

        mem = store.get(mid)
        assert mem is not None
        # Should be saved without enrichment (RuntimeError caught)
        del sys.modules["litellm"]

    @patch("lore.enrichment.llm.litellm", create=True)
    def test_remember_preserves_user_metadata(self, mock_litellm):
        """User-supplied metadata is preserved alongside enrichment."""
        import sys
        sys.modules["litellm"] = mock_litellm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = MOCK_ENRICHMENT_RESPONSE
        mock_litellm.completion.return_value = mock_response

        store = MemoryStore()
        lore = _make_lore(enrichment=True, store=store)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            mid = lore.remember("content", metadata={"custom": "value"})

        mem = store.get(mid)
        assert mem.metadata["custom"] == "value"
        assert "enrichment" in mem.metadata
        del sys.modules["litellm"]

    def test_env_var_enables_enrichment(self, monkeypatch):
        """LORE_ENRICHMENT_ENABLED env var enables enrichment."""
        monkeypatch.setenv("LORE_ENRICHMENT_ENABLED", "true")
        # This will try to import litellm, so mock it
        import sys
        mock_litellm = MagicMock()
        sys.modules["litellm"] = mock_litellm

        store = MemoryStore()
        lore = _make_lore(enrichment=False, store=store)  # False in code, but env overrides

        assert lore._enrichment_pipeline is not None
        del sys.modules["litellm"]

    def test_env_var_model_override(self, monkeypatch):
        """LORE_ENRICHMENT_MODEL env var overrides model."""
        monkeypatch.setenv("LORE_ENRICHMENT_MODEL", "claude-3-haiku")
        import sys
        mock_litellm = MagicMock()
        sys.modules["litellm"] = mock_litellm

        store = MemoryStore()
        lore = _make_lore(enrichment=True, store=store)

        assert lore._enrichment_pipeline.llm.model == "claude-3-haiku"
        del sys.modules["litellm"]


# ---------------------------------------------------------------
# Story 5: recall() filtering
# ---------------------------------------------------------------

class TestRecallEnrichmentFilters:
    def _setup_store_with_memories(self):
        store = MemoryStore()
        # Memory 1: kubernetes deployment, negative
        store.save(_make_enriched_memory(
            "m1", "K8s deployment failed",
            topics=["kubernetes", "deployment"],
            sentiment_label="negative", sentiment_score=-0.7,
            entities=[{"name": "Kubernetes", "type": "tool"}, {"name": "AWS", "type": "platform"}],
            categories=["infrastructure", "incident"],
        ))
        # Memory 2: python testing, positive
        store.save(_make_enriched_memory(
            "m2", "Python unit tests are great",
            topics=["python", "testing"],
            sentiment_label="positive", sentiment_score=0.8,
            entities=[{"name": "pytest", "type": "tool"}],
            categories=["testing"],
        ))
        # Memory 3: unenriched
        store.save(_make_unenriched_memory("m3", "Some unenriched memory"))
        return store

    def test_filter_by_topic(self):
        store = self._setup_store_with_memories()
        lore = _make_lore(store=store)
        results = lore.recall("test", topic="kubernetes")
        assert all(
            "kubernetes" in (r.memory.metadata or {}).get("enrichment", {}).get("topics", [])
            for r in results
        )
        ids = [r.memory.id for r in results]
        assert "m1" in ids
        assert "m2" not in ids

    def test_filter_by_sentiment(self):
        store = self._setup_store_with_memories()
        lore = _make_lore(store=store)
        results = lore.recall("test", sentiment="positive")
        ids = [r.memory.id for r in results]
        assert "m2" in ids
        assert "m1" not in ids

    def test_filter_by_entity(self):
        store = self._setup_store_with_memories()
        lore = _make_lore(store=store)
        results = lore.recall("test", entity="AWS")
        ids = [r.memory.id for r in results]
        assert "m1" in ids
        assert "m2" not in ids

    def test_filter_by_category(self):
        store = self._setup_store_with_memories()
        lore = _make_lore(store=store)
        results = lore.recall("test", category="testing")
        ids = [r.memory.id for r in results]
        assert "m2" in ids
        assert "m1" not in ids

    def test_filter_excludes_unenriched(self):
        store = self._setup_store_with_memories()
        lore = _make_lore(store=store)
        results = lore.recall("test", topic="kubernetes")
        ids = [r.memory.id for r in results]
        assert "m3" not in ids

    def test_no_filter_includes_unenriched(self):
        store = self._setup_store_with_memories()
        lore = _make_lore(store=store)
        results = lore.recall("test", limit=10)
        ids = [r.memory.id for r in results]
        assert "m3" in ids  # Unenriched included when no filters

    def test_filter_case_insensitive(self):
        store = self._setup_store_with_memories()
        lore = _make_lore(store=store)
        results = lore.recall("test", topic="Kubernetes")  # uppercase
        ids = [r.memory.id for r in results]
        assert "m1" in ids

    def test_multiple_filters(self):
        store = self._setup_store_with_memories()
        lore = _make_lore(store=store)
        results = lore.recall("test", topic="kubernetes", sentiment="negative")
        ids = [r.memory.id for r in results]
        assert "m1" in ids
        assert "m2" not in ids

    def test_entity_case_insensitive(self):
        store = self._setup_store_with_memories()
        lore = _make_lore(store=store)
        results = lore.recall("test", entity="aws")  # lowercase
        ids = [r.memory.id for r in results]
        assert "m1" in ids


# ---------------------------------------------------------------
# Story 6: batch enrichment
# ---------------------------------------------------------------

class TestBatchEnrichment:
    @patch("lore.enrichment.llm.litellm", create=True)
    def test_enrich_all_unenriched(self, mock_litellm):
        import sys
        sys.modules["litellm"] = mock_litellm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = MOCK_ENRICHMENT_RESPONSE
        mock_litellm.completion.return_value = mock_response

        store = MemoryStore()
        for i in range(5):
            store.save(_make_unenriched_memory(f"m{i}", f"content {i}"))

        lore = _make_lore(enrichment=True, store=store)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = lore.enrich_memories()

        assert result["enriched"] == 5
        assert result["skipped"] == 0
        assert result["failed"] == 0
        del sys.modules["litellm"]

    @patch("lore.enrichment.llm.litellm", create=True)
    def test_skip_already_enriched(self, mock_litellm):
        import sys
        sys.modules["litellm"] = mock_litellm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = MOCK_ENRICHMENT_RESPONSE
        mock_litellm.completion.return_value = mock_response

        store = MemoryStore()
        store.save(_make_enriched_memory("m1", "enriched", topics=["test"]))
        store.save(_make_enriched_memory("m2", "enriched", topics=["test"]))
        store.save(_make_unenriched_memory("m3", "unenriched"))
        store.save(_make_unenriched_memory("m4", "unenriched"))

        lore = _make_lore(enrichment=True, store=store)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = lore.enrich_memories(force=False)

        assert result["skipped"] == 2
        assert result["enriched"] == 2
        del sys.modules["litellm"]

    @patch("lore.enrichment.llm.litellm", create=True)
    def test_force_re_enriches(self, mock_litellm):
        import sys
        sys.modules["litellm"] = mock_litellm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = MOCK_ENRICHMENT_RESPONSE
        mock_litellm.completion.return_value = mock_response

        store = MemoryStore()
        store.save(_make_enriched_memory("m1", "enriched", topics=["old"]))
        store.save(_make_enriched_memory("m2", "enriched", topics=["old"]))
        store.save(_make_enriched_memory("m3", "enriched", topics=["old"]))

        lore = _make_lore(enrichment=True, store=store)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = lore.enrich_memories(force=True)

        assert result["enriched"] == 3
        assert result["skipped"] == 0
        del sys.modules["litellm"]

    @patch("lore.enrichment.llm.litellm", create=True)
    def test_enrich_by_ids(self, mock_litellm):
        import sys
        sys.modules["litellm"] = mock_litellm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = MOCK_ENRICHMENT_RESPONSE
        mock_litellm.completion.return_value = mock_response

        store = MemoryStore()
        store.save(_make_unenriched_memory("m1", "content 1"))
        store.save(_make_unenriched_memory("m2", "content 2"))
        store.save(_make_unenriched_memory("m3", "content 3"))

        lore = _make_lore(enrichment=True, store=store)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = lore.enrich_memories(memory_ids=["m1", "m3"])

        assert result["enriched"] == 2
        # m2 not touched
        m2 = store.get("m2")
        assert m2.metadata is None or "enrichment" not in (m2.metadata or {})
        del sys.modules["litellm"]

    @patch("lore.enrichment.llm.litellm", create=True)
    def test_partial_failure(self, mock_litellm):
        import sys
        sys.modules["litellm"] = mock_litellm

        call_count = 0
        def mock_complete(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise Exception("LLM error on memory 3")
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = MOCK_ENRICHMENT_RESPONSE
            return mock_resp

        mock_litellm.completion.side_effect = mock_complete

        store = MemoryStore()
        for i in range(5):
            store.save(_make_unenriched_memory(f"m{i}", f"content {i}"))

        lore = _make_lore(enrichment=True, store=store)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = lore.enrich_memories()

        assert result["enriched"] == 4
        assert result["failed"] == 1
        assert len(result["errors"]) == 1
        assert "m2" in result["errors"][0]  # m2 is the 3rd memory (0-indexed)
        del sys.modules["litellm"]

    def test_not_enabled_raises(self):
        store = MemoryStore()
        lore = _make_lore(enrichment=False, store=store)
        with pytest.raises(RuntimeError, match="Enrichment not enabled"):
            lore.enrich_memories()

    @patch("lore.enrichment.llm.litellm", create=True)
    def test_enrich_by_project(self, mock_litellm):
        import sys
        sys.modules["litellm"] = mock_litellm
        mock_response = MagicMock()
        mock_response.choices[0].message.content = MOCK_ENRICHMENT_RESPONSE
        mock_litellm.completion.return_value = mock_response

        store = MemoryStore()
        m1 = _make_unenriched_memory("m1", "proj A content")
        m1.project = "projA"
        m2 = _make_unenriched_memory("m2", "proj B content")
        m2.project = "projB"
        store.save(m1)
        store.save(m2)

        lore = _make_lore(enrichment=True, store=store)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            result = lore.enrich_memories(project="projA")

        assert result["enriched"] == 1
        del sys.modules["litellm"]
