"""Graph extraction from F2 facts."""

from __future__ import annotations

import logging
from typing import List

from lore.graph.entities import EntityManager
from lore.graph.relationships import RelationshipManager
from lore.types import Fact

logger = logging.getLogger(__name__)


def update_graph_from_facts(
    memory_id: str,
    facts: List[Fact],
    entity_manager: EntityManager,
    relationship_manager: RelationshipManager,
    confidence_threshold: float = 0.5,
    co_occurrence: bool = True,
    co_occurrence_weight: float = 0.3,
) -> None:
    """Convert F2 fact triples into graph entities and edges."""
    entities_for_memory = []

    for fact in facts:
        # Skip low-confidence facts
        if fact.confidence < confidence_threshold:
            continue
        # Skip invalidated facts
        if fact.invalidated_by:
            continue

        try:
            rel = relationship_manager.ingest_from_fact(memory_id, fact)
            if rel:
                # Track entities for co-occurrence
                source = entity_manager.store.get_entity_by_name(
                    entity_manager._normalize_name(fact.subject)
                )
                target = entity_manager.store.get_entity_by_name(
                    entity_manager._normalize_name(fact.object)
                )
                if source and source not in entities_for_memory:
                    entities_for_memory.append(source)
                if target and target not in entities_for_memory:
                    entities_for_memory.append(target)
        except Exception:
            logger.warning(
                "Failed to ingest fact %s into graph", fact.id, exc_info=True
            )

    # Co-occurrence edges
    if co_occurrence and len(entities_for_memory) >= 2:
        try:
            relationship_manager.ingest_co_occurrences(
                memory_id, entities_for_memory, weight=co_occurrence_weight
            )
        except Exception:
            logger.warning(
                "Failed to create co-occurrence edges for memory %s",
                memory_id, exc_info=True,
            )
