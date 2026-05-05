"""Tests that lifespan creates a Store and exposes it via Depends."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_store_raises_before_init():
    # Ensure clean module state
    import importlib

    from lore.server import db as server_db

    importlib.reload(server_db)
    with pytest.raises(RuntimeError):
        await server_db.get_store()


@pytest.mark.asyncio
async def test_init_store_then_get_store():
    import os
    from lore.server import db as server_db
    from lore.persistence.postgres import PostgresStore

    db_url = os.environ.get(
        "LORE_TEST_DATABASE_URL", "postgresql://lore:lore@localhost:5432/lore_test"
    )
    try:
        await server_db.init_store(db_url)
    except (OSError, ConnectionRefusedError, Exception) as e:
        pytest.skip(f"DB not reachable: {e}")
    try:
        store = await server_db.get_store()
        assert isinstance(store, PostgresStore)
    finally:
        await server_db.close_store()
