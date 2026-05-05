"""Pick a Store implementation from a database URL."""

from __future__ import annotations

from urllib.parse import urlparse

from lore.persistence.exceptions import ConfigError
from lore.persistence.protocol import Store


async def make_store(database_url: str) -> Store:
    """Build a Store from a database URL.

    Supported schemes:
    - postgresql://..., postgres://...    -> PostgresStore (requires lore-sdk[server])
    - sqlite:///path/to/file.db            -> SqliteStore (Phase 3+; not yet implemented)

    Raises ConfigError on unknown or unsupported schemes.
    """
    scheme = urlparse(database_url).scheme.lower()
    if scheme in ("postgres", "postgresql"):
        try:
            import asyncpg
        except ImportError as e:
            raise ConfigError(
                "asyncpg is required for postgres URLs. "
                "Install with: pip install lore-sdk[server]"
            ) from e
        from lore.persistence.postgres import PostgresStore

        pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
        return PostgresStore.from_pool(pool)
    if scheme == "sqlite":
        raise ConfigError(
            "sqlite:// URLs are not yet supported (Phase 3 of solo-mode work). "
            "Use a postgresql:// URL until then."
        )
    raise ConfigError(
        f"Unsupported database_url scheme: {scheme!r}. "
        "Supported schemes: postgresql://, sqlite:// (coming in Phase 3)."
    )
