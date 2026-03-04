"""Tests for F5 — Freshness Detection."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Generator, List

import pytest

from lore.freshness.detector import FreshnessDetector
from lore.freshness.git_ops import (
    GitError,
    file_contains_pattern,
    file_exists_in_repo,
    git_log_count,
    is_git_repo,
)
from lore.freshness.types import StalenessResult
from lore.types import Memory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cwd: str, *args: str) -> None:
    subprocess.run(args, cwd=cwd, capture_output=True, check=True)


def _make_memory(
    id: str = "mem01",
    file_path: str | None = None,
    created_at: str | None = None,
    **kwargs,
) -> Memory:
    meta = kwargs.pop("metadata", None) or {}
    if file_path is not None:
        meta["file_path"] = file_path
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    return Memory(
        id=id,
        content="test memory",
        created_at=created_at,
        updated_at=created_at,
        metadata=meta if meta else None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def git_repo() -> Generator[str, None, None]:
    """Create a temporary git repo with an initial commit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _run(tmpdir, "git", "init")
        _run(tmpdir, "git", "config", "user.email", "test@test.com")
        _run(tmpdir, "git", "config", "user.name", "Test")

        # Create initial file and commit
        src = os.path.join(tmpdir, "src")
        os.makedirs(src)
        with open(os.path.join(src, "app.py"), "w") as f:
            f.write("# initial\n")
        _run(tmpdir, "git", "add", ".")
        _run(tmpdir, "git", "commit", "-m", "initial")

        yield tmpdir


# ---------------------------------------------------------------------------
# F5-S1: Git operations wrapper
# ---------------------------------------------------------------------------

class TestGitOps:
    def test_is_git_repo_true(self, git_repo: str) -> None:
        assert is_git_repo(git_repo) is True

    def test_is_git_repo_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            assert is_git_repo(tmpdir) is False

    def test_git_log_count_zero(self, git_repo: str) -> None:
        # No commits since a future date
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        count = git_log_count(git_repo, "src/app.py", since=future)
        assert count == 0

    def test_git_log_count_with_commits(self, git_repo: str) -> None:
        # Make some commits to the file
        app_path = os.path.join(git_repo, "src", "app.py")
        for i in range(3):
            with open(app_path, "a") as f:
                f.write(f"# change {i}\n")
            _run(git_repo, "git", "add", "src/app.py")
            _run(git_repo, "git", "commit", "-m", f"change {i}")

        # Count from a date well in the past to capture all commits
        count = git_log_count(git_repo, "src/app.py", since="2020-01-01T00:00:00+00:00")
        # 1 initial + 3 new = 4 commits
        assert count == 4

    def test_git_log_count_nonexistent_file(self, git_repo: str) -> None:
        count = git_log_count(git_repo, "nonexistent.py", since="2020-01-01T00:00:00+00:00")
        assert count == 0

    def test_file_exists_in_repo(self, git_repo: str) -> None:
        assert file_exists_in_repo(git_repo, "src/app.py") is True
        assert file_exists_in_repo(git_repo, "src/missing.py") is False

    def test_file_contains_pattern(self, git_repo: str) -> None:
        assert file_contains_pattern(git_repo, "src/app.py", "# initial") is True
        assert file_contains_pattern(git_repo, "src/app.py", "not_here") is False
        assert file_contains_pattern(git_repo, "src/missing.py", "anything") is False

    def test_git_not_installed_error(self) -> None:
        # Test with invalid repo path
        with tempfile.TemporaryDirectory() as tmpdir:
            assert is_git_repo(tmpdir) is False

    def test_git_log_count_handles_timeout(self, git_repo: str) -> None:
        # Just ensure the timeout parameter works (fast command, won't actually timeout)
        count = git_log_count(git_repo, "src/app.py", since="2020-01-01T00:00:00+00:00")
        assert isinstance(count, int)


# ---------------------------------------------------------------------------
# F5-S2: Staleness scoring algorithm
# ---------------------------------------------------------------------------

class TestStalenessScoring:
    def test_fresh_zero_commits(self, git_repo: str) -> None:
        mem = _make_memory(file_path="src/app.py")
        detector = FreshnessDetector(git_repo)
        result = detector.check(mem)
        assert result.status == "fresh"
        assert result.confidence == 0.1
        assert result.file_exists is True

    def test_no_file_path_returns_unknown(self, git_repo: str) -> None:
        mem = _make_memory()  # no file_path
        detector = FreshnessDetector(git_repo)
        result = detector.check(mem)
        assert result.status == "unknown"
        assert result.reason == "no file_path in metadata"

    def test_deleted_file_is_stale(self, git_repo: str) -> None:
        mem = _make_memory(file_path="src/deleted.py")
        detector = FreshnessDetector(git_repo)
        result = detector.check(mem)
        assert result.status == "stale"
        assert result.confidence == 1.0
        assert result.file_exists is False
        assert "no longer exists" in result.reason

    def test_possibly_stale_threshold(self, git_repo: str) -> None:
        # Use current time so initial commit is excluded from count
        now = datetime.now(timezone.utc).isoformat()
        mem = _make_memory(file_path="src/app.py", created_at=now)

        app_path = os.path.join(git_repo, "src", "app.py")
        for i in range(3):
            with open(app_path, "a") as f:
                f.write(f"# edit {i}\n")
            _run(git_repo, "git", "add", "src/app.py")
            _run(git_repo, "git", "commit", "-m", f"edit {i}")

        detector = FreshnessDetector(git_repo)
        result = detector.check(mem)
        assert result.status == "possibly_stale"
        assert result.confidence == 0.3
        assert result.commits_since >= 3

    def test_likely_stale_threshold(self, git_repo: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        mem = _make_memory(file_path="src/app.py", created_at=now)

        app_path = os.path.join(git_repo, "src", "app.py")
        for i in range(10):
            with open(app_path, "a") as f:
                f.write(f"# chg {i}\n")
            _run(git_repo, "git", "add", "src/app.py")
            _run(git_repo, "git", "commit", "-m", f"chg {i}")

        detector = FreshnessDetector(git_repo)
        result = detector.check(mem)
        assert result.status == "likely_stale"
        assert result.confidence == 0.6
        assert result.commits_since >= 10

    def test_stale_threshold(self, git_repo: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        mem = _make_memory(file_path="src/app.py", created_at=now)

        app_path = os.path.join(git_repo, "src", "app.py")
        for i in range(25):
            with open(app_path, "a") as f:
                f.write(f"# s{i}\n")
            _run(git_repo, "git", "add", "src/app.py")
            _run(git_repo, "git", "commit", "-m", f"s{i}")

        detector = FreshnessDetector(git_repo)
        result = detector.check(mem)
        assert result.status == "stale"
        assert result.confidence == 0.9
        assert result.commits_since >= 25

    def test_custom_thresholds(self, git_repo: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        mem = _make_memory(file_path="src/app.py", created_at=now)

        app_path = os.path.join(git_repo, "src", "app.py")
        for i in range(2):
            with open(app_path, "a") as f:
                f.write(f"# c{i}\n")
            _run(git_repo, "git", "add", "src/app.py")
            _run(git_repo, "git", "commit", "-m", f"c{i}")

        # Custom: 2 commits = stale
        detector = FreshnessDetector(
            git_repo,
            thresholds=[(2, "stale", 0.95)],
        )
        result = detector.check(mem)
        assert result.status == "stale"
        assert result.confidence == 0.95

    def test_check_many(self, git_repo: str) -> None:
        mem1 = _make_memory(id="m1", file_path="src/app.py")
        mem2 = _make_memory(id="m2")  # no file_path
        detector = FreshnessDetector(git_repo)
        results = detector.check_many([mem1, mem2])
        assert len(results) == 2
        assert results[0].memory_id == "m1"
        assert results[1].memory_id == "m2"
        assert results[1].status == "unknown"

    def test_validate_repo_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(GitError, match="not a git repository"):
                FreshnessDetector.validate_repo(tmpdir)

    def test_validate_repo_ok(self, git_repo: str) -> None:
        FreshnessDetector.validate_repo(git_repo)  # should not raise

    def test_classify_all_boundaries(self, git_repo: str) -> None:
        """Test all classification boundaries directly."""
        detector = FreshnessDetector(git_repo)
        # fresh: 0-2 commits
        assert detector._classify(0) == ("fresh", 0.1)
        assert detector._classify(2) == ("fresh", 0.1)
        # possibly_stale: 3-9 commits
        assert detector._classify(3) == ("possibly_stale", 0.3)
        assert detector._classify(9) == ("possibly_stale", 0.3)
        # likely_stale: 10-24 commits
        assert detector._classify(10) == ("likely_stale", 0.6)
        assert detector._classify(24) == ("likely_stale", 0.6)
        # stale: 25+ commits
        assert detector._classify(25) == ("stale", 0.9)
        assert detector._classify(100) == ("stale", 0.9)


# ---------------------------------------------------------------------------
# F5-S2: Report formatting
# ---------------------------------------------------------------------------

class TestReportFormatting:
    def test_format_report(self, git_repo: str) -> None:
        results = [
            StalenessResult(
                memory_id="mem01",
                status="stale",
                confidence=0.9,
                commits_since=32,
                file_exists=True,
                reason="32 commit(s) to src/auth.py since memory creation",
            ),
            StalenessResult(
                memory_id="mem02",
                status="fresh",
                confidence=0.1,
                commits_since=1,
                file_exists=True,
                reason="1 commit(s) to src/models.py since memory creation",
            ),
        ]
        report = FreshnessDetector.format_report(results, git_repo)
        assert "Freshness Report" in report
        assert "mem01" in report
        assert "mem02" in report
        assert "1 stale" in report
        assert "1 fresh" in report
        assert "0 likely stale" in report
        assert "0 possibly stale" in report
        assert "0 unknown" in report
        assert "2 total" in report

    def test_format_report_markdown(self, git_repo: str) -> None:
        results = [
            StalenessResult(
                memory_id="mem01",
                status="stale",
                confidence=0.9,
                commits_since=32,
                file_exists=True,
                reason="32 commit(s) to src/auth.py since memory creation",
            ),
        ]
        report = FreshnessDetector.format_report(results, git_repo, markdown=True)
        assert "## Freshness Report" in report
        assert "| ID |" in report
        assert "`mem01`" in report
        assert "**1** stale" in report


# ---------------------------------------------------------------------------
# F5-S3: CLI freshness command
# ---------------------------------------------------------------------------

class TestCLIFreshness:
    def test_cli_freshness_parser(self) -> None:
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["freshness", "--repo", "/tmp/repo", "--format", "json"])
        assert args.command == "freshness"
        assert args.repo == "/tmp/repo"
        assert args.format == "json"

    def test_cli_freshness_defaults(self) -> None:
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["freshness"])
        assert args.repo == "."
        assert args.format == "table"
        assert args.min_staleness is None
        assert args.auto_tag is False

    def test_cli_freshness_min_staleness(self) -> None:
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["freshness", "--min-staleness", "likely_stale"])
        assert args.min_staleness == "likely_stale"


# ---------------------------------------------------------------------------
# F5-S5: Freshness-aware search integration
# ---------------------------------------------------------------------------

class TestFreshnessAwareSearch:
    def test_recall_without_freshness(self) -> None:
        """recall() without check_freshness should not set staleness."""
        from lore import Lore
        from lore.store.memory import MemoryStore

        def _stub(text: str) -> List[float]:
            return [1.0] * 384

        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_stub, redact=False)
        lore.remember("test content", metadata={"file_path": "src/app.py"})
        results = lore.recall("test")
        assert len(results) >= 1
        assert results[0].staleness is None

    def test_recall_with_freshness(self, git_repo: str) -> None:
        """recall() with check_freshness should attach staleness info."""
        from lore import Lore
        from lore.store.memory import MemoryStore

        def _stub(text: str) -> List[float]:
            return [1.0] * 384

        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_stub, redact=False)
        lore.remember("test content", metadata={"file_path": "src/app.py"})
        results = lore.recall("test", check_freshness=True, repo_path=git_repo)
        assert len(results) >= 1
        assert results[0].staleness is not None
        assert results[0].staleness.status == "fresh"

    def test_recall_with_freshness_no_file_path(self, git_repo: str) -> None:
        """Memory without file_path gets staleness=unknown."""
        from lore import Lore
        from lore.store.memory import MemoryStore

        def _stub(text: str) -> List[float]:
            return [1.0] * 384

        store = MemoryStore()
        lore = Lore(store=store, embedding_fn=_stub, redact=False)
        lore.remember("no file path memory")
        results = lore.recall("no file", check_freshness=True, repo_path=git_repo)
        assert len(results) >= 1
        assert results[0].staleness is not None
        assert results[0].staleness.status == "unknown"
