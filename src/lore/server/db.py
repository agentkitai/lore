"""Database connection pool and migration runner."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from lore.persistence.protocol import Store

logger = logging.getLogger(__name__)

# Global connection pool
_pool: Optional["asyncpg.Pool"] = None


async def get_pool() -> "asyncpg.Pool":
    """Return the global connection pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    return _pool


async def init_pool(database_url: str) -> "asyncpg.Pool":
    """Create and store the global connection pool."""
    global _pool
    if asyncpg is None:
        raise ImportError(
            "asyncpg is required for the Lore server. "
            "Install it with: pip install lore-sdk[server]"
        )
    _pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    logger.info("Database connection pool created")
    return _pool


async def close_pool() -> None:
    """Close the global connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


async def run_migrations(pool: "asyncpg.Pool", migrations_dir: str = "migrations") -> None:
    """Run SQL migration files in order.

    Migrations are idempotent — they use IF NOT EXISTS throughout.
    """
    migrations_path = Path(migrations_dir)
    if not migrations_path.exists():
        # Try relative to the project root
        project_root = Path(__file__).parent.parent.parent.parent
        migrations_path = project_root / migrations_dir
    if not migrations_path.exists():
        logger.warning("Migrations directory not found: %s", migrations_dir)
        return

    sql_files = sorted(migrations_path.glob("*.sql"))
    if not sql_files:
        logger.warning("No migration files found in %s", migrations_path)
        return

    async with pool.acquire() as conn:
        for sql_file in sql_files:
            logger.info("Running migration: %s", sql_file.name)
            sql = sql_file.read_text()
            await conn.execute(sql)
            logger.info("Migration complete: %s", sql_file.name)


# ── Store (new persistence abstraction) ───────────────────────────────────────
# Coexists with _pool / init_pool / get_pool / close_pool above.
# Other routes continue using get_pool() until they migrate in 1B–1G.

_store: "Store | None" = None  # type: ignore[assignment]


async def init_store(database_url: str) -> "Store":
    """Create and store the global Store. Idempotent."""
    global _store
    from lore.persistence.factory import make_store

    if _store is None:
        _store = await make_store(database_url)
        logger.info("Store initialized: %s", type(_store).__name__)
    return _store


async def get_store() -> "Store":
    """Return the global Store. Raises if not initialized."""
    if _store is None:
        raise RuntimeError("Store not initialized. Call init_store() first.")
    return _store


async def close_store() -> None:
    """Close the global Store."""
    global _store
    if _store is not None:
        await _store.close()
        _store = None
        logger.info("Store closed")
