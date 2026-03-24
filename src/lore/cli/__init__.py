"""Lore CLI — thin entry point with main() that dispatches to command submodules."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence

# Re-export shared helpers so that existing imports and patches continue to work.
# e.g. ``from lore.cli import _get_lore`` and ``patch("lore.cli._get_lore", ...)``
from lore.cli._helpers import _api_request, _get_api_config, _get_lore

# Re-export individual command handlers that tests import directly.
from lore.cli.commands.misc import cmd_setup
from lore.cli.commands.server import cmd_ui

__all__ = [
    "main",
    "build_parser",
    "_get_lore",
    "_get_api_config",
    "_api_request",
    "cmd_setup",
    "cmd_ui",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lore",
        description="Lore SDK — cross-agent memory CLI",
    )
    parser.add_argument("--db", default=None, help=argparse.SUPPRESS)  # deprecated, ignored

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
    p_setup.add_argument("--validate", action="store_true",
                         help="Validate hook scripts and config files after setup")
    p_setup.add_argument("--test-connection", action="store_true", dest="test_connection",
                         help="Test connectivity to the Lore server")
    p_setup.add_argument("--dry-run", action="store_true", dest="setup_dry_run",
                         help="Show what would be done without making changes")

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

    p_ss = sub.add_parser("snapshot-save", help="Save a session snapshot")
    p_ss.add_argument("content", help="Content to save")
    p_ss.add_argument("--title", default=None)
    p_ss.add_argument("--session-id", default=None, dest="session_id")

    p_topics = sub.add_parser("topics", help="List or view topic notes")
    p_topics.add_argument("name", nargs="?", default=None)
    p_topics.add_argument("--type", dest="entity_type", default=None)
    p_topics.add_argument("--min-mentions", type=int, default=3, dest="min_mentions")
    p_topics.add_argument("--format", dest="fmt", default="brief", choices=["brief", "detailed"])
    p_topics.add_argument("--limit", type=int, default=50)

    # review (E6)
    p_review = sub.add_parser("review", help="Review pending knowledge graph connections")
    p_review.add_argument("--approve", metavar="ID", default=None, help="Approve a relationship by ID")
    p_review.add_argument("--reject", metavar="ID", default=None, help="Reject a relationship by ID")
    p_review.add_argument("--approve-all", action="store_true", dest="approve_all", help="Approve all pending")
    p_review.add_argument("--reject-all", action="store_true", dest="reject_all", help="Reject all pending")
    p_review.add_argument("--limit", type=int, default=50, help="Max items to show (default: 50)")

    # slo
    slo_parser = sub.add_parser("slo", help="Manage SLO definitions and alerts")
    slo_parser.add_argument("--api-url", default=None, help="Lore API URL (or LORE_API_URL)")
    slo_parser.add_argument("--api-key", default=None, help="Lore API key (or LORE_API_KEY)")
    slo_sub = slo_parser.add_subparsers(dest="slo_command")

    slo_sub.add_parser("list", help="List SLO definitions")
    slo_sub.add_parser("status", help="Show current SLO pass/fail status")
    slo_sub.add_parser("alerts", help="Show alert history")

    slo_c = slo_sub.add_parser("create", help="Create an SLO")
    slo_c.add_argument("--name", required=True, dest="slo_name")
    slo_c.add_argument("--metric", required=True, choices=["p50_latency", "p95_latency", "p99_latency", "hit_rate"])
    slo_c.add_argument("--threshold", required=True, type=float)
    slo_c.add_argument("--operator", default="lt", choices=["lt", "gt"])
    slo_c.add_argument("--window", type=int, default=60, dest="window_minutes")

    slo_d = slo_sub.add_parser("delete", help="Delete an SLO")
    slo_d.add_argument("slo_id", help="SLO ID to delete")

    slo_t = slo_sub.add_parser("test", help="Fire a test alert")
    slo_t.add_argument("slo_id", help="SLO ID to test")

    # profiles
    prof_parser = sub.add_parser("profiles", help="Manage retrieval profiles")
    prof_parser.add_argument("--api-url", default=None, help="Lore API URL")
    prof_parser.add_argument("--api-key", default=None, help="Lore API key")
    prof_sub = prof_parser.add_subparsers(dest="prof_command")

    prof_sub.add_parser("list", help="List profiles")
    prof_cr = prof_sub.add_parser("create", help="Create a profile")
    prof_cr.add_argument("--name", required=True)
    prof_cr.add_argument("--semantic-weight", type=float, default=1.0, dest="semantic_weight")
    prof_cr.add_argument("--graph-weight", type=float, default=1.0, dest="graph_weight")
    prof_cr.add_argument("--recency-bias", type=float, default=30.0, dest="recency_bias")
    prof_cr.add_argument("--min-score", type=float, default=0.3, dest="min_score")
    prof_cr.add_argument("--max-results", type=int, default=10, dest="max_results")

    prof_del = prof_sub.add_parser("delete", help="Delete a profile")
    prof_del.add_argument("profile_id")

    # policy
    pol_parser = sub.add_parser("policy", help="Manage retention policies")
    pol_parser.add_argument("--api-url", default=None, help="Lore API URL")
    pol_parser.add_argument("--api-key", default=None, help="Lore API key")
    pol_sub = pol_parser.add_subparsers(dest="pol_command")

    pol_sub.add_parser("list", help="List policies")
    pol_sub.add_parser("compliance", help="Check policy compliance")
    pol_cr = pol_sub.add_parser("create", help="Create a policy")
    pol_cr.add_argument("--name", required=True)
    pol_cr.add_argument("--snapshot-schedule", default=None, dest="snapshot_schedule")
    pol_cr.add_argument("--max-snapshots", type=int, default=50, dest="max_snapshots")

    pol_del = pol_sub.add_parser("delete", help="Delete a policy")
    pol_del.add_argument("policy_id")

    # restore-drill
    p_drill = sub.add_parser("restore-drill", help="Run a restore drill")
    p_drill.add_argument("snapshot_name", nargs="?", default=None, help="Snapshot name to drill")
    p_drill.add_argument("--latest", action="store_true", help="Use latest snapshot")
    p_drill.add_argument("--api-url", default=None, help="Lore API URL")
    p_drill.add_argument("--api-key", default=None, help="Lore API key")

    # workspace
    ws_parser = sub.add_parser("workspace", help="Manage workspaces")
    ws_parser.add_argument("--api-url", default=None, help="Lore API URL")
    ws_parser.add_argument("--api-key", default=None, help="Lore API key")
    ws_sub = ws_parser.add_subparsers(dest="ws_command")

    ws_sub.add_parser("list", help="List workspaces")
    ws_cr = ws_sub.add_parser("create", help="Create a workspace")
    ws_cr.add_argument("name", help="Workspace name")
    ws_cr.add_argument("--slug", default=None, help="URL slug (auto-generated if omitted)")

    ws_sw = ws_sub.add_parser("switch", help="Switch to a workspace")
    ws_sw.add_argument("slug", help="Workspace slug")

    ws_mem = ws_sub.add_parser("members", help="List workspace members")
    ws_mem.add_argument("--workspace", default=None, help="Workspace slug")

    # audit
    p_audit = sub.add_parser("audit", help="Query audit log")
    p_audit.add_argument("--workspace", default=None, help="Filter by workspace slug")
    p_audit.add_argument("--since", default=None, help="ISO 8601 start time")
    p_audit.add_argument("--limit", type=int, default=50)
    p_audit.add_argument("--api-url", default=None, help="Lore API URL")
    p_audit.add_argument("--api-key", default=None, help="Lore API key")

    # plugin
    plug_parser = sub.add_parser("plugin", help="Manage plugins")
    plug_sub = plug_parser.add_subparsers(dest="plug_command")

    plug_sub.add_parser("list", help="List installed plugins")
    plug_cr = plug_sub.add_parser("create", help="Scaffold a new plugin project")
    plug_cr.add_argument("name", help="Plugin name")
    plug_cr.add_argument("--output", default=".", help="Output directory")

    plug_en = plug_sub.add_parser("enable", help="Enable a plugin")
    plug_en.add_argument("name")
    plug_dis = plug_sub.add_parser("disable", help="Disable a plugin")
    plug_dis.add_argument("name")
    plug_rel = plug_sub.add_parser("reload", help="Reload a plugin")
    plug_rel.add_argument("name")

    # suggest
    p_suggest = sub.add_parser("suggest", help="Get proactive memory suggestions")
    p_suggest.add_argument("--context", default="", help="Session context text")
    p_suggest.add_argument("--feedback", nargs=2, metavar=("MEMORY_ID", "FEEDBACK"), default=None,
                           help="Submit feedback: <memory_id> positive|negative")
    p_suggest.add_argument("--config", action="store_true", dest="show_config",
                           help="Show recommendation config")
    p_suggest.add_argument("--aggressiveness", type=float, default=None,
                           help="Set aggressiveness (0.0-1.0)")

    # bootstrap
    p_boot = sub.add_parser("bootstrap", help="Validate prerequisites and set up Lore")
    p_boot.add_argument("--fix", action="store_true", help="Attempt to auto-fix missing dependencies")
    p_boot.add_argument("--skip-docker", action="store_true", dest="skip_docker", help="Skip Docker check")
    p_boot.add_argument("--skip-server", action="store_true", dest="skip_server", help="Skip server start/health check")
    p_boot.add_argument("--db-url", default=None, dest="db_url", help="Database URL (or DATABASE_URL env)")
    p_boot.add_argument("--verbose", action="store_true", help="Show all fix hints")

    # serve
    p_serve = sub.add_parser("serve", help="Start Lore HTTP server")
    p_serve.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=None, help="Port (default: $LORE_PORT or 8765)")

    # mcp
    sub.add_parser("mcp", help="Start MCP server (stdio transport)")

    # ui
    p_ui = sub.add_parser("ui", help="Open graph visualization in browser")
    p_ui.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    p_ui.add_argument("--port", type=int, default=8766, help="Port (default: 8766)")
    p_ui.add_argument("--no-open", action="store_true", dest="no_open", help="Don't open browser")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Lazy-import command handlers to avoid circular imports and keep startup fast.
    from lore.cli.commands.graph import (
        cmd_entities,
        cmd_graph,
        cmd_graph_backfill,
        cmd_relationships,
        cmd_review,
        cmd_topics,
    )
    from lore.cli.commands.keys import cmd_keys_create, cmd_keys_list, cmd_keys_revoke
    from lore.cli.commands.manage import (
        cmd_export,
        cmd_forget,
        cmd_import,
        cmd_memories,
        cmd_recent,
        cmd_stats,
    )
    from lore.cli.commands.misc import (
        cmd_add_conversation,
        cmd_audit,
        cmd_backfill_facts,
        cmd_bootstrap,
        cmd_classify,
        cmd_conflicts,
        cmd_enrich,
        cmd_facts,
        cmd_freshness,
        cmd_github_sync,
        cmd_ingest,
        cmd_on_this_day,
        cmd_plugin,
        cmd_policy,
        cmd_profiles,
        cmd_reindex,
        cmd_setup,
        cmd_slo,
        cmd_suggest,
        cmd_workspace,
        cmd_wrap,
    )
    from lore.cli.commands.recall import cmd_prompt, cmd_recall
    from lore.cli.commands.remember import cmd_remember
    from lore.cli.commands.server import cmd_mcp, cmd_serve, cmd_ui
    from lore.cli.commands.snapshot import cmd_consolidate, cmd_snapshot, cmd_snapshot_save

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

    # Subcommand groups that need special routing
    if args.command in ("slo", "profiles", "policy", "workspace", "plugin"):
        group_handlers = {
            "slo": cmd_slo,
            "profiles": cmd_profiles,
            "policy": cmd_policy,
            "workspace": cmd_workspace,
            "plugin": cmd_plugin,
        }
        group_handlers[args.command](args)
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
        "snapshot-save": cmd_snapshot_save,
        "topics": cmd_topics,
        "review": cmd_review,
        "bootstrap": cmd_bootstrap,
        "audit": cmd_audit,
        "suggest": cmd_suggest,
        "restore-drill": lambda a: print("Use: lore policy drill (via API)"),
        "serve": cmd_serve,
        "mcp": cmd_mcp,
        "ui": cmd_ui,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
