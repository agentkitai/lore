"""Typed exception hierarchy for the persistence layer.

Later phases extend this hierarchy (e.g. StoreCorruption, EmbeddingDimMismatch
for SQLite). Phase 1A seeds the base set used by PostgresStore.
"""

from __future__ import annotations


class LoreError(Exception):
    """Base for all Lore errors."""


class StoreError(LoreError):
    """Base for any error raised by a Store implementation."""


class StoreNotFoundError(StoreError):
    """A row the caller asserted must exist was not found."""

    def __init__(self, entity: str, identifier: str):
        self.entity = entity
        self.identifier = identifier
        super().__init__(f"{entity} not found: id={identifier!r}")


class StoreBusyError(StoreError):
    """Storage is temporarily contended; retry may succeed."""


class StoreSchemaMismatchError(StoreError):
    """The DB's schema version does not match what this Lore expects."""


class ConfigError(LoreError):
    """Bad configuration: URL, env var, or flag combination."""


class BackendUnavailableError(ConfigError):
    """The selected backend's runtime is not available (driver, extension)."""
