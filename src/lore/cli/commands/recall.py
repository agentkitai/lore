"""Recall and search commands."""

from __future__ import annotations

import argparse
import sys

import lore.cli._helpers as _helpers


def cmd_recall(args: argparse.Namespace) -> None:
    lore = _helpers._get_lore(args.db)
    tags = None
    if getattr(args, "tags", None):
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    tier = getattr(args, "tier", None)
    verbatim = getattr(args, "verbatim", False)
    offset = getattr(args, "offset", 0)
    results = lore.recall(
        args.query, type=args.type, tier=tier, tags=tags, limit=args.limit,
        offset=offset,
        topic=getattr(args, "topic", None),
        sentiment=getattr(args, "sentiment", None),
        entity=getattr(args, "entity", None),
        category=getattr(args, "category", None),
        verbatim=verbatim,
        year=getattr(args, "year", None),
        month=getattr(args, "month", None),
        day=getattr(args, "day", None),
        days_ago=getattr(args, "days_ago", None),
        hours_ago=getattr(args, "hours_ago", None),
        window=getattr(args, "window", None),
        before=getattr(args, "before", None),
        after=getattr(args, "after", None),
        date_from=getattr(args, "date_from", None),
        date_to=getattr(args, "date_to", None),
    )
    lore.close()
    if not results:
        print("No results.")
        return
    if verbatim:
        for r in results:
            created = r.memory.created_at[:19] if r.memory.created_at else "unknown"
            source = r.memory.source or "unknown"
            project = r.memory.project or "default"
            tier_val = r.memory.tier
            print(f"[{created}] {source} ({project}, {tier_val})")
            print(r.memory.content)
            print("---")
    else:
        for r in results:
            print(f"[{r.score:.3f}] {r.memory.id} ({r.memory.type}, {r.memory.tier})")
            print(f"  {r.memory.content[:200]}")
            enrichment = (r.memory.metadata or {}).get("enrichment", {})
            if enrichment.get("topics"):
                print(f"  Topics: {', '.join(enrichment['topics'])}")
            if r.memory.tags:
                print(f"  Tags: {', '.join(r.memory.tags)}")
            print()


def cmd_prompt(args: argparse.Namespace) -> None:
    lore = _helpers._get_lore(args.db)
    tags = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    result = lore.as_prompt(
        args.query,
        format=args.format,
        max_tokens=args.max_tokens,
        max_chars=args.max_chars,
        limit=args.limit,
        type=args.type,
        tags=tags,
        min_score=args.min_score,
        include_metadata=args.include_metadata,
        project=args.project,
    )
    lore.close()
    print(result, end="")
