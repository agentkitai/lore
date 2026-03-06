"""Scenario 2 — Graph-enhanced recall (knowledge_graph=True, no LLM)."""

from __future__ import annotations

import pytest

from lore import Lore


class TestGraphRecall:
    """Test graph infrastructure availability and basic recall with graph enabled."""

    def test_graph_traverser_available(self, lore_with_graph: Lore) -> None:
        """When knowledge_graph=True, the graph traverser is initialized."""
        assert lore_with_graph._graph_traverser is not None
        assert lore_with_graph._entity_manager is not None
        assert lore_with_graph._relationship_manager is not None

    def test_graph_not_available_by_default(self, lore_no_llm: Lore) -> None:
        """Without knowledge_graph=True, graph components are None."""
        assert lore_no_llm._graph_traverser is None
        assert lore_no_llm._entity_manager is None

    def test_recall_with_graph_depth_zero(self, lore_with_graph: Lore) -> None:
        """With graph_depth=0 (default), recall works normally (no graph boost)."""
        lore_with_graph.remember("database indexing best practices")
        results = lore_with_graph.recall("database indexing", graph_depth=0)
        assert len(results) >= 1
        assert results[0].score > 0

    def test_remember_with_graph_enabled(self, lore_with_graph: Lore) -> None:
        """Remembering with graph enabled does not raise errors (no LLM = no enrichment entities)."""
        mid = lore_with_graph.remember("kubernetes pod scheduling")
        assert isinstance(mid, str)
        mem = lore_with_graph.get(mid)
        assert mem is not None
        assert mem.content == "kubernetes pod scheduling"

    def test_recall_with_graph_depth_nonzero(self, lore_with_graph: Lore) -> None:
        """Recall with graph_depth>0 does not crash even without entities in graph."""
        lore_with_graph.remember("microservice architecture patterns")
        # Should not raise — just returns normal vector results
        results = lore_with_graph.recall("microservice", graph_depth=2)
        assert len(results) >= 1

    def test_graph_entity_cache_initialized(self, lore_with_graph: Lore) -> None:
        """Entity cache is initialized when knowledge_graph=True."""
        assert lore_with_graph._entity_cache is not None
