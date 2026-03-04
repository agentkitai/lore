"""GitHubSyncer — orchestrates fetching GitHub data and storing as memories."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from lore.github.state import get_sync_state, update_sync_state, utc_now_iso
from lore.github.transforms import (
    commit_to_memory_kwargs,
    issue_to_memory_kwargs,
    pr_to_memory_kwargs,
    release_to_memory_kwargs,
)


class GitHubCLIError(Exception):
    """Raised when the ``gh`` CLI is missing, not authenticated, or returns an error."""


@dataclass
class SyncResult:
    """Summary of a GitHub sync operation."""

    repo: str
    prs: int = 0
    issues: int = 0
    commits: int = 0
    releases: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.prs + self.issues + self.commits + self.releases

    def summary(self) -> str:
        parts = []
        if self.prs:
            parts.append(f"{self.prs} PRs")
        if self.issues:
            parts.append(f"{self.issues} issues")
        if self.commits:
            parts.append(f"{self.commits} commits")
        if self.releases:
            parts.append(f"{self.releases} releases")
        detail = ", ".join(parts) if parts else "nothing new"
        msg = f"Synced {self.total} memories from {self.repo} ({detail})"
        if self.errors:
            msg += f"\nErrors: {'; '.join(self.errors)}"
        return msg


# ---------------------------------------------------------------------------
# gh CLI wrapper
# ---------------------------------------------------------------------------

def _run_gh(args: Sequence[str], timeout: int = 30) -> str:
    """Execute ``gh`` with *args* and return stdout.

    Raises :class:`GitHubCLIError` on failure.
    """
    cmd = ["gh", *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise GitHubCLIError(
            "GitHub CLI (gh) not found. Install it: https://cli.github.com"
        )
    except subprocess.TimeoutExpired:
        raise GitHubCLIError(f"gh command timed out after {timeout}s: {' '.join(cmd)}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "auth login" in stderr or "not logged" in stderr.lower():
            raise GitHubCLIError("GitHub CLI not authenticated. Run `gh auth login` first.")
        raise GitHubCLIError(f"gh failed (exit {result.returncode}): {stderr}")

    return result.stdout


def _gh_api(endpoint: str, params: Optional[Dict[str, str]] = None, paginate: bool = False) -> Any:
    """Call ``gh api`` and return parsed JSON."""
    args = ["api", endpoint]
    if paginate:
        args.append("--paginate")
    for k, v in (params or {}).items():
        args.extend(["-f", f"{k}={v}"])
    raw = _run_gh(args, timeout=60)
    # --paginate can return multiple JSON arrays; merge them
    if paginate and raw.strip().startswith("["):
        merged: List[Any] = []
        for chunk in raw.split("\n"):
            chunk = chunk.strip()
            if chunk:
                merged.extend(json.loads(chunk))
        return merged
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def fetch_merged_prs(repo: str, since: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch merged PRs from *repo* using ``gh pr list``."""
    args = [
        "pr", "list",
        "--repo", repo,
        "--state", "merged",
        "--limit", str(limit),
        "--json", "number,title,body,labels,url,mergedAt",
    ]
    raw = _run_gh(args, timeout=60)
    prs = json.loads(raw)
    if since:
        prs = [p for p in prs if (p.get("mergedAt") or "") >= since]
    return prs


def fetch_closed_issues(repo: str, since: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch closed issues from *repo* using ``gh issue list``."""
    args = [
        "issue", "list",
        "--repo", repo,
        "--state", "closed",
        "--limit", str(limit),
        "--json", "number,title,body,labels,url,comments",
    ]
    raw = _run_gh(args, timeout=60)
    issues = json.loads(raw)
    if since:
        issues = [i for i in issues if (i.get("closedAt") or i.get("updatedAt") or "") >= since]
    return issues


def fetch_releases(repo: str, since: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
    """Fetch published releases from *repo*."""
    args = [
        "release", "list",
        "--repo", repo,
        "--limit", str(limit),
        "--json", "name,tagName,body,url,publishedAt",
    ]
    # gh release list --json may not support 'body' on older versions; fall back
    try:
        raw = _run_gh(args, timeout=60)
        releases = json.loads(raw)
    except GitHubCLIError:
        args = [
            "release", "list",
            "--repo", repo,
            "--limit", str(limit),
            "--json", "name,tagName,url,publishedAt",
        ]
        raw = _run_gh(args, timeout=60)
        releases = json.loads(raw)
    if since:
        releases = [r for r in releases if (r.get("publishedAt") or "") >= since]
    return releases


def fetch_notable_commits(repo: str, since: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch recent commits using ``gh api``."""
    endpoint = f"repos/{repo}/commits"
    params: Dict[str, str] = {"per_page": str(limit)}
    if since:
        params["since"] = since
    try:
        commits_raw = _gh_api(endpoint, params)
    except GitHubCLIError:
        return []
    commits = []
    for c in commits_raw:
        cm = c.get("commit", {})
        commits.append({
            "sha": c.get("sha", ""),
            "message": cm.get("message", ""),
            "url": c.get("html_url", ""),
        })
    return commits


# ---------------------------------------------------------------------------
# Syncer
# ---------------------------------------------------------------------------

ALL_TYPES = ("prs", "issues", "commits", "releases")


class GitHubSyncer:
    """Orchestrates syncing GitHub repo data into Lore memories.

    Parameters
    ----------
    lore : Lore
        A configured Lore client instance.
    state_path : str, optional
        Override the default sync state file path.
    """

    def __init__(self, lore: Any, state_path: Optional[str] = None) -> None:
        self._lore = lore
        self._state_path = state_path

    def sync(
        self,
        repo: str,
        *,
        types: Optional[Sequence[str]] = None,
        since: Optional[str] = None,
        full: bool = False,
        dry_run: bool = False,
        project: Optional[str] = None,
    ) -> SyncResult:
        """Run a sync for *repo*.

        Parameters
        ----------
        repo : str
            GitHub ``owner/repo`` identifier.
        types : sequence of str, optional
            Subset of ``("prs", "issues", "commits", "releases")``. Default all.
        since : str, optional
            ISO-8601 date/time override for incremental start.
        full : bool
            If ``True``, ignore saved sync state and do a full sync.
        dry_run : bool
            If ``True``, fetch and transform but do not store.
        project : str, optional
            Project namespace for stored memories.
        """
        types = types or ALL_TYPES
        result = SyncResult(repo=repo)

        # Determine incremental start point
        state_kwargs = {"path": self._state_path} if self._state_path else {}
        state = None if full else get_sync_state(repo, **state_kwargs)
        effective_since = since or (state.get("last_sync") if state else None)

        for entity_type in types:
            if entity_type not in ALL_TYPES:
                result.errors.append(f"Unknown type: {entity_type}")
                continue
            try:
                count = self._sync_type(
                    repo, entity_type, effective_since, dry_run, project,
                )
                setattr(result, entity_type, count)
            except GitHubCLIError as exc:
                result.errors.append(f"{entity_type}: {exc}")

        # Update sync state
        if not dry_run and result.total > 0:
            update_sync_state(repo, last_sync=utc_now_iso(), **state_kwargs)

        return result

    def _sync_type(
        self,
        repo: str,
        entity_type: str,
        since: Optional[str],
        dry_run: bool,
        project: Optional[str],
    ) -> int:
        """Fetch, transform, and store one entity type. Returns count stored."""
        items = _fetch(entity_type, repo, since)

        transform = {
            "prs": pr_to_memory_kwargs,
            "issues": issue_to_memory_kwargs,
            "commits": commit_to_memory_kwargs,
            "releases": release_to_memory_kwargs,
        }[entity_type]

        count = 0
        for item in items:
            kwargs = transform(item, repo)
            if kwargs is None:
                continue
            if project:
                kwargs["project"] = project
            if dry_run:
                count += 1
                continue
            # Dedup by source (gh_type + identifier)
            source = kwargs.get("source", "")
            if source and self._source_exists(source):
                continue
            self._lore.remember(**kwargs)
            count += 1
        return count

    def _source_exists(self, source: str) -> bool:
        """Check if a memory with this source already exists."""
        memories = self._lore.list_memories()
        return any(m.source == source for m in memories)


def _fetch(entity_type: str, repo: str, since: Optional[str]) -> List[Dict[str, Any]]:
    """Dispatch to the right fetcher."""
    fetchers = {
        "prs": fetch_merged_prs,
        "issues": fetch_closed_issues,
        "commits": fetch_notable_commits,
        "releases": fetch_releases,
    }
    return fetchers[entity_type](repo, since=since)
