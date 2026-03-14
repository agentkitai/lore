"""Tests for E2 S10: LLM Summary Enhancement."""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from lore.lore import Lore
from lore.store.memory import MemoryStore
from lore.types import Memory


def _stub_embed(text: str) -> List[float]:
    return [0.1] * 384


def _make_memory(
    id: str,
    content: str = "test content",
    project: str | None = "lore",
    hours_ago: float = 1,
) -> Memory:
    created = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return Memory(
        id=id,
        content=content,
        project=project,
        created_at=created,
        updated_at=created,
        embedding=struct.pack("384f", *([0.1] * 384)),
    )


def _lore_with_enrichment(memories, llm_response="- Key point A\n- Key point B"):
    """Create a Lore instance with a mock enrichment pipeline."""
    store = MemoryStore()
    for m in memories:
        store.save(m)

    lore = Lore(embedding_fn=_stub_embed)
    lore._store = store

    # Mock the enrichment pipeline
    mock_pipeline = MagicMock()
    mock_pipeline._llm.complete.return_value = llm_response
    lore._enrichment_pipeline = mock_pipeline

    return lore


class TestLLMSummary:
    def test_summary_enabled(self):
        mems = [_make_memory("m1", "Decision: use FastMCP")]
        lore = _lore_with_enrichment(mems)
        result = lore.recent_activity(format="brief")
        assert result.has_llm_summary is True
        assert result.groups[0].summary is not None
        assert "Key point" in result.groups[0].summary

    def test_summary_disabled_without_enrichment(self):
        store = MemoryStore()
        store.save(_make_memory("m1", "test"))
        lore = Lore(embedding_fn=_stub_embed)
        lore._store = store

        result = lore.recent_activity(format="brief")
        assert result.has_llm_summary is False
        assert result.groups[0].summary is None

    def test_structured_skips_llm(self):
        mems = [_make_memory("m1", "test")]
        lore = _lore_with_enrichment(mems)
        result = lore.recent_activity(format="structured")
        assert result.has_llm_summary is False
        # LLM should not have been called
        lore._enrichment_pipeline._llm.complete.assert_not_called()

    def test_llm_failure_fallback(self):
        mems = [_make_memory("m1", "test")]
        lore = _lore_with_enrichment(mems)
        lore._enrichment_pipeline._llm.complete.side_effect = RuntimeError("LLM down")

        result = lore.recent_activity(format="brief")
        assert result.has_llm_summary is False
        assert result.groups[0].summary is None
        assert result.total_count == 1  # Still returns data

    def test_llm_timeout_fallback(self):
        mems = [_make_memory("m1", "test")]
        lore = _lore_with_enrichment(mems)
        lore._enrichment_pipeline._llm.complete.side_effect = TimeoutError("timeout")

        result = lore.recent_activity(format="brief")
        assert result.has_llm_summary is False

    def test_content_truncated_for_llm(self):
        # Create a memory with very long content
        long_content = "x" * 3000
        mems = [_make_memory("m1", long_content)]
        lore = _lore_with_enrichment(mems)

        lore.recent_activity(format="brief")
        call_args = lore._enrichment_pipeline._llm.complete.call_args[0][0]
        # The prompt should contain truncated content (2000 char cap)
        # The memory line itself won't exceed the cap
        assert len(call_args) < 3500  # Prompt + instructions < original content

    def test_has_llm_summary_flag_set(self):
        mems = [
            _make_memory("m1", "Memory A", project="lore"),
            _make_memory("m2", "Memory B", project="app"),
        ]
        lore = _lore_with_enrichment(mems)
        result = lore.recent_activity(format="brief")
        assert result.has_llm_summary is True
