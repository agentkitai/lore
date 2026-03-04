"""CLI for Lore SDK — universal AI memory layer."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import List, Optional, Sequence


def _get_lore(args: argparse.Namespace) -> "Lore":  # noqa: F821
    from lore import Lore

    kwargs: dict = {"redact": False}
    db = getattr(args, "db", None)
    project = getattr(args, "project", None)
    if db:
        kwargs["db_path"] = db
    if project:
        kwargs["project"] = project
    return Lore(**kwargs)


# ── Memory Commands ──────────────────────────────────────────────────


def cmd_remember(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    tags: List[str] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    memory = lore.remember(
        content=args.content,
        type=args.type,
        tags=tags or None,
        source=args.source,
        ttl=args.ttl,
    )
    lore.close()
    if args.json:
        print(json.dumps({"id": memory.id}))
    else:
        print(f"Memory saved (ID: {memory.id})")


def cmd_recall(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    results = lore.recall(
        query_text=args.query,
        type=args.type,
        limit=args.limit,
    )
    lore.close()

    if args.json:
        out = []
        for r in results:
            d = asdict(r.memory)
            d.pop("embedding", None)
            out.append({"memory": d, "score": round(r.score, 4)})
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    if not results:
        print("No relevant memories found.")
        return

    for r in results:
        m = r.memory
        tag_str = ", ".join(m.tags) if m.tags else ""
        print(f"[{r.score:.3f}] {m.id}")
        print(f"  Type:    {m.type}")
        print(f"  Content: {m.content[:120]}{'...' if len(m.content) > 120 else ''}")
        if tag_str:
            print(f"  Tags:    {tag_str}")
        if m.project:
            print(f"  Project: {m.project}")
        print()


def cmd_forget(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    count = lore.forget(
        id=args.id if hasattr(args, "id") and args.id else None,
        type=args.type,
    )
    lore.close()
    if args.json:
        print(json.dumps({"deleted": count}))
    else:
        print(f"Deleted {count} memory(ies).")


def cmd_memories(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    memories, total = lore.list_memories(
        type=args.type,
        limit=args.limit,
        offset=args.offset,
        include_expired=getattr(args, "include_expired", False),
    )
    lore.close()

    if args.json:
        out = []
        for m in memories:
            d = asdict(m)
            d.pop("embedding", None)
            out.append(d)
        print(json.dumps({"memories": out, "total": total}, indent=2, ensure_ascii=False))
        return

    if not memories:
        print("No memories found.")
        return

    print(f"Showing {len(memories)} of {total} memories:\n")
    for m in memories:
        tag_str = f" [{', '.join(m.tags)}]" if m.tags else ""
        created = m.created_at[:10] if m.created_at else ""
        content = m.content[:80] + ("..." if len(m.content) > 80 else "")
        print(f"  {m.id[:12]}... ({m.type}) {content}{tag_str}  {created}")


def cmd_stats(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    s = lore.memory_stats()
    lore.close()

    if args.json:
        print(json.dumps(asdict(s), indent=2, ensure_ascii=False))
        return

    print(f"Total memories: {s.total_count}")
    if s.count_by_type:
        parts = [f"{k} ({v})" for k, v in s.count_by_type.items()]
        print(f"By type: {', '.join(parts)}")
    if s.count_by_project:
        parts = [f"{k} ({v})" for k, v in s.count_by_project.items()]
        print(f"By project: {', '.join(parts)}")
    oldest = s.oldest_memory[:10] if s.oldest_memory else "N/A"
    newest = s.newest_memory[:10] if s.newest_memory else "N/A"
    print(f"Date range: {oldest} to {newest}")


def cmd_mcp(args: argparse.Namespace) -> None:
    try:
        from lore.mcp.server import run_server
    except ImportError:
        print(
            "Error: MCP dependencies not installed.\n"
            "Install with: pip install lore-sdk[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)
    run_server()


# ── Parser ───────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lore",
        description="Lore — universal AI memory layer CLI",
    )
    parser.add_argument("--db", default=None, help="Path to SQLite database")
    parser.add_argument("--project", default=None, help="Default project scope")

    sub = parser.add_subparsers(dest="command")

    # remember
    p = sub.add_parser("remember", help="Store a memory")
    p.add_argument("content", help="Memory content")
    p.add_argument("--type", default="note", help="Memory type (note, lesson, snippet, etc.)")
    p.add_argument("--tags", default=None, help="Comma-separated tags")
    p.add_argument("--source", default=None, help="Source identifier")
    p.add_argument("--ttl", default=None, help="Time-to-live (e.g. 7d, 1h, 30m)")
    p.add_argument("--json", action="store_true", help="JSON output")

    # recall
    p = sub.add_parser("recall", help="Search memories by meaning")
    p.add_argument("query", help="Search query")
    p.add_argument("--type", default=None, help="Filter by memory type")
    p.add_argument("--limit", type=int, default=5, help="Max results")
    p.add_argument("--json", action="store_true", help="JSON output")

    # forget
    p = sub.add_parser("forget", help="Delete memories")
    p.add_argument("id", nargs="?", default=None, help="Memory ID to delete")
    p.add_argument("--type", default=None, help="Delete by type")
    p.add_argument("--json", action="store_true", help="JSON output")

    # memories (list memories)
    p = sub.add_parser("memories", help="List memories")
    p.add_argument("--type", default=None, help="Filter by type")
    p.add_argument("--limit", type=int, default=20, help="Max results")
    p.add_argument("--offset", type=int, default=0, help="Offset for pagination")
    p.add_argument("--include-expired", action="store_true", help="Include expired memories")
    p.add_argument("--json", action="store_true", help="JSON output")

    # stats
    p = sub.add_parser("stats", help="Memory store statistics")
    p.add_argument("--json", action="store_true", help="JSON output")

    # mcp
    sub.add_parser("mcp", help="Start MCP server (stdio transport)")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "remember": cmd_remember,
        "recall": cmd_recall,
        "forget": cmd_forget,
        "memories": cmd_memories,
        "stats": cmd_stats,
        "mcp": cmd_mcp,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
