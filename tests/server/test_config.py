"""Phase 3J: server config default tests.

When neither ``LORE_DATABASE_URL`` nor the legacy ``DATABASE_URL`` is set,
``Settings.from_env()`` falls back to ``sqlite:///~/.lore/lore.db`` so that
``pip install lore-sdk[solo] && lore serve`` works without any config.
"""

from __future__ import annotations


def test_database_url_defaults_to_sqlite_solo(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LORE_DATABASE_URL", raising=False)

    from lore.server.config import Settings

    settings = Settings.from_env()
    assert settings.database_url == "sqlite:///~/.lore/lore.db"


def test_database_url_lore_env_takes_precedence(monkeypatch):
    """``LORE_DATABASE_URL`` wins over ``DATABASE_URL`` and the default."""
    monkeypatch.setenv("LORE_DATABASE_URL", "sqlite:///custom/path.db")
    monkeypatch.setenv("DATABASE_URL", "postgres://override@host/db")

    from lore.server.config import Settings

    settings = Settings.from_env()
    assert settings.database_url == "sqlite:///custom/path.db"


def test_database_url_legacy_env_still_supported(monkeypatch):
    """Legacy ``DATABASE_URL`` populates the field when ``LORE_DATABASE_URL`` is unset."""
    monkeypatch.delenv("LORE_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://lore@host:5432/lore")

    from lore.server.config import Settings

    settings = Settings.from_env()
    assert settings.database_url == "postgres://lore@host:5432/lore"


def test_resolve_default_database_url_helper(monkeypatch):
    """The internal helper is the single source of truth for the default."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LORE_DATABASE_URL", raising=False)

    from lore.server.config import _resolve_default_database_url

    assert _resolve_default_database_url() == "sqlite:///~/.lore/lore.db"
