"""Tests for the typed exception hierarchy."""

import pytest

from lore.persistence.exceptions import (
    BackendUnavailableError,
    ConfigError,
    LoreError,
    StoreBusyError,
    StoreError,
    StoreNotFoundError,
)


def test_hierarchy():
    assert issubclass(StoreError, LoreError)
    assert issubclass(StoreNotFoundError, StoreError)
    assert issubclass(StoreBusyError, StoreError)
    assert issubclass(ConfigError, LoreError)
    assert issubclass(BackendUnavailableError, ConfigError)


def test_store_not_found_message():
    with pytest.raises(StoreNotFoundError) as ei:
        raise StoreNotFoundError("memories", "mem_missing")
    assert "memories" in str(ei.value)
    assert "mem_missing" in str(ei.value)


def test_config_error_holds_value():
    err = ConfigError("bad scheme: foo://")
    assert "foo://" in str(err)
