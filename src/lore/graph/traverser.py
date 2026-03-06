"""App-level hop-by-hop graph traversal engine."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from lore.store.base import Store
from lore.types import Entity, GraphContext, Relationship


class GraphTraverser:
    """App-level hop-by-hop graph traversal engine.

    Each hop = one indexed SQL query.
    Between hops: score, filter, prune.
    """

    DEFAULT_DEPTH = 2
    MAX_DEPTH = 3
    DEFAULT_MIN_WEIGHT = 0.1
    DEFAULT_MAX_FANOUT = 20
    HOP_DECAY = [1.0, 0.7, 0.5]

    def __init__(self, store: Store) -> None:
        self.store = store
        env_max = os.environ.get("LORE_GRAPH_MAX_DEPTH")
        if env_max is not None:
            self.MAX_DEPTH = int(env_max)

    def traverse(
        self,
        seed_entity_ids: List[str],
        depth: int = DEFAULT_DEPTH,
        min_weight: float = DEFAULT_MIN_WEIGHT,
        max_fanout: int = DEFAULT_MAX_FANOUT,
        rel_types: Optional[List[str]] = None,
        direction: str = "both",
        active_only: bool = True,
        at_time: Optional[str] = None,
    ) -> GraphContext:
        """Traverse the graph hop-by-hop from seed entities."""
        depth = min(depth, self.MAX_DEPTH)
        visited_entities: Set[str] = set(seed_entity_ids)
        visited_rels: Set[str] = set()
        all_relationships: List[Relationship] = []
        all_entities: Dict[str, Entity] = {}
        paths: List[List[str]] = [[eid] for eid in seed_entity_ids]

        # Load seed entities
        for eid in seed_entity_ids:
            entity = self.store.get_entity(eid)
            if entity:
                all_entities[eid] = entity

        frontier = set(seed_entity_ids)

        for hop_num in range(depth):
            if not frontier:
                break

            # HOP: One indexed SQL query
            hop_edges = self._hop(frontier, direction, rel_types, active_only, at_time)

            # SCORE: Apply weight-based scoring
            scored_edges = self._score(hop_edges, hop_num)

            # PRUNE: Filter by weight, limit fanout
            surviving_edges = self._prune(scored_edges, min_weight, max_fanout)

            if not surviving_edges:
                break

            # Deduplicate edges across hops
            for edge in surviving_edges:
                if edge.id not in visited_rels:
                    visited_rels.add(edge.id)
                    all_relationships.append(edge)

            # Determine next frontier (new entities not yet visited)
            next_frontier: Set[str] = set()
            for edge in surviving_edges:
                for eid in (edge.source_entity_id, edge.target_entity_id):
                    if eid not in visited_entities:
                        next_frontier.add(eid)
                        visited_entities.add(eid)

            # Load newly discovered entities
            for eid in next_frontier:
                entity = self.store.get_entity(eid)
                if entity:
                    all_entities[eid] = entity

            # Extend paths
            new_paths = []
            for edge in surviving_edges:
                for path in paths:
                    tail = path[-1]
                    if tail == edge.source_entity_id and edge.target_entity_id not in path:
                        new_paths.append(path + [edge.target_entity_id])
                    elif tail == edge.target_entity_id and edge.source_entity_id not in path:
                        new_paths.append(path + [edge.source_entity_id])
            if new_paths:
                paths.extend(new_paths)

            frontier = next_frontier

        relevance = self._compute_relevance(
            all_relationships, len(seed_entity_ids), depth
        )

        return GraphContext(
            entities=list(all_entities.values()),
            relationships=all_relationships,
            paths=paths,
            relevance_score=relevance,
        )

    def traverse_at_time(
        self, seed_entity_ids: List[str], at_time: str, depth: int = 2
    ) -> GraphContext:
        """Traverse the graph as it existed at a specific point in time."""
        return self.traverse(
            seed_entity_ids=seed_entity_ids,
            depth=depth,
            active_only=False,
            at_time=at_time,
        )

    def _hop(
        self,
        frontier: Set[str],
        direction: str,
        rel_types: Optional[List[str]],
        active_only: bool,
        at_time: Optional[str],
    ) -> List[Relationship]:
        """Execute one hop: find all edges connected to frontier entities."""
        if not frontier:
            return []
        return self.store.query_relationships(
            entity_ids=list(frontier),
            direction=direction,
            active_only=active_only,
            at_time=at_time,
            rel_types=rel_types,
        )

    def _score(
        self, edges: List[Relationship], hop_num: int
    ) -> List[Relationship]:
        """Apply hop-distance decay to edge weights."""
        decay = self.HOP_DECAY[min(hop_num, len(self.HOP_DECAY) - 1)]
        for edge in edges:
            edge._effective_weight = edge.weight * decay  # type: ignore[attr-defined]
        return edges

    def _prune(
        self,
        edges: List[Relationship],
        min_weight: float,
        max_fanout: int,
    ) -> List[Relationship]:
        """Prune edges below weight threshold and limit fanout."""
        surviving = [
            e for e in edges
            if getattr(e, "_effective_weight", e.weight) >= min_weight
        ]
        surviving.sort(
            key=lambda e: getattr(e, "_effective_weight", e.weight),
            reverse=True,
        )
        return surviving[:max_fanout]

    def _compute_relevance(
        self,
        relationships: List[Relationship],
        seed_count: int,
        depth: int,
    ) -> float:
        """Compute aggregate graph relevance score (0.0-1.0)."""
        if not relationships:
            return 0.0

        avg_weight = sum(
            getattr(r, "_effective_weight", r.weight) for r in relationships
        ) / len(relationships)

        connection_factor = min(1.0, len(relationships) / (seed_count * 5))

        return min(1.0, avg_weight * (0.5 + 0.5 * connection_factor))
