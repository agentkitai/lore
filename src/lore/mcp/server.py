"""MCP server that wraps the Lore SDK.

Exposes 20 memory tools over stdio transport for knowledge management,
knowledge graphs, fact extraction, classification, and more.

Configure via environment variables:
  LORE_PROJECT — default project scope
  LORE_ENRICHMENT_ENABLED — enable LLM enrichment pipeline
  LORE_KNOWLEDGE_GRAPH — enable knowledge graph features
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from lore.lore import Lore

# ---------------------------------------------------------------------------
# Lore instance (created lazily so import doesn't trigger side-effects)
# ---------------------------------------------------------------------------

_lore: Optional[Lore] = None


def _get_lore() -> Lore:
    """Return the module-level Lore instance, creating it on first call."""
    global _lore
    if _lore is not None:
        return _lore

    project = os.environ.get("LORE_PROJECT") or None
    store_type = os.environ.get("LORE_STORE", "local")

    if store_type == "remote":
        _lore = Lore(
            project=project,
            store="remote",
            api_url=os.environ.get("LORE_API_URL"),
            api_key=os.environ.get("LORE_API_KEY"),
        )
    elif store_type == "local":
        enrichment = os.environ.get("LORE_ENRICHMENT_ENABLED", "").lower() in ("true", "1", "yes")
        enrichment_model = os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")
        _lore = Lore(
            project=project,
            enrichment=enrichment,
            enrichment_model=enrichment_model,
        )
    else:
        raise ValueError(
            f"Invalid LORE_STORE value: {store_type!r}. "
            "Must be 'local' or 'remote'."
        )

    return _lore


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="lore",
    instructions=(
        "Lore is a cross-agent memory system. Use it to remember knowledge, "
        "recall relevant memories when facing problems, and forget outdated "
        "information. Memories can be facts, lessons, preferences, context, "
        "or any knowledge worth preserving across sessions."
    ),
)


@mcp.tool(
    description=(
        "Save a memory — any knowledge worth preserving. "
        "USE THIS WHEN: you just solved a tricky bug, found a non-obvious fix, "
        "discovered a workaround, learned a user preference, or encountered "
        "something that future agents (or your future self) would benefit from knowing. "
        "DO NOT save trivial things — only save memories that would save someone "
        "real time or prevent a real mistake. "
        "The content should be a clear, self-contained piece of knowledge. "
        "Optionally set tier: 'working' (auto-expires in 1h, for scratch context), "
        "'short' (auto-expires in 7d, for session learnings), "
        "or 'long' (default, no expiry, for lasting knowledge). "
        "When enrichment is enabled, automatically extracts topics, entities, sentiment, "
        "classifies intent/domain/emotion, and extracts structured facts."
    ),
)
def remember(
    content: str,
    type: str = "general",
    tier: str = "long",
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    project: Optional[str] = None,
    ttl: Optional[int] = None,
) -> str:
    """Store a memory in Lore."""
    try:
        lore = _get_lore()
        memory_id = lore.remember(
            content=content,
            type=type,
            tier=tier,
            tags=tags,
            metadata=metadata,
            source=source,
            project=project,
            ttl=ttl,
        )
        return f"Memory saved (ID: {memory_id}, tier: {tier})"
    except Exception as e:
        return f"Failed to save memory: {e}"


@mcp.tool(
    description=(
        "Search for relevant memories from past experience. "
        "USE THIS WHEN: you're about to solve a problem, debug an error, "
        "or make a design decision — especially if you suspect someone has "
        "hit this before. Search with a natural-language description of "
        "your problem or question. "
        "GOOD queries: 'CORS errors with FastAPI', 'Docker build fails on M1', "
        "'rate limiting strategy for API'. "
        "BAD queries: 'help', 'error', 'fix this'. Be specific. "
        "Supports filtering by tier (working/short/long), type, tags, "
        "entity, topic, intent, domain, and emotion. "
        "When knowledge graph is enabled, set graph_depth (e.g. via LORE_GRAPH_DEPTH) "
        "to surface memories connected via entity relationships."
    ),
)
def recall(
    query: str,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 5,
    repo_path: Optional[str] = None,
    intent: Optional[str] = None,
    domain: Optional[str] = None,
    emotion: Optional[str] = None,
    topic: Optional[str] = None,
    sentiment: Optional[str] = None,
    entity: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """Search Lore memory for relevant memories."""
    try:
        lore = _get_lore()
        limit = max(1, min(limit, 20))
        results = lore.recall(
            query=query, tags=tags, type=type, tier=tier, limit=limit,
            check_freshness=bool(repo_path), repo_path=repo_path,
            intent=intent, domain=domain, emotion=emotion,
            topic=topic, sentiment=sentiment, entity=entity, category=category,
        )
        if not results:
            return "No relevant memories found. Try a different query or broader terms."

        lines: List[str] = [f"Found {len(results)} relevant memory(ies):\n"]
        for i, r in enumerate(results, 1):
            mem = r.memory
            lines.append(f"{'─' * 60}")
            staleness_badge = ""
            if r.staleness and r.staleness.status not in ("fresh", "unknown"):
                staleness_badge = (
                    f" [POSSIBLY STALE - {r.staleness.commits_since} "
                    f"commits since memory]"
                )
            # Classification badge
            cls_badge = ""
            cls_data = (mem.metadata or {}).get("classification")
            if cls_data:
                cls_badge = (
                    f" [{cls_data.get('intent', '?')}, "
                    f"{cls_data.get('domain', '?')}, "
                    f"{cls_data.get('emotion', '?')}]"
                )
            lines.append(
                f"Memory {i}  (importance: {mem.importance_score:.2f}, "
                f"score: {r.score:.2f}, id: {mem.id}, "
                f"type: {mem.type}, tier: {mem.tier}){staleness_badge}{cls_badge}"
            )
            lines.append(f"Content: {mem.content}")
            if mem.tags:
                lines.append(f"Tags:    {', '.join(mem.tags)}")
            enrichment = (mem.metadata or {}).get("enrichment", {})
            if enrichment:
                if enrichment.get("topics"):
                    parts = [f"Topics: {', '.join(enrichment['topics'])}"]
                    if enrichment.get("sentiment"):
                        s = enrichment["sentiment"]
                        parts.append(f"Sentiment: {s['label']} ({s['score']:+.1f})")
                    lines.append(" | ".join(parts))
                if enrichment.get("entities"):
                    ents = [f"{e['name']} ({e['type']})" for e in enrichment["entities"]]
                    lines.append(f"Entities: {', '.join(ents)}")
                if enrichment.get("categories"):
                    lines.append(f"Categories: {', '.join(enrichment['categories'])}")
            if mem.project:
                lines.append(f"Project: {mem.project}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"Failed to recall memories: {e}"


@mcp.tool(
    description=(
        "Delete a memory by its ID. "
        "USE THIS WHEN: a memory is outdated, incorrect, or no longer relevant. "
        "Pass the memory ID from recall output."
    ),
)
def forget(memory_id: str) -> str:
    """Delete a memory from Lore."""
    try:
        lore = _get_lore()
        if lore.forget(memory_id):
            return f"Memory {memory_id} forgotten."
        return f"Memory {memory_id} not found."
    except Exception as e:
        return f"Failed to forget memory: {e}"


@mcp.tool(
    description=(
        "List stored memories, optionally filtered by type, tier, or project. "
        "USE THIS WHEN: you want to browse all stored memories, audit what's "
        "in the knowledge base, or find memories by type/tier without semantic search. "
        "For semantic search, use recall instead."
    ),
)
def list_memories(
    type: Optional[str] = None,
    tier: Optional[str] = None,
    project: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """List memories in Lore."""
    try:
        lore = _get_lore()
        memories = lore.list_memories(type=type, tier=tier, project=project, limit=limit)
        if not memories:
            return "No memories found."

        lines: List[str] = [f"Found {len(memories)} memory(ies):\n"]
        for mem in memories:
            lines.append(
                f"[{mem.id}] ({mem.type}, importance: {mem.importance_score:.2f}) "
                f"{mem.content[:100]}"
            )
            if mem.tags:
                lines.append(f"  Tags: {', '.join(mem.tags)}")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list memories: {e}"


@mcp.tool(
    description=(
        "Return memory statistics: total count, counts by type and tier, "
        "oldest and newest memory timestamps. "
        "USE THIS WHEN: you want an overview of the knowledge base, check how many "
        "memories exist, or see the distribution across types and tiers."
    ),
)
def stats(project: Optional[str] = None) -> str:
    """Return memory statistics."""
    try:
        lore = _get_lore()
        s = lore.stats(project=project)
        lines = [
            f"Total memories: {s.total}",
        ]
        if s.by_type:
            lines.append("By type:")
            for t, count in sorted(s.by_type.items()):
                lines.append(f"  {t}: {count}")
        if s.by_tier:
            lines.append("By tier:")
            for t, count in sorted(s.by_tier.items()):
                lines.append(f"  {t}: {count}")
        if s.oldest:
            lines.append(f"Oldest: {s.oldest}")
            lines.append(f"Newest: {s.newest}")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to get stats: {e}"


@mcp.tool(
    description=(
        "Upvote a memory that was helpful. "
        "USE THIS WHEN: you recalled a memory and it actually helped solve "
        "your problem. This boosts the memory's ranking in future searches. "
        "Pass the memory ID from recall output."
    ),
)
def upvote_memory(memory_id: str) -> str:
    """Upvote a memory to boost its ranking."""
    try:
        lore = _get_lore()
        lore.upvote(memory_id)
        return f"Upvoted memory {memory_id}"
    except Exception as e:
        return f"Failed to upvote: {e}"


@mcp.tool(
    description=(
        "Downvote a memory that was wrong or unhelpful. "
        "USE THIS WHEN: you recalled a memory but it was outdated, incorrect, "
        "or misleading. This lowers the memory's ranking so others don't waste "
        "time on bad advice. Pass the memory ID from recall output."
    ),
)
def downvote_memory(memory_id: str) -> str:
    """Downvote a memory to lower its ranking."""
    try:
        lore = _get_lore()
        lore.downvote(memory_id)
        return f"Downvoted memory {memory_id}"
    except Exception as e:
        return f"Failed to downvote: {e}"


@mcp.tool(
    description=(
        "Export memories formatted for LLM context injection. "
        "USE THIS WHEN: you need to inject relevant memories directly into a prompt "
        "or system message. Returns a formatted block of memories optimized for your "
        "LLM's preferred format. Supports XML (Claude), ChatML (OpenAI), markdown, "
        "and raw text."
    ),
)
def as_prompt(
    query: str,
    format: str = "xml",
    max_tokens: Optional[int] = None,
    limit: int = 10,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    include_metadata: bool = False,
) -> str:
    """Export memories formatted for LLM context injection."""
    try:
        lore = _get_lore()
        return lore.as_prompt(
            query,
            format=format,
            max_tokens=max_tokens,
            limit=limit,
            tags=tags,
            type=type,
            include_metadata=include_metadata,
        )
    except Exception as e:
        return f"Failed to format memories: {e}"


@mcp.tool(
    description=(
        "Check if stored memories are still fresh against current git state. "
        "USE THIS WHEN: you want to verify that code-pattern memories are "
        "still relevant before acting on them. Compares memories with "
        "file_path metadata against the git commit history to detect staleness."
    ),
)
def check_freshness(
    repo_path: str,
    project: Optional[str] = None,
) -> str:
    """Check memory freshness against git history."""
    try:
        from lore.freshness.detector import FreshnessDetector
        from lore.freshness.git_ops import GitError

        try:
            FreshnessDetector.validate_repo(repo_path)
        except GitError as e:
            return f"Error: {e}"

        lore = _get_lore()
        memories = lore.list_memories(project=project)
        if not memories:
            return "No memories to check."

        detector = FreshnessDetector(repo_path)
        results = detector.check_many(memories)

        report = FreshnessDetector.format_report(results, repo_path, markdown=True)

        stale = [r for r in results if r.status in ("stale", "likely_stale")]
        if stale:
            ids = ", ".join(r.memory_id[:12] + "..." for r in stale[:5])
            report += (
                f"\n\nFound {len(stale)} stale/likely stale memory(ies). "
                f"Consider reviewing: {ids}"
            )

        return report
    except Exception as e:
        return f"Failed to check freshness: {e}"


@mcp.tool(
    description=(
        "Sync GitHub repository data (PRs, issues, commits, releases) into Lore as memories. "
        "USE THIS WHEN: you want to ingest tribal knowledge from a GitHub repo so it's searchable. "
        "Requires the `gh` CLI to be installed and authenticated."
    ),
)
def github_sync(
    repo: str,
    types: Optional[str] = None,
    since: Optional[str] = None,
    project: Optional[str] = None,
) -> str:
    """Sync GitHub repo data into Lore memories."""
    try:
        from lore.github.syncer import GitHubCLIError, GitHubSyncer

        lore = _get_lore()
        syncer = GitHubSyncer(lore)
        type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
        result = syncer.sync(repo, types=type_list, since=since, project=project)
        return result.summary()
    except GitHubCLIError as e:
        return f"GitHub sync failed: {e}"
    except Exception as e:
        return f"Failed to sync: {e}"


@mcp.tool(
    description=(
        "Classify a piece of text by intent, domain, and emotion. "
        "Returns structured classification without storing anything. "
        "USE THIS WHEN: you want to understand the nature of a piece of text "
        "before storing it, or to analyze conversation patterns."
    ),
)
def classify(text: str) -> str:
    """Classify text without storing it."""
    try:
        lore = _get_lore()
        result = lore.classify(text)
        return (
            f"Intent: {result.intent} ({result.confidence.get('intent', 0):.0%})\n"
            f"Domain: {result.domain} ({result.confidence.get('domain', 0):.0%})\n"
            f"Emotion: {result.emotion} ({result.confidence.get('emotion', 0):.0%})"
        )
    except Exception as e:
        return f"Failed to classify: {e}"


@mcp.tool(
    description=(
        "Enrich memories with LLM-extracted metadata (topics, sentiment, entities, categories). "
        "USE THIS WHEN: you want to add structured metadata to existing memories for better filtering. "
        "Requires LORE_ENRICHMENT_ENABLED=true and a configured LLM provider (LORE_LLM_PROVIDER + API key). "
        "Enrichment runs automatically on remember() when enabled; use this tool to enrich older memories."
    ),
)
def enrich(
    memory_id: Optional[str] = None,
    all: bool = False,
    project: Optional[str] = None,
    force: bool = False,
) -> str:
    """Enrich memories with LLM-extracted metadata."""
    try:
        lore = _get_lore()
        if memory_id:
            result = lore.enrich_memories(memory_ids=[memory_id], force=force)
        elif all:
            result = lore.enrich_memories(project=project, force=force)
        else:
            return "Provide memory_id or set all=True."

        return (
            f"Enrichment complete: {result['enriched']} enriched, "
            f"{result['skipped']} skipped, {result['failed']} failed."
        )
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Enrichment failed: {e}"


@mcp.tool(
    description=(
        "Extract structured facts from text without storing them. "
        "Returns atomic (subject, predicate, object) triples with confidence scores. "
        "USE THIS WHEN: you need to understand what facts are contained in a piece of text, "
        "or to preview what facts would be extracted before remembering."
    ),
)
def extract_facts(text: str) -> str:
    """Extract facts from text, return formatted list."""
    try:
        lore = _get_lore()
        facts = lore.extract_facts(text)
        if not facts:
            if not lore._fact_extraction_enabled:
                return (
                    "Fact extraction requires an LLM provider. "
                    "Configure llm_provider and set fact_extraction=True."
                )
            return "No facts extracted."

        lines = [f"Extracted {len(facts)} fact(s):\n"]
        for i, f in enumerate(facts, 1):
            lines.append(
                f"{i}. ({f.subject}, {f.predicate}, {f.object}) "
                f"[confidence: {f.confidence:.2f}]"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to extract facts: {e}"


@mcp.tool(
    description=(
        "List active (non-invalidated) facts from the knowledge base. "
        "USE THIS WHEN: you want to see what structured facts Lore knows "
        "about a subject, or to review all known facts."
    ),
)
def list_facts(
    subject: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List active facts."""
    try:
        lore = _get_lore()
        facts = lore.get_active_facts(subject=subject, limit=limit)
        if not facts:
            return "No active facts found."

        lines = [f"Active facts ({len(facts)}):\n"]
        lines.append(f"{'Subject':<20} {'Predicate':<20} {'Object':<30} {'Confidence':<12} {'Source'}")
        lines.append("-" * 95)
        for f in facts:
            lines.append(
                f"{f.subject:<20} {f.predicate:<20} {f.object:<30} "
                f"{f.confidence:<12.2f} {f.memory_id[:12]}..."
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list facts: {e}"


@mcp.tool(
    description=(
        "List recent fact conflicts detected during memory ingestion. "
        "Shows what facts were superseded, merged, or flagged as contradictions. "
        "USE THIS WHEN: you want to review knowledge changes, audit what facts "
        "were updated, or resolve flagged contradictions."
    ),
)
def conflicts(
    resolution: Optional[str] = None,
    limit: int = 10,
) -> str:
    """List recent conflicts."""
    try:
        lore = _get_lore()
        entries = lore.list_conflicts(resolution=resolution, limit=limit)
        if not entries:
            return "No conflicts found."

        lines = [f"Recent conflicts ({len(entries)} total):\n"]
        for i, c in enumerate(entries, 1):
            if c.resolution == "SUPERSEDE":
                lines.append(
                    f"{i}. [SUPERSEDE] {c.subject}/{c.predicate}: "
                    f"\"{c.old_value}\" -> \"{c.new_value}\""
                )
            elif c.resolution == "CONTRADICT":
                lines.append(
                    f"{i}. [CONTRADICT] {c.subject}/{c.predicate}: "
                    f"\"{c.old_value}\" vs \"{c.new_value}\""
                )
            elif c.resolution == "MERGE":
                lines.append(
                    f"{i}. [MERGE] {c.subject}/{c.predicate}: "
                    f"\"{c.old_value}\" + \"{c.new_value}\""
                )
            else:
                lines.append(
                    f"{i}. [{c.resolution}] {c.subject}/{c.predicate}: "
                    f"\"{c.old_value}\" / \"{c.new_value}\""
                )
            lines.append(f"   Memory: {c.new_memory_id[:12]}... ({c.resolved_at[:10]})")
            reasoning = (c.metadata or {}).get("reasoning", "")
            if reasoning:
                lines.append(f"   Reason: {reasoning}")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list conflicts: {e}"


@mcp.tool(
    description=(
        "Query the knowledge graph to find entities connected to a given entity. "
        "USE THIS WHEN: you want to understand relationships between concepts, "
        "find dependencies, or explore how entities are connected. "
        "Returns connected entities and relationship types within the specified depth."
    ),
)
def graph_query(
    entity: str,
    depth: int = 2,
    rel_types: Optional[List[str]] = None,
    direction: str = "both",
    min_weight: float = 0.1,
) -> str:
    """Traverse the knowledge graph from a given entity."""
    try:
        lore = _get_lore()
        if not lore._knowledge_graph_enabled:
            return "Knowledge graph is not enabled. Set LORE_KNOWLEDGE_GRAPH=true."

        from lore.graph.cache import find_query_entities
        entities = find_query_entities(entity, lore._entity_cache)
        if not entities:
            return f"No entity matching '{entity}' found in the graph."

        seed_ids = [e.id for e in entities]
        graph_ctx = lore._graph_traverser.traverse(
            seed_entity_ids=seed_ids,
            depth=min(depth, 3),
            min_weight=min_weight,
            rel_types=rel_types,
            direction=direction,
        )

        if not graph_ctx.relationships:
            names = ", ".join(e.name for e in entities)
            return f"Entity '{names}' found but has no connections within {depth} hop(s)."

        lines = [f"Graph query for '{entity}' (depth={depth}):\n"]
        lines.append(f"Found {len(graph_ctx.entities)} entities, {len(graph_ctx.relationships)} relationships\n")

        entity_map = {e.id: e for e in graph_ctx.entities}
        for rel in graph_ctx.relationships:
            src = entity_map.get(rel.source_entity_id)
            tgt = entity_map.get(rel.target_entity_id)
            if src and tgt:
                lines.append(
                    f"  {src.name} --{rel.rel_type}--> {tgt.name} "
                    f"(weight: {rel.weight:.2f})"
                )

        lines.append(f"\nRelevance score: {graph_ctx.relevance_score:.2f}")
        return "\n".join(lines)
    except Exception as e:
        return f"Graph query failed: {e}"


@mcp.tool(
    description=(
        "List entities in the knowledge graph, optionally filtered by type. "
        "USE THIS WHEN: you want to see what entities Lore knows about, "
        "find entity names for graph queries, or get an overview of the knowledge graph. "
        "Set format='json' for D3-compatible graph visualization output."
    ),
)
def entity_map(
    entity_type: Optional[str] = None,
    limit: int = 50,
    format: str = "text",
) -> str:
    """List entities in the knowledge graph."""
    try:
        lore = _get_lore()
        if not lore._knowledge_graph_enabled:
            return "Knowledge graph is not enabled. Set LORE_KNOWLEDGE_GRAPH=true."

        entities = lore._store.list_entities(entity_type=entity_type, limit=limit)
        if not entities:
            return "No entities in the knowledge graph."

        if format == "json":
            import json

            from lore.graph.visualization import to_d3_json
            from lore.types import GraphContext
            # Create a minimal graph context for visualization
            rels = []
            for e in entities:
                rels.extend(lore._store.list_relationships(entity_id=e.id, limit=5))
            ctx = GraphContext(entities=entities, relationships=rels)
            return json.dumps(to_d3_json(ctx), indent=2)

        lines = [f"Entities ({len(entities)}):\n"]
        lines.append(f"{'Name':<30} {'Type':<15} {'Mentions':<10} {'Aliases'}")
        lines.append("-" * 80)
        for e in entities:
            aliases = ", ".join(e.aliases[:3]) if e.aliases else "-"
            lines.append(f"{e.name:<30} {e.entity_type:<15} {e.mention_count:<10} {aliases}")
        return "\n".join(lines)
    except Exception as e:
        return f"Entity map failed: {e}"


@mcp.tool(
    description=(
        "Find memories and entities related to a given memory or entity. "
        "USE THIS WHEN: you want a quick overview of what's connected to something, "
        "without needing the full graph_query options. "
        "Simpler interface than graph_query for common lookups."
    ),
)
def related(
    memory_id: Optional[str] = None,
    entity_name: Optional[str] = None,
    depth: int = 1,
) -> str:
    """Find related memories and entities by memory ID or entity name."""
    try:
        lore = _get_lore()
        if not lore._knowledge_graph_enabled:
            return "Knowledge graph is not enabled. Set LORE_KNOWLEDGE_GRAPH=true."

        if not memory_id and not entity_name:
            return "Provide either memory_id or entity_name."

        from lore.graph.cache import find_query_entities

        seed_ids: list = []

        if entity_name:
            entities = find_query_entities(entity_name, lore._entity_cache)
            if not entities:
                return f"No entity matching '{entity_name}' found in the graph."
            seed_ids = [e.id for e in entities]

        if memory_id:
            mentions = lore._store.get_entity_mentions_for_memory(memory_id)
            if not mentions:
                return f"No entities linked to memory '{memory_id}'."
            mention_entity_ids = [m.entity_id for m in mentions]
            seed_ids = list(set(seed_ids + mention_entity_ids))

        graph_ctx = lore._graph_traverser.traverse(
            seed_entity_ids=seed_ids,
            depth=min(depth, 3),
        )

        if not graph_ctx.relationships and not graph_ctx.entities:
            return "No related entities or memories found."

        lines = ["Related (depth={}):\n".format(depth)]

        entity_map_dict = {e.id: e for e in graph_ctx.entities}
        if graph_ctx.entities:
            lines.append("Entities:")
            for e in graph_ctx.entities:
                lines.append(f"  - {e.name} ({e.entity_type})")

        if graph_ctx.relationships:
            lines.append("\nRelationships:")
            for rel in graph_ctx.relationships:
                src = entity_map_dict.get(rel.source_entity_id)
                tgt = entity_map_dict.get(rel.target_entity_id)
                if src and tgt:
                    lines.append(
                        f"  {src.name} --{rel.rel_type}--> {tgt.name} "
                        f"(weight: {rel.weight:.2f})"
                    )

        # Include related memories via entity mentions
        related_memory_ids: set = set()
        for e in graph_ctx.entities:
            mentions = lore._store.get_entity_mentions_for_entity(e.id)
            for m in mentions:
                if m.memory_id != memory_id:
                    related_memory_ids.add(m.memory_id)

        if related_memory_ids:
            lines.append(f"\nRelated memories ({len(related_memory_ids)}):")
            for mid in list(related_memory_ids)[:10]:
                mem = lore._store.get(mid)
                if mem:
                    preview = mem.content[:80] + "..." if len(mem.content) > 80 else mem.content
                    lines.append(f"  [{mid}] {preview}")

        return "\n".join(lines)
    except Exception as e:
        return f"Related lookup failed: {e}"


@mcp.tool(
    description=(
        "Ingest content from external sources with source tracking. "
        "USE THIS WHEN: you want to import content from Slack, Telegram, Git, or any "
        "external source with full provenance tracking (who said it, where, when). "
        "Content goes through normalization and deduplication before storage. "
        "Supports source-specific formatting cleanup (Slack mrkdwn, Telegram HTML, etc)."
    ),
)
def ingest(
    content: str,
    source: str = "mcp",
    user: Optional[str] = None,
    channel: Optional[str] = None,
    type: str = "general",
    tags: Optional[str] = None,
    project: Optional[str] = None,
) -> str:
    """Ingest content with source tracking."""
    try:
        from datetime import datetime, timezone

        lore = _get_lore()
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        metadata = {
            "source_info": {
                "adapter": source,
                "user": user,
                "channel": channel,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "raw_format": "plain_text",
            }
        }
        memory_id = lore.remember(
            content=content,
            type=type,
            tier="long",
            tags=tag_list,
            metadata=metadata,
            source=source,
            project=project,
        )
        return f"Ingested as memory {memory_id} (source: {source})"
    except Exception as e:
        return f"Ingestion failed: {e}"


@mcp.tool(
    description=(
        "Trigger memory consolidation. Merges near-duplicate memories and "
        "summarizes related memory clusters into concise long-term memories. "
        "USE THIS WHEN: memory bloat is high, or you want to compress episodic "
        "memories into semantic knowledge. Defaults to dry-run (preview only)."
    ),
)
async def consolidate(
    project: Optional[str] = None,
    dry_run: bool = True,
    strategy: Optional[str] = None,
) -> str:
    """Trigger memory consolidation."""
    try:
        lore = _get_lore()
        result = await lore.consolidate(
            project=project,
            dry_run=dry_run,
            strategy=strategy or "all",
        )
        return _format_consolidation_result(result)
    except Exception as e:
        return f"Consolidation failed: {e}"


def _format_consolidation_result(result: Any) -> str:
    """Format consolidation result for display."""
    lines: List[str] = []
    if result.dry_run:
        lines.append("Consolidation Preview (DRY RUN)")
        lines.append("=" * 32)
    else:
        lines.append("Consolidation Complete")
        lines.append("=" * 22)

    lines.append(f"Groups found: {result.groups_found}")
    lines.append(f"Memories consolidated: {result.memories_consolidated}")
    lines.append(f"Memories created: {result.memories_created}")
    lines.append(f"Duplicates merged: {result.duplicates_merged}")

    if result.groups:
        lines.append("")
        for i, g in enumerate(result.groups, 1):
            strat = g.get("strategy", "?")
            count = g.get("memory_count", 0)
            preview = g.get("preview", "")
            line = f"  Group {i}: {count} memories (strategy: {strat})"
            if "similarity" in g:
                line += f" [similarity: {g['similarity']:.2f}]"
            if "entities" in g:
                line += f" [entities: {', '.join(g['entities'][:3])}]"
            lines.append(line)
            lines.append(f"    Preview: {preview[:120]}")

    if result.dry_run:
        lines.append("")
        lines.append("Run with dry_run=false to execute.")

    return "\n".join(lines)


def run_server() -> None:
    """Start the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
