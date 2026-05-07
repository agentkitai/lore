"""Reuse the persistence-layer store fixture for service tests.

Also re-exports the SqliteStore-aware ``pytest_runtest_call`` hook so the
parametrized ``store`` matrix skips cleanly on the ``sqlite`` param when
the underlying Store method is still a Phase 3D+ stub. Hooks bubble up
the conftest tree but don't auto-import — re-declaring here keeps the
service-level tests in the same skip semantics as the persistence ones.
"""

from tests.persistence.conftest import (  # noqa: F401
    _pg_pool,
    pytest_runtest_call,
    store,
)
