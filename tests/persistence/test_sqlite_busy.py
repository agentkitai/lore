"""Phase 3J: SqliteStore SQLITE_BUSY retry tests.

The ``SqliteStore.transaction()`` context manager catches
``aiosqlite.OperationalError("database is locked")`` on the BEGIN IMMEDIATE
acquisition and retries with exponential backoff (50/100/200/400 ms).
After 5 total failed attempts, ``StoreBusyError`` is raised.

The tests mock the underlying ``conn.execute("BEGIN IMMEDIATE")`` call to
return controlled failures rather than relying on real lock contention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip the whole module if the optional [solo] deps aren't installed.
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")

import aiosqlite  # noqa: E402

from lore.persistence.exceptions import StoreBusyError  # noqa: E402


def _busy_error() -> aiosqlite.OperationalError:
    return aiosqlite.OperationalError("database is locked")


@pytest.mark.asyncio
async def test_busy_retries_then_succeeds(tmp_path: Path, monkeypatch):
    """Two transient SQLITE_BUSY failures, then a successful commit."""
    from lore.persistence.sqlite import SqliteStore

    db_path = tmp_path / "lore.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    store = await SqliteStore.open(f"sqlite:///{db_path}")

    # Speed up the test by stubbing asyncio.sleep so the retry budget fires
    # instantly. (No need to model real wall-clock for the unit test.)
    import lore.persistence.sqlite as sqlite_mod

    sleep_calls: list[float] = []

    async def _fast_sleep(d: float) -> None:
        sleep_calls.append(d)

    monkeypatch.setattr(sqlite_mod.asyncio, "sleep", _fast_sleep)

    try:
        original_execute = store._conn.execute
        call_count = {"n": 0}

        def _proxy_execute(sql, *args, **kwargs):
            # Fail the first two BEGIN IMMEDIATE invocations; pass through
            # everything else (including the eventual success and the inner
            # statement we run inside the transaction).
            if isinstance(sql, str) and sql.strip().upper().startswith("BEGIN IMMEDIATE"):
                call_count["n"] += 1
                if call_count["n"] <= 2:
                    async def _raise():
                        raise _busy_error()
                    return _raise()
            return original_execute(sql, *args, **kwargs)

        store._conn.execute = _proxy_execute  # type: ignore[assignment]
        async with store.transaction() as tx:
            await tx.execute(
                "INSERT INTO orgs (id, name) VALUES (?, ?)",
                ("retry_org", "Retry Org"),
            )

        # Two failures, two backoff sleeps, third try succeeded.
        assert call_count["n"] == 3
        assert sleep_calls == [0.05, 0.1]

        # The committed insert is visible.
        store._conn.execute = original_execute  # type: ignore[assignment]
        async with store._conn.execute(
            "SELECT name FROM orgs WHERE id = ?", ("retry_org",)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None and row["name"] == "Retry Org"
    finally:
        store._conn.execute = original_execute  # type: ignore[assignment]
        await store.close()


@pytest.mark.asyncio
async def test_busy_exhausts_after_max_attempts_raises_typed_error(
    tmp_path: Path, monkeypatch
):
    """Persistent SQLITE_BUSY exhausts the retry budget and raises StoreBusyError."""
    from lore.persistence.sqlite import SqliteStore

    db_path = tmp_path / "lore.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    store = await SqliteStore.open(f"sqlite:///{db_path}")

    import lore.persistence.sqlite as sqlite_mod

    sleep_calls: list[float] = []

    async def _fast_sleep(d: float) -> None:
        sleep_calls.append(d)

    monkeypatch.setattr(sqlite_mod.asyncio, "sleep", _fast_sleep)

    try:
        original_execute = store._conn.execute

        def _always_busy(sql, *args, **kwargs):
            if isinstance(sql, str) and sql.strip().upper().startswith("BEGIN IMMEDIATE"):
                async def _raise():
                    raise _busy_error()
                return _raise()
            return original_execute(sql, *args, **kwargs)

        store._conn.execute = _always_busy  # type: ignore[assignment]

        with pytest.raises(StoreBusyError) as ei:
            async with store.transaction() as tx:
                await tx.execute("SELECT 1")
        # Original cause preserved.
        assert isinstance(ei.value.__cause__, aiosqlite.OperationalError)
        # 4 backoff sleeps before exhaustion (one per failed retry).
        assert sleep_calls == [0.05, 0.1, 0.2, 0.4]
    finally:
        store._conn.execute = original_execute  # type: ignore[assignment]
        await store.close()


@pytest.mark.asyncio
async def test_non_busy_operational_error_not_retried(tmp_path: Path, monkeypatch):
    """OperationalError without the busy hint is propagated without retry."""
    from lore.persistence.sqlite import SqliteStore

    db_path = tmp_path / "lore.db"
    monkeypatch.setenv("HOME", str(tmp_path))
    store = await SqliteStore.open(f"sqlite:///{db_path}")

    try:
        original_execute = store._conn.execute

        def _other_error(sql, *args, **kwargs):
            if isinstance(sql, str) and sql.strip().upper().startswith("BEGIN IMMEDIATE"):
                async def _raise():
                    raise aiosqlite.OperationalError("syntax error near foo")
                return _raise()
            return original_execute(sql, *args, **kwargs)

        store._conn.execute = _other_error  # type: ignore[assignment]
        with pytest.raises(aiosqlite.OperationalError, match="syntax error"):
            async with store.transaction():
                pass
    finally:
        store._conn.execute = original_execute  # type: ignore[assignment]
        await store.close()
