"""Contract-test fixtures.

Provides a parametrized `store` fixture that runs every test once per
implementation. Phase 1A wires PostgresStore; Phase 3 will add SqliteStore
to the params list.

Postgres setup:
- Reads LORE_TEST_DATABASE_URL (default: postgresql://lore:lore@localhost:5432/lore_test).
- Each test runs inside a transaction that is rolled back at teardown.
- If the DB cannot be reached, tests are skipped with a clear message.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio

DEFAULT_TEST_PG_URL = "postgresql://lore:lore@localhost:5432/lore_test"


def _test_pg_url() -> str:
    return os.environ.get("LORE_TEST_DATABASE_URL", DEFAULT_TEST_PG_URL)


@pytest_asyncio.fixture(loop_scope="function")
async def _pg_pool() -> AsyncIterator:
    """Module-level pool for Postgres contract tests."""
    try:
        import asyncpg
    except ImportError:
        pytest.skip("asyncpg not installed")
    try:
        pool = await asyncpg.create_pool(_test_pg_url(), min_size=1, max_size=2)
    except (OSError, ConnectionRefusedError, Exception) as e:
        pytest.skip(
            f"Cannot reach LORE_TEST_DATABASE_URL ({_test_pg_url()}): {e}. "
            "Start it with: docker compose up -d db && createdb -U lore lore_test "
            "&& psql -U lore -d lore_test -f migrations/001_initial.sql ..."
        )
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(params=["postgres"], loop_scope="function")
async def store(request, _pg_pool):
    """A Store ready for use; rolled back at teardown.

    Each test gets its own connection acquired from the shared pool, wrapped
    in a transaction that is rolled back. This isolates tests without
    requiring schema reset between each one.
    """
    if request.param == "postgres":
        from lore.persistence.postgres import PostgresStore

        async with _pg_pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                pg_store = PostgresStore.from_connection(conn)
                yield pg_store
            finally:
                await tr.rollback()
    else:
        pytest.skip(f"Backend {request.param!r} not yet implemented (Phase 3+)")
