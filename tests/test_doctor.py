"""Tests for `lore doctor` and the bootstrap self-heal path."""

from __future__ import annotations

import hashlib
import json

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")

from lore.cli.commands.doctor import _diagnose, cmd_doctor


def _ns(**kwargs):
    """Lightweight Namespace stand-in."""
    import argparse
    n = argparse.Namespace()
    n.fix = False
    n.json = False
    n.key_path = None
    n.db_path = None
    n.env_path = None
    for k, v in kwargs.items():
        setattr(n, k, v)
    return n


# ── _diagnose unit tests ──────────────────────────────────────────


def test_diagnose_all_empty():
    s = _diagnose(key_file=None, db_keys=None, env_key=None)
    assert s["key_file_present"] is False
    assert s["db_present"] is False
    assert s["fixable"] is False
    assert any("no key file and no DB keys" in i for i in s["issues"])


def test_diagnose_key_file_only():
    """Common drift: key.txt has a key, DB is empty."""
    s = _diagnose(key_file="lore_sk_abc123", db_keys=[], env_key=None)
    assert s["fixable"] is True
    assert any("DB is empty" in i for i in s["issues"])


def test_diagnose_aligned():
    """Happy path: key.txt and DB hash match."""
    raw = "lore_sk_aligned_xyz"
    h = hashlib.sha256(raw.encode()).hexdigest()
    s = _diagnose(key_file=raw, db_keys=[(h, "lore_sk_alig")], env_key=None)
    assert s["fixable"] is False
    assert s["issues"] == []


def test_diagnose_drift():
    """key.txt and DB both populated but different keys."""
    s = _diagnose(
        key_file="lore_sk_aaaaaaaa",
        db_keys=[("0" * 64, "lore_sk_bbbb")],
        env_key=None,
    )
    assert s["fixable"] is True
    assert any("drift detected" in i for i in s["issues"])


def test_diagnose_env_drift():
    raw = "lore_sk_aligned_xyz"
    h = hashlib.sha256(raw.encode()).hexdigest()
    s = _diagnose(
        key_file=raw,
        db_keys=[(h, "lore_sk_alig")],
        env_key="lore_sk_DIFFERENT",
    )
    # Aligned key.txt + DB → no fix needed; but env mismatch is flagged.
    assert s["fixable"] is False
    assert any(".env" in i for i in s["issues"])


# ── CLI integration ──────────────────────────────────────────────


def test_cmd_doctor_json(tmp_path, capsys):
    key_path = tmp_path / "key.txt"
    db_path = tmp_path / "lore.db"
    env_path = tmp_path / ".env"
    args = _ns(json=True, key_path=str(key_path), db_path=str(db_path), env_path=str(env_path))
    cmd_doctor(args)
    out = capsys.readouterr().out
    state = json.loads(out)
    assert state["key_file_present"] is False
    assert state["db_present"] is False


def test_cmd_doctor_human_readable(tmp_path, capsys):
    key_path = tmp_path / "key.txt"
    key_path.write_text("lore_sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n")
    args = _ns(
        key_path=str(key_path),
        db_path=str(tmp_path / "lore.db"),
        env_path=str(tmp_path / ".env"),
    )
    cmd_doctor(args)
    out = capsys.readouterr().out
    assert "key.txt" in out
    assert "lore_sk_xxxx" in out


def test_cmd_doctor_fix_drift(tmp_path, capsys):
    """End-to-end: open a SQLite DB with no api_keys, run --fix, verify the
    file's key was inserted."""
    import asyncio

    from lore.persistence.sqlite import SqliteStore

    db_path = tmp_path / "lore.db"
    store = asyncio.run(SqliteStore.open(f"sqlite:///{db_path}"))
    asyncio.run(store.close())

    raw_key = "lore_sk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    key_path = tmp_path / "key.txt"
    key_path.write_text(raw_key + "\n")

    args = _ns(
        fix=True,
        key_path=str(key_path),
        db_path=str(db_path),
        env_path=str(tmp_path / ".env"),
    )
    cmd_doctor(args)
    out = capsys.readouterr().out
    assert "Imported key.txt's key into the DB" in out

    # Verify the row landed.
    import sqlite3
    with sqlite3.connect(str(db_path)) as c:
        rows = c.execute("SELECT key_hash FROM api_keys").fetchall()
    expected_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    assert any(row[0] == expected_hash for row in rows)


# ── Bootstrap self-heal ──────────────────────────────────────────


async def _bare_store(db_path):
    """Open a SqliteStore without auto-running bootstrap (so tests below
    can exercise the bootstrap function directly on a known-empty DB)."""
    from lore.persistence.sqlite import SqliteStore

    store = SqliteStore(db_path=str(db_path))
    store._owned_conn = await store._open_connection(str(db_path))
    await store._apply_migrations(store._owned_conn)
    await store._init_vec_tables(store._owned_conn)
    return store


@pytest.mark.asyncio
async def test_bootstrap_adopts_existing_key_file(tmp_path):
    """When DB is empty but key.txt exists with content, bootstrap should
    ADOPT that key into the DB instead of generating a new one."""
    from lore.persistence.bootstrap import bootstrap_solo_if_empty

    db_path = tmp_path / "lore.db"
    key_path = tmp_path / "key.txt"
    existing_key = "lore_sk_adoptedxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    key_path.write_text(existing_key + "\n")

    store = await _bare_store(db_path)
    try:
        result = await bootstrap_solo_if_empty(
            store, key_path=key_path, force_for_memory=False,
        )
    finally:
        await store.close()

    assert result == existing_key
    # File contents unchanged
    assert key_path.read_text().strip() == existing_key
    # DB has the matching hash
    import sqlite3
    expected_hash = hashlib.sha256(existing_key.encode()).hexdigest()
    with sqlite3.connect(str(db_path)) as c:
        rows = c.execute("SELECT key_hash FROM api_keys").fetchall()
    assert any(row[0] == expected_hash for row in rows)


@pytest.mark.asyncio
async def test_bootstrap_generates_when_no_key_file(tmp_path):
    """When DB is empty AND key.txt is absent, bootstrap generates fresh."""
    from lore.persistence.bootstrap import bootstrap_solo_if_empty

    db_path = tmp_path / "lore.db"
    key_path = tmp_path / "key.txt"
    assert not key_path.exists()

    store = await _bare_store(db_path)
    try:
        result = await bootstrap_solo_if_empty(
            store, key_path=key_path, force_for_memory=False,
        )
    finally:
        await store.close()

    assert result is not None
    assert result.startswith("lore_sk_")
    # File now exists with the generated key
    assert key_path.read_text().strip() == result


@pytest.mark.asyncio
async def test_bootstrap_skips_when_db_has_keys(tmp_path):
    """Idempotency: re-running bootstrap on a populated DB is a no-op."""
    from lore.persistence.bootstrap import bootstrap_solo_if_empty

    db_path = tmp_path / "lore.db"
    key_path = tmp_path / "key.txt"

    store = await _bare_store(db_path)
    try:
        first = await bootstrap_solo_if_empty(
            store, key_path=key_path, force_for_memory=False,
        )
        second = await bootstrap_solo_if_empty(
            store, key_path=key_path, force_for_memory=False,
        )
    finally:
        await store.close()

    assert first is not None
    assert second is None  # Skipped second run.
