"""Tests for fact extraction, parsing, and FactExtractor."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from lore.extract.extractor import FactExtractor
from lore.store.memory import MemoryStore
from lore.types import VALID_RESOLUTIONS, Fact

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_response(facts: list) -> str:
    """Build a valid LLM JSON response string."""
    return json.dumps({"facts": facts})


def _make_extractor(
    llm_response: str | None = None,
    store: MemoryStore | None = None,
    confidence_threshold: float = 0.3,
) -> FactExtractor:
    """Build a FactExtractor with a mock LLM client."""
    store = store or MemoryStore()
    llm = MagicMock(return_value=llm_response or _make_llm_response([]))
    return FactExtractor(llm_client=llm, store=store, confidence_threshold=confidence_threshold)


# ---------------------------------------------------------------------------
# Dataclass tests (S1)
# ---------------------------------------------------------------------------

class TestFactDataclass:
    def test_fact_creation_defaults(self):
        f = Fact(id="f1", memory_id="m1", subject="user", predicate="lives_in", object="Berlin")
        assert f.confidence == 1.0
        assert f.extracted_at == ""
        assert f.invalidated_by is None
        assert f.invalidated_at is None
        assert f.metadata is None

    def test_fact_all_fields(self):
        f = Fact(
            id="f1", memory_id="m1", subject="user", predicate="lives_in",
            object="Berlin", confidence=0.9, extracted_at="2026-01-01T00:00:00",
            invalidated_by="m2", invalidated_at="2026-02-01T00:00:00",
            metadata={"model": "gpt-4"},
        )
        assert f.invalidated_by == "m2"
        assert f.metadata["model"] == "gpt-4"

    def test_valid_resolutions_constant(self):
        assert VALID_RESOLUTIONS == ("SUPERSEDE", "MERGE", "CONTRADICT", "NOOP")

    def test_conflict_entry_creation(self):
        from lore.types import ConflictEntry
        c = ConflictEntry(
            id="c1", new_memory_id="m2", old_fact_id="f1", new_fact_id="f2",
            subject="user", predicate="lives_in", old_value="NYC", new_value="Berlin",
            resolution="SUPERSEDE", resolved_at="2026-01-01T00:00:00",
        )
        assert c.resolution == "SUPERSEDE"
        assert c.metadata is None

    def test_conflict_entry_contradict_no_new_fact(self):
        from lore.types import ConflictEntry
        c = ConflictEntry(
            id="c1", new_memory_id="m2", old_fact_id="f1", new_fact_id=None,
            subject="project", predicate="database", old_value="MySQL", new_value="PostgreSQL",
            resolution="CONTRADICT", resolved_at="2026-01-01T00:00:00",
            metadata={"proposed_fact": {"object": "PostgreSQL"}},
        )
        assert c.new_fact_id is None
        assert c.metadata["proposed_fact"]["object"] == "PostgreSQL"


# ---------------------------------------------------------------------------
# FactExtractor parsing tests (S6)
# ---------------------------------------------------------------------------

class TestFactExtractorParsing:
    def test_extract_produces_facts(self):
        response = _make_llm_response([
            {"subject": "project", "predicate": "uses", "object": "PostgreSQL 16",
             "confidence": 0.95, "resolution": "NOOP", "reasoning": "new fact"},
        ])
        ext = _make_extractor(llm_response=response)
        results = ext.extract("m1", "We use PostgreSQL 16")
        assert len(results) == 1
        assert results[0].fact.subject == "project"
        assert results[0].fact.object == "PostgreSQL 16"
        assert results[0].resolution == "NOOP"

    def test_subject_normalization(self):
        response = _make_llm_response([
            {"subject": "  PostgreSQL  ", "predicate": "version", "object": "16",
             "confidence": 0.9, "resolution": "NOOP"},
        ])
        ext = _make_extractor(llm_response=response)
        results = ext.extract("m1", "PostgreSQL 16")
        assert results[0].fact.subject == "postgresql"

    def test_predicate_normalization(self):
        response = _make_llm_response([
            {"subject": "user", "predicate": "lives in", "object": "Berlin",
             "confidence": 0.9, "resolution": "NOOP"},
        ])
        ext = _make_extractor(llm_response=response)
        results = ext.extract("m1", "User lives in Berlin")
        assert results[0].fact.predicate == "lives_in"

    def test_confidence_clamping_high(self):
        response = _make_llm_response([
            {"subject": "x", "predicate": "y", "object": "z",
             "confidence": 1.5, "resolution": "NOOP"},
        ])
        ext = _make_extractor(llm_response=response)
        results = ext.extract("m1", "test")
        assert results[0].fact.confidence == 1.0

    def test_confidence_clamping_low(self):
        response = _make_llm_response([
            {"subject": "x", "predicate": "y", "object": "z",
             "confidence": -0.1, "resolution": "NOOP"},
        ])
        ext = _make_extractor(llm_response=response, confidence_threshold=0.0)
        results = ext.extract("m1", "test")
        assert results[0].fact.confidence == 0.0

    def test_confidence_threshold_filtering(self):
        response = _make_llm_response([
            {"subject": "a", "predicate": "b", "object": "c",
             "confidence": 0.2, "resolution": "NOOP"},
            {"subject": "d", "predicate": "e", "object": "f",
             "confidence": 0.5, "resolution": "NOOP"},
        ])
        ext = _make_extractor(llm_response=response, confidence_threshold=0.3)
        results = ext.extract("m1", "test")
        assert len(results) == 1
        assert results[0].fact.subject == "d"

    def test_invalid_resolution_defaults_to_noop(self):
        response = _make_llm_response([
            {"subject": "x", "predicate": "y", "object": "z",
             "confidence": 0.9, "resolution": "UNKNOWN"},
        ])
        ext = _make_extractor(llm_response=response)
        results = ext.extract("m1", "test")
        assert results[0].resolution == "NOOP"

    def test_malformed_json_returns_empty(self):
        ext = _make_extractor(llm_response="not json at all {garbage}")
        results = ext.extract("m1", "test")
        assert results == []

    def test_empty_content_returns_empty(self):
        ext = _make_extractor()
        results = ext.extract("m1", "")
        assert results == []

    def test_empty_facts_array(self):
        response = json.dumps({"facts": []})
        ext = _make_extractor(llm_response=response)
        results = ext.extract("m1", "test")
        assert results == []

    def test_json_in_markdown_code_block(self):
        inner = json.dumps({"facts": [
            {"subject": "x", "predicate": "y", "object": "z",
             "confidence": 0.9, "resolution": "NOOP"},
        ]})
        response = f"```json\n{inner}\n```"
        ext = _make_extractor(llm_response=response)
        results = ext.extract("m1", "test")
        assert len(results) == 1

    def test_extract_preview_no_store_context(self):
        response = _make_llm_response([
            {"subject": "proj", "predicate": "lang", "object": "Python",
             "confidence": 0.9, "resolution": "NOOP"},
        ])
        ext = _make_extractor(llm_response=response)
        facts = ext.extract_preview("We use Python")
        assert len(facts) == 1
        assert isinstance(facts[0], Fact)

    def test_resolution_passed_through(self):
        store = MemoryStore()
        old_fact = Fact(id="old1", memory_id="m0", subject="user", predicate="city",
                       object="NYC", confidence=0.9, extracted_at="2026-01-01")
        store.save_fact(old_fact)

        response = _make_llm_response([
            {"subject": "user", "predicate": "city", "object": "Berlin",
             "confidence": 0.9, "resolution": "SUPERSEDE", "reasoning": "moved",
             "conflicts_with": "old1"},
        ])
        ext = _make_extractor(llm_response=response, store=store)
        results = ext.extract("m1", "User moved to Berlin")
        assert results[0].resolution == "SUPERSEDE"
        assert results[0].conflicting_fact is not None
        assert results[0].conflicting_fact.id == "old1"

    def test_enrichment_context_in_prompt(self):
        response = _make_llm_response([])
        llm = MagicMock(return_value=response)
        ext = FactExtractor(llm_client=llm, store=MemoryStore())
        ext.extract("m1", "test", enrichment_context={"entities": ["PostgreSQL"]})
        # Verify the LLM was called and enrichment context is in the prompt
        assert llm.called
        prompt = llm.call_args[0][0]
        assert "PostgreSQL" in prompt

    def test_skip_facts_with_empty_subject(self):
        response = _make_llm_response([
            {"subject": "", "predicate": "y", "object": "z",
             "confidence": 0.9, "resolution": "NOOP"},
        ])
        ext = _make_extractor(llm_response=response)
        results = ext.extract("m1", "test")
        assert len(results) == 0
