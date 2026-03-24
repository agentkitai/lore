"""Graph commands — graph query, entities, relationships, topics, review."""

from __future__ import annotations

import argparse
import json
import sys

import lore.cli._helpers as _helpers


def cmd_graph(args: argparse.Namespace) -> None:
    from lore import Lore

    lore = Lore(knowledge_graph=True)
    if not lore._knowledge_graph_enabled:
        print("Knowledge graph is not enabled.", file=sys.stderr)
        lore.close()
        sys.exit(1)

    from lore.graph.cache import find_query_entities
    entities = find_query_entities(args.entity, lore._entity_cache)
    if not entities:
        print(f"No entity matching '{args.entity}' found.")
        lore.close()
        return

    rel_types = [args.rel_type] if args.rel_type else None
    seed_ids = [e.id for e in entities]
    graph_ctx = lore._graph_traverser.traverse(
        seed_entity_ids=seed_ids,
        depth=min(args.depth, 3),
        min_weight=args.min_weight,
        rel_types=rel_types,
        direction=args.direction,
    )

    if args.format == "json":
        from lore.graph.visualization import to_d3_json
        print(json.dumps(to_d3_json(graph_ctx), indent=2))
    else:
        from lore.graph.visualization import to_text_tree
        print(to_text_tree(graph_ctx))
        print(f"\n{len(graph_ctx.entities)} entities, {len(graph_ctx.relationships)} relationships")
        print(f"Relevance: {graph_ctx.relevance_score:.2f}")

    lore.close()


def cmd_entities(args: argparse.Namespace) -> None:
    from lore import Lore

    lore = Lore(knowledge_graph=True)
    entities = lore._store.list_entities(entity_type=args.entity_type, limit=args.limit)
    lore.close()

    if not entities:
        print("No entities found.")
        return

    if args.sort == "name":
        entities.sort(key=lambda e: e.name)
    elif args.sort == "created":
        entities.sort(key=lambda e: e.created_at, reverse=True)

    print(f"{'Name':<30} {'Type':<15} {'Mentions':<10} {'Aliases'}")
    print("-" * 80)
    for e in entities:
        aliases = ", ".join(e.aliases[:3]) if e.aliases else "-"
        print(f"{e.name:<30} {e.entity_type:<15} {e.mention_count:<10} {aliases}")


def cmd_relationships(args: argparse.Namespace) -> None:
    from lore import Lore

    lore = Lore(knowledge_graph=True)

    entity_id = None
    if args.entity:
        e = lore._store.get_entity_by_name(args.entity.lower())
        if e:
            entity_id = e.id
        else:
            print(f"Entity '{args.entity}' not found.")
            lore.close()
            return

    rels = lore._store.list_relationships(
        entity_id=entity_id,
        rel_type=args.rel_type,
        include_expired=args.include_expired,
        limit=args.limit,
    )
    lore.close()

    if not rels:
        print("No relationships found.")
        return

    print(f"{'Source':<25} {'Type':<20} {'Target':<25} {'Weight':<10} {'Status'}")
    print("-" * 90)
    for r in rels:
        lore._store.get_entity(r.source_entity_id) if hasattr(lore, '_store') else None
        lore._store.get_entity(r.target_entity_id) if hasattr(lore, '_store') else None
        # We already closed lore, so show IDs
        status = "active" if r.valid_until is None else "expired"
        print(f"{r.source_entity_id[:24]:<25} {r.rel_type:<20} {r.target_entity_id[:24]:<25} {r.weight:<10.2f} {status}")


def cmd_graph_backfill(args: argparse.Namespace) -> None:
    from lore import Lore

    lore = Lore(knowledge_graph=True)
    count = lore.graph_backfill(project=args.project, limit=args.limit)
    lore.close()
    print(f"Processed {count} memory(ies) into the knowledge graph.")


def cmd_topics(args) -> None:
    lore = _helpers._get_lore(args.db)
    if not lore._knowledge_graph_enabled:
        lore.close()
        print("Topics require the knowledge graph. Run `lore config set knowledge_graph true`.")
        return
    if args.name:
        detail = lore.topic_detail(args.name, max_memories=20, include_summary=True)
        lore.close()
        if detail is None:
            print(f"No topic found matching '{args.name}'.")
            return
        entity = detail.entity
        print(f"Topic: {entity.name} ({entity.entity_type})")
        print(f"Mentions: {detail.memory_count}")
        if detail.memories:
            print(f"Memories ({len(detail.memories)} of {detail.memory_count}):")
            for m in detail.memories:
                ts = m.created_at[:10] if m.created_at else "?"
                ct = m.content if args.fmt == "detailed" else m.content[:100]
                if args.fmt != "detailed" and len(m.content) > 100:
                    ct += "..."
                print(f"  [{ts}] {m.type}: {ct}")
    else:
        results = lore.list_topics(entity_type=args.entity_type, min_mentions=args.min_mentions, limit=args.limit)
        lore.close()
        if not results:
            print(f"No topics found (threshold: {args.min_mentions}+ mentions).")
            return
        print(f"Topics ({len(results)} found, threshold: {args.min_mentions}+ mentions):")
        for t in results:
            print(f"  {t.name} ({t.entity_type}) — {t.mention_count} memories")


def cmd_review(args: argparse.Namespace) -> None:
    """Review pending knowledge graph connections (E6)."""
    lore = _helpers._get_lore(args.db)

    if args.approve:
        ok = lore.review_connection(args.approve, "approve")
        lore.close()
        if ok:
            print(f"Approved: {args.approve}")
        else:
            print(f"Not found: {args.approve}", file=sys.stderr)
            sys.exit(1)
        return

    if args.reject:
        ok = lore.review_connection(args.reject, "reject")
        lore.close()
        if ok:
            print(f"Rejected: {args.reject}")
        else:
            print(f"Not found: {args.reject}", file=sys.stderr)
            sys.exit(1)
        return

    if args.approve_all:
        count = lore.review_all("approve")
        lore.close()
        print(f"Approved {count} connection(s).")
        return

    if args.reject_all:
        count = lore.review_all("reject")
        lore.close()
        print(f"Rejected {count} connection(s).")
        return

    # Default: list pending
    items = lore.get_pending_reviews(limit=args.limit)
    lore.close()
    if not items:
        print("Nothing to review.")
        return

    print(f"Pending connections ({len(items)} total):\n")
    for i, item in enumerate(items, 1):
        rel = item.relationship
        print(f"  {i}. {item.source_entity_name} --[{rel.rel_type}]--> {item.target_entity_name}")
        if item.source_memory_content:
            snippet = item.source_memory_content[:100].replace("\n", " ")
            print(f"     Source: \"{snippet}\"")
        print(f"     ID: {rel.id}  Created: {rel.created_at[:19] if rel.created_at else 'unknown'}")
        print()
    print("Use --approve <id> or --reject <id> to act on items.")
    print("Use --approve-all or --reject-all for bulk actions.")
