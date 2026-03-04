"""Git CLI wrapper for freshness detection.

All git operations use subprocess with read-only commands and a 5-second timeout.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMEOUT = 5


class GitError(Exception):
    """Raised when a git operation fails."""


def _run_git(repo_path: str, *args: str, timeout: int = _TIMEOUT) -> str:
    """Run a git command and return stdout. Raises GitError on failure."""
    cmd = ["git", "-C", repo_path, *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise GitError(f"git command timed out after {timeout}s")

    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git exited with code {result.returncode}")

    return result.stdout


def is_git_repo(repo_path: str) -> bool:
    """Check if the given path is inside a git repository."""
    try:
        _run_git(repo_path, "rev-parse", "--git-dir")
        return True
    except GitError:
        return False


def git_log_count(repo_path: str, file_path: str, since: str) -> int:
    """Count commits to a file since a given ISO date.

    Args:
        repo_path: Path to the git repository root.
        file_path: Relative path within the repo.
        since: ISO 8601 date string.

    Returns:
        Number of commits touching the file since the given date.

    Raises:
        GitError: If git is not installed or the repo path is invalid.
    """
    try:
        output = _run_git(
            repo_path,
            "log",
            "--oneline",
            f"--since={since}",
            "--",
            file_path,
        )
    except GitError as e:
        # Propagate "git not installed" and "not a git repo" errors
        msg = str(e)
        if "not installed" in msg or "not a git repository" in msg:
            raise
        # Other errors (e.g. file not found in log) → 0
        return 0

    lines = [line for line in output.strip().splitlines() if line]
    return len(lines)


def file_exists_in_repo(repo_path: str, file_path: str) -> bool:
    """Check if a file exists in the repo's git index (HEAD)."""
    try:
        _run_git(repo_path, "cat-file", "-e", f"HEAD:{file_path}")
        return True
    except GitError:
        # Fall back to filesystem check (for files that are tracked but not yet committed)
        full = Path(repo_path) / file_path
        return full.is_file()


def file_contains_pattern(repo_path: str, file_path: str, pattern: str) -> bool:
    """Check if a file in the repo contains the given text pattern."""
    full = Path(repo_path) / file_path
    if not full.is_file():
        return False
    try:
        content = full.read_text(errors="replace")
        return pattern in content
    except OSError:
        return False
