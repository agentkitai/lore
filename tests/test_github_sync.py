"""Tests for F4 — GitHub Sync (PRs, issues, commits, releases)."""

from __future__ import annotations

import pytest
mcp = pytest.importorskip("mcp", reason="mcp not installed")

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from lore import Lore
from lore.github.state import get_sync_state, list_synced_repos, update_sync_state
from lore.github.syncer import (
    GitHubCLIError,
    GitHubSyncer,
    SyncResult,
    _run_gh,
    fetch_closed_issues,
    fetch_merged_prs,
    fetch_notable_commits,
    fetch_releases,
)
from lore.github.transforms import (
    commit_to_memory_kwargs,
    issue_to_memory_kwargs,
    pr_to_memory_kwargs,
    release_to_memory_kwargs,
)
from lore.store.memory import MemoryStore


def _stub_embed(text: str):
    return [0.0] * 384


def _make_lore() -> Lore:
    return Lore(store=MemoryStore(), embedding_fn=_stub_embed)


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


class TestTransforms:
    def test_pr_to_memory_kwargs(self):
        pr = {
            "number": 42,
            "title": "Fix auth bug",
            "body": "Resolved JWT expiration issue",
            "labels": [{"name": "bug"}, {"name": "auth"}],
            "url": "https://github.com/acme/repo/pull/42",
            "mergedAt": "2026-01-15T10:00:00Z",
        }
        result = pr_to_memory_kwargs(pr, "acme/repo")
        assert result is not None
        assert "Fix auth bug" in result["content"]
        assert "JWT expiration" in result["content"]
        assert result["type"] == "lesson"
        assert "github" in result["tags"]
        assert "pr" in result["tags"]
        assert "bug" in result["tags"]
        assert result["source"] == "github:acme/repo:pr:42"
        assert result["metadata"]["gh_type"] == "pr"
        assert result["metadata"]["gh_number"] == 42
        assert result["metadata"]["gh_repo"] == "acme/repo"
        assert "gh_synced_at" in result["metadata"]

    def test_pr_empty_title_returns_none(self):
        assert pr_to_memory_kwargs({"title": "", "number": 1}, "a/b") is None

    def test_pr_empty_body_uses_title(self):
        pr = {"number": 1, "title": "Title only", "body": None, "labels": []}
        result = pr_to_memory_kwargs(pr, "a/b")
        assert result is not None
        assert "Title only" in result["content"]

    def test_issue_to_memory_kwargs(self):
        issue = {
            "number": 10,
            "title": "App crashes on startup",
            "body": "Steps to reproduce: open the app",
            "labels": [{"name": "critical"}],
            "url": "https://github.com/acme/repo/issues/10",
            "comments": {"nodes": [{"body": "Fixed in PR #42"}]},
        }
        result = issue_to_memory_kwargs(issue, "acme/repo")
        assert result is not None
        assert "App crashes" in result["content"]
        assert "Fixed in PR #42" in result["content"]
        assert "issue" in result["tags"]
        assert result["metadata"]["gh_type"] == "issue"
        assert "gh_synced_at" in result["metadata"]

    def test_issue_no_comments_uses_body(self):
        issue = {
            "number": 5,
            "title": "Bug",
            "body": "Body text here",
            "labels": [],
            "comments": {"nodes": []},
        }
        result = issue_to_memory_kwargs(issue, "a/b")
        assert result is not None
        assert "Body text here" in result["content"]

    def test_issue_empty_title_returns_none(self):
        assert issue_to_memory_kwargs({"title": "", "number": 1}, "a/b") is None

    def test_commit_to_memory_kwargs(self):
        commit = {
            "sha": "abc123def456",
            "message": "refactor: simplify auth flow\n\nRemoved unused middleware.",
            "url": "https://github.com/acme/repo/commit/abc123def456",
        }
        result = commit_to_memory_kwargs(commit, "acme/repo")
        assert result is not None
        assert "simplify auth flow" in result["content"]
        assert "Removed unused middleware" in result["content"]
        assert "commit" in result["tags"]
        assert result["metadata"]["gh_sha"] == "abc123def456"
        assert "gh_synced_at" in result["metadata"]

    def test_commit_empty_message_returns_none(self):
        assert commit_to_memory_kwargs({"message": "", "sha": "abc"}, "a/b") is None

    def test_release_to_memory_kwargs(self):
        release = {
            "name": "v2.0.0",
            "tagName": "v2.0.0",
            "body": "Major release with new API",
            "url": "https://github.com/acme/repo/releases/tag/v2.0.0",
        }
        result = release_to_memory_kwargs(release, "acme/repo")
        assert result is not None
        assert "v2.0.0" in result["content"]
        assert "release" in result["tags"]
        assert "v2.0.0" in result["tags"]
        assert result["metadata"]["gh_tag"] == "v2.0.0"
        assert "gh_synced_at" in result["metadata"]

    def test_release_no_name_uses_tag(self):
        release = {"name": "", "tagName": "v1.0", "body": ""}
        result = release_to_memory_kwargs(release, "a/b")
        assert result is not None
        assert "v1.0" in result["content"]

    def test_release_empty_returns_none(self):
        assert release_to_memory_kwargs({"name": "", "tagName": ""}, "a/b") is None


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestSyncState:
    def test_get_state_missing_file(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        assert get_sync_state("owner/repo", path=path) is None

    def test_update_and_get_state(self, tmp_path):
        path = str(tmp_path / "state.json")
        update_sync_state("acme/repo", last_sync="2026-03-01T00:00:00Z", last_pr=42, path=path)
        state = get_sync_state("acme/repo", path=path)
        assert state is not None
        assert state["last_sync"] == "2026-03-01T00:00:00Z"
        assert state["last_pr"] == 42

    def test_update_merges(self, tmp_path):
        path = str(tmp_path / "state.json")
        update_sync_state("a/b", last_pr=10, path=path)
        update_sync_state("a/b", last_issue=20, path=path)
        state = get_sync_state("a/b", path=path)
        assert state["last_pr"] == 10
        assert state["last_issue"] == 20

    def test_list_synced_repos(self, tmp_path):
        path = str(tmp_path / "state.json")
        update_sync_state("a/b", last_sync="2026-01-01T00:00:00Z", path=path)
        update_sync_state("c/d", last_sync="2026-02-01T00:00:00Z", path=path)
        repos = list_synced_repos(path=path)
        assert "a/b" in repos
        assert "c/d" in repos


# ---------------------------------------------------------------------------
# gh CLI wrapper
# ---------------------------------------------------------------------------


class TestRunGh:
    def test_gh_not_found(self):
        with patch("lore.github.syncer.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(GitHubCLIError, match="not found"):
                _run_gh(["version"])

    def test_gh_timeout(self):
        with patch(
            "lore.github.syncer.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5),
        ):
            with pytest.raises(GitHubCLIError, match="timed out"):
                _run_gh(["api", "user"], timeout=5)

    def test_gh_auth_error(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "To get started with GitHub CLI, please run:  gh auth login"
        with patch("lore.github.syncer.subprocess.run", return_value=mock_result):
            with pytest.raises(GitHubCLIError, match="auth login"):
                _run_gh(["api", "user"])

    def test_gh_generic_error(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"
        with patch("lore.github.syncer.subprocess.run", return_value=mock_result):
            with pytest.raises(GitHubCLIError, match="some error"):
                _run_gh(["api", "user"])

    def test_gh_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"login": "octocat"}'
        with patch("lore.github.syncer.subprocess.run", return_value=mock_result):
            output = _run_gh(["api", "user"])
            assert "octocat" in output


# ---------------------------------------------------------------------------
# Fetchers (mock subprocess) — now use gh api with --paginate
# ---------------------------------------------------------------------------

# API-format sample data (REST API response format)
SAMPLE_API_PRS = json.dumps([
    {
        "number": 1,
        "title": "Add caching",
        "body": "Implemented Redis caching layer",
        "labels": [{"name": "enhancement"}],
        "html_url": "https://github.com/acme/app/pull/1",
        "merged_at": "2026-02-01T10:00:00Z",
    },
    {
        "number": 2,
        "title": "Fix login",
        "body": "Session cookie was not being set",
        "labels": [{"name": "bug"}],
        "html_url": "https://github.com/acme/app/pull/2",
        "merged_at": "2026-03-01T10:00:00Z",
    },
    {
        "number": 3,
        "title": "Unmerged PR",
        "body": "Still open",
        "labels": [],
        "html_url": "https://github.com/acme/app/pull/3",
        "merged_at": None,
    },
])

SAMPLE_API_ISSUES = json.dumps([
    {
        "number": 10,
        "title": "Dashboard slow",
        "body": "Takes 5s to load",
        "labels": [],
        "html_url": "https://github.com/acme/app/issues/10",
        "closed_at": "2026-02-15T10:00:00Z",
        "comments": 1,
        "comments_url": "https://api.github.com/repos/acme/app/issues/10/comments",
    },
])

SAMPLE_API_ISSUE_COMMENTS = json.dumps([
    {"body": "Fixed with query optimization"},
])

SAMPLE_API_RELEASES = json.dumps([
    {
        "name": "v1.0.0",
        "tag_name": "v1.0.0",
        "body": "Initial stable release",
        "html_url": "https://github.com/acme/app/releases/tag/v1.0.0",
        "published_at": "2026-01-01T00:00:00Z",
    },
])

SAMPLE_API_COMMITS = json.dumps([
    {
        "sha": "aaa111",
        "commit": {"message": "feat: add user dashboard"},
        "html_url": "https://github.com/acme/app/commit/aaa111",
    },
])


def _mock_run_gh(args, timeout=30):
    """Simulate ``_run_gh`` for different gh api subcommands."""
    joined = " ".join(args)
    if "api" in joined and "repos/" in joined:
        if "/pulls" in joined and "/comments" not in joined:
            return SAMPLE_API_PRS
        if "/issues/" in joined and "/comments" in joined:
            return SAMPLE_API_ISSUE_COMMENTS
        if "/issues" in joined:
            return SAMPLE_API_ISSUES
        if "/releases" in joined:
            return SAMPLE_API_RELEASES
        if "/commits" in joined:
            return SAMPLE_API_COMMITS
    return "[]"


class TestFetchers:
    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_fetch_merged_prs(self, mock):
        prs = fetch_merged_prs("acme/app")
        assert len(prs) == 2  # Only merged PRs, not the unmerged one
        assert prs[0]["title"] == "Add caching"

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_fetch_prs_since(self, mock):
        prs = fetch_merged_prs("acme/app", since="2026-02-15T00:00:00Z")
        assert len(prs) == 1
        assert prs[0]["title"] == "Fix login"

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_fetch_closed_issues(self, mock):
        issues = fetch_closed_issues("acme/app")
        assert len(issues) == 1

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_fetch_releases(self, mock):
        releases = fetch_releases("acme/app")
        assert len(releases) == 1
        assert releases[0]["tagName"] == "v1.0.0"

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_fetch_commits(self, mock):
        commits = fetch_notable_commits("acme/app")
        assert len(commits) == 1
        assert "dashboard" in commits[0]["message"]


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------


class TestSyncResult:
    def test_total(self):
        r = SyncResult(repo="a/b", prs=3, issues=2, commits=1, releases=1)
        assert r.total == 7

    def test_summary(self):
        r = SyncResult(repo="a/b", prs=3, issues=2)
        s = r.summary()
        assert "Synced 5 memories from a/b" in s
        assert "3 PRs" in s
        assert "2 issues" in s

    def test_summary_nothing(self):
        r = SyncResult(repo="a/b")
        assert "nothing new" in r.summary()

    def test_summary_errors(self):
        r = SyncResult(repo="a/b", errors=["oops"])
        assert "oops" in r.summary()


# ---------------------------------------------------------------------------
# GitHubSyncer integration (mocked subprocess)
# ---------------------------------------------------------------------------


class TestGitHubSyncer:
    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_full_sync(self, mock_gh, tmp_path):
        lore = _make_lore()
        state_path = str(tmp_path / "state.json")
        syncer = GitHubSyncer(lore, state_path=state_path)

        result = syncer.sync("acme/app", full=True)

        assert result.prs == 2
        assert result.issues == 1
        assert result.commits == 1
        assert result.releases == 1
        assert result.total == 5
        assert not result.errors

        # Verify memories were stored
        memories = lore.list_memories()
        assert len(memories) == 5

        # Verify sync state updated
        state = get_sync_state("acme/app", path=state_path)
        assert state is not None
        assert "last_sync" in state

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_selective_types(self, mock_gh, tmp_path):
        lore = _make_lore()
        syncer = GitHubSyncer(lore, state_path=str(tmp_path / "state.json"))
        result = syncer.sync("acme/app", types=["prs", "releases"])

        assert result.prs == 2
        assert result.releases == 1
        assert result.issues == 0
        assert result.commits == 0

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_dry_run(self, mock_gh, tmp_path):
        lore = _make_lore()
        state_path = str(tmp_path / "state.json")
        syncer = GitHubSyncer(lore, state_path=state_path)
        result = syncer.sync("acme/app", dry_run=True)

        assert result.total > 0
        # Nothing stored
        memories = lore.list_memories()
        assert len(memories) == 0
        # State not updated
        assert get_sync_state("acme/app", path=state_path) is None

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_dedup_on_second_sync(self, mock_gh, tmp_path):
        lore = _make_lore()
        state_path = str(tmp_path / "state.json")
        syncer = GitHubSyncer(lore, state_path=state_path)

        syncer.sync("acme/app", full=True)
        count1 = len(lore.list_memories())

        # Second sync — should not duplicate
        syncer.sync("acme/app", full=True)
        count2 = len(lore.list_memories())

        assert count1 == count2

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_project_scoping(self, mock_gh, tmp_path):
        lore = _make_lore()
        syncer = GitHubSyncer(lore, state_path=str(tmp_path / "state.json"))
        syncer.sync("acme/app", types=["prs"], project="my-project")

        memories = lore.list_memories()
        assert all(m.project == "my-project" for m in memories)

    def test_unknown_type_in_errors(self, tmp_path):
        lore = _make_lore()
        syncer = GitHubSyncer(lore, state_path=str(tmp_path / "state.json"))
        result = syncer.sync("acme/app", types=["invalid_type"])
        assert any("Unknown type" in e for e in result.errors)

    @patch(
        "lore.github.syncer._run_gh",
        side_effect=GitHubCLIError("gh not found"),
    )
    def test_gh_error_captured(self, mock_gh, tmp_path):
        lore = _make_lore()
        syncer = GitHubSyncer(lore, state_path=str(tmp_path / "state.json"))
        result = syncer.sync("acme/app", types=["prs"])
        assert result.errors
        assert "gh not found" in result.errors[0]

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_gh_synced_at_in_metadata(self, mock_gh, tmp_path):
        """All stored memories should have gh_synced_at in metadata."""
        lore = _make_lore()
        syncer = GitHubSyncer(lore, state_path=str(tmp_path / "state.json"))
        syncer.sync("acme/app", full=True)

        for mem in lore.list_memories():
            assert mem.metadata is not None
            assert "gh_synced_at" in mem.metadata

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_last_pr_issue_in_state(self, mock_gh, tmp_path):
        """Sync state should track last_pr/last_issue numbers."""
        lore = _make_lore()
        state_path = str(tmp_path / "state.json")
        syncer = GitHubSyncer(lore, state_path=state_path)
        syncer.sync("acme/app", full=True)

        state = get_sync_state("acme/app", path=state_path)
        assert state is not None
        assert "last_pr" in state
        assert "last_issue" in state


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestCLIGithubSync:
    def test_list_empty(self, tmp_path, capsys):
        from lore.cli import main

        state_path = str(tmp_path / "state.json")
        with patch("lore.github.state._STATE_FILE", state_path):
            main(["github-sync", "--list"])
        out = capsys.readouterr().out
        assert "No synced repos" in out

    def test_missing_repo(self, capsys):
        from lore.cli import main

        with pytest.raises(SystemExit):
            main(["github-sync"])

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_sync_via_cli(self, mock_gh, tmp_path, capsys):
        from lore.cli import main

        db_path = str(tmp_path / "test.db")
        state_path = str(tmp_path / "state.json")
        with patch("lore.github.state._STATE_FILE", state_path):
            main(["--db", db_path, "github-sync", "--repo", "acme/app", "--types", "prs"])
        out = capsys.readouterr().out
        assert "Synced" in out
        assert "PRs" in out

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_dry_run_cli(self, mock_gh, tmp_path, capsys):
        from lore.cli import main

        db_path = str(tmp_path / "test.db")
        state_path = str(tmp_path / "state.json")
        with patch("lore.github.state._STATE_FILE", state_path):
            main(["--db", db_path, "github-sync", "--repo", "acme/app", "--dry-run"])
        out = capsys.readouterr().out
        assert "[dry-run]" in out


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------


class TestMCPGithubSync:
    @pytest.fixture
    def mock_lore(self, tmp_path):
        lore = _make_lore()
        state_path = str(tmp_path / "mcp_state.json")
        with (
            patch("lore.mcp.server._get_lore", return_value=lore),
            patch("lore.github.state._STATE_FILE", state_path),
        ):
            yield lore

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_github_sync_tool(self, mock_gh, mock_lore):
        from lore.mcp.server import github_sync

        result = github_sync(repo="acme/app", types="prs")
        assert "Synced" in result
        assert "PRs" in result

    @patch("lore.github.syncer._run_gh", side_effect=_mock_run_gh)
    def test_github_sync_all_types(self, mock_gh, mock_lore):
        from lore.mcp.server import github_sync

        result = github_sync(repo="acme/app")
        assert "Synced" in result

    @patch(
        "lore.github.syncer._run_gh",
        side_effect=GitHubCLIError("Install GitHub CLI"),
    )
    def test_github_sync_gh_missing(self, mock_gh, mock_lore):
        from lore.mcp.server import github_sync

        result = github_sync(repo="acme/app")
        assert "Install GitHub CLI" in result
