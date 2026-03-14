"""Minimal CLI for Lore SDK using argparse."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Sequence


def _get_lore(db: Optional[str] = None) -> "Lore":  # noqa: F821
    from lore import Lore

    kwargs = {}
    if db:
        kwargs["db_path"] = db
    return Lore(**kwargs)


def cmd_remember(args: argparse.Namespace) -> None:
    from lore.exceptions import SecretBlockedError

    lore = _get_lore(args.db)
    tags: List[str] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    metadata = None
    if args.metadata:
        metadata = json.loads(args.metadata)
    try:
        mid = lore.remember(
            content=args.content,
            type=args.type,
            tier=args.tier,
            context=getattr(args, "context", None),
            tags=tags,
            metadata=metadata,
            source=args.source,
            project=args.project,
            ttl=args.ttl,
            confidence=args.confidence,
        )
    except SecretBlockedError as exc:
        lore.close()
        print(f"Blocked: {exc.finding_type} detected — remove the secret and retry.", file=sys.stderr)
        sys.exit(1)
    lore.close()
    print(mid)


def cmd_recall(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
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


def cmd_forget(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    if lore.forget(args.id):
        print(f"Forgotten: {args.id}")
    else:
        print(f"Not found: {args.id}")
    lore.close()


def cmd_memories(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
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
    lore = _get_lore(args.db)
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
    lore = _get_lore(args.db)
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
    p.add_argument(
        "--tier", choices=["working", "short", "long"], default="long",
        help="Memory tier: working (1h), short (7d), long (permanent)",
    )
    p.add_argument("--tags", default=None, help="Comma-separated tags")
    p.add_argument("--context", default=None, help="Additional context for the memory")
    p.add_argument("--ttl", type=int, default=None, help="Time-to-live in seconds")
    p.add_argument("--source", default=None)
    p.add_argument("--confidence", type=float, default=1.0)
    p.add_argument("--project", default=None, help="Project namespace")
    p.add_argument("--metadata", default=None, help="JSON metadata (e.g. '{\"key\": \"val\"}')")

    # recall
    p = sub.add_parser("recall", help="Search memories")
    p.add_argument("query", help="Search query")
    p.add_argument("--type", default=None, help="Filter by memory type")
    p.add_argument(
        "--tier", choices=["working", "short", "long"], default=None,
        help="Filter by memory tier",
    )
    p.add_argument("--tags", default=None, help="Comma-separated tags to filter by")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--topic", default=None, help="Filter by enrichment topic")
    p.add_argument(
        "--sentiment", default=None, choices=["positive", "negative", "neutral"],
        help="Filter by sentiment label",
    )
    p.add_argument("--entity", default=None, help="Filter by entity name")
    p.add_argument("--category", default=None, help="Filter by category")
    p.add_argument("--offset", type=int, default=0, help="Number of results to skip (pagination)")
    p.add_argument(
        "--verbatim", "-v", action="store_true", default=False,
        help="Return raw original content with metadata",
    )
    # Temporal filters
    p.add_argument("--year", type=int, default=None, help="Filter by year (e.g. 2024)")
    p.add_argument("--month", type=int, default=None, help="Filter by month (1-12)")
    p.add_argument("--day", type=int, default=None, help="Filter by day (1-31)")
    p.add_argument(
        "--days-ago", type=int, default=None, dest="days_ago",
        help="Filter to last N days (0 = today only)",
    )
    p.add_argument(
        "--hours-ago", type=int, default=None, dest="hours_ago",
        help="Filter to last N hours",
    )
    p.add_argument(
        "--window", default=None,
        choices=["today", "last_hour", "last_day", "last_week", "last_month", "last_year"],
        help="Preset time window",
    )
    p.add_argument("--before", default=None, help="ISO 8601 exclusive upper bound")
    p.add_argument("--after", default=None, help="ISO 8601 inclusive lower bound")
    p.add_argument(
        "--date-from", default=None, dest="date_from",
        help="ISO 8601 range start (inclusive)",
    )
    p.add_argument(
        "--date-to", default=None, dest="date_to",
        help="ISO 8601 range end (inclusive)",
    )

    # forget
    p = sub.add_parser("forget", help="Delete a memory")
    p.add_argument("id", help="Memory ID to delete")

    # memories (list)
    p = sub.add_parser("memories", help="List memories")
    p.add_argument("--type", default=None, help="Filter by memory type")
    p.add_argument(
        "--tier", choices=["working", "short", "long"], default=None,
        help="Filter by memory tier",
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument(
        "--sort", type=str, choices=["created", "importance"],
        default="created", help="Sort order (default: created)",
    )

    # stats
    sub.add_parser("stats", help="Show memory statistics")

    # recent
    recent_parser = sub.add_parser("recent", help="Show recent activity summary")
    recent_parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default: 24)")
    recent_parser.add_argument("--project", default=None, help="Filter to specific project")
    recent_parser.add_argument("--format", default="brief", choices=["brief", "detailed"], help="Output format (default: brief)")

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

    # prompt
    p = sub.add_parser("prompt", help="Export memories formatted for LLM prompts")
    p.add_argument("query", help="Search query")
    p.add_argument("--format", default="xml", choices=["xml", "chatml", "markdown", "raw"])
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--max-chars", type=int, default=None)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--type", default=None)
    p.add_argument("--tags", default=None, help="Comma-separated tags")
    p.add_argument("--min-score", type=float, default=0.0)
    p.add_argument("--include-metadata", action="store_true", default=False)
    p.add_argument("--project", default=None, help="Project namespace")

    # freshness
    p = sub.add_parser("freshness", help="Check memories for staleness against git history")
    p.add_argument("--repo", default=".", help="Path to git repository (default: .)")
    p.add_argument("--project", default=None, help="Filter to specific project")
    p.add_argument(
        "--format", choices=["table", "json"], default="table",
        help="Output format (default: table)",
    )
    p.add_argument(
        "--min-staleness",
        choices=["possibly_stale", "likely_stale", "stale"],
        default=None, dest="min_staleness",
        help="Only show results at or above this staleness level",
    )
    p.add_argument(
        "--auto-tag", action="store_true", default=False, dest="auto_tag",
        help="Add 'stale' tag to memories with stale status",
    )

    # github-sync
    gs = sub.add_parser("github-sync", help="Sync GitHub repo data as memories")
    gs.add_argument("--repo", required=False, help="GitHub owner/repo (e.g. octocat/Hello-World)")
    gs.add_argument(
        "--types",
        default=None,
        help="Comma-separated entity types to sync (prs,issues,commits,releases). Default: all",
    )
    gs.add_argument("--since", default=None, help="ISO-8601 date to start sync from")
    gs.add_argument("--full", action="store_true", help="Ignore saved state and do a full re-sync")
    gs.add_argument("--dry-run", action="store_true", help="Show what would be synced without storing")
    gs.add_argument("--list", action="store_true", dest="list_repos", help="List all synced repos")
    gs.add_argument("--project", default=None, help="Project namespace for synced memories")

    # reindex
    p = sub.add_parser("reindex", help="Re-embed memories with current embedding model")
    p.add_argument(
        "--dual", action="store_true", default=False,
        help="Use dual embedding (code + prose models)",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=False, dest="dry_run",
        help="Show what would change without modifying data",
    )

    # enrich
    p = sub.add_parser("enrich", help="Enrich memories with LLM-extracted metadata")
    p.add_argument("memory_id", nargs="?", default=None, help="Memory ID to enrich")
    p.add_argument("--all", action="store_true", help="Enrich all unenriched memories")
    p.add_argument("--project", default=None, help="Filter to project (with --all)")
    p.add_argument("--force", action="store_true", help="Re-enrich already enriched memories")
    p.add_argument(
        "--model", default=None,
        help="LLM model for enrichment (default: gpt-4o-mini)",
    )

    # classify
    p = sub.add_parser("classify", help="Classify text by intent, domain, and emotion")
    p.add_argument("text", help="Text to classify")
    p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    # facts
    p = sub.add_parser("facts", help="Show facts for a memory or list active facts")
    p.add_argument("memory_id", nargs="?", default=None, help="Memory ID (omit to list all active facts)")
    p.add_argument("--subject", default=None, help="Filter by subject")
    p.add_argument("--limit", type=int, default=50)

    # conflicts
    p = sub.add_parser("conflicts", help="Show conflict log")
    p.add_argument("--resolution", default=None, help="Filter by resolution (SUPERSEDE, MERGE, CONTRADICT)")
    p.add_argument("--limit", type=int, default=20)

    # backfill-facts
    p = sub.add_parser("backfill-facts", help="Extract facts from existing memories")
    p.add_argument("--project", default=None, help="Filter to project")
    p.add_argument("--limit", type=int, default=100)

    # graph
    p = sub.add_parser("graph", help="Traverse knowledge graph from an entity")
    p.add_argument("entity", help="Entity name to start traversal from")
    p.add_argument("--depth", type=int, default=2, help="Traversal depth (1-3)")
    p.add_argument("--type", dest="rel_type", default=None, help="Filter by relationship type")
    p.add_argument("--direction", choices=["outbound", "inbound", "both"], default="both")
    p.add_argument("--min-weight", type=float, default=0.1, dest="min_weight")
    p.add_argument("--format", choices=["text", "json"], default="text")

    # entities
    p = sub.add_parser("entities", help="List entities in the knowledge graph")
    p.add_argument("--type", dest="entity_type", default=None, help="Filter by entity type")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--sort", choices=["mentions", "name", "created"], default="mentions")

    # relationships
    p = sub.add_parser("relationships", help="List relationships in the knowledge graph")
    p.add_argument("--entity", default=None, help="Filter by entity name")
    p.add_argument("--type", dest="rel_type", default=None, help="Filter by relationship type")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--include-expired", action="store_true", dest="include_expired")

    # graph-backfill
    p = sub.add_parser("graph-backfill", help="Build graph from existing memories")
    p.add_argument("--project", default=None, help="Filter to project")
    p.add_argument("--limit", type=int, default=1000)

    # ingest
    p = sub.add_parser("ingest", help="Ingest content with source tracking")
    p.add_argument("content", nargs="?", default=None, help="Content to ingest (or use --file)")
    p.add_argument("--source", default="manual", help="Source adapter name (default: manual)")
    p.add_argument("--file", default=None, dest="file_path", help="File to import (JSON array or text lines)")
    p.add_argument("--user", default=None, help="Source user identity")
    p.add_argument("--channel", default=None, help="Source channel/location")
    p.add_argument("--type", default="general", help="Memory type")
    p.add_argument("--tags", default=None, help="Comma-separated tags")
    p.add_argument("--project", default=None, help="Project namespace")
    p.add_argument(
        "--dedup-mode", default="reject", dest="dedup_mode",
        choices=["reject", "skip", "merge", "allow"],
        help="Deduplication mode (default: reject)",
    )
    p.add_argument("--no-enrich", action="store_true", dest="no_enrich", help="Disable enrichment")

    # consolidate
    p = sub.add_parser("consolidate", help="Run memory consolidation pipeline")
    p.add_argument("--dry-run", action="store_true", default=True, dest="dry_run",
                    help="Preview consolidation without changes (default)")
    p.add_argument("--execute", action="store_true", help="Run consolidation and apply changes")
    p.add_argument("--project", default=None, help="Filter to a specific project")
    p.add_argument("--tier", choices=["working", "short", "long"], default=None,
                    help="Filter to a specific tier")
    p.add_argument("--strategy", choices=["deduplicate", "summarize", "all"],
                    default="all", help="Consolidation strategy (default: all)")
    p.add_argument("--log", action="store_true", dest="show_log",
                    help="Show consolidation history instead of running consolidation")
    p.add_argument("--limit", type=int, default=10, help="Number of log entries to show")

    # on-this-day
    p = sub.add_parser("on-this-day", help="Show memories from this day in past years")
    p.add_argument("--month", type=int, default=None, help="Month (1-12, default: today)")
    p.add_argument("--day", type=int, default=None, help="Day (1-31, default: today)")
    p.add_argument("--project", default=None, help="Filter by project")
    p.add_argument(
        "--tier", choices=["working", "short", "long"], default=None,
        help="Filter by memory tier",
    )
    p.add_argument("--limit", type=int, default=None, help="Max memories to return")
    p.add_argument("--offset", type=int, default=0, help="Skip N memories (pagination)")
    p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    # add-conversation
    p_conv = sub.add_parser("add-conversation", help="Extract memories from conversation")
    p_conv.add_argument("--file", "-f", help="Path to JSON file with messages")
    p_conv.add_argument("--user-id", dest="user_id", help="Scope extracted memories to this user")
    p_conv.add_argument("--session-id", dest="session_id", help="Session identifier for tracking")
    p_conv.add_argument("--project", "-p", help="Project scope")

    # wrap
    p_wrap = sub.add_parser(
        "wrap",
        help="Wrap a CLI command and capture conversation for memory extraction",
    )
    p_wrap.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to wrap (e.g. claude, codex)")
    p_wrap.add_argument("--api-url", dest="api_url", default=None, help="Lore API URL (or LORE_API_URL)")
    p_wrap.add_argument("--api-key", dest="api_key", default=None, help="Lore API key (or LORE_API_KEY)")
    p_wrap.add_argument("--user-id", dest="user_id", default=None, help="User ID for extracted memories")
    p_wrap.add_argument("--project", "-p", default=None, help="Project scope")

    # setup
    p_setup = sub.add_parser("setup", help="Install Lore hooks for a runtime")
    p_setup.add_argument(
        "runtime", nargs="?", default=None,
        choices=["claude-code", "openclaw", "cursor", "codex"],
        help="Runtime to configure",
    )
    p_setup.add_argument("--server-url", default=None, dest="server_url",
                         help="Lore server URL (default: http://localhost:8765)")
    p_setup.add_argument("--api-key", default=None, dest="api_key",
                         help="Lore API key (or LORE_API_KEY)")
    p_setup.add_argument("--status", action="store_true",
                         help="Show current setup status for all runtimes")
    p_setup.add_argument("--remove", default=None, metavar="RUNTIME",
                         choices=["claude-code", "openclaw", "cursor", "codex"],
                         help="Remove hooks for a runtime")

    # export
    p = sub.add_parser("export", help="Export memories and knowledge graph")
    p.add_argument(
        "--format", choices=["json", "markdown", "both"], default="json",
        help="Export format (default: json)",
    )
    p.add_argument("--output", "-o", default=None, help="Output file/directory path")
    p.add_argument("--project", default=None, help="Filter by project")
    p.add_argument("--type", default=None, help="Filter by memory type")
    p.add_argument("--tier", choices=["working", "short", "long"], default=None, help="Filter by tier")
    p.add_argument("--since", default=None, help="Only memories created after DATE (ISO 8601)")
    p.add_argument("--include-embeddings", action="store_true", default=False, dest="include_embeddings",
                    help="Include raw embedding vectors (base64)")
    p.add_argument("--pretty", action="store_true", default=False, help="Pretty-print JSON")

    # import (use "import-data" because "import" is a Python keyword)
    p = sub.add_parser("import", help="Import from a JSON export file")
    p.add_argument("file", help="Path to JSON export file")
    p.add_argument("--overwrite", action="store_true", default=False, help="Replace existing memories on ID conflict")
    p.add_argument("--dry-run", action="store_true", default=False, dest="dry_run",
                    help="Show what would be imported, don't write")
    p.add_argument("--project", default=None, help="Override project for all imported memories")
    p.add_argument("--skip-embeddings", action="store_true", default=False, dest="skip_embeddings",
                    help="Don't regenerate embeddings after import")
    p.add_argument("--redact", action="store_true", default=False, help="Re-run PII redaction on imported content")

    # snapshot
    p = sub.add_parser("snapshot", help="Quick snapshot and restore")
    p.add_argument("--list", action="store_true", dest="list_snapshots", help="List available snapshots")
    p.add_argument("--restore", default=None, nargs="?", const="__prompt__",
                    help="Restore from named snapshot")
    p.add_argument("--latest", action="store_true", default=False, help="Use most recent snapshot (with --restore)")
    p.add_argument("--delete", default=None, help="Delete a specific snapshot")
    p.add_argument("--older-than", default=None, dest="older_than",
                    help="Delete snapshots older than duration (e.g. 30d, 4w)")
    p.add_argument("--yes", "-y", action="store_true", default=False, help="Skip confirmation on restore")
    p.add_argument("--max-snapshots", type=int, default=50, dest="max_snapshots",
                    help="Maximum snapshots to retain (default: 50)")

    # serve
    p_serve = sub.add_parser("serve", help="Start Lore HTTP server")
    p_serve.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=None, help="Port (default: $LORE_PORT or 8765)")

    # mcp
    sub.add_parser("mcp", help="Start MCP server (stdio transport)")

    return parser


def cmd_add_conversation(args: argparse.Namespace) -> None:
    """Handle add-conversation subcommand."""
    # Read messages from file or stdin
    if args.file:
        with open(args.file, "r") as f:
            data = json.load(f)
    elif not sys.stdin.isatty():
        data = json.load(sys.stdin)
    else:
        print("Error: provide --file or pipe JSON to stdin", file=sys.stderr)
        sys.exit(1)

    # Accept both {"messages": [...]} and bare [...]
    if isinstance(data, list):
        messages = data
    elif isinstance(data, dict) and "messages" in data:
        messages = data["messages"]
    else:
        print('Error: JSON must be a list or {"messages": [...]}', file=sys.stderr)
        sys.exit(1)

    lore = _get_lore(args.db)
    try:
        result = lore.add_conversation(
            messages=messages,
            user_id=getattr(args, "user_id", None),
            session_id=getattr(args, "session_id", None),
            project=getattr(args, "project", None),
        )
    except (RuntimeError, ValueError) as exc:
        lore.close()
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    lore.close()

    print(f"Accepted {result.message_count} messages for extraction.")
    print(f"Extracted {result.memories_extracted} memories, skipped {result.duplicates_skipped} duplicates.")
    if result.memory_ids:
        print(f"Memory IDs: {', '.join(result.memory_ids)}")

    # Cost estimation
    transcript_words = sum(len(m.get("content", "").split()) for m in messages)
    est_tokens = int(transcript_words / 0.75)
    est_cost = est_tokens * 0.15 / 1_000_000  # gpt-4o-mini pricing
    model_name = "unknown"
    if hasattr(lore, '_enrichment_pipeline') and lore._enrichment_pipeline:
        model_name = lore._enrichment_pipeline.llm.model
    print(f"Estimated cost: ~${est_cost:.3f} ({est_tokens} tokens, {model_name})")


def cmd_prompt(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
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


def cmd_github_sync(args: argparse.Namespace) -> None:
    from lore.github.state import list_synced_repos
    from lore.github.syncer import GitHubCLIError, GitHubSyncer

    # --list mode
    if args.list_repos:
        repos = list_synced_repos()
        if not repos:
            print("No synced repos.")
            return
        print(f"{'Repo':<40} {'Last Sync'}")
        print("-" * 70)
        for repo, state in sorted(repos.items()):
            print(f"{repo:<40} {state.get('last_sync', 'unknown')}")
        return

    if not args.repo:
        print("Error: --repo is required (unless using --list)", file=sys.stderr)
        sys.exit(1)

    lore = _get_lore(args.db)
    syncer = GitHubSyncer(lore)

    types = None
    if args.types:
        types = [t.strip() for t in args.types.split(",") if t.strip()]

    try:
        result = syncer.sync(
            args.repo,
            types=types,
            since=args.since,
            full=args.full,
            dry_run=args.dry_run,
            project=args.project,
        )
    except GitHubCLIError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        lore.close()
        sys.exit(1)

    lore.close()
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}{result.summary()}")
    if result.errors:
        sys.exit(1)


def cmd_freshness(args: argparse.Namespace) -> None:
    from lore.freshness.detector import FreshnessDetector
    from lore.freshness.git_ops import GitError

    try:
        FreshnessDetector.validate_repo(args.repo)
    except GitError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    lore = _get_lore(args.db)
    memories = lore.list_memories(project=args.project)
    lore.close()

    if not memories:
        print("No memories to check.")
        return

    detector = FreshnessDetector(args.repo)
    results = detector.check_many(memories)

    # Filter by min-staleness
    status_order = ["fresh", "possibly_stale", "likely_stale", "stale"]
    if args.min_staleness:
        min_idx = status_order.index(args.min_staleness)
        results = [
            r for r in results
            if r.status != "unknown"
            and status_order.index(r.status) >= min_idx
        ]

    if args.format == "json":
        import dataclasses
        print(json.dumps([dataclasses.asdict(r) for r in results], indent=2))
    else:
        print(FreshnessDetector.format_report(results, args.repo))

    # Auto-tag stale memories
    if args.auto_tag:
        lore = _get_lore(args.db)
        tagged = 0
        for r in results:
            if r.status == "stale":
                mem = lore.get(r.memory_id)
                if mem and "stale" not in mem.tags:
                    mem.tags.append("stale")
                    from datetime import datetime, timezone
                    mem.updated_at = datetime.now(timezone.utc).isoformat()
                    lore._store.update(mem)
                    tagged += 1
        lore.close()
        if tagged:
            print(f"\nTagged {tagged} memory(ies) as stale.")

    # Exit code: 1 if any stale found
    has_stale = any(r.status == "stale" for r in results)
    if has_stale:
        sys.exit(1)


def cmd_reindex(args: argparse.Namespace) -> None:
    from lore import Lore

    kwargs: dict = {}
    if args.db:
        kwargs["db_path"] = args.db
    if args.dual:
        kwargs["dual_embedding"] = True

    lore = Lore(**kwargs)
    total_memories = len(lore.list_memories())
    if total_memories == 0:
        print("No memories to reindex.")
        lore.close()
        return

    def progress(done: int, total: int) -> None:
        if sys.stderr.isatty():
            pct = done * 100 // total
            sys.stderr.write(f"\rReindexing: {done}/{total} ({pct}%)")
            sys.stderr.flush()

    updated = lore.reindex(dry_run=args.dry_run, progress_fn=progress)
    lore.close()

    if sys.stderr.isatty():
        sys.stderr.write("\n")

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}Reindexed {updated}/{total_memories} memories.")


def cmd_enrich(args: argparse.Namespace) -> None:
    import os

    from lore import Lore

    model = args.model or os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")
    kwargs = {"enrichment": True, "enrichment_model": model}
    if args.db:
        kwargs["db_path"] = args.db
    lore = Lore(**kwargs)

    if args.memory_id:
        result = lore.enrich_memories(memory_ids=[args.memory_id], force=args.force)
    elif getattr(args, "all", False):
        result = lore.enrich_memories(project=args.project, force=args.force)
    else:
        print("Provide a memory ID or use --all", file=sys.stderr)
        lore.close()
        sys.exit(1)

    lore.close()
    print(
        f"Enriched: {result['enriched']}, "
        f"Skipped: {result['skipped']}, "
        f"Failed: {result['failed']}"
    )
    if result["errors"]:
        for err in result["errors"]:
            print(f"  Error: {err}", file=sys.stderr)


def cmd_classify(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    result = lore.classify(args.text)
    lore.close()
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        intent_pct = result.confidence.get("intent", 0) * 100
        domain_pct = result.confidence.get("domain", 0) * 100
        emotion_pct = result.confidence.get("emotion", 0) * 100
        print(f"Intent:   {result.intent:<12} ({intent_pct:.0f}%)")
        print(f"Domain:   {result.domain:<12} ({domain_pct:.0f}%)")
        print(f"Emotion:  {result.emotion:<12} ({emotion_pct:.0f}%)")


def cmd_facts(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    if args.memory_id:
        facts = lore.get_facts(args.memory_id)
        lore.close()
        if not facts:
            print(f"No facts for memory {args.memory_id}.")
            return
        print(f"Facts for memory {args.memory_id}:\n")
        print(f"{'Subject':<20} {'Predicate':<20} {'Object':<30} {'Confidence':<12} {'Status'}")
        print("-" * 95)
        for f in facts:
            status = "invalidated" if f.invalidated_by else "active"
            print(
                f"{f.subject:<20} {f.predicate:<20} {f.object:<30} "
                f"{f.confidence:<12.2f} {status}"
            )
    else:
        facts = lore.get_active_facts(subject=args.subject, limit=args.limit)
        lore.close()
        if not facts:
            print("No active facts found.")
            return
        filter_msg = f" (filtered by subject: {args.subject})" if args.subject else ""
        print(f"Active facts{filter_msg}:\n")
        print(f"{'Subject':<20} {'Predicate':<20} {'Object':<30} {'Confidence':<12} {'Source Memory'}")
        print("-" * 105)
        for f in facts:
            print(
                f"{f.subject:<20} {f.predicate:<20} {f.object:<30} "
                f"{f.confidence:<12.2f} {f.memory_id[:12]}..."
            )


def cmd_conflicts(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    entries = lore.list_conflicts(resolution=args.resolution, limit=args.limit)
    lore.close()
    if not entries:
        print("No conflicts found.")
        return
    for i, c in enumerate(entries, 1):
        print(f"{i}. [{c.resolution}] {c.subject}/{c.predicate}: \"{c.old_value}\" -> \"{c.new_value}\"")
        print(f"   Memory: {c.new_memory_id[:12]}... ({c.resolved_at[:10]})")
        reasoning = (c.metadata or {}).get("reasoning", "")
        if reasoning:
            print(f"   Reason: {reasoning}")
        print()


def cmd_backfill_facts(args: argparse.Namespace) -> None:

    lore = _get_lore(args.db)
    if not lore._fact_extraction_enabled:
        lore.close()
        print(
            "Error: Fact extraction not enabled. "
            "Configure llm_provider, llm_api_key, and set fact_extraction=True.",
            file=sys.stderr,
        )
        sys.exit(1)

    count = lore.backfill_facts(project=args.project, limit=args.limit)
    lore.close()
    print(f"Extracted {count} fact(s) from existing memories.")


def cmd_graph(args: argparse.Namespace) -> None:
    from lore import Lore

    lore = Lore(db_path=args.db, knowledge_graph=True) if args.db else Lore(knowledge_graph=True)
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

    lore = Lore(db_path=args.db, knowledge_graph=True) if args.db else Lore(knowledge_graph=True)
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

    lore = Lore(db_path=args.db, knowledge_graph=True) if args.db else Lore(knowledge_graph=True)

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

    lore = Lore(db_path=args.db, knowledge_graph=True) if args.db else Lore(knowledge_graph=True)
    count = lore.graph_backfill(project=args.project, limit=args.limit)
    lore.close()
    print(f"Processed {count} memory(ies) into the knowledge graph.")


def cmd_ingest(args: argparse.Namespace) -> None:
    from lore.ingest.adapters.raw import RawAdapter
    from lore.ingest.dedup import Deduplicator
    from lore.ingest.pipeline import IngestionPipeline

    lore = _get_lore(args.db)
    tags: List[str] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    source = args.source
    dedup_mode = args.dedup_mode
    enrich = not args.no_enrich

    adapter = RawAdapter()
    adapter.adapter_name = source  # override adapter name to match --source
    deduplicator = Deduplicator(store=lore._store, embedder=lore._embedder)
    pipeline = IngestionPipeline(
        lore=lore,
        deduplicator=deduplicator,
        default_dedup_mode=dedup_mode,
        auto_enrich=enrich,
    )

    def _ingest_one(content: str, user=None, channel=None) -> str:
        payload = {
            "content": content,
            "user": user or args.user,
            "channel": channel or args.channel,
            "type": args.type,
            "tags": tags,
        }
        result = pipeline.ingest(
            adapter=adapter,
            payload=payload,
            project=args.project,
            dedup_mode=dedup_mode,
            enrich=enrich,
        )
        if result.status == "ingested":
            return result.memory_id
        elif result.status.startswith("duplicate"):
            raise RuntimeError(f"Duplicate detected ({result.dedup_strategy}): {result.duplicate_of}")
        else:
            raise RuntimeError(result.error or result.status)

    if args.file_path:
        import os

        if not os.path.exists(args.file_path):
            print(f"Error: File not found: {args.file_path}", file=sys.stderr)
            lore.close()
            sys.exit(1)

        with open(args.file_path, "r") as f:
            raw = f.read()

        # Try JSON array first
        items = None
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                items = data
        except (json.JSONDecodeError, ValueError):
            pass

        if items is not None:
            ingested = 0
            failed = 0
            for i, item in enumerate(items):
                if isinstance(item, dict):
                    content = item.get("content", "")
                    user = item.get("user", args.user)
                    channel = item.get("channel", args.channel)
                else:
                    content = str(item)
                    user = args.user
                    channel = args.channel

                if not content.strip():
                    failed += 1
                    continue
                try:
                    mid = _ingest_one(content, user, channel)
                    print(f"[{i}] Ingested: {mid}")
                    ingested += 1
                except Exception as e:
                    print(f"[{i}] Failed: {e}", file=sys.stderr)
                    failed += 1
            print(f"\nTotal: {ingested} ingested, {failed} failed")
        else:
            # Treat as newline-delimited text
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            if not lines:
                print("No content found in file.", file=sys.stderr)
                lore.close()
                sys.exit(1)
            ingested = 0
            for i, line in enumerate(lines):
                try:
                    mid = _ingest_one(line)
                    print(f"[{i}] Ingested: {mid}")
                    ingested += 1
                except Exception as e:
                    print(f"[{i}] Failed: {e}", file=sys.stderr)
            print(f"\nTotal: {ingested} ingested")
    elif args.content:
        try:
            mid = _ingest_one(args.content)
            print(mid)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            lore.close()
            sys.exit(1)
    else:
        print("Provide content or use --file", file=sys.stderr)
        lore.close()
        sys.exit(1)

    lore.close()


def cmd_consolidate(args: argparse.Namespace) -> None:
    import asyncio

    lore = _get_lore(args.db)

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


def cmd_on_this_day(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
    try:
        results = lore.on_this_day(
            month=args.month,
            day=args.day,
            project=args.project,
            tier=args.tier,
            limit=args.limit,
            offset=args.offset,
        )
    except ValueError as exc:
        lore.close()
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        lore.close()
        json_result = {}
        for year, memories in sorted(results.items(), reverse=True):
            json_result[str(year)] = [
                {
                    "id": m.id,
                    "content": m.content,
                    "type": m.type,
                    "tier": m.tier,
                    "importance_score": m.importance_score,
                    "created_at": m.created_at,
                    "project": m.project,
                    "tags": m.tags,
                    "source": m.source,
                }
                for m in memories
            ]
        print(json.dumps(json_result, indent=2))
    else:
        formatted = lore._temporal_engine.format_results(results, include_metadata=True)
        lore.close()
        print(formatted)


def cmd_wrap(args: argparse.Namespace) -> None:
    """Wrap a CLI command and capture conversation for memory extraction."""
    from lore.wrap import run_wrap

    # Strip leading '--' separator if present
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if not cmd:
        print("Error: no command specified. Usage: lore wrap <command> [args...]", file=sys.stderr)
        sys.exit(1)

    exit_code = run_wrap(
        cmd,
        api_url=args.api_url,
        api_key=args.api_key,
        user_id=args.user_id,
        project=args.project,
        db=args.db,
    )
    sys.exit(exit_code)


def cmd_setup(args: argparse.Namespace) -> None:
    """Handle setup subcommand: install/remove hooks for runtimes."""
    from lore.setup import remove_runtime, setup_claude_code, setup_codex, setup_cursor, setup_openclaw, show_status

    if args.status:
        show_status()
        return

    if args.remove:
        remove_runtime(args.remove)
        return

    if not args.runtime:
        print("Usage: lore setup <runtime> [--server-url URL]", file=sys.stderr)
        print("       lore setup --status", file=sys.stderr)
        print("       lore setup --remove <runtime>", file=sys.stderr)
        sys.exit(1)

    server_url = args.server_url or "http://localhost:8765"
    api_key = args.api_key

    if args.runtime == "claude-code":
        setup_claude_code(server_url=server_url, api_key=api_key)
    elif args.runtime == "openclaw":
        setup_openclaw(server_url=server_url, api_key=api_key)
    elif args.runtime == "cursor":
        setup_cursor(server_url=server_url, api_key=api_key)
    elif args.runtime == "codex":
        setup_codex(server_url=server_url, api_key=api_key)


def cmd_export(args: argparse.Namespace) -> None:
    lore = _get_lore(args.db)
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

    lore = _get_lore(args.db)
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


def cmd_snapshot(args: argparse.Namespace) -> None:
    from lore.export.snapshot import SnapshotManager

    lore = _get_lore(args.db)
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


def cmd_serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError:
        print(
            "Error: Server dependencies not installed.\n"
            "Install with: pip install lore-sdk[server]",
            file=sys.stderr,
        )
        sys.exit(1)
    port = args.port or int(os.environ.get("LORE_PORT", "8765"))
    host = args.host
    print(f"🧠 Starting Lore server on {host}:{port}")
    uvicorn.run("lore.server.app:app", host=host, port=port)


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
        "recent": cmd_recent,
        "prompt": cmd_prompt,
        "freshness": cmd_freshness,
        "github-sync": cmd_github_sync,
        "reindex": cmd_reindex,
        "classify": cmd_classify,
        "enrich": cmd_enrich,
        "facts": cmd_facts,
        "conflicts": cmd_conflicts,
        "backfill-facts": cmd_backfill_facts,
        "graph": cmd_graph,
        "entities": cmd_entities,
        "relationships": cmd_relationships,
        "graph-backfill": cmd_graph_backfill,
        "ingest": cmd_ingest,
        "consolidate": cmd_consolidate,
        "on-this-day": cmd_on_this_day,
        "add-conversation": cmd_add_conversation,
        "wrap": cmd_wrap,
        "setup": cmd_setup,
        "export": cmd_export,
        "import": cmd_import,
        "snapshot": cmd_snapshot,
        "serve": cmd_serve,
        "mcp": cmd_mcp,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
