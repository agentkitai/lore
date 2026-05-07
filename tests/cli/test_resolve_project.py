"""Phase 6G T2: ``resolve_project`` and ``_normalize_remote_url`` helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lore.cli.commands._project import (
    _normalize_remote_url,
    resolve_project,
)


# ── _normalize_remote_url ─────────────────────────────────────────────


def test_normalize_https_with_dot_git():
    assert (
        _normalize_remote_url("https://github.com/user/repo.git")
        == "github.com/user/repo"
    )


def test_normalize_https_without_dot_git():
    assert (
        _normalize_remote_url("https://github.com/user/repo")
        == "github.com/user/repo"
    )


def test_normalize_ssh_scp_form_with_dot_git():
    assert (
        _normalize_remote_url("git@github.com:user/repo.git")
        == "github.com/user/repo"
    )


def test_normalize_ssh_scp_form_without_dot_git():
    assert _normalize_remote_url("git@github.com:user/repo") == "github.com/user/repo"


def test_normalize_ssh_url_form():
    assert (
        _normalize_remote_url("ssh://git@gitlab.com/group/repo.git")
        == "gitlab.com/group/repo"
    )


def test_normalize_lowercases_host_only():
    """Host case folds; path case is preserved (some hosts are case-sensitive)."""
    assert (
        _normalize_remote_url("https://GitHub.com/User/Repo.git")
        == "github.com/User/Repo"
    )


def test_normalize_handles_port_in_url():
    assert (
        _normalize_remote_url("https://gitea.example.com:8080/team/repo.git")
        == "gitea.example.com/team/repo"
    )


def test_normalize_returns_none_for_empty_or_garbage():
    assert _normalize_remote_url("") is None
    assert _normalize_remote_url("   ") is None
    assert _normalize_remote_url("not a url") is None


# ── resolve_project (uses real git on a tmp repo) ─────────────────────


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _has_git() -> bool:
    try:
        subprocess.run(
            ["git", "--version"],
            check=True,
            capture_output=True,
            timeout=2.0,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


needs_git = pytest.mark.skipif(not _has_git(), reason="git not available")


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the resolve_project cache between tests so each test sees fresh git state."""
    resolve_project.cache_clear()
    yield
    resolve_project.cache_clear()


def test_resolve_project_returns_none_outside_git_repo(tmp_path: Path):
    # tmp_path is not inside a git repo (and likely not inside any
    # filesystem ancestor that is one — make sure by writing into a
    # fresh subdir).
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    assert resolve_project(not_a_repo) is None


@needs_git
def test_resolve_project_uses_remote_url_when_present(tmp_path: Path):
    repo = tmp_path / "repo_with_remote"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", "https://github.com/example/myproj.git")
    assert resolve_project(repo) == "github.com/example/myproj"


@needs_git
def test_resolve_project_falls_back_to_common_dir_basename(tmp_path: Path):
    repo = tmp_path / "named_repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    # No remote configured → fall back to common-dir parent's basename.
    assert resolve_project(repo) == "named_repo"


@needs_git
def test_resolve_project_caches_result(tmp_path: Path):
    repo = tmp_path / "cached_repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", "https://github.com/example/cached.git")

    first = resolve_project(repo)
    # Mutate the remote — if the cache works, we should still see the
    # original answer for the same cwd.
    _git(repo, "remote", "set-url", "origin", "https://github.com/example/changed.git")
    second = resolve_project(repo)

    assert first == "github.com/example/cached"
    assert second == first  # cached, not re-read

    # After clearing the cache, the new value comes through.
    resolve_project.cache_clear()
    assert resolve_project(repo) == "github.com/example/changed"
