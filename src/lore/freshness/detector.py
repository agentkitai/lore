"""Freshness detector — compares memories against current git state."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from lore.freshness.git_ops import (
    GitError,
    file_exists_in_repo,
    git_log_count,
    is_git_repo,
)
from lore.freshness.types import StalenessResult, StalenessStatus
from lore.types import Memory

# Default commit-count thresholds: (min_commits, status, confidence)
_DEFAULT_THRESHOLDS: List[Tuple[int, StalenessStatus, float]] = [
    (25, "stale", 0.9),
    (10, "likely_stale", 0.6),
    (3, "possibly_stale", 0.3),
]


class FreshnessDetector:
    """Detects stale memories by comparing against git commit history.

    Args:
        repo_path: Path to the git repository.
        thresholds: Optional custom thresholds as list of
            (min_commits, status, confidence) tuples, sorted descending by min_commits.
    """

    def __init__(
        self,
        repo_path: str,
        thresholds: Optional[List[Tuple[int, StalenessStatus, float]]] = None,
    ) -> None:
        self.repo_path = repo_path
        self._thresholds = thresholds or _DEFAULT_THRESHOLDS

    def check(self, memory: Memory) -> StalenessResult:
        """Check a single memory for staleness."""
        meta = memory.metadata or {}
        file_path = meta.get("file_path")

        if not file_path:
            return StalenessResult(
                memory_id=memory.id,
                status="unknown",
                confidence=0.0,
                commits_since=0,
                file_exists=True,
                reason="no file_path in metadata",
            )

        if not file_exists_in_repo(self.repo_path, file_path):
            return StalenessResult(
                memory_id=memory.id,
                status="stale",
                confidence=1.0,
                commits_since=0,
                file_exists=False,
                reason="file no longer exists",
            )

        commits = git_log_count(
            self.repo_path, file_path, since=memory.created_at,
        )

        status, confidence = self._classify(commits)

        return StalenessResult(
            memory_id=memory.id,
            status=status,
            confidence=confidence,
            commits_since=commits,
            file_exists=True,
            reason=f"{commits} commit(s) to {file_path} since memory creation",
        )

    def check_many(self, memories: List[Memory]) -> List[StalenessResult]:
        """Check multiple memories for staleness."""
        return [self.check(m) for m in memories]

    def _classify(self, commits: int) -> Tuple[StalenessStatus, float]:
        """Classify commit count into staleness status and confidence."""
        for min_commits, status, confidence in self._thresholds:
            if commits >= min_commits:
                return status, confidence
        return "fresh", 0.1

    @staticmethod
    def validate_repo(repo_path: str) -> None:
        """Raise GitError if repo_path is not a valid git repository."""
        if not is_git_repo(repo_path):
            raise GitError(f"not a git repository: {repo_path}")

    @staticmethod
    def format_report(
        results: List[StalenessResult],
        repo_path: str,
        markdown: bool = False,
    ) -> str:
        """Format staleness results as a human-readable report.

        Args:
            results: List of staleness check results.
            repo_path: Path to the git repository.
            markdown: If True, format as Markdown (for MCP).
        """
        if markdown:
            return FreshnessDetector._format_markdown(results, repo_path)
        return FreshnessDetector._format_table(results, repo_path)

    @staticmethod
    def _format_table(results: List[StalenessResult], repo_path: str) -> str:
        """Format as plain-text table."""
        lines: List[str] = [
            f"Freshness Report for {repo_path}",
            "\u2500" * 60,
            f"{'ID':<28} {'Status':<20} {'Commits':<8} {'Reason'}",
            "\u2500" * 60,
        ]

        counts: Dict[str, int] = {
            "stale": 0, "likely_stale": 0, "possibly_stale": 0,
            "fresh": 0, "unknown": 0,
        }

        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
            status_str = f"{r.status} ({r.confidence:.1f})"
            lines.append(
                f"{r.memory_id:<28} {status_str:<20} {r.commits_since:<8} {r.reason}"
            )

        lines.append("\u2500" * 60)
        # Always show all categories
        parts = []
        for label in ("stale", "likely stale", "possibly stale", "fresh", "unknown"):
            key = label.replace(" ", "_")
            parts.append(f"{counts[key]} {label}")
        lines.append(f"Summary: {', '.join(parts)} ({len(results)} total)")

        return "\n".join(lines)

    @staticmethod
    def _format_markdown(results: List[StalenessResult], repo_path: str) -> str:
        """Format as Markdown for MCP responses."""
        lines: List[str] = [
            f"## Freshness Report for `{repo_path}`",
            "",
            "| ID | Status | Commits | Reason |",
            "|---|---|---|---|",
        ]

        counts: Dict[str, int] = {
            "stale": 0, "likely_stale": 0, "possibly_stale": 0,
            "fresh": 0, "unknown": 0,
        }

        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
            status_display = r.status.replace("_", " ")
            lines.append(
                f"| `{r.memory_id}` | {status_display} ({r.confidence:.1f}) "
                f"| {r.commits_since} | {r.reason} |"
            )

        lines.append("")
        parts = []
        for label in ("stale", "likely stale", "possibly stale", "fresh", "unknown"):
            key = label.replace(" ", "_")
            parts.append(f"**{counts[key]}** {label}")
        lines.append(f"**Summary:** {', '.join(parts)} ({len(results)} total)")

        return "\n".join(lines)
