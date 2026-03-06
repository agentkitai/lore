"""Graph visualization utilities."""

from __future__ import annotations

from typing import Any, Dict, List

from lore.types import GraphContext


def to_d3_json(graph_context: GraphContext) -> Dict[str, Any]:
    """Convert GraphContext to D3 force-graph compatible JSON."""
    nodes = []
    for entity in graph_context.entities:
        nodes.append({
            "id": entity.id,
            "name": entity.name,
            "type": entity.entity_type,
            "mention_count": entity.mention_count,
        })

    links = []
    for rel in graph_context.relationships:
        links.append({
            "source": rel.source_entity_id,
            "target": rel.target_entity_id,
            "type": rel.rel_type,
            "weight": rel.weight,
        })

    return {"nodes": nodes, "links": links}


def to_text_tree(graph_context: GraphContext, max_depth: int = 3) -> str:
    """Convert GraphContext to indented ASCII tree."""
    if not graph_context.entities:
        return "(empty graph)"

    entity_map = {e.id: e for e in graph_context.entities}

    # Build adjacency from relationships
    adjacency: Dict[str, List[Dict[str, str]]] = {}
    for rel in graph_context.relationships:
        if rel.source_entity_id not in adjacency:
            adjacency[rel.source_entity_id] = []
        adjacency[rel.source_entity_id].append({
            "target": rel.target_entity_id,
            "type": rel.rel_type,
            "weight": f"{rel.weight:.2f}",
        })

    # Find root entities (entities that appear in paths as first element)
    roots = set()
    for path in graph_context.paths:
        if path:
            roots.add(path[0])
    if not roots:
        roots = {graph_context.entities[0].id}

    lines: List[str] = []
    visited: set = set()

    def _render(entity_id: str, indent: int, depth: int) -> None:
        if entity_id in visited or depth > max_depth:
            return
        visited.add(entity_id)
        entity = entity_map.get(entity_id)
        if not entity:
            return
        prefix = "  " * indent
        lines.append(f"{prefix}{entity.name} [{entity.entity_type}]")
        for edge in adjacency.get(entity_id, []):
            target = entity_map.get(edge["target"])
            if target and edge["target"] not in visited:
                lines.append(f"{prefix}  --{edge['type']}--> ")
                _render(edge["target"], indent + 2, depth + 1)

    for root_id in sorted(roots):
        _render(root_id, 0, 0)

    # Add any unvisited entities
    for entity in graph_context.entities:
        if entity.id not in visited:
            lines.append(f"{entity.name} [{entity.entity_type}]")

    return "\n".join(lines) if lines else "(empty graph)"
