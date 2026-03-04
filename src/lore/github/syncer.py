"""GitHubSyncer — orchestrates fetching GitHub data and storing as memories."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

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
# Data fetchers — paginated via gh api with --paginate
# ---------------------------------------------------------------------------

def fetch_merged_prs(repo: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch merged PRs from *repo* using ``gh api`` with pagination."""
    endpoint = f"repos/{repo}/pulls"
    params: Dict[str, str] = {
        "state": "closed",
        "per_page": "100",
        "sort": "updated",
        "direction": "desc",
    }
    raw = _gh_api(endpoint, params, paginate=True)
    prs = []
    for p in raw:
        if not p.get("merged_at"):
            continue
        merged_at = p.get("merged_at", "")
        if since and merged_at < since:
            continue
        labels = [lb.get("name", "") for lb in p.get("labels", []) if isinstance(lb, dict)]
        prs.append({
            "number": p.get("number"),
            "title": p.get("title", ""),
            "body": p.get("body") or "",
            "labels": [{"name": n} for n in labels],
            "url": p.get("html_url", ""),
            "mergedAt": merged_at,
        })
    return prs


def fetch_closed_issues(repo: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch closed issues from *repo* using ``gh api`` with pagination."""
    endpoint = f"repos/{repo}/issues"
    params: Dict[str, str] = {
        "state": "closed",
        "per_page": "100",
        "sort": "updated",
        "direction": "desc",
    }
    if since:
        params["since"] = since
    raw = _gh_api(endpoint, params, paginate=True)
    issues = []
    for i in raw:
        # Skip pull requests (GitHub API returns them in /issues too)
        if "pull_request" in i:
            continue
        closed_at = i.get("closed_at") or ""
        if since and closed_at < since:
            continue
        # Fetch last comment if any
        comments_list: List[Dict[str, Any]] = []
        comments_url = i.get("comments_url")
        comment_count = i.get("comments", 0)
        if comment_count and comments_url:
            try:
                endpoint_c = f"repos/{repo}/issues/{i['number']}/comments"
                comments_raw = _gh_api(endpoint_c, {"per_page": "1", "page": str(max(1, comment_count))})
                if isinstance(comments_raw, list):
                    comments_list = comments_raw
            except GitHubCLIError:
                pass
        labels = [lb.get("name", "") for lb in i.get("labels", []) if isinstance(lb, dict)]
        issues.append({
            "number": i.get("number"),
            "title": i.get("title", ""),
            "body": i.get("body") or "",
            "labels": [{"name": n} for n in labels],
            "url": i.get("html_url", ""),
            "closedAt": closed_at,
            "comments": comments_list,
        })
    return issues


def fetch_releases(repo: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch published releases from *repo* using ``gh api`` with pagination."""
    endpoint = f"repos/{repo}/releases"
    params: Dict[str, str] = {"per_page": "100"}
    raw = _gh_api(endpoint, params, paginate=True)
    releases = []
    for r in raw:
        published_at = r.get("published_at") or ""
        if since and published_at < since:
            continue
        releases.append({
            "name": r.get("name") or "",
            "tagName": r.get("tag_name") or "",
            "body": r.get("body") or "",
            "url": r.get("html_url") or "",
            "publishedAt": published_at,
        })
    return releases


def fetch_notable_commits(repo: str, since: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch recent commits using ``gh api``."""
    endpoint = f"repos/{repo}/commits"
    params: Dict[str, str] = {"per_page": str(limit)}
    if since:
        params["since"] = since
    commits_raw = _gh_api(endpoint, params)
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
        self._known_sources: Optional[Set[str]] = None

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

        # Pre-cache known sources for O(1) dedup lookups
        self._known_sources = None

        last_pr = None
        last_issue = None

        for entity_type in types:
            if entity_type not in ALL_TYPES:
                result.errors.append(f"Unknown type: {entity_type}")
                continue
            try:
                count, last_id = self._sync_type(
                    repo, entity_type, effective_since, dry_run, project,
                )
                setattr(result, entity_type, count)
                if entity_type == "prs" and last_id is not None:
                    last_pr = last_id
                elif entity_type == "issues" and last_id is not None:
                    last_issue = last_id
            except GitHubCLIError as exc:
                result.errors.append(f"{entity_type}: {exc}")

        # Update sync state
        if not dry_run and result.total > 0:
            state_update: Dict[str, Any] = {"last_sync": utc_now_iso()}
            if last_pr is not None:
                state_update["last_pr"] = last_pr
            if last_issue is not None:
                state_update["last_issue"] = last_issue
            update_sync_state(repo, **state_update, **state_kwargs)

        return result

    def _sync_type(
        self,
        repo: str,
        entity_type: str,
        since: Optional[str],
        dry_run: bool,
        project: Optional[str],
    ) -> tuple[int, Optional[int]]:
        """Fetch, transform, and store one entity type. Returns (count, last_id)."""
        items = _fetch(entity_type, repo, since)

        transform = {
            "prs": pr_to_memory_kwargs,
            "issues": issue_to_memory_kwargs,
            "commits": commit_to_memory_kwargs,
            "releases": release_to_memory_kwargs,
        }[entity_type]

        count = 0
        last_id = None
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
            # Track the source for future dedup within this sync run
            if self._known_sources is not None:
                self._known_sources.add(source)
            count += 1
            # Track last PR/issue number
            number = kwargs.get("metadata", {}).get("gh_number")
            if number is not None:
                last_id = number
        return count, last_id

    def _source_exists(self, source: str) -> bool:
        """Check if a memory with this source already exists (cached)."""
        if self._known_sources is None:
            memories = self._lore.list_memories()
            self._known_sources = {m.source for m in memories if m.source}
        return source in self._known_sources


def _fetch(entity_type: str, repo: str, since: Optional[str]) -> List[Dict[str, Any]]:
    """Dispatch to the right fetcher."""
    fetchers = {
        "prs": fetch_merged_prs,
        "issues": fetch_closed_issues,
        "commits": fetch_notable_commits,
        "releases": fetch_releases,
    }
    return fetchers[entity_type](repo, since=since)
