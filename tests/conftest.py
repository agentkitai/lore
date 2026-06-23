"""Global test fixtures — patches CLI to use MemoryStore instead of HttpStore."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lore.store.memory import MemoryStore


@pytest.fixture(autouse=True)
def _patch_cli_get_lore():
    """Patch _get_lore so CLI tests don't need a Postgres server."""
    shared_store = MemoryStore()

    def _make_test_lore(db=None):
        from lore import Lore
        return Lore(store=shared_store)

    with patch("lore.cli._helpers._get_lore", side_effect=_make_test_lore), \
         patch("lore.cli._get_lore", side_effect=_make_test_lore):
        yield


@pytest.fixture(autouse=True)
def _reconciliation_off_by_default(monkeypatch):
    """Default the test suite to append-only writes.

    Write-time AUDN reconciliation (#66) defaults ON in production, but it would
    reshape existing tests that create several memories with near-identical
    embeddings (e.g. ``_vec(1)`` vs ``_vec(2)`` cosine ≈ 0.98) into dedup/supersede.
    Disable it by default here; reconciliation tests opt back in explicitly.
    """
    monkeypatch.setenv("LORE_RECONCILIATION_ENABLED", "0")
    from lore.services.reconciliation import get_reconcile_config

    get_reconcile_config.cache_clear()
    yield
    get_reconcile_config.cache_clear()
