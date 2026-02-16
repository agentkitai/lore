"""Tests for LO-E8: Docker secrets + Secrets Manager resolution."""

from __future__ import annotations

import os
import tempfile

import pytest


class TestFileEnvResolution:
    """Test _FILE suffix resolution."""

    def test_file_suffix_reads_from_file(self, monkeypatch):
        from lore.server.secrets import resolve_file_env

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("postgresql://secret-from-file\n")
            f.flush()
            monkeypatch.setenv("DATABASE_URL_FILE", f.name)
            monkeypatch.delenv("DATABASE_URL", raising=False)

            result = resolve_file_env("DATABASE_URL")
            assert result == "postgresql://secret-from-file"
            os.unlink(f.name)

    def test_file_suffix_takes_precedence(self, monkeypatch):
        from lore.server.secrets import resolve_file_env

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("from-file")
            f.flush()
            monkeypatch.setenv("DATABASE_URL_FILE", f.name)
            monkeypatch.setenv("DATABASE_URL", "from-env")

            result = resolve_file_env("DATABASE_URL")
            assert result == "from-file"
            os.unlink(f.name)

    def test_plain_env_works_without_file(self, monkeypatch):
        from lore.server.secrets import resolve_file_env

        monkeypatch.delenv("DATABASE_URL_FILE", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://from-env")

        result = resolve_file_env("DATABASE_URL")
        assert result == "postgresql://from-env"

    def test_returns_none_when_neither_set(self, monkeypatch):
        from lore.server.secrets import resolve_file_env

        monkeypatch.delenv("DATABASE_URL_FILE", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        result = resolve_file_env("DATABASE_URL")
        assert result is None

    def test_file_not_found_raises(self, monkeypatch):
        from lore.server.secrets import resolve_file_env

        monkeypatch.setenv("DATABASE_URL_FILE", "/nonexistent/path")
        with pytest.raises(OSError):
            resolve_file_env("DATABASE_URL")

    def test_apply_secrets_resolves_all_file_vars(self, monkeypatch):
        from lore.server.secrets import apply_secrets_to_env

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("redis://from-file:6379")
            f.flush()
            monkeypatch.setenv("REDIS_URL_FILE", f.name)
            monkeypatch.delenv("REDIS_URL", raising=False)
            monkeypatch.delenv("DATABASE_URL_FILE", raising=False)
            monkeypatch.delenv("LORE_ROOT_KEY_FILE", raising=False)
            monkeypatch.delenv("AWS_SECRET_ARN", raising=False)

            apply_secrets_to_env()
            assert os.environ["REDIS_URL"] == "redis://from-file:6379"
            os.unlink(f.name)

    def test_lore_root_key_file(self, monkeypatch):
        from lore.server.secrets import resolve_file_env

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("  sk_secret_key_here  \n")
            f.flush()
            monkeypatch.setenv("LORE_ROOT_KEY_FILE", f.name)

            result = resolve_file_env("LORE_ROOT_KEY")
            assert result == "sk_secret_key_here"
            os.unlink(f.name)
