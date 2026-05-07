"""Phase 3J: SqliteStore solo bootstrap tests.

When ``SqliteStore.open()`` runs against an empty file-backed SQLite DB,
the bootstrap path:
  * inserts ``orgs(id='solo')`` + ``workspaces(id='solo', slug='solo')``
  * generates a ``lore_sk_<hex>`` key + writes its sha256 hash to api_keys
  * writes the raw key to ``~/.lore/key.txt`` (default; tests use a tmp path)
  * is idempotent on subsequent opens (existing root key suppresses re-bootstrap)
  * skips entirely for ``sqlite:///:memory:`` URLs.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

# Skip the whole module if the optional [solo] deps aren't installed.
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")


@pytest.mark.asyncio
async def test_bootstrap_creates_solo_org_and_key(tmp_path: Path, monkeypatch):
    """A fresh DB triggers solo org/workspace insert + a single root API key."""
    from lore.persistence.bootstrap import (
        DEFAULT_KEY_PATH,
        SOLO_ORG_ID,
        SOLO_WORKSPACE_ID,
        bootstrap_solo_if_empty,
    )
    from lore.persistence.sqlite import SqliteStore

    db_path = tmp_path / "lore.db"
    key_file = tmp_path / "key.txt"

    # SqliteStore.open() will auto-call bootstrap with the default
    # ~/.lore/key.txt; we point HOME at the tmp dir so the default path
    # routes there too. Pre-emptively writing the key to a different path
    # via direct call exercises the explicit-arg branch.
    monkeypatch.setenv("HOME", str(tmp_path))
    store = await SqliteStore.open(f"sqlite:///{db_path}")
    try:
        # The auto-bootstrap from open() should have created exactly one
        # root key. Re-running bootstrap_solo_if_empty with an explicit
        # key_path is a no-op (idempotent) and returns None.
        result = await bootstrap_solo_if_empty(store, key_path=key_file)
        assert result is None, "second bootstrap should no-op"

        # Verify the org was inserted.
        async with store._conn.execute(
            "SELECT id, name FROM orgs WHERE id = ?", (SOLO_ORG_ID,)
        ) as cur:
            org_row = await cur.fetchone()
        assert org_row is not None
        assert org_row["id"] == SOLO_ORG_ID

        # Verify the workspace was inserted.
        async with store._conn.execute(
            "SELECT id, slug, org_id FROM workspaces WHERE id = ?",
            (SOLO_WORKSPACE_ID,),
        ) as cur:
            ws_row = await cur.fetchone()
        assert ws_row is not None
        assert ws_row["slug"] == "solo"
        assert ws_row["org_id"] == SOLO_ORG_ID

        # Verify exactly one active root API key for the solo org.
        active = await store.count_active_root_keys(SOLO_ORG_ID)
        assert active == 1

        # Verify the key file exists and is mode 0600.
        default_target = (DEFAULT_KEY_PATH).expanduser()
        assert default_target.exists(), default_target
        # Mode bits — strip the type bits and compare the perm bits.
        mode = stat.S_IMODE(os.stat(default_target).st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

        # Sanity: the raw key starts with the expected prefix and the
        # stored hash matches its sha256.
        raw_key = default_target.read_text().strip()
        assert raw_key.startswith("lore_sk_")
        async with store._conn.execute(
            "SELECT key_hash, is_root, workspace_id FROM api_keys "
            "WHERE org_id = ? AND is_root = 1",
            (SOLO_ORG_ID,),
        ) as cur:
            key_row = await cur.fetchone()
        assert key_row is not None
        expected_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        assert key_row["key_hash"] == expected_hash
        assert bool(key_row["is_root"]) is True
        assert key_row["workspace_id"] == SOLO_WORKSPACE_ID
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(tmp_path: Path, monkeypatch):
    """Opening the same DB twice produces exactly one root key."""
    from lore.persistence.sqlite import SqliteStore

    db_path = tmp_path / "lore.db"
    monkeypatch.setenv("HOME", str(tmp_path))

    store1 = await SqliteStore.open(f"sqlite:///{db_path}")
    try:
        n1 = await store1.count_active_root_keys("solo")
    finally:
        await store1.close()
    assert n1 == 1

    store2 = await SqliteStore.open(f"sqlite:///{db_path}")
    try:
        n2 = await store2.count_active_root_keys("solo")
    finally:
        await store2.close()

    assert n2 == 1, "second open should not create another root key"


@pytest.mark.asyncio
async def test_bootstrap_skipped_for_memory_db(tmp_path: Path, monkeypatch):
    """``sqlite:///:memory:`` URLs skip bootstrap entirely (no key file written)."""
    from lore.persistence.sqlite import SqliteStore

    monkeypatch.setenv("HOME", str(tmp_path))
    default_target = tmp_path / ".lore" / "key.txt"
    assert not default_target.exists()

    store = await SqliteStore.open("sqlite:///:memory:")
    try:
        active = await store.count_active_root_keys("solo")
    finally:
        await store.close()

    assert active == 0, "in-memory DB should never bootstrap"
    assert not default_target.exists(), "key.txt must not be written for :memory: DBs"


@pytest.mark.asyncio
async def test_bootstrap_explicit_key_path(tmp_path: Path):
    """Passing an explicit ``key_path`` writes the raw key there with mode 0600."""
    from lore.persistence.bootstrap import bootstrap_solo_if_empty
    from lore.persistence.sqlite import SqliteStore

    custom_key = tmp_path / "mykey" / "raw.txt"

    # Open via from_connection-equivalent path: open() auto-bootstraps to
    # the default location. To exercise the explicit-arg path we use a DB
    # that hasn't been opened with auto-bootstrap (a separate file).
    db_path2 = tmp_path / "lore2.db"
    store = SqliteStore(db_path=str(db_path2))
    store._owned_conn = await store._open_connection(str(db_path2))
    try:
        await store._apply_migrations(store._owned_conn)
        await store._init_vec_tables(store._owned_conn)
        result = await bootstrap_solo_if_empty(store, key_path=custom_key)
        assert result is not None
        assert result.startswith("lore_sk_")
        assert custom_key.exists()
        mode = stat.S_IMODE(os.stat(custom_key).st_mode)
        assert mode == 0o600
    finally:
        await store.close()
