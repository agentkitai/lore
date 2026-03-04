"""MCP server that wraps the Lore SDK.

Exposes memory tools over stdio transport:
  - remember: store a memory
  - recall: semantic search for relevant memories
  - forget: delete a memory
  - list_memories: list stored memories
  - stats: memory statistics
  - upvote_memory: boost a memory's ranking
  - downvote_memory: lower a memory's ranking

Configure via environment variables:
  LORE_PROJECT — default project scope
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from lore.lore import Lore

# ---------------------------------------------------------------------------
# Lore instance (created lazily so import doesn't trigger side-effects)
# ---------------------------------------------------------------------------

_lore: Optional[Lore] = None


def _get_lore() -> Lore:
    """Return the module-level Lore instance, creating it on first call."""
    global _lore
    if _lore is not None:
        return _lore

    project = os.environ.get("LORE_PROJECT") or None
    _lore = Lore(project=project)
    return _lore


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="lore",
    instructions=(
        "Lore is a cross-agent memory system. Use it to remember knowledge, "
        "recall relevant memories when facing problems, and forget outdated "
        "information. Memories can be facts, lessons, preferences, context, "
        "or any knowledge worth preserving across sessions."
    ),
)


@mcp.tool(
    description=(
        "Save a memory — any knowledge worth preserving. "
        "USE THIS WHEN: you just solved a tricky bug, found a non-obvious fix, "
        "discovered a workaround, learned a user preference, or encountered "
        "something that future agents (or your future self) would benefit from knowing. "
        "DO NOT save trivial things — only save memories that would save someone "
        "real time or prevent a real mistake. "
        "The content should be a clear, self-contained piece of knowledge."
    ),
)
def remember(
    content: str,
    type: str = "general",
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    project: Optional[str] = None,
    ttl: Optional[int] = None,
) -> str:
    """Store a memory in Lore."""
    try:
        lore = _get_lore()
        memory_id = lore.remember(
            content=content,
            type=type,
            tags=tags,
            metadata=metadata,
            source=source,
            project=project,
            ttl=ttl,
        )
        return f"Memory saved (ID: {memory_id})"
    except Exception as e:
        return f"Failed to save memory: {e}"


@mcp.tool(
    description=(
        "Search for relevant memories from past experience. "
        "USE THIS WHEN: you're about to solve a problem, debug an error, "
        "or make a design decision — especially if you suspect someone has "
        "hit this before. Search with a natural-language description of "
        "your problem or question. "
        "GOOD queries: 'CORS errors with FastAPI', 'Docker build fails on M1', "
        "'rate limiting strategy for API'. "
        "BAD queries: 'help', 'error', 'fix this'. Be specific."
    ),
)
def recall(
    query: str,
    tags: Optional[List[str]] = None,
    type: Optional[str] = None,
    limit: int = 5,
    repo_path: Optional[str] = None,
) -> str:
    """Search Lore memory for relevant memories."""
    try:
        lore = _get_lore()
        limit = max(1, min(limit, 20))
        results = lore.recall(
            query=query, tags=tags, type=type, limit=limit,
            check_freshness=bool(repo_path), repo_path=repo_path,
        )
        if not results:
            return "No relevant memories found. Try a different query or broader terms."

        lines: List[str] = [f"Found {len(results)} relevant memory(ies):\n"]
        for i, r in enumerate(results, 1):
            mem = r.memory
            lines.append(f"{'─' * 60}")
            staleness_badge = ""
            if r.staleness and r.staleness.status not in ("fresh", "unknown"):
                staleness_badge = (
                    f" [POSSIBLY STALE - {r.staleness.commits_since} "
                    f"commits since memory]"
                )
            lines.append(
                f"Memory {i}  (score: {r.score:.2f}, id: {mem.id}, "
                f"type: {mem.type}){staleness_badge}"
            )
            lines.append(f"Content: {mem.content}")
            if mem.tags:
                lines.append(f"Tags:    {', '.join(mem.tags)}")
            if mem.project:
                lines.append(f"Project: {mem.project}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"Failed to recall memories: {e}"


@mcp.tool(
    description=(
        "Delete a memory by its ID. "
        "USE THIS WHEN: a memory is outdated, incorrect, or no longer relevant. "
        "Pass the memory ID from recall output."
    ),
)
def forget(memory_id: str) -> str:
    """Delete a memory from Lore."""
    try:
        lore = _get_lore()
        if lore.forget(memory_id):
            return f"Memory {memory_id} forgotten."
        return f"Memory {memory_id} not found."
    except Exception as e:
        return f"Failed to forget memory: {e}"


@mcp.tool(
    description=(
        "List stored memories, optionally filtered by type or project."
    ),
)
def list_memories(
    type: Optional[str] = None,
    project: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """List memories in Lore."""
    try:
        lore = _get_lore()
        memories = lore.list_memories(type=type, project=project, limit=limit)
        if not memories:
            return "No memories found."

        lines: List[str] = [f"Found {len(memories)} memory(ies):\n"]
        for mem in memories:
            lines.append(f"[{mem.id}] ({mem.type}) {mem.content[:100]}")
            if mem.tags:
                lines.append(f"  Tags: {', '.join(mem.tags)}")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list memories: {e}"


@mcp.tool(
    description="Return memory statistics: total count, count by type, oldest and newest.",
)
def stats(project: Optional[str] = None) -> str:
    """Return memory statistics."""
    try:
        lore = _get_lore()
        s = lore.stats(project=project)
        lines = [
            f"Total memories: {s.total}",
        ]
        if s.by_type:
            lines.append("By type:")
            for t, count in sorted(s.by_type.items()):
                lines.append(f"  {t}: {count}")
        if s.oldest:
            lines.append(f"Oldest: {s.oldest}")
            lines.append(f"Newest: {s.newest}")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to get stats: {e}"


@mcp.tool(
    description=(
        "Upvote a memory that was helpful. "
        "USE THIS WHEN: you recalled a memory and it actually helped solve "
        "your problem. This boosts the memory's ranking in future searches. "
        "Pass the memory ID from recall output."
    ),
)
def upvote_memory(memory_id: str) -> str:
    """Upvote a memory to boost its ranking."""
    try:
        lore = _get_lore()
        lore.upvote(memory_id)
        return f"Upvoted memory {memory_id}"
    except Exception as e:
        return f"Failed to upvote: {e}"


@mcp.tool(
    description=(
        "Downvote a memory that was wrong or unhelpful. "
        "USE THIS WHEN: you recalled a memory but it was outdated, incorrect, "
        "or misleading. This lowers the memory's ranking so others don't waste "
        "time on bad advice. Pass the memory ID from recall output."
    ),
)
def downvote_memory(memory_id: str) -> str:
    """Downvote a memory to lower its ranking."""
    try:
        lore = _get_lore()
        lore.downvote(memory_id)
        return f"Downvoted memory {memory_id}"
    except Exception as e:
        return f"Failed to downvote: {e}"


@mcp.tool(
    description=(
        "Check if stored memories are still fresh against current git state. "
        "USE THIS WHEN: you want to verify that code-pattern memories are "
        "still relevant before acting on them. Compares memories with "
        "file_path metadata against the git commit history to detect staleness."
    ),
)
def check_freshness(
    repo_path: str,
    project: Optional[str] = None,
) -> str:
    """Check memory freshness against git history."""
    try:
        from lore.freshness.detector import FreshnessDetector
        from lore.freshness.git_ops import GitError

        try:
            FreshnessDetector.validate_repo(repo_path)
        except GitError as e:
            return f"Error: {e}"

        lore = _get_lore()
        memories = lore.list_memories(project=project)
        if not memories:
            return "No memories to check."

        detector = FreshnessDetector(repo_path)
        results = detector.check_many(memories)

        report = FreshnessDetector.format_report(results, repo_path)

        stale = [r for r in results if r.status in ("stale", "likely_stale")]
        if stale:
            ids = ", ".join(r.memory_id[:12] + "..." for r in stale[:5])
            report += (
                f"\n\nFound {len(stale)} stale/likely stale memory(ies). "
                f"Consider reviewing: {ids}"
            )

        return report
    except Exception as e:
        return f"Failed to check freshness: {e}"


@mcp.tool(
    description=(
        "Sync GitHub repository data (PRs, issues, commits, releases) into Lore as memories. "
        "USE THIS WHEN: you want to ingest tribal knowledge from a GitHub repo so it's searchable. "
        "Requires the `gh` CLI to be installed and authenticated."
    ),
)
def github_sync(
    repo: str,
    types: Optional[str] = None,
    since: Optional[str] = None,
    project: Optional[str] = None,
) -> str:
    """Sync GitHub repo data into Lore memories."""
    try:
        from lore.github.syncer import GitHubCLIError, GitHubSyncer

        lore = _get_lore()
        syncer = GitHubSyncer(lore)
        type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
        result = syncer.sync(repo, types=type_list, since=since, project=project)
        return result.summary()
    except GitHubCLIError as e:
        return f"GitHub sync failed: {e}"
    except Exception as e:
        return f"Failed to sync: {e}"


def run_server() -> None:
    """Start the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
