"""Minimal CLI for Lore SDK using argparse."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from typing import List, Optional, Sequence


def _get_lore(db: Optional[str] = None) -> "Lore":  # noqa: F821
    from lore import Lore

    kwargs = {}
    if db:
        kwargs["db_path"] = db
    return Lore(**kwargs)


def cmd_remember(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    tags: List[str] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    mid = lore.remember(
        content=args.content,
        type=args.type,
        context=getattr(args, "context", None),
        tags=tags,
        source=args.source,
        ttl=args.ttl,
        confidence=args.confidence,
    )
    lore.close()
    print(mid)


def cmd_recall(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    tags = None
    if getattr(args, "tags", None):
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    results = lore.recall(args.query, type=args.type, tags=tags, limit=args.limit)
    lore.close()
    if not results:
        print("No results.")
        return
    for r in results:
        print(f"[{r.score:.3f}] {r.memory.id} ({r.memory.type})")
        print(f"  {r.memory.content[:200]}")
        if r.memory.tags:
            print(f"  Tags: {', '.join(r.memory.tags)}")
        print()


def cmd_forget(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    if lore.forget(args.id):
        print(f"Forgotten: {args.id}")
    else:
        print(f"Not found: {args.id}")
    lore.close()


def cmd_memories(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    memories = lore.list_memories(type=args.type, limit=args.limit)
    lore.close()
    if not memories:
        print("No memories.")
        return
    print(f"{'ID':<28} {'Type':<12} {'Content':<60}")
    print("-" * 100)
    for m in memories:
        print(f"{m.id:<28} {m.type:<12} {m.content[:60]:<60}")


def cmd_stats(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    s = lore.stats()
    lore.close()
    print(f"Total: {s.total}")
    if s.by_type:
        for t, count in sorted(s.by_type.items()):
            print(f"  {t}: {count}")
    if s.oldest:
        print(f"Oldest: {s.oldest}")
        print(f"Newest: {s.newest}")


# ------------------------------------------------------------------
# Deprecated legacy commands
# ------------------------------------------------------------------

def cmd_publish(args: argparse.Namespace) -> None:
    print("Warning: 'publish' is deprecated, use 'remember' instead.", file=sys.stderr)
    lore = _get_lore(args.db)
    tags: List[str] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        mid = lore.publish(
            problem=args.problem,
            resolution=args.resolution,
            tags=tags,
            confidence=args.confidence,
        )
    lore.close()
    print(mid)


def cmd_query(args: argparse.Namespace) -> None:
    print("Warning: 'query' is deprecated, use 'recall' instead.", file=sys.stderr)
    lore = _get_lore(args.db)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        results = lore.query(args.text, limit=args.limit)
    lore.close()
    if not results:
        print("No results.")
        return
    for r in results:
        print(f"[{r.score:.3f}] {r.memory.id} ({r.memory.type})")
        print(f"  {r.memory.content[:200]}")
        print()


# ------------------------------------------------------------------
# API key management
# ------------------------------------------------------------------

def _get_api_config(args: argparse.Namespace) -> tuple:
    """Get API URL and key from args or env vars."""
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
    """Make an HTTP request to the Lore API."""
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lore",
        description="Lore SDK — cross-agent memory CLI",
    )
    parser.add_argument("--db", default=None, help="Path to SQLite database")

    sub = parser.add_subparsers(dest="command")

    # remember
    p = sub.add_parser("remember", help="Store a new memory")
    p.add_argument("content", help="The memory content")
    p.add_argument("--type", default="general", help="Memory type (general, lesson, fact, preference, context)")
    p.add_argument("--tags", default=None, help="Comma-separated tags")
    p.add_argument("--context", default=None, help="Additional context for the memory")
    p.add_argument("--ttl", type=int, default=None, help="Time-to-live in seconds")
    p.add_argument("--source", default=None)
    p.add_argument("--confidence", type=float, default=1.0)

    # recall
    p = sub.add_parser("recall", help="Search memories")
    p.add_argument("query", help="Search query")
    p.add_argument("--type", default=None, help="Filter by memory type")
    p.add_argument("--tags", default=None, help="Comma-separated tags to filter by")
    p.add_argument("--limit", type=int, default=5)

    # forget
    p = sub.add_parser("forget", help="Delete a memory")
    p.add_argument("id", help="Memory ID to delete")

    # memories (list)
    p = sub.add_parser("memories", help="List memories")
    p.add_argument("--type", default=None, help="Filter by memory type")
    p.add_argument("--limit", type=int, default=None)

    # stats
    sub.add_parser("stats", help="Show memory statistics")

    # --- Deprecated legacy commands ---
    p = sub.add_parser("publish", help="(deprecated) Use 'remember' instead")
    p.add_argument("problem", help="What went wrong")
    p.add_argument("resolution", help="How to fix it")
    p.add_argument("--tags", default=None, help="Comma-separated tags")
    p.add_argument("--confidence", type=float, default=0.5)

    p = sub.add_parser("query", help="(deprecated) Use 'recall' instead")
    p.add_argument("text", help="Search query")
    p.add_argument("--limit", type=int, default=5)

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
        "mcp": cmd_mcp,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
