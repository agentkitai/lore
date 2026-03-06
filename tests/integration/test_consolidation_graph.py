"""Scenario 4 — Consolidation (dry_run only, no LLM)."""

from __future__ import annotations

import pytest

from lore import Lore
from lore.store.memory import MemoryStore
from lore.types import ConsolidationResult

from .conftest import _stub_embed


class TestConsolidation:
    """Test consolidation engine in dry_run mode without LLM."""

    @pytest.mark.asyncio
    async def test_consolidate_dry_run(self, lore_no_llm: Lore) -> None:
        """Dry-run consolidation returns a ConsolidationResult with dry_run=True."""
        lore_no_llm.remember("first memory about testing")
        lore_no_llm.remember("second memory about deployment")

        result = await lore_no_llm.consolidate(dry_run=True)
        assert isinstance(result, ConsolidationResult)
        assert result.dry_run is True

    @pytest.mark.asyncio
    async def test_consolidate_finds_duplicates(self) -> None:
        """Near-duplicate memories are grouped by the consolidation engine.

        We use a zero-retention config so freshly-created memories qualify
        as candidates, and store exact duplicates (same embedding) that
        exceed the 0.95 dedup threshold.
        """
        store = MemoryStore()
        lore = Lore(
            store=store,
            embedding_fn=_stub_embed,
            redact=False,
            consolidation_config={
                "retention_policies": {"working": 0, "short": 0, "long": 0},
                "dedup_threshold": 0.95,
                "min_group_size": 3,
                "batch_size": 50,
                "max_groups_per_run": 100,
                "llm_model": None,
            },
        )

        # Store several identical memories (same text = same embedding = cosine 1.0)
        lore.remember("always use exponential backoff for retries")
        lore.remember("always use exponential backoff for retries")
        lore.remember("always use exponential backoff for retries")

        result = await lore.consolidate(dry_run=True, strategy="all")
        assert isinstance(result, ConsolidationResult)
        assert result.dry_run is True
        # Exact duplicates should be found as a dedup group
        assert result.groups_found >= 1

    @pytest.mark.asyncio
    async def test_consolidate_empty_store(self, lore_no_llm: Lore) -> None:
        """Consolidation on empty store returns zero groups."""
        result = await lore_no_llm.consolidate(dry_run=True)
        assert isinstance(result, ConsolidationResult)
        assert result.groups_found == 0
        assert result.memories_consolidated == 0

    @pytest.mark.asyncio
    async def test_consolidate_no_duplicates(self) -> None:
        """Distinct memories should not be grouped as duplicates."""
        store = MemoryStore()
        lore = Lore(
            store=store,
            embedding_fn=_stub_embed,
            redact=False,
            consolidation_config={
                "retention_policies": {"working": 0, "short": 0, "long": 0},
                "dedup_threshold": 0.95,
                "min_group_size": 3,
                "batch_size": 50,
                "max_groups_per_run": 100,
                "llm_model": None,
            },
        )
        lore.remember("python async await patterns")
        lore.remember("kubernetes pod scheduling")

        result = await lore.consolidate(dry_run=True, strategy="all")
        assert isinstance(result, ConsolidationResult)
        # Two very different memories should not be grouped as duplicates
        assert result.duplicates_merged == 0
