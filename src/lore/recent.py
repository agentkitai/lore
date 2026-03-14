"""Recent activity grouping and formatting."""

from __future__ import annotations

from typing import Any, Dict, List

from lore.types import Memory, ProjectGroup, RecentActivityResult


def group_memories_by_project(memories: List[Memory]) -> List[ProjectGroup]:
    """Group memories by project, sorted by newest first within each group.

    Groups are sorted by the most recent memory in each group.
    Memories with project=None are grouped under "default".
    """
    groups: Dict[str, List[Memory]] = {}
    for m in memories:
        key = m.project or "default"
        groups.setdefault(key, []).append(m)

    result = []
    for project, mems in groups.items():
        mems.sort(key=lambda m: m.created_at, reverse=True)
        result.append(ProjectGroup(
            project=project,
            memories=mems,
            count=len(mems),
        ))

    # Sort groups by most recent memory
    result.sort(key=lambda g: g.memories[0].created_at if g.memories else "", reverse=True)
    return result


def format_brief(result: RecentActivityResult) -> str:
    """Format as brief one-liner-per-memory output.

    Shows first 3 memories per group + overflow indicator for token budget.
    """
    if not result.groups:
        return f"No recent activity in the last {result.hours}h."

    lines = [f"## Recent Activity (last {result.hours}h)\n"]
    for group in result.groups:
        lines.append(f"### {group.project} ({group.count})")
        if group.summary:
            lines.append(group.summary)
        else:
            shown = min(3, len(group.memories))
            for m in group.memories[:shown]:
                ts = _format_time(m.created_at)
                content = m.content[:100]
                if len(m.content) > 100:
                    content += "..."
                prefix = "[Session Snapshot] " if m.type == "session_snapshot" else ""
                lines.append(f"- [{ts}] {prefix}{m.type}: {content}")
            overflow = group.count - shown
            if overflow > 0:
                lines.append(f"- ({overflow} more)")
        lines.append("")
    return "\n".join(lines)


def format_detailed(result: RecentActivityResult) -> str:
    """Format with full content and metadata."""
    if not result.groups:
        return f"No recent activity in the last {result.hours}h."

    lines = [f"## Recent Activity (last {result.hours}h)\n"]
    for group in result.groups:
        lines.append(f"### {group.project} ({group.count})")
        if group.summary:
            lines.append(f"**Summary:** {group.summary}\n")
        for m in group.memories:
            ts = _format_time(m.created_at)
            prefix = "[Session Snapshot] " if m.type == "session_snapshot" else ""
            lines.append(f"**[{ts}] {prefix}{m.type}** (tier: {m.tier}, importance: {m.importance_score:.2f})")
            lines.append(m.content)
            if m.tags:
                lines.append(f"Tags: {', '.join(m.tags)}")
            lines.append("")
    return "\n".join(lines)


def format_structured(result: RecentActivityResult) -> Dict[str, Any]:
    """Return structured dict for JSON serialization."""
    return {
        "groups": [
            {
                "project": g.project,
                "memories": [
                    {
                        "id": m.id,
                        "content": m.content,
                        "type": m.type,
                        "tier": m.tier,
                        "created_at": m.created_at,
                        "tags": m.tags,
                        "importance_score": m.importance_score,
                    }
                    for m in g.memories
                ],
                "count": g.count,
                "summary": g.summary,
            }
            for g in result.groups
        ],
        "total_count": result.total_count,
        "hours": result.hours,
        "generated_at": result.generated_at,
        "has_llm_summary": result.has_llm_summary,
        "query_time_ms": result.query_time_ms,
    }


def format_cli(result: RecentActivityResult) -> str:
    """Format for terminal output (no markdown, clean text)."""
    if not result.groups:
        return f"No recent activity in the last {result.hours}h."

    lines = [f"Recent Activity (last {result.hours}h)\n"]
    for group in result.groups:
        lines.append(f"{group.project} ({group.count} memories)")
        if group.summary:
            lines.append(f"  {group.summary}")
        else:
            for m in group.memories:
                ts = _format_time(m.created_at)
                content = m.content[:100]
                if len(m.content) > 100:
                    content += "..."
                prefix = "[Session Snapshot] " if m.type == "session_snapshot" else ""
                lines.append(f"  [{ts}] {prefix}{m.type}: {content}")
        lines.append("")
    return "\n".join(lines)


def _format_time(iso_str: str) -> str:
    """Extract HH:MM from ISO 8601 timestamp."""
    if not iso_str or len(iso_str) < 16:
        return "??:??"
    return iso_str[11:16]
