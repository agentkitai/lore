"""Tests for lore bootstrap command."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from lore.bootstrap import BootstrapRunner, CheckResult, format_results


class TestCheckPythonVersion:
    def test_passes_on_current_python(self):
        runner = BootstrapRunner()
        result = runner.check_python_version()
        assert result.status == "ok"
        assert "3." in result.message

    def test_fails_on_old_python(self):
        runner = BootstrapRunner()
        with patch.object(sys, "version_info", (3, 9, 0)):
            result = runner.check_python_version()
            assert result.status == "fail"
            assert "3.9" in result.message


class TestCheckEnvVars:
    def test_passes_with_db_url_arg(self):
        runner = BootstrapRunner(db_url="postgresql://localhost/lore")
        result = runner.check_env_vars()
        assert result.status == "ok"

    def test_passes_with_env_var(self):
        runner = BootstrapRunner()
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://localhost/lore"}):
            result = runner.check_env_vars()
            assert result.status == "ok"

    def test_fails_without_db_url(self):
        runner = BootstrapRunner()
        with patch.dict("os.environ", {}, clear=True):
            result = runner.check_env_vars()
            assert result.status == "fail"


class TestCheckDocker:
    def test_passes_when_docker_available(self):
        runner = BootstrapRunner()
        with patch("shutil.which", return_value="/usr/bin/docker"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.check_docker()
            assert result.status == "ok"

    def test_fails_when_docker_not_found(self):
        runner = BootstrapRunner()
        with patch("shutil.which", return_value=None):
            result = runner.check_docker()
            assert result.status == "fail"

    def test_warns_when_daemon_not_running(self):
        runner = BootstrapRunner()
        with patch("shutil.which", return_value="/usr/bin/docker"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = runner.check_docker()
            assert result.status == "warn"


class TestCheckPostgres:
    def test_fails_without_db_url(self):
        runner = BootstrapRunner()
        with patch.dict("os.environ", {}, clear=True):
            result = runner.check_postgres()
            assert result.status == "fail"

    def test_warns_without_asyncpg(self):
        runner = BootstrapRunner(db_url="postgresql://localhost/lore")
        with patch("shutil.which", return_value=None), \
             patch.dict("sys.modules", {"asyncpg": None}):
            # asyncpg import will fail
            result = runner.check_postgres()
            assert result.status in ("warn", "fail")


class TestCheckPgvector:
    def test_fails_without_db_url(self):
        runner = BootstrapRunner()
        with patch.dict("os.environ", {}, clear=True):
            result = runner.check_pgvector()
            assert result.status == "fail"


class TestFormatResults:
    def test_all_ok(self):
        results = [
            CheckResult("test1", "ok", "All good"),
            CheckResult("test2", "ok", "Fine"),
        ]
        output = format_results(results)
        assert "2 passed" in output
        assert "0 failed" in output
        assert "ready" in output.lower()

    def test_with_failures(self):
        results = [
            CheckResult("test1", "ok", "Good"),
            CheckResult("test2", "fail", "Bad", fix_hint="Fix it"),
        ]
        output = format_results(results)
        assert "1 passed" in output
        assert "1 failed" in output
        assert "Fix it" in output

    def test_verbose_shows_all_hints(self):
        results = [
            CheckResult("test1", "warn", "Maybe", fix_hint="Try this"),
        ]
        output = format_results(results, verbose=True)
        assert "Try this" in output


class TestRunAll:
    def test_skips_docker_and_server(self):
        runner = BootstrapRunner(
            db_url="postgresql://localhost/lore",
            skip_docker=True,
            skip_server=True,
        )
        with patch.object(runner, "check_postgres", return_value=CheckResult("pg", "ok", "ok")), \
             patch.object(runner, "check_pgvector", return_value=CheckResult("pgv", "ok", "ok")), \
             patch.object(runner, "run_migrations", return_value=CheckResult("mig", "ok", "ok")):
            results = runner.run_all()
            names = [r.name for r in results]
            assert "docker" not in names
            assert "server_start" not in names
            assert "health" not in names
            assert "python_version" in names
            assert "env_vars" in names


class TestBootstrapCLI:
    def test_bootstrap_command_exists(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["bootstrap"])
        assert args.command == "bootstrap"

    def test_bootstrap_with_flags(self):
        from lore.cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "bootstrap", "--fix", "--skip-docker", "--skip-server",
            "--db-url", "postgresql://localhost/lore", "--verbose",
        ])
        assert args.fix is True
        assert args.skip_docker is True
        assert args.skip_server is True
        assert args.db_url == "postgresql://localhost/lore"
        assert args.verbose is True
