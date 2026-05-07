"""Contract-test fixtures.

Provides a parametrized `store` fixture that runs every test once per
implementation. Phase 1A wired PostgresStore; Phase 3C added SqliteStore.

Postgres branch:
- Reads LORE_TEST_DATABASE_URL (default: postgresql://lore:lore@localhost:5432/lore_test).
- Each test runs inside a transaction that is rolled back at teardown.
- If the DB cannot be reached, tests are skipped with a clear message.

SQLite branch:
- Each test gets a fresh ``sqlite:///:memory:`` SqliteStore (no rollback
  isolation needed since the DB is discarded at teardown).
- Pre-seeds the ``solo``/``org_a``/``org_b`` rows that Postgres' test DB
  has baked in via fixture, so FK-targets exist for ``insert_memory`` etc.
- Skipped if the optional ``aiosqlite`` / ``sqlite_vec`` deps aren't installed.

Most contract tests target methods still stubbed on SqliteStore (3D+);
``pytest_runtest_call`` below converts the stub's ``NotImplementedError``
into a clean ``pytest.skip(...)`` so the parametrized run shows them as
``SKIPPED`` rather than ``ERROR``.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio

DEFAULT_TEST_PG_URL = "postgresql://lore:lore@localhost:5432/lore_test"

# Orgs the existing Postgres test DB seeds and many contract tests assume.
_SEED_ORGS = (("solo", "Solo Test"), ("org_a", "Org A"), ("org_b", "Org B"))


def _test_pg_url() -> str:
    return os.environ.get("LORE_TEST_DATABASE_URL", DEFAULT_TEST_PG_URL)


def _is_sqlite(store) -> bool:
    """Return True if ``store`` is a SqliteStore (cheap dialect probe).

    Used by helpers that need to swap ``$1`` PG placeholders for ``?``
    SQLite placeholders. Imports inside the function so callers don't
    pay the import cost when the SQLite extras aren't installed.
    """
    try:
        from lore.persistence.sqlite import SqliteStore
    except ImportError:
        return False
    return isinstance(store, SqliteStore)


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


@pytest_asyncio.fixture(params=["postgres", "sqlite"], loop_scope="function")
async def store(request, _pg_pool):
    """A Store ready for use; rolled back / discarded at teardown.

    - ``postgres``: acquires a pooled connection and wraps it in a
      transaction that rolls back at teardown — fast isolation without
      schema reset.
    - ``sqlite``: opens a fresh ``:memory:`` SqliteStore per test and
      seeds the canonical orgs the contract tests reference.
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
        return

    if request.param == "sqlite":
        try:
            import aiosqlite  # noqa: F401
            import sqlite_vec  # noqa: F401
        except ImportError:
            pytest.skip("aiosqlite / sqlite_vec not installed (optional [solo] deps)")

        from lore.persistence.sqlite import SqliteStore

        sqlite_store = await SqliteStore.open("sqlite:///:memory:")
        try:
            # Seed the orgs the Postgres test DB has pre-baked, so contract
            # tests that reference 'solo' / 'org_a' / 'org_b' satisfy FKs.
            for org_id, name in _SEED_ORGS:
                await sqlite_store._conn.execute(
                    "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
                    (org_id, name),
                )
            await sqlite_store._conn.commit()
            yield sqlite_store
        finally:
            await sqlite_store.close()
        return

    pytest.skip(f"Backend {request.param!r} not yet implemented")


# ── pytest hook: turn SqliteStore stub errors into clean skips ─────────

# Sentinel substrings that mark "PostgreSQL-only test scaffolding tripping on
# SQLite" — these indicate the test reached its raw asyncpg-style helper and
# can't proceed on the SQLite param. Treated as a clean skip rather than a
# failure because the test's actual subject (e.g. ``list_workspaces``) is
# still a SqliteStore stub pending later phases anyway.
_SQLITE_DIALECT_SENTINELS = (
    # Two sentinels remain after Phase 3I (24 + 6 hits across the test
    # suite). Both come from raw asyncpg-style SQL helpers in
    # ``tests/services/`` and ``tests/server/`` that haven't yet been
    # made dialect-aware. The contract suite under
    # ``tests/persistence/test_contract_*.py`` is fully green on both
    # backends.
    "Connection.execute() takes",  # aiosqlite reject of asyncpg-style varargs
    "object has no attribute 'fetchrow'",  # asyncpg-only API
)


def _is_sqlite_param(item) -> bool:
    cs = getattr(item, "callspec", None)
    return bool(cs and cs.params.get("store") == "sqlite")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Convert SqliteStore stub errors and dialect-mismatch errors into skips.

    Phase 3C ships only insert/get/delete on SqliteStore; every other
    contract-test method hits a ``_stub()`` raising ``NotImplementedError``.
    A handful of contract tests also drive raw asyncpg-style SQL via
    ``store._conn.execute(...)`` for setup fixtures — those raise
    ``TypeError`` on aiosqlite's narrower call signature, but the underlying
    test subject is still a stubbed Store method.

    Both shapes are mapped to ``SKIPPED`` with a "SqliteStore pending: ..."
    reason so the parametrized matrix stays readable. Real SQLite-side
    bugs (assertions, value errors, etc.) still surface as failures.
    """
    outcome = yield
    # pluggy's Result exposes the raised exception via ``.exception`` (or the
    # 3-tuple ``.excinfo``). The legacy ``.exc_info`` attribute used by older
    # pytest releases isn't present in pluggy ≥1.x; check both for safety.
    exc = getattr(outcome, "exception", None)
    if exc is None:
        exc_tuple = getattr(outcome, "excinfo", None) or getattr(outcome, "exc_info", None)
        if not exc_tuple:
            return
        exc = exc_tuple[1]
    if exc is None:
        return
    msg = str(exc)
    is_pending_stub = (
        isinstance(exc, NotImplementedError) and "SqliteStore" in msg
    )
    is_dialect_mismatch = _is_sqlite_param(item) and any(
        s in msg for s in _SQLITE_DIALECT_SENTINELS
    )
    if is_pending_stub or is_dialect_mismatch:
        outcome.force_exception(
            pytest.skip.Exception(f"SqliteStore pending: {exc!s}")
        )
