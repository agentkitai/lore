"""Reuse the persistence-layer store fixture for service tests."""

from tests.persistence.conftest import _pg_pool, store  # noqa: F401
