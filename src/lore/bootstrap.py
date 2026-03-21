"""Guided bootstrap for Lore — validates prerequisites and sets up the environment."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CheckResult:
    """Result of a single bootstrap check."""
    name: str
    status: str  # "ok", "warn", "fail"
    message: str
    fix_hint: Optional[str] = None


class BootstrapRunner:
    """Runs all bootstrap checks and optional fixes."""

    def __init__(
        self,
        *,
        db_url: Optional[str] = None,
        fix: bool = False,
        skip_docker: bool = False,
        skip_server: bool = False,
        verbose: bool = False,
    ) -> None:
        self.db_url = db_url
        self.fix = fix
        self.skip_docker = skip_docker
        self.skip_server = skip_server
        self.verbose = verbose

    def run_all(self) -> List[CheckResult]:
        """Run all checks in order. Returns list of CheckResults."""
        results: List[CheckResult] = []
        results.append(self.check_python_version())
        results.append(self.check_env_vars())
        if not self.skip_docker:
            results.append(self.check_docker())
        results.append(self.check_postgres())
        results.append(self.check_pgvector())
        results.append(self.run_migrations())
        if not self.skip_server:
            results.append(self.start_server())
            results.append(self.verify_health())
        return results

    def check_python_version(self) -> CheckResult:
        """Check Python >= 3.10."""
        major, minor = sys.version_info[:2]
        if (major, minor) >= (3, 10):
            return CheckResult(
                name="python_version",
                status="ok",
                message=f"Python {major}.{minor} (>= 3.10)",
            )
        return CheckResult(
            name="python_version",
            status="fail",
            message=f"Python {major}.{minor} found, need >= 3.10",
            fix_hint="Install Python 3.10+ from https://python.org",
        )

    def check_env_vars(self) -> CheckResult:
        """Check DATABASE_URL is set."""
        import os
        db_url = self.db_url or os.environ.get("DATABASE_URL")
        if db_url:
            return CheckResult(
                name="env_vars",
                status="ok",
                message="DATABASE_URL is set",
            )
        return CheckResult(
            name="env_vars",
            status="fail",
            message="DATABASE_URL not set",
            fix_hint="Set DATABASE_URL=postgresql://user:pass@localhost:5432/lore",
        )

    def check_docker(self) -> CheckResult:
        """Check Docker is installed and running."""
        if not shutil.which("docker"):
            hint = "Install Docker from https://docs.docker.com/get-docker/"
            if self.fix:
                return CheckResult(
                    name="docker",
                    status="fail",
                    message="Docker not found in PATH",
                    fix_hint=hint,
                )
            return CheckResult(
                name="docker",
                status="fail",
                message="Docker not found in PATH",
                fix_hint=hint,
            )
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return CheckResult(
                    name="docker",
                    status="ok",
                    message="Docker is installed and running",
                )
            return CheckResult(
                name="docker",
                status="warn",
                message="Docker installed but daemon may not be running",
                fix_hint="Start Docker daemon: sudo systemctl start docker",
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return CheckResult(
                name="docker",
                status="warn",
                message="Docker check timed out",
                fix_hint="Ensure Docker daemon is running",
            )

    def check_postgres(self) -> CheckResult:
        """Check PostgreSQL is reachable."""
        import os
        db_url = self.db_url or os.environ.get("DATABASE_URL", "")
        if not db_url:
            return CheckResult(
                name="postgres",
                status="fail",
                message="Cannot check Postgres — no DATABASE_URL",
                fix_hint="Set DATABASE_URL first",
            )

        # Try pg_isready if available
        if shutil.which("pg_isready"):
            try:
                result = subprocess.run(
                    ["pg_isready", "-d", db_url],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return CheckResult(
                        name="postgres",
                        status="ok",
                        message="PostgreSQL is accepting connections",
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # Try a direct connection via asyncpg
        try:
            import asyncpg  # noqa: F811

            async def _check():
                conn = await asyncpg.connect(db_url, timeout=5)
                await conn.fetchval("SELECT 1")
                await conn.close()

            asyncio.run(_check())
            return CheckResult(
                name="postgres",
                status="ok",
                message="PostgreSQL connection successful",
            )
        except ImportError:
            return CheckResult(
                name="postgres",
                status="warn",
                message="asyncpg not installed — cannot verify Postgres",
                fix_hint="pip install asyncpg",
            )
        except Exception as e:
            hint = None
            if self.fix:
                hint = "Try: docker compose up -d db"
            return CheckResult(
                name="postgres",
                status="fail",
                message=f"Cannot connect to PostgreSQL: {e}",
                fix_hint=hint or "Ensure PostgreSQL is running and DATABASE_URL is correct",
            )

    def check_pgvector(self) -> CheckResult:
        """Check pgvector extension is installed."""
        import os
        db_url = self.db_url or os.environ.get("DATABASE_URL", "")
        if not db_url:
            return CheckResult(
                name="pgvector",
                status="fail",
                message="Cannot check pgvector — no DATABASE_URL",
            )
        try:
            import asyncpg

            async def _check():
                conn = await asyncpg.connect(db_url, timeout=5)
                result = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                )
                await conn.close()
                return result

            has_vector = asyncio.run(_check())
            if has_vector:
                return CheckResult(
                    name="pgvector",
                    status="ok",
                    message="pgvector extension is installed",
                )
            return CheckResult(
                name="pgvector",
                status="fail",
                message="pgvector extension not found",
                fix_hint="Run: CREATE EXTENSION IF NOT EXISTS vector;",
            )
        except ImportError:
            return CheckResult(
                name="pgvector",
                status="warn",
                message="asyncpg not installed — cannot verify pgvector",
                fix_hint="pip install asyncpg",
            )
        except Exception as e:
            return CheckResult(
                name="pgvector",
                status="warn",
                message=f"pgvector check failed: {e}",
            )

    def run_migrations(self) -> CheckResult:
        """Run database migrations."""
        import os
        db_url = self.db_url or os.environ.get("DATABASE_URL", "")
        if not db_url:
            return CheckResult(
                name="migrations",
                status="fail",
                message="Cannot run migrations — no DATABASE_URL",
            )
        try:
            from lore.server.config import settings
            from lore.server.db import close_pool, init_pool
            from lore.server.db import run_migrations as _run_migrations

            async def _migrate():
                pool = await init_pool(db_url)
                await _run_migrations(pool, settings.migrations_dir)
                await close_pool()

            asyncio.run(_migrate())
            return CheckResult(
                name="migrations",
                status="ok",
                message="Migrations completed successfully",
            )
        except ImportError:
            return CheckResult(
                name="migrations",
                status="warn",
                message="Server dependencies not installed",
                fix_hint="pip install lore-sdk[server]",
            )
        except Exception as e:
            return CheckResult(
                name="migrations",
                status="fail",
                message=f"Migration failed: {e}",
            )

    def start_server(self) -> CheckResult:
        """Start the Lore server (optional)."""
        try:
            import uvicorn  # noqa: F401
        except ImportError:
            return CheckResult(
                name="server_start",
                status="warn",
                message="uvicorn not installed — skipping server start",
                fix_hint="pip install lore-sdk[server]",
            )
        # Just verify the module is importable, don't actually start
        try:
            import lore.server.app  # noqa: F401
            return CheckResult(
                name="server_start",
                status="ok",
                message="Server module is importable",
            )
        except Exception as e:
            return CheckResult(
                name="server_start",
                status="fail",
                message=f"Server import failed: {e}",
            )

    def verify_health(self) -> CheckResult:
        """Verify the /ready endpoint (if server is running)."""
        import os
        import urllib.error
        import urllib.request

        port = os.environ.get("LORE_PORT", "8765")
        url = f"http://localhost:{port}/ready"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return CheckResult(
                        name="health",
                        status="ok",
                        message=f"Server healthy at {url}",
                    )
                return CheckResult(
                    name="health",
                    status="warn",
                    message=f"Server responded with {resp.status}",
                )
        except Exception:
            return CheckResult(
                name="health",
                status="warn",
                message="Server not reachable (may not be running)",
                fix_hint=f"Start server: lore serve --port {port}",
            )


def format_results(results: List[CheckResult], verbose: bool = False) -> str:
    """Format check results for display."""
    lines: List[str] = []
    icons = {"ok": "\u2713", "warn": "\u26a0", "fail": "\u2717"}
    for r in results:
        icon = icons.get(r.status, "?")
        lines.append(f"  [{icon}] {r.name}: {r.message}")
        if r.fix_hint and (verbose or r.status == "fail"):
            lines.append(f"      Fix: {r.fix_hint}")

    ok_count = sum(1 for r in results if r.status == "ok")
    warn_count = sum(1 for r in results if r.status == "warn")
    fail_count = sum(1 for r in results if r.status == "fail")
    lines.append("")
    lines.append(f"  Summary: {ok_count} passed, {warn_count} warnings, {fail_count} failed")

    if fail_count == 0:
        lines.append("  Lore is ready!")
    else:
        lines.append("  Run with --fix to attempt auto-remediation.")

    return "\n".join(lines)
