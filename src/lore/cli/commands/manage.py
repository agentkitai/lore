"""Management commands — forget, list, stats, export, import."""

from __future__ import annotations

import argparse
import json
import sys

import lore.cli._helpers as _helpers


def cmd_forget(args: argparse.Namespace) -> None:
    lore = _helpers._get_lore(args.db)
    if lore.forget(args.id):
        print(f"Forgotten: {args.id}")
    else:
        print(f"Not found: {args.id}")
    lore.close()


def cmd_memories(args: argparse.Namespace) -> None:
    lore = _helpers._get_lore(args.db)
    tier = getattr(args, "tier", None)
    memories = lore.list_memories(type=args.type, tier=tier, limit=args.limit)
    lore.close()
    if not memories:
        print("No memories.")
        return
    sort_key = getattr(args, "sort", "created")
    if sort_key == "importance":
        memories.sort(key=lambda m: m.importance_score, reverse=True)
    print(f"{'ID':<28} {'Tier':<10} {'Type':<12} {'Importance':<12} {'Created':<22} {'Topics':<30} {'Content':<40}")
    print("-" * 154)
    for m in memories:
        created = m.created_at[:19] if m.created_at else ""
        enrichment = (m.metadata or {}).get("enrichment", {})
        topics = ", ".join(enrichment.get("topics", [])) if enrichment else "-"
        print(
            f"{m.id:<28} {m.tier:<10} {m.type:<12} {m.importance_score:<12.2f} "
            f"{created:<22} {topics:<30} {m.content[:40]:<40}"
        )


def cmd_stats(args: argparse.Namespace) -> None:
    lore = _helpers._get_lore(args.db)
    s = lore.stats()
    lore.close()
    print(f"Total: {s.total}")
    if s.by_type:
        print("By type:")
        for t, count in sorted(s.by_type.items()):
            print(f"  {t}: {count}")
    if s.by_tier:
        print("By tier:")
        for t, count in sorted(s.by_tier.items()):
            print(f"  {t}: {count}")
    if s.oldest:
        print(f"Oldest: {s.oldest}")
        print(f"Newest: {s.newest}")


def cmd_recent(args: argparse.Namespace) -> None:
    lore = _helpers._get_lore(args.db)
    result = lore.recent_activity(
        hours=args.hours,
        project=args.project,
        format=args.format,
    )
    lore.close()
    from lore.recent import format_cli, format_detailed

    if args.format == "detailed":
        print(format_detailed(result))
    else:
        print(format_cli(result))


def cmd_export(args: argparse.Namespace) -> None:
    lore = _helpers._get_lore(args.db)
    try:
        result = lore.export_data(
            format=args.format,
            output=args.output,
            project=args.project,
            type=args.type,
            tier=args.tier,
            since=args.since,
            include_embeddings=args.include_embeddings,
            pretty=args.pretty,
        )
    except Exception as exc:
        lore.close()
        print(f"Export failed: {exc}", file=sys.stderr)
        sys.exit(2)
    lore.close()
    print(f"Exported to: {result.path}")
    print(
        f"  Memories: {result.memories}, Entities: {result.entities}, "
        f"Relationships: {result.relationships}, Facts: {result.facts}"
    )
    print(f"  Hash: {result.content_hash}")
    print(f"  Duration: {result.duration_ms}ms")
    if result.memories == 0:
        sys.exit(1)


def cmd_import(args: argparse.Namespace) -> None:
    import os
    if not os.path.exists(args.file):
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    lore = _helpers._get_lore(args.db)
    try:
        result = lore.import_data(
            file_path=args.file,
            overwrite=args.overwrite,
            skip_embeddings=args.skip_embeddings,
            project_override=args.project,
            dry_run=args.dry_run,
            redact=args.redact,
        )
    except ValueError as exc:
        lore.close()
        if "newer" in str(exc).lower() or "schema" in str(exc).lower():
            print(f"Schema error: {exc}", file=sys.stderr)
            sys.exit(2)
        if "hash" in str(exc).lower() or "mismatch" in str(exc).lower():
            print(f"Integrity error: {exc}", file=sys.stderr)
            sys.exit(3)
        print(f"Import failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        lore.close()
        print(f"Import failed: {exc}", file=sys.stderr)
        sys.exit(1)
    lore.close()

    if args.dry_run:
        print("Dry run — no changes written")
    print(
        f"Total: {result.total}, Imported: {result.imported}, "
        f"Skipped: {result.skipped}, Overwritten: {result.overwritten}, "
        f"Errors: {result.errors}"
    )
    if result.embeddings_regenerated:
        print(f"Embeddings regenerated: {result.embeddings_regenerated}")
    for w in result.warnings[:10]:
        print(f"  Warning: {w}")
    print(f"Duration: {result.duration_ms}ms")
