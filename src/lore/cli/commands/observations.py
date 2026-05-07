"""``lore observations`` — list and inspect Phase 6B observations.

Read-only debugging command. Uses the existing ``lore.list_memories``
filter machinery (``type='observation'``) plus per-id lookups.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import lore.cli._helpers as _helpers


def _format_age(created_at: Optional[str]) -> str:
    if not created_at:
        return "?"
    try:
        # Memory.created_at is an ISO 8601 string set by Lore.remember.
        dt = datetime.fromisoformat(created_at.rstrip("Z"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return created_at[:19] if len(created_at) >= 19 else created_at
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _meta(memory) -> Dict[str, Any]:
    return memory.metadata or {}


def cmd_observations_list(args: argparse.Namespace) -> None:
    """List recent observations: title + first fact + age."""
    lore = _helpers._get_lore(getattr(args, "db", None))
    try:
        limit = getattr(args, "limit", 20) or 20
        memories = lore.list_memories(
            type="observation",
            project=getattr(args, "project", None),
            limit=limit,
        )
    finally:
        lore.close()

    if not memories:
        print("No observations found.")
        return

    # Show newest first.
    memories.sort(key=lambda m: m.created_at or "", reverse=True)

    print(f"{'ID':<28} {'AGE':<10} {'TITLE':<50} FIRST FACT")
    print("-" * 120)
    for m in memories:
        meta = _meta(m)
        title = (meta.get("title") or m.context or "")[:48]
        facts = meta.get("facts") or []
        first_fact = facts[0] if facts else ""
        if not isinstance(first_fact, str):
            first_fact = str(first_fact)
        first_fact = first_fact[:60]
        age = _format_age(m.created_at)
        print(f"{m.id:<28} {age:<10} {title:<50} {first_fact}")


def cmd_observations_show(args: argparse.Namespace) -> None:
    """Show a single observation as JSON (full structured payload)."""
    lore = _helpers._get_lore(getattr(args, "db", None))
    try:
        memory = lore.get(args.observation_id) if hasattr(lore, "get") else None
        if memory is None and hasattr(lore, "_store"):
            memory = lore._store.get(args.observation_id)
    finally:
        lore.close()

    if memory is None:
        print(f"Observation not found: {args.observation_id}", file=sys.stderr)
        sys.exit(1)

    meta = _meta(memory)
    if meta.get("type") != "observation":
        print(
            f"Memory {args.observation_id} is not an observation "
            f"(type={meta.get('type', '?')})",
            file=sys.stderr,
        )
        sys.exit(1)

    payload: Dict[str, Any] = {
        "id": memory.id,
        "title": meta.get("title") or memory.context or "",
        "facts": list(meta.get("facts") or []),
        "narrative": meta.get("narrative") or memory.content,
        "tags": list(memory.tags or []),
        "project": memory.project,
        "source": memory.source,
        "captured_by": meta.get("captured_by", "auto"),
        "session_id": meta.get("session_id"),
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "meta": meta,
    }
    print(json.dumps(payload, indent=2, default=str))


def cmd_observations(args: argparse.Namespace) -> None:
    """Dispatch ``lore observations <subcommand>``."""
    sub = getattr(args, "obs_command", None)
    if sub == "list":
        cmd_observations_list(args)
    elif sub == "show":
        cmd_observations_show(args)
    else:
        print(
            "Usage: lore observations {list|show <id>}",
            file=sys.stderr,
        )
        sys.exit(1)
