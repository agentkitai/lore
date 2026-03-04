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


# ── New Memory Commands ──────────────────────────────────────────────


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


# ── Legacy Lesson Commands ───────────────────────────────────────────


def cmd_publish(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    tags: List[str] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    lid = lore.publish(
        problem=args.problem,
        resolution=args.resolution,
        context=args.context,
        tags=tags,
        confidence=args.confidence,
        source=args.source,
    )
    lore.close()
    print(lid)


def cmd_query(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    results = lore.query(args.text, limit=args.limit)
    lore.close()
    if not results:
        print("No results.")
        return
    for r in results:
        print(f"[{r.score:.3f}] {r.lesson.id}")
        print(f"  Problem:    {r.lesson.problem}")
        print(f"  Resolution: {r.lesson.resolution}")
        print()


def cmd_list(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    lessons = lore.list(limit=args.limit)
    lore.close()
    if not lessons:
        print("No lessons.")
        return
    print(f"{'ID':<28} {'Problem':<50} {'Resolution':<50}")
    print("-" * 80)
    for l in lessons:
        print(f"{l.id:<28} {l.problem[:50]:<50} {l.resolution[:50]:<50}")


def cmd_export(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    lessons = lore.export_lessons(path=args.output)
    lore.close()
    if args.output:
        print(f"Exported {len(lessons)} lessons to {args.output}")
    else:
        payload = {"version": 1, "lessons": lessons}
        print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_import(args: argparse.Namespace) -> None:
    lore = _get_lore(args)
    count = lore.import_lessons(path=args.file)
    lore.close()
    print(f"Imported {count} lessons from {args.file}")


# ── Remote API Commands ──────────────────────────────────────────────


def _get_api_config(args: argparse.Namespace) -> tuple:
    import os

    api_url = getattr(args, "api_url", None) or os.environ.get("LORE_API_URL")
    api_key = getattr(args, "api_key", None) or os.environ.get("LORE_API_KEY")
    if not api_url:
        print("Error: --api-url or LORE_API_URL required", file=sys.stderr)
        sys.exit(1)
    if not api_key:
        print("Error: --api-key or LORE_API_KEY required", file=sys.stderr)
        sys.exit(1)
    return api_url.rstrip("/"), api_key


def _api_request(
    method: str, url: str, api_key: str, json_data: Optional[dict] = None
) -> dict:
    import urllib.error
    import urllib.request

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = None
    if json_data is not None:
        data = json.dumps(json_data).encode()

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 204:
                return {}
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            err = json.loads(body)
            detail = err.get("detail", err.get("error", body))
        except (json.JSONDecodeError, ValueError):
            detail = body
        print(f"Error {e.code}: {detail}", file=sys.stderr)
        sys.exit(1)


def cmd_keys_create(args: argparse.Namespace) -> None:
    api_url, api_key = _get_api_config(args)
    payload: dict = {"name": args.name}
    if args.project:
        payload["project"] = args.project
    if getattr(args, "root", False):
        payload["is_root"] = True
    result = _api_request("POST", f"{api_url}/v1/keys", api_key, payload)
    print(f"Created key: {result['id']}")
    print(f"  Name:    {result['name']}")
    print(f"  Project: {result.get('project') or '(all)'}")
    print(f"  Key:     {result['key']}")
    print()
    print("Save this key now -- it will not be shown again.")


def cmd_keys_list(args: argparse.Namespace) -> None:
    api_url, api_key = _get_api_config(args)
    result = _api_request("GET", f"{api_url}/v1/keys", api_key)
    keys = result.get("keys", [])
    if not keys:
        print("No keys.")
        return
    print(f"{'ID':<28} {'Name':<20} {'Prefix':<14} {'Project':<15} {'Root':<6} {'Revoked'}")
    print("-" * 100)
    for k in keys:
        print(
            f"{k['id']:<28} {k['name']:<20} {k['key_prefix']:<14} "
            f"{(k.get('project') or '-'):<15} {'yes' if k['is_root'] else 'no':<6} "
            f"{'yes' if k['revoked'] else 'no'}"
        )


def cmd_keys_revoke(args: argparse.Namespace) -> None:
    api_url, api_key = _get_api_config(args)
    _api_request("DELETE", f"{api_url}/v1/keys/{args.key_id}", api_key)
    print(f"Key {args.key_id} revoked.")


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

    # ── New memory commands ──────────────────────────────────────

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

    # ── Legacy lesson commands ───────────────────────────────────

    # publish
    p = sub.add_parser("publish", help="Publish a new lesson (legacy)")
    p.add_argument("--problem", required=True)
    p.add_argument("--resolution", required=True)
    p.add_argument("--context", default=None)
    p.add_argument("--tags", default=None, help="Comma-separated tags")
    p.add_argument("--confidence", type=float, default=0.5)
    p.add_argument("--source", default=None)

    # query
    p = sub.add_parser("query", help="Query lessons (legacy)")
    p.add_argument("text", help="Search text")
    p.add_argument("--limit", type=int, default=5)

    # list
    p = sub.add_parser("list", help="List lessons (legacy)")
    p.add_argument("--limit", type=int, default=None)

    # export
    p = sub.add_parser("export", help="Export lessons to JSON")
    p.add_argument("-o", "--output", default=None, help="Output file path")

    # import
    p = sub.add_parser("import", help="Import lessons from JSON")
    p.add_argument("file", help="JSON file to import")

    # ── Remote commands ──────────────────────────────────────────

    # keys
    keys_parser = sub.add_parser("keys", help="Manage API keys (remote server)")
    keys_parser.add_argument("--api-url", default=None, help="Lore API URL (or LORE_API_URL)")
    keys_parser.add_argument("--api-key", default=None, help="Lore API key (or LORE_API_KEY)")
    keys_sub = keys_parser.add_subparsers(dest="keys_command")

    kc = keys_sub.add_parser("create", help="Create a new API key")
    kc.add_argument("--name", required=True, help="Key name")
    kc.add_argument("--project", default=None, help="Project scope (optional)")
    kc.add_argument("--root", action="store_true", help="Create a root key")

    keys_sub.add_parser("list", help="List all API keys")

    kr = keys_sub.add_parser("revoke", help="Revoke an API key")
    kr.add_argument("key_id", help="Key ID to revoke")

    # mcp
    sub.add_parser("mcp", help="Start MCP server (stdio transport)")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "keys":
        if not args.keys_command:
            parser.parse_args(["keys", "--help"])
            return
        keys_handlers = {
            "create": cmd_keys_create,
            "list": cmd_keys_list,
            "revoke": cmd_keys_revoke,
        }
        keys_handlers[args.keys_command](args)
        return

    handlers = {
        "remember": cmd_remember,
        "recall": cmd_recall,
        "forget": cmd_forget,
        "memories": cmd_memories,
        "stats": cmd_stats,
        "publish": cmd_publish,
        "query": cmd_query,
        "list": cmd_list,
        "export": cmd_export,
        "import": cmd_import,
        "mcp": cmd_mcp,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
