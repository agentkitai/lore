"""Tests for the typed exception hierarchy."""

import pytest

from lore.persistence.exceptions import (
    BackendUnavailable,
    ConfigError,
    LoreError,
    StoreBusy,
    StoreError,
    StoreNotFound,
)


def test_hierarchy():
    assert issubclass(StoreError, LoreError)
    assert issubclass(StoreNotFound, StoreError)
    assert issubclass(StoreBusy, StoreError)
    assert issubclass(ConfigError, LoreError)
    assert issubclass(BackendUnavailable, ConfigError)


def test_store_not_found_message():
    with pytest.raises(StoreNotFound) as ei:
        raise StoreNotFound("memories", "mem_missing")
    assert "memories" in str(ei.value)
    assert "mem_missing" in str(ei.value)


def test_config_error_holds_value():
    err = ConfigError("bad scheme: foo://")
    assert "foo://" in str(err)
