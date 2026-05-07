"""Shared fixtures for ``lore migrate`` tests.

The CLI's autouse fixture in ``tests/conftest.py`` patches ``_get_lore`` to
use the in-memory ``MemoryStore`` so unit tests don't need a real DB. The
migrate path opens raw aiosqlite/asyncpg connections instead, so that
patch has no effect — but we still need the fixture to be inert here so
the surrounding tests don't trip on its import.

This conftest also exposes ``pg_test_url`` and ``with_isolated_state``
fixtures used by the round-trip tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

DEFAULT_TEST_PG_URL = "postgresql://lore:lore@localhost:5432/lore_test"


@pytest.fixture
def pg_test_url() -> str:
    return os.environ.get("LORE_TEST_DATABASE_URL", DEFAULT_TEST_PG_URL)


@pytest.fixture
def with_isolated_state(tmp_path, monkeypatch):
    """Redirect ``~/.lore/migrate-state.json`` to a per-test tmp file.

    Tests that hit the resume path need their state file isolated so
    they don't see real machine state and don't pollute it. We do this
    by monkey-patching ``Path.home`` to return ``tmp_path`` for the
    duration of the test.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def state_file(with_isolated_state) -> Path:
    return with_isolated_state / ".lore" / "migrate-state.json"


def write_state(path: Path, key: str, table_counts: dict) -> None:
    """Write a fake migrate-state.json keyed by ``key``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: table_counts} if path.exists() is False else {}
    if path.exists():
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            payload = {}
        payload[key] = table_counts
    path.write_text(json.dumps(payload, indent=2))
