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
