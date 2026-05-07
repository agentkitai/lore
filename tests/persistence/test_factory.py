"""Tests for make_store URL routing."""

from __future__ import annotations

import pytest

from lore.persistence import ConfigError
from lore.persistence.factory import make_store
from lore.persistence.postgres import PostgresStore


def test_postgres_url_returns_postgres_store(monkeypatch):
    # Build a store synchronously from URL — for Postgres this requires a pool,
    # but the factory's contract is to return the right *type*; pool creation
    # is deferred to first use OR done eagerly. The factory is async because
    # asyncpg.create_pool is async.
    import asyncio

    async def _go():
        store = await make_store("postgresql://lore:lore@localhost:5432/lore_test")
        try:
            assert isinstance(store, PostgresStore)
        finally:
            await store.close()

    try:
        asyncio.run(_go())
    except (OSError, ConnectionRefusedError) as e:
        pytest.skip(f"Test DB not reachable: {e}")


@pytest.mark.asyncio
async def test_unknown_scheme_raises_config_error():
    with pytest.raises(ConfigError) as ei:
        await make_store("mongodb://localhost/foo")
    assert "scheme" in str(ei.value).lower()
    assert "mongodb" in str(ei.value)


@pytest.mark.asyncio
async def test_sqlite_scheme_returns_sqlite_store(tmp_path):
    """Phase 3A: sqlite:// URLs return a real SqliteStore."""
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")
    from lore.persistence.sqlite import SqliteStore

    store = await make_store(f"sqlite:///{tmp_path / 'factory.db'}")
    try:
        assert isinstance(store, SqliteStore)
    finally:
        await store.close()
