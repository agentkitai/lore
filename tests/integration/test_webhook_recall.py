"""Scenario 5 — Ingest via remember with source metadata."""

from __future__ import annotations

from lore import Lore


class TestWebhookRecall:
    """Test ingestion via remember() with source metadata, simulating webhook ingest."""

    def test_ingest_stores_with_source(self, lore_no_llm: Lore) -> None:
        """remember() with source parameter persists the source field."""
        mid = lore_no_llm.remember(
            "deployment completed for service-abc",
            source="slack",
            metadata={"channel": "#deployments", "ts": "1234567890"},
        )
        mem = lore_no_llm.get(mid)
        assert mem is not None
        assert mem.source == "slack"
        assert mem.metadata["channel"] == "#deployments"

    def test_ingested_content_recallable(self, lore_no_llm: Lore) -> None:
        """Content ingested via remember() with source is retrievable via recall."""
        lore_no_llm.remember(
            "CI pipeline failed on main branch due to flaky test",
            source="github",
            metadata={"repo": "org/app", "event": "workflow_run"},
        )
        results = lore_no_llm.recall("CI pipeline failed")
        assert len(results) >= 1
        assert "CI pipeline failed" in results[0].memory.content

    def test_multiple_sources(self, lore_no_llm: Lore) -> None:
        """Memories from different sources coexist and are all recallable."""
        lore_no_llm.remember("slack message about deploy", source="slack")
        lore_no_llm.remember("github PR merged", source="github")
        lore_no_llm.remember("manual note about config", source="manual")

        memories = lore_no_llm.list_memories()
        sources = {m.source for m in memories}
        assert sources == {"slack", "github", "manual"}

    def test_source_none_by_default(self, lore_no_llm: Lore) -> None:
        """Without explicit source, memory.source is None."""
        mid = lore_no_llm.remember("no source specified")
        mem = lore_no_llm.get(mid)
        assert mem is not None
        assert mem.source is None
