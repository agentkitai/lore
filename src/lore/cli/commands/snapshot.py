"""Snapshot and consolidation commands."""

from __future__ import annotations

import argparse
import sys

import lore.cli._helpers as _helpers


def cmd_snapshot(args: argparse.Namespace) -> None:
    from lore.export.snapshot import SnapshotManager

    lore = _helpers._get_lore(args.db)
    mgr = SnapshotManager(lore, max_snapshots=args.max_snapshots)

    if args.list_snapshots:
        snapshots = mgr.list()
        lore.close()
        if not snapshots:
            print("No snapshots available.")
            return
        print(f"{'NAME':<22} {'MEMORIES':<10} {'SIZE':<12} {'DATE'}")
        print("-" * 60)
        for s in snapshots:
            print(f"{s['name']:<22} {s.get('memories', '?'):<10} {s.get('size_human', '?'):<12} {s.get('created_at', '?')}")
        return

    if args.delete is not None:
        if args.older_than:
            count = mgr.cleanup(args.older_than)
            lore.close()
            print(f"Deleted {count} snapshot(s).")
        else:
            ok = mgr.delete(args.delete)
            lore.close()
            if ok:
                print(f"Deleted snapshot: {args.delete}")
            else:
                print(f"Snapshot not found: {args.delete}", file=sys.stderr)
                sys.exit(1)
        return

    if args.restore is not None or args.latest:
        name = "__latest__" if args.latest else args.restore
        if name == "__prompt__":
            if not args.latest:
                print("Specify a snapshot name or use --latest", file=sys.stderr)
                lore.close()
                sys.exit(1)

        if not args.yes:
            confirm = input(f"Restore from snapshot '{name}'? [y/N] ")
            if confirm.lower() not in ("y", "yes"):
                print("Aborted.")
                lore.close()
                return

        result = mgr.restore(name)
        lore.close()
        print(
            f"Restored: {result.imported} imported, "
            f"{result.skipped} skipped, {result.errors} errors"
        )
        return

    # Default: create snapshot
    info = mgr.create()
    lore.close()
    print(f"Snapshot created: {info['name']}")
    print(f"  Path: {info['path']}")
    print(f"  Memories: {info['memories']}")
    print(f"  Size: {info['size_human']}")


def cmd_snapshot_save(args) -> None:
    if not args.content or not args.content.strip():
        print("Error: content must be non-empty", file=sys.stderr)
        sys.exit(1)
    lore = _helpers._get_lore(args.db)
    try:
        memory = lore.save_snapshot(content=args.content, title=args.title, session_id=args.session_id)
    except ValueError as exc:
        lore.close()
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    lore.close()
    print(f"Snapshot saved: {memory.id}")


def cmd_consolidate(args: argparse.Namespace) -> None:
    import asyncio

    lore = _helpers._get_lore(args.db)

    if args.show_log:
        entries = lore.get_consolidation_log(limit=args.limit)
        lore.close()
        if not entries:
            print("No consolidation log entries.")
            return
        print(f"{'ID':<28} {'Strategy':<14} {'Originals':<10} {'Created':<22}")
        print("-" * 80)
        for e in entries:
            print(
                f"{e.id:<28} {e.strategy:<14} {e.original_count:<10} "
                f"{e.created_at[:19]}"
            )
        return

    dry_run = not args.execute
    result = asyncio.run(lore.consolidate(
        project=args.project,
        tier=args.tier,
        strategy=args.strategy,
        dry_run=dry_run,
    ))
    lore.close()

    prefix = "[dry-run] " if dry_run else ""
    print(f"{prefix}Groups found: {result.groups_found}")
    print(f"{prefix}Memories consolidated: {result.memories_consolidated}")
    print(f"{prefix}Memories created: {result.memories_created}")
    print(f"{prefix}Duplicates merged: {result.duplicates_merged}")

    if result.groups:
        print()
        for i, g in enumerate(result.groups, 1):
            strat = g.get("strategy", "?")
            count = g.get("memory_count", 0)
            line = f"  Group {i}: {count} memories (strategy: {strat})"
            if "similarity" in g:
                line += f" [similarity: {g['similarity']:.2f}]"
            if "entities" in g:
                line += f" [entities: {', '.join(g['entities'][:3])}]"
            print(line)
            preview = g.get("preview", "")
            print(f"    Preview: {preview[:120]}")

    if dry_run:
        print(f"\n{prefix}Run with --execute to apply changes.")
