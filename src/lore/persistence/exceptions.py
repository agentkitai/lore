"""Typed exception hierarchy for the persistence layer.

Phase 3J extends the base hierarchy with the SQLite-specific failure modes
called out in the solo-mode design (``StoreCorruption``, ``EmbeddingDimMismatch``,
``DanglingVectorError``, ``InsecureBindError``). The hierarchy after 3J:

    LoreError
    ├── StoreError
    │   ├── StoreNotFoundError
    │   ├── StoreBusyError
    │   ├── StoreSchemaMismatchError
    │   ├── StoreCorruption          (new in 3J)
    │   └── IntegrityError
    │       ├── EmbeddingDimMismatch (new in 3J)
    │       └── DanglingVectorError  (new in 3J)
    ├── ConfigError
    │   ├── BackendUnavailableError
    │   └── InsecureBindError        (new in 3J)
    ├── ProfileImmutableError
    └── LastRootKeyError
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
    """Storage is temporarily contended; retry may succeed.

    SqliteStore raises this after exhausting the busy-retry budget on
    SQLITE_BUSY (``database is locked`` / ``database table is locked``).
    """


class StoreSchemaMismatchError(StoreError):
    """The DB's schema version does not match what this Lore expects."""


class StoreCorruption(StoreError):  # noqa: N818 — name fixed by Phase 3J spec
    """The underlying database file is malformed or unreadable.

    Wraps SQLite ``database disk image is malformed`` or vec0 corruption
    errors. Recovery requires restoring from a snapshot.
    """


class IntegrityError(StoreError):
    """Cross-table or constraint invariant violated (e.g. unique (org_id, name))."""


class EmbeddingDimMismatch(IntegrityError):  # noqa: N818 — name fixed by Phase 3J spec
    """An embedding's dimensionality doesn't match the configured ``EMBED_DIM``.

    Raised at the boundary of any Store method that accepts an embedding
    (insert / upsert / recall) when ``len(embedding)`` differs from the
    fixed ``EMBED_DIM``. Catches the common "model upgrade swapped 384 → 768
    vectors" misconfiguration before it pollutes vec0 / pgvector.
    """

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Embedding dimension mismatch: expected {expected}, got {actual}"
        )


class DanglingVectorError(IntegrityError):
    """A ``memories`` row exists without its companion ``memory_vectors`` row.

    The ``memories`` ⇆ ``memory_vectors`` invariant is enforced by the
    transactional pair in production code, so this should never appear in
    practice. Reserved for the ``check_dangling_vectors`` diagnostic and any
    future ``lore doctor`` tooling that surfaces invariant breakages from a
    corrupt or hand-edited DB.
    """


class ConfigError(LoreError):
    """Bad configuration: URL, env var, or flag combination."""


class BackendUnavailableError(ConfigError):
    """The selected backend's runtime is not available (driver, extension)."""


class InsecureBindError(ConfigError):
    """Refuse to bind a non-loopback address without ``--require-auth``.

    Solo mode listens on ``127.0.0.1`` by default; binding to ``0.0.0.0``
    (or any non-loopback host) without authentication explicitly enabled is
    a foot-gun (the local API key file would be the only protection).
    """


class ProfileImmutableError(LoreError):
    """Raised when caller attempts to modify or delete a preset profile."""


class LastRootKeyError(LoreError):
    """Cannot revoke the last active root API key for an org."""
