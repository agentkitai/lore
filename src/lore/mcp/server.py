"""Lore MCP server — exposes memory tools over stdio transport.

Tools: remember, recall, forget, list, stats

Configure via environment variables:
  LORE_STORE   — "local" (default) or "remote"
  LORE_PROJECT — default project scope
  LORE_API_URL — required when LORE_STORE=remote
  LORE_API_KEY — required when LORE_STORE=remote
  LORE_DB_PATH — SQLite path (local mode only)
  LORE_MODEL_DIR — embedding model cache directory
"""

from __future__ import annotations

import os
import re
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from ulid import ULID

# ---------------------------------------------------------------------------
# Lazy initialization
# ---------------------------------------------------------------------------

_store = None
_embedder = None
_default_project: Optional[str] = None


def _get_store():
    """Return the store instance, creating it on first call."""
    global _store, _default_project
    if _store is not None:
        return _store

    store_type = os.environ.get("LORE_STORE", "local").lower()
    _default_project = os.environ.get("LORE_PROJECT") or None

    if store_type == "remote":
        api_url = os.environ.get("LORE_API_URL")
        api_key = os.environ.get("LORE_API_KEY")
        if not api_url or not api_key:
            raise RuntimeError(
                "LORE_API_URL and LORE_API_KEY must be set when LORE_STORE=remote"
            )
        from lore.memory_store.remote import RemoteStore
        _store = RemoteStore(api_url=api_url, api_key=api_key)
    else:
        db_path = os.environ.get(
            "LORE_DB_PATH",
            os.path.join(os.path.expanduser("~"), ".lore", "default.db"),
        )
        from lore.memory_store.sqlite import SqliteStore
        _store = SqliteStore(db_path)

    return _store


def _get_embedder():
    """Return the local embedder for local mode."""
    global _embedder
    if _embedder is not None:
        return _embedder

    from lore.embed.local import LocalEmbedder
    model_dir = os.environ.get("LORE_MODEL_DIR")
    _embedder = LocalEmbedder(model_dir=model_dir)
    return _embedder


def _serialize_embedding(vec: List[float]) -> bytes:
    """Serialize a float list to bytes (float32)."""
    return struct.pack(f"{len(vec)}f", *vec)


def _is_remote() -> bool:
    return os.environ.get("LORE_STORE", "local").lower() == "remote"


def _parse_ttl(ttl: Optional[str]) -> Optional[str]:
    """Parse a TTL string (e.g. '7d', '1h', '30m') into an ISO expires_at string."""
    if not ttl:
        return None
    match = re.match(r"^(\d+)([smhdw])$", ttl.strip().lower())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    delta_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    expires = datetime.now(timezone.utc) + timedelta(**{delta_map[unit]: value})
    return expires.isoformat()


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="lore",
    instructions=(
        "Lore is a persistent memory system for AI. Use it to save "
        "important information for future recall — lessons learned, decisions, "
        "facts, code snippets, and more. Before starting tasks, check if "
        "relevant memories exist with recall."
    ),
)


# ---------------------------------------------------------------------------
# Tool: remember
# ---------------------------------------------------------------------------

@mcp.tool(
    description=(
        "Store a memory for future recall. "
        "USE THIS WHEN: you learn something important, receive instructions to "
        "remember, want to save a decision/fact/lesson/code snippet for later. "
        "The content should be self-contained — include enough context that the "
        "memory is useful without the original conversation. "
        "DO NOT store trivial or temporary information."
    ),
)
def remember(
    content: str,
    type: str = "note",
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    project: Optional[str] = None,
    source: Optional[str] = None,
    ttl: Optional[str] = None,
) -> str:
    """Store a memory in Lore."""
    try:
        store = _get_store()
        effective_project = project or _default_project
        expires_at = _parse_ttl(ttl)

        if _is_remote():
            from lore.types import Memory
            memory = Memory(
                id=str(ULID()),
                content=content,
                type=type,
                source=source,
                project=effective_project,
                tags=tags or [],
                metadata=metadata or {},
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
                expires_at=expires_at,
            )
            store.save(memory)
            return f"\u2705 Memory saved (ID: {memory.id})"
        else:
            embedder = _get_embedder()
            embedding_vec = embedder.embed(content)
            embedding_bytes = _serialize_embedding(embedding_vec)

            now = datetime.now(timezone.utc).isoformat()
            from lore.types import Memory
            memory = Memory(
                id=str(ULID()),
                content=content,
                type=type,
                source=source,
                project=effective_project,
                tags=tags or [],
                metadata=metadata or {},
                embedding=embedding_bytes,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )
            store.save(memory)
            return f"\u2705 Memory saved (ID: {memory.id})"
    except Exception as e:
        return f"\u274c Failed to save: {e}"


# ---------------------------------------------------------------------------
# Tool: recall
# ---------------------------------------------------------------------------

@mcp.tool(
    description=(
        "Search memories by semantic similarity. "
        "USE THIS WHEN: you need information that might have been stored "
        "previously, before starting a task to check for relevant context, "
        "or when the user asks 'do you remember...'. "
        "Be specific in your query — describe what you're looking for in "
        "natural language. "
        "GOOD queries: 'Stripe rate limiting strategy', 'React project "
        "architecture decisions'. "
        "BAD queries: 'help', 'stuff', 'everything'."
    ),
)
def recall(
    query: str,
    type: Optional[str] = None,
    tags: Optional[List[str]] = None,
    project: Optional[str] = None,
    limit: int = 5,
) -> str:
    """Search Lore memory for relevant memories."""
    try:
        store = _get_store()
        limit = max(1, min(limit, 20))
        effective_project = project or _default_project

        if _is_remote():
            from lore.memory_store.remote import RemoteStore
            assert isinstance(store, RemoteStore)
            results = store.search_text(
                query=query,
                type=type,
                tags=tags,
                project=effective_project,
                limit=limit,
            )
        else:
            embedder = _get_embedder()
            query_vec = embedder.embed(query)
            results = store.search(
                embedding=query_vec,
                type=type,
                tags=tags,
                project=effective_project,
                limit=limit,
            )

        if not results:
            return "No relevant memories found. Try a different query or broader terms."

        lines: List[str] = [f"Found {len(results)} relevant memory(ies):\n"]
        for i, r in enumerate(results, 1):
            m = r.memory
            lines.append(f"{'─' * 50}")
            lines.append(f"Memory {i}  (score: {r.score:.2f}, id: {m.id})")
            lines.append(f"Type: {m.type} | Tags: {', '.join(m.tags) if m.tags else 'none'}")
            lines.append(f"Content: {m.content}")
            if m.metadata:
                lines.append(f"Metadata: {m.metadata}")
            lines.append(f"Created: {m.created_at}")
            if m.project:
                lines.append(f"Project: {m.project}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"\u274c Failed to recall: {e}"


# ---------------------------------------------------------------------------
# Tool: forget
# ---------------------------------------------------------------------------

@mcp.tool(
    description=(
        "Delete one or more memories. "
        "USE THIS WHEN: a memory is outdated, incorrect, or no longer relevant. "
        "Pass an ID to delete a specific memory, or use filters to bulk-delete. "
        "Bulk delete without any filter requires confirm=true as a safety measure."
    ),
)
def forget(
    id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    project: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Delete memories from Lore."""
    try:
        store = _get_store()

        if id:
            deleted = store.delete(id)
            if deleted:
                return f"\u2705 Memory {id} deleted"
            return f"\u274c Memory {id} not found"

        has_filter = any([tags, type, project])
        if not has_filter and not confirm:
            stats = store.stats()
            return (
                f"\u26a0\ufe0f Bulk delete requires 'confirm: true'. "
                f"This would delete {stats.total_count} memories."
            )

        count = store.delete_by_filter(type=type, tags=tags, project=project)
        filter_desc = []
        if type:
            filter_desc.append(f"type={type}")
        if tags:
            filter_desc.append(f"tags={tags}")
        if project:
            filter_desc.append(f"project={project}")
        desc = ", ".join(filter_desc) if filter_desc else "all"
        return f"\u2705 Deleted {count} memories matching filters ({desc})"
    except Exception as e:
        return f"\u274c Failed to forget: {e}"


# ---------------------------------------------------------------------------
# Tool: list
# ---------------------------------------------------------------------------

@mcp.tool(
    name="list",
    description=(
        "Browse and list memories with optional filters. Unlike 'recall', "
        "this does NOT use semantic search — it returns memories in "
        "chronological order. "
        "USE THIS WHEN: you want to see recent memories, browse by "
        "type/project/tags, or get an overview of what's stored."
    ),
)
def list_memories(
    type: Optional[str] = None,
    tags: Optional[List[str]] = None,
    project: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """List memories in Lore."""
    try:
        store = _get_store()
        limit = max(1, min(limit, 100))
        effective_project = project or _default_project

        memories, total = store.list(
            type=type,
            tags=tags,
            project=effective_project,
            limit=limit,
            offset=offset,
        )

        if not memories:
            return "No memories found matching the given filters."

        lines: List[str] = [f"Showing {len(memories)} of {total} memories (offset {offset}):\n"]
        for i, m in enumerate(memories, offset + 1):
            tag_str = ", ".join(m.tags) if m.tags else ""
            created = m.created_at[:10] if m.created_at else ""
            content_preview = m.content[:80] + ("..." if len(m.content) > 80 else "")
            lines.append(
                f"{i}. [{m.id[:12]}...] ({m.type}) {content_preview}"
                + (f" — tags: {tag_str}" if tag_str else "")
                + f" | {created}"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"\u274c Failed to list memories: {e}"


# ---------------------------------------------------------------------------
# Tool: stats
# ---------------------------------------------------------------------------

@mcp.tool(
    description=(
        "Get summary statistics about the memory store. "
        "Shows total count, breakdown by type and project, and date range."
    ),
)
def stats(project: Optional[str] = None) -> str:
    """Get memory store statistics."""
    try:
        store = _get_store()
        effective_project = project or _default_project
        s = store.stats(project=effective_project)

        lines: List[str] = ["Memory Store Statistics:"]
        lines.append(f"  Total memories: {s.total_count}")

        if s.count_by_type:
            type_parts = [f"{k} ({v})" for k, v in s.count_by_type.items()]
            lines.append(f"  By type: {', '.join(type_parts)}")

        if s.count_by_project:
            project_parts = [f"{k} ({v})" for k, v in s.count_by_project.items()]
            lines.append(f"  By project: {', '.join(project_parts)}")

        oldest = s.oldest_memory[:10] if s.oldest_memory else "N/A"
        newest = s.newest_memory[:10] if s.newest_memory else "N/A"
        lines.append(f"  Date range: {oldest} to {newest}")

        return "\n".join(lines)
    except Exception as e:
        return f"\u274c Failed to get stats: {e}"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_server() -> None:
    """Start the MCP server with stdio transport."""
    mcp.run(transport="stdio")
