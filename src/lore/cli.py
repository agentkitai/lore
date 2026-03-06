"""Minimal CLI for Lore SDK using argparse."""

from __future__ import annotations

import argparse
import json
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
    results = lore.recall(
        args.query, type=args.type, tier=tier, tags=tags, limit=args.limit,
        topic=getattr(args, "topic", None),
        sentiment=getattr(args, "sentiment", None),
        entity=getattr(args, "entity", None),
        category=getattr(args, "category", None),
    )
    lore.close()
    if not results:
        print("No results.")
        return
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

    # mcp
    sub.add_parser("mcp", help="Start MCP server (stdio transport)")

    return parser


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
    from lore import Lore

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
        src = lore._store.get_entity(r.source_entity_id) if hasattr(lore, '_store') else None
        tgt = lore._store.get_entity(r.target_entity_id) if hasattr(lore, '_store') else None
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
    from datetime import datetime, timezone

    lore = _get_lore(args.db)
    tags: List[str] = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    source = args.source

    def _ingest_one(content: str, user=None, channel=None) -> str:
        metadata = {
            "source_info": {
                "adapter": source,
                "user": user or args.user,
                "channel": channel or args.channel,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "raw_format": "plain_text",
            }
        }
        mid = lore.remember(
            content=content,
            type=args.type,
            tier="long",
            tags=tags,
            metadata=metadata,
            source=source,
            project=args.project,
        )
        return mid

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
        "mcp": cmd_mcp,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
