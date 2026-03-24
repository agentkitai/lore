"""Miscellaneous commands — classify, facts, conflicts, backfill, enrich, reindex,
freshness, github-sync, ingest, on-this-day, add-conversation, wrap, setup,
bootstrap, slo, profiles, policy, workspace, audit, plugin, suggest."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

import lore.cli._helpers as _helpers


def cmd_classify(args: argparse.Namespace) -> None:
    lore = _helpers._get_lore(args.db)
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
    lore = _helpers._get_lore(args.db)
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
    lore = _helpers._get_lore(args.db)
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

    lore = _helpers._get_lore(args.db)
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


def cmd_reindex(args: argparse.Namespace) -> None:
    from lore import Lore

    kwargs: dict = {}
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
    lore = Lore(enrichment=True, enrichment_model=model)

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


def cmd_freshness(args: argparse.Namespace) -> None:
    from lore.freshness.detector import FreshnessDetector
    from lore.freshness.git_ops import GitError

    try:
        FreshnessDetector.validate_repo(args.repo)
    except GitError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    lore = _helpers._get_lore(args.db)
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
        lore = _helpers._get_lore(args.db)
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

    lore = _helpers._get_lore(args.db)
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


def cmd_ingest(args: argparse.Namespace) -> None:
    from lore.ingest.adapters.raw import RawAdapter
    from lore.ingest.dedup import Deduplicator
    from lore.ingest.pipeline import IngestionPipeline

    lore = _helpers._get_lore(args.db)
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
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
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


def cmd_on_this_day(args: argparse.Namespace) -> None:
    lore = _helpers._get_lore(args.db)
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

    lore = _helpers._get_lore(args.db)
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
    from lore.setup import (
        _test_connection,
        _validate_hook,
        remove_runtime,
        setup_claude_code,
        setup_codex,
        setup_cursor,
        setup_openclaw,
        show_status,
    )

    if args.status:
        show_status()
        return

    if args.remove:
        remove_runtime(args.remove)
        return

    # Test connection mode (standalone)
    if getattr(args, "test_connection", False) and not args.runtime:
        server_url = args.server_url or "http://localhost:8765"
        api_key = args.api_key
        print(f"Testing connection to {server_url}...")
        result = _test_connection(server_url, api_key)
        print(f"  Status:     {result['status']}")
        print(f"  Health:     {'ok' if result.get('health') else 'fail'}")
        print(f"  Retrieve:   {'ok' if result.get('retrieve') else 'skip/fail'}")
        print(f"  Latency:    {result.get('latency_ms', 0):.1f}ms")
        if result.get("error"):
            print(f"  Error:      {result['error']}")
        return

    if not args.runtime:
        print("Usage: lore setup <runtime> [--server-url URL]", file=sys.stderr)
        print("       lore setup --status", file=sys.stderr)
        print("       lore setup --remove <runtime>", file=sys.stderr)
        print("       lore setup --test-connection [--server-url URL]", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "setup_dry_run", False):
        print(f"[dry-run] Would set up {args.runtime}")
        print(f"[dry-run] Server URL: {args.server_url or 'http://localhost:8765'}")
        print(f"[dry-run] API Key: {'set' if args.api_key else 'not set'}")
        return

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

    # Post-setup validation
    if getattr(args, "validate", False):
        print("\nValidating...")
        from lore.setup import _claude_hook_path, _codex_hook_path, _cursor_hook_path, _openclaw_hook_path
        hook_paths = {
            "claude-code": _claude_hook_path,
            "cursor": _cursor_hook_path,
            "codex": _codex_hook_path,
            "openclaw": _openclaw_hook_path,
        }
        hook_fn = hook_paths.get(args.runtime)
        if hook_fn:
            errors = _validate_hook(hook_fn())
            if errors:
                for err in errors:
                    print(f"  Warning: {err}")
            else:
                print("  Hook validation: ok")

    # Post-setup connection test
    if getattr(args, "test_connection", False):
        print("\nTesting connection...")
        result = _test_connection(server_url, api_key)
        print(f"  Status:  {result['status']}")
        print(f"  Latency: {result.get('latency_ms', 0):.1f}ms")
        if not result.get("health"):
            print("  Warning: Server not reachable. Start it with: lore serve")


def cmd_bootstrap(args: argparse.Namespace) -> None:
    """Run guided bootstrap checks."""
    from lore.bootstrap import BootstrapRunner, format_results

    runner = BootstrapRunner(
        db_url=args.db_url,
        fix=args.fix,
        skip_docker=args.skip_docker,
        skip_server=args.skip_server,
        verbose=args.verbose,
    )
    print("Lore Bootstrap")
    print("=" * 40)
    results = runner.run_all()
    print(format_results(results, verbose=args.verbose))

    has_failures = any(r.status == "fail" for r in results)
    if has_failures:
        sys.exit(1)


def cmd_slo(args: argparse.Namespace) -> None:
    """Handle SLO subcommands."""
    api_url, api_key = _helpers._get_api_config(args)
    cmd = getattr(args, "slo_command", None)
    if not cmd:
        print("Usage: lore slo <list|create|delete|status|alerts|test>", file=sys.stderr)
        sys.exit(1)
    if cmd == "list":
        result = _helpers._api_request("GET", f"{api_url}/v1/slo", api_key)
        for s in result:
            print(f"  {s['id']}  {s['name']}  {s['metric']} {s['operator']} {s['threshold']}  {'enabled' if s.get('enabled') else 'disabled'}")
    elif cmd == "status":
        result = _helpers._api_request("GET", f"{api_url}/v1/slo/status", api_key)
        for s in result:
            icon = "PASS" if s.get("passing") else "FAIL"
            val = s.get("current_value")
            val_str = f"{val:.2f}" if val is not None else "N/A"
            print(f"  [{icon}] {s['name']}: {val_str} ({s['operator']} {s['threshold']})")
    elif cmd == "alerts":
        result = _helpers._api_request("GET", f"{api_url}/v1/slo/alerts?limit=20", api_key)
        for a in result:
            print(f"  [{a['status']}] {a['metric_value']:.2f} / {a['threshold']:.2f}  ({a.get('created_at', '')})")
    elif cmd == "create":
        payload = {
            "name": args.slo_name, "metric": args.metric,
            "threshold": args.threshold, "operator": args.operator,
            "window_minutes": args.window_minutes,
        }
        result = _helpers._api_request("POST", f"{api_url}/v1/slo", api_key, payload)
        print(f"Created SLO: {result['id']} ({result['name']})")
    elif cmd == "delete":
        _helpers._api_request("DELETE", f"{api_url}/v1/slo/{args.slo_id}", api_key)
        print(f"Deleted SLO: {args.slo_id}")
    elif cmd == "test":
        result = _helpers._api_request("POST", f"{api_url}/v1/slo/{args.slo_id}/test", api_key)
        print(f"Test alert fired: {result.get('status', 'unknown')}")


def cmd_profiles(args: argparse.Namespace) -> None:
    """Handle profiles subcommands."""
    api_url, api_key = _helpers._get_api_config(args)
    cmd = getattr(args, "prof_command", None)
    if not cmd:
        print("Usage: lore profiles <list|create|delete>", file=sys.stderr)
        sys.exit(1)
    if cmd == "list":
        result = _helpers._api_request("GET", f"{api_url}/v1/profiles", api_key)
        for p in result:
            preset = " [preset]" if p.get("is_preset") else ""
            print(f"  {p['id']}  {p['name']}{preset}  sw={p['semantic_weight']} gw={p['graph_weight']} rb={p['recency_bias']}")
    elif cmd == "create":
        payload = {
            "name": args.name, "semantic_weight": args.semantic_weight,
            "graph_weight": args.graph_weight, "recency_bias": args.recency_bias,
            "min_score": args.min_score, "max_results": args.max_results,
        }
        result = _helpers._api_request("POST", f"{api_url}/v1/profiles", api_key, payload)
        print(f"Created profile: {result['id']} ({result['name']})")
    elif cmd == "delete":
        _helpers._api_request("DELETE", f"{api_url}/v1/profiles/{args.profile_id}", api_key)
        print(f"Deleted profile: {args.profile_id}")


def cmd_policy(args: argparse.Namespace) -> None:
    """Handle policy subcommands."""
    api_url, api_key = _helpers._get_api_config(args)
    cmd = getattr(args, "pol_command", None)
    if not cmd:
        print("Usage: lore policy <list|create|delete|compliance>", file=sys.stderr)
        sys.exit(1)
    if cmd == "list":
        result = _helpers._api_request("GET", f"{api_url}/v1/policies", api_key)
        for p in result:
            active = "active" if p.get("is_active") else "inactive"
            print(f"  {p['id']}  {p['name']}  [{active}]  max_snapshots={p.get('max_snapshots', 50)}")
    elif cmd == "compliance":
        result = _helpers._api_request("GET", f"{api_url}/v1/policies/compliance", api_key)
        for c in result:
            status = "COMPLIANT" if c.get("compliant") else "NON-COMPLIANT"
            print(f"  [{status}] {c['policy_name']}")
            for issue in c.get("issues", []):
                print(f"    - {issue}")
    elif cmd == "create":
        payload = {
            "name": args.name,
            "snapshot_schedule": args.snapshot_schedule,
            "max_snapshots": args.max_snapshots,
        }
        result = _helpers._api_request("POST", f"{api_url}/v1/policies", api_key, payload)
        print(f"Created policy: {result['id']} ({result['name']})")
    elif cmd == "delete":
        _helpers._api_request("DELETE", f"{api_url}/v1/policies/{args.policy_id}", api_key)
        print(f"Deleted policy: {args.policy_id}")


def cmd_workspace(args: argparse.Namespace) -> None:
    """Handle workspace subcommands."""
    api_url, api_key = _helpers._get_api_config(args)
    cmd = getattr(args, "ws_command", None)
    if not cmd:
        print("Usage: lore workspace <list|create|switch|members>", file=sys.stderr)
        sys.exit(1)
    if cmd == "list":
        result = _helpers._api_request("GET", f"{api_url}/v1/workspaces", api_key)
        for w in result:
            print(f"  {w['slug']:<20} {w['name']}")
    elif cmd == "create":
        slug = args.slug or args.name.lower().replace(" ", "-")
        payload = {"name": args.name, "slug": slug}
        result = _helpers._api_request("POST", f"{api_url}/v1/workspaces", api_key, payload)
        print(f"Created workspace: {result['slug']}")
    elif cmd == "switch":
        print(f"Switched to workspace: {args.slug}")
        print(f"Set LORE_WORKSPACE={args.slug} in your environment to persist.")
    elif cmd == "members":
        ws = args.workspace or "default"
        result = _helpers._api_request("GET", f"{api_url}/v1/workspaces/{ws}/members", api_key)
        for m in result:
            print(f"  {m.get('user_id', 'unknown'):<20} {m['role']}")


def cmd_audit(args: argparse.Namespace) -> None:
    """Query audit log."""
    api_url, api_key = _helpers._get_api_config(args)
    params = f"?limit={args.limit}"
    if args.workspace:
        params += f"&workspace_id={args.workspace}"
    if args.since:
        params += f"&since={args.since}"
    result = _helpers._api_request("GET", f"{api_url}/v1/audit{params}", api_key)
    for entry in result:
        ts = entry.get("created_at", "")[:19]
        print(f"  [{ts}] {entry['action']}  by {entry['actor_id']} ({entry['actor_type']})")


def cmd_plugin(args: argparse.Namespace) -> None:
    """Handle plugin subcommands."""
    cmd = getattr(args, "plug_command", None)
    if not cmd:
        print("Usage: lore plugin <list|create|enable|disable|reload>", file=sys.stderr)
        sys.exit(1)
    if cmd == "list":
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        registry.load_all()
        plugins = registry.list_plugins()
        if not plugins:
            print("No plugins installed.")
            return
        for p in plugins:
            status = "enabled" if p["enabled"] else "disabled"
            print(f"  {p['name']:<20} v{p['version']}  [{status}]  {p.get('description', '')}")
    elif cmd == "create":
        from lore.plugin.scaffold import scaffold_plugin
        project_dir = scaffold_plugin(args.name, output_dir=args.output)
        print(f"Plugin scaffolded: {project_dir}")
        print(f"  Install with: cd {project_dir} && pip install -e .")
    elif cmd == "enable":
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        registry.load_all()
        if registry.enable(args.name):
            print(f"Enabled: {args.name}")
        else:
            print(f"Plugin not found: {args.name}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "disable":
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        registry.load_all()
        if registry.disable(args.name):
            print(f"Disabled: {args.name}")
        else:
            print(f"Plugin not found: {args.name}", file=sys.stderr)
            sys.exit(1)
    elif cmd == "reload":
        from lore.plugin.registry import PluginRegistry
        registry = PluginRegistry()
        registry.load_all()
        if registry.reload(args.name):
            print(f"Reloaded: {args.name}")
        else:
            print(f"Plugin not found: {args.name}", file=sys.stderr)
            sys.exit(1)


def cmd_suggest(args: argparse.Namespace) -> None:
    """Get proactive memory suggestions."""
    if args.feedback:
        memory_id, feedback = args.feedback
        lore = _helpers._get_lore(args.db)
        from lore.recommend.feedback import FeedbackRecorder
        recorder = FeedbackRecorder()
        recorder.record(memory_id, feedback, "cli-user")
        lore.close()
        print(f"Feedback recorded: {feedback} for {memory_id}")
        return

    if args.show_config:
        print("Recommendation config:")
        print(f"  Aggressiveness: {args.aggressiveness or 0.5}")
        return

    lore = _helpers._get_lore(args.db)
    from lore.recommend.engine import RecommendationEngine
    engine = RecommendationEngine(
        store=lore._store,
        embedder=lore._embedder,
        aggressiveness=args.aggressiveness or 0.5,
    )
    recs = engine.suggest(context=args.context)
    lore.close()

    if not recs:
        print("No suggestions at this time.")
        return
    for i, rec in enumerate(recs, 1):
        print(f"  {i}. [{rec.score:.2f}] {rec.content_preview}")
        if rec.explanation:
            print(f"     {rec.explanation}")
        print(f"     ID: {rec.memory_id}")
        print()
