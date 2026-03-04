"""Transform GitHub API responses into Lore Memory objects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _synced_at() -> str:
    """Return current UTC timestamp for gh_synced_at metadata."""
    return datetime.now(timezone.utc).isoformat()


def pr_to_memory_kwargs(pr: Dict[str, Any], repo: str) -> Optional[Dict[str, Any]]:
    """Convert a merged PR dict (from ``gh`` JSON) to ``Lore.remember()`` kwargs.

    Returns ``None`` if the PR has no usable content.
    """
    title = pr.get("title", "").strip()
    if not title:
        return None

    body = (pr.get("body") or "").strip()
    resolution = body[:2000] if body else title

    labels = [l["name"] for l in pr.get("labels", []) if isinstance(l, dict) and "name" in l]
    tags = ["github", "pr"] + labels

    number = pr.get("number")
    url = pr.get("url") or pr.get("html_url") or ""
    merged_at = pr.get("mergedAt") or pr.get("merged_at") or ""

    return dict(
        content=f"{title}\n\n{resolution}",
        type="lesson",
        tags=tags,
        source=f"github:{repo}:pr:{number}",
        metadata={
            "gh_type": "pr",
            "gh_number": number,
            "gh_repo": repo,
            "gh_url": url,
            "gh_merged_at": merged_at,
            "gh_synced_at": _synced_at(),
        },
    )


def issue_to_memory_kwargs(issue: Dict[str, Any], repo: str) -> Optional[Dict[str, Any]]:
    """Convert a closed issue dict to ``Lore.remember()`` kwargs."""
    title = issue.get("title", "").strip()
    if not title:
        return None

    body = (issue.get("body") or "").strip()
    # Use first comment or body as resolution
    comments = issue.get("comments", {})
    if isinstance(comments, dict):
        nodes = comments.get("nodes", [])
    elif isinstance(comments, list):
        nodes = comments
    else:
        nodes = []

    resolution = ""
    if nodes:
        resolution = (nodes[-1].get("body") or "").strip()[:2000]
    if not resolution:
        resolution = body[:2000] if body else title

    problem = f"{title}\n\n{body[:1000]}" if body else title

    labels = [l["name"] for l in issue.get("labels", []) if isinstance(l, dict) and "name" in l]
    tags = ["github", "issue"] + labels

    number = issue.get("number")
    url = issue.get("url") or issue.get("html_url") or ""

    return dict(
        content=f"{problem}\n\nResolution: {resolution}",
        type="lesson",
        tags=tags,
        source=f"github:{repo}:issue:{number}",
        metadata={
            "gh_type": "issue",
            "gh_number": number,
            "gh_repo": repo,
            "gh_url": url,
            "gh_synced_at": _synced_at(),
        },
    )


def commit_to_memory_kwargs(commit: Dict[str, Any], repo: str) -> Optional[Dict[str, Any]]:
    """Convert a commit dict to ``Lore.remember()`` kwargs."""
    message = commit.get("message") or commit.get("messageHeadline") or ""
    message = message.strip()
    if not message:
        return None

    subject = message.split("\n", 1)[0]
    body = message.split("\n", 1)[1].strip() if "\n" in message else ""

    sha = commit.get("oid") or commit.get("sha") or ""
    url = commit.get("url") or commit.get("html_url") or ""

    content = f"{subject}\n\n{body}" if body else subject

    return dict(
        content=content,
        type="lesson",
        tags=["github", "commit"],
        source=f"github:{repo}:commit:{sha[:12]}",
        metadata={
            "gh_type": "commit",
            "gh_sha": sha,
            "gh_repo": repo,
            "gh_url": url,
            "gh_synced_at": _synced_at(),
        },
    )


def release_to_memory_kwargs(release: Dict[str, Any], repo: str) -> Optional[Dict[str, Any]]:
    """Convert a release dict to ``Lore.remember()`` kwargs."""
    name = (release.get("name") or release.get("tagName") or "").strip()
    tag = (release.get("tagName") or release.get("tag_name") or "").strip()
    if not name and not tag:
        return None

    body = (release.get("description") or release.get("body") or "").strip()
    title = name or tag
    content = f"{title}\n\n{body[:2000]}" if body else title

    url = release.get("url") or release.get("html_url") or ""

    return dict(
        content=content,
        type="lesson",
        tags=["github", "release", tag] if tag else ["github", "release"],
        source=f"github:{repo}:release:{tag}",
        metadata={
            "gh_type": "release",
            "gh_tag": tag,
            "gh_repo": repo,
            "gh_url": url,
            "gh_synced_at": _synced_at(),
        },
    )


TRANSFORM_MAP = {
    "prs": pr_to_memory_kwargs,
    "issues": issue_to_memory_kwargs,
    "commits": commit_to_memory_kwargs,
    "releases": release_to_memory_kwargs,
}
