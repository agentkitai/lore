"""Tests for _seed_root_key_from_env — declarative root-key provisioning + rotation.

Uses a mocked asyncpg pool (same pattern as test_org_init.py). Covers the guard,
first-boot create, in-place rotation when LORE_API_KEY changes, and the no-op
when it hasn't — the behavior a docker-compose/k8s deploy relies on.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from lore.server.app import _seed_root_key_from_env

KEY = "lore_sk_" + "a" * 32
HASH = hashlib.sha256(KEY.encode()).hexdigest()


def _make_mock_pool(*, fetchval_return=None, fetchrow_return=None):
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=fetchval_return)
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_conn.execute = AsyncMock()

    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    mock_pool = AsyncMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=mock_conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=acm)
    return mock_pool, mock_conn


@pytest.mark.asyncio
async def test_noop_when_key_unset(monkeypatch):
    monkeypatch.delenv("LORE_API_KEY", raising=False)
    with patch("lore.server.db.get_pool") as gp:
        await _seed_root_key_from_env()
    gp.assert_not_called()


@pytest.mark.asyncio
async def test_raises_on_prefixless_key(monkeypatch):
    monkeypatch.setenv("LORE_API_KEY", "plain-no-prefix")
    with patch("lore.server.db.get_pool") as gp:
        with pytest.raises(RuntimeError, match="lore_sk_"):
            await _seed_root_key_from_env()
    gp.assert_not_called()  # guard fires before any DB access


@pytest.mark.asyncio
async def test_creates_org_and_key_when_empty(monkeypatch):
    monkeypatch.setenv("LORE_API_KEY", KEY)
    mock_pool, mock_conn = _make_mock_pool(fetchval_return=None)
    with patch("lore.server.db.get_pool", return_value=mock_pool):
        await _seed_root_key_from_env()
    # INSERT orgs + INSERT api_keys
    assert mock_conn.execute.call_count == 2
    insert_key = mock_conn.execute.call_args_list[1][0]
    assert "INSERT INTO api_keys" in insert_key[0]
    assert insert_key[4] == HASH  # $4 = key_hash


@pytest.mark.asyncio
async def test_rotates_key_when_env_changed(monkeypatch):
    monkeypatch.setenv("LORE_API_KEY", KEY)
    mock_pool, mock_conn = _make_mock_pool(
        fetchval_return="org-1",
        fetchrow_return={"id": "key-1", "key_hash": "stale-old-hash"},
    )
    with patch("lore.server.db.get_pool", return_value=mock_pool):
        await _seed_root_key_from_env()
    assert mock_conn.execute.call_count == 1
    sql, *args = mock_conn.execute.call_args_list[0][0]
    assert "UPDATE api_keys" in sql
    assert args[0] == HASH  # new key_hash
    assert args[2] == "key-1"  # WHERE id


@pytest.mark.asyncio
async def test_noop_when_key_unchanged(monkeypatch):
    monkeypatch.setenv("LORE_API_KEY", KEY)
    mock_pool, mock_conn = _make_mock_pool(
        fetchval_return="org-1",
        fetchrow_return={"id": "key-1", "key_hash": HASH},
    )
    with patch("lore.server.db.get_pool", return_value=mock_pool):
        await _seed_root_key_from_env()
    mock_conn.execute.assert_not_called()  # hash matches — nothing to do


@pytest.mark.asyncio
async def test_inserts_key_when_org_has_none(monkeypatch):
    monkeypatch.setenv("LORE_API_KEY", KEY)
    mock_pool, mock_conn = _make_mock_pool(fetchval_return="org-1", fetchrow_return=None)
    with patch("lore.server.db.get_pool", return_value=mock_pool):
        await _seed_root_key_from_env()
    assert mock_conn.execute.call_count == 1
    assert "INSERT INTO api_keys" in mock_conn.execute.call_args_list[0][0][0]
