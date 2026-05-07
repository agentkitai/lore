"""Phase 4A: ``lore.AsyncLore`` round-trip + lifecycle tests.

These tests exercise the embedded async API end-to-end against an
in-memory SQLite store, covering:

* ``async with AsyncLore("sqlite:///:memory:") as lore:`` opens cleanly
  (Phase 3J bootstrap is force-run for ``:memory:`` so the solo org
  exists for service calls to attach to).
* ``remember`` -> ``get`` -> ``recall`` -> ``forget`` round-trip.
* ``list_memories`` returns inserted memories.
* The bootstrap path leaves the solo org row in place but does NOT write
  ``~/.lore/key.txt`` (in-memory runs must not touch the FS).
* ``__aexit__`` closes the underlying Store.

A lightweight stub embedder (deterministic 384-dim vectors derived from
``hash(text)``) keeps these tests off the onnxruntime download path so
they run in CI without GPU/model-cache setup.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import List

import pytest

# Skip the whole module if the optional [solo] deps aren't installed.
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")


# ── Fixtures ─────────────────────────────────────────────────────────


def _stub_embed(text: str) -> List[float]:
    """Deterministic 384-dim vector — keeps tests off the ONNX download path.

    Builds a vector by hashing ``text`` to a seed and emitting 384 floats
    in [-1, 1]. Stable for the same input so recall() can match on it.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    # Stretch 32 bytes -> 384 floats by repeating the seed and tweaking
    # each float by its position. Good enough for distinct-vector tests.
    out: List[float] = []
    for i in range(384):
        b = digest[i % len(digest)]
        out.append(((b ^ (i * 7 & 0xFF)) - 128) / 128.0)
    return out


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Redirect ``$HOME`` so any stray bootstrap key write lands in tmp.

    The ``:memory:`` path under test must NOT write a key file; this
    fixture is a belt-and-suspenders to keep the developer's real
    ``~/.lore/key.txt`` untouched if anything regresses.
    """
    monkeypatch.setenv("HOME", str(tmp_path))


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lifecycle_open_and_close():
    """``async with AsyncLore(":memory:")`` opens, sets org_id, closes cleanly."""
    from lore import AsyncLore

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        assert lore.org_id == "solo"
        assert lore.workspace == "solo"
        # The Store should be open and usable.
        assert lore.store is not None

    # After exit, accessing the store should fail.
    with pytest.raises(RuntimeError):
        _ = lore.store


@pytest.mark.asyncio
async def test_memory_db_does_not_write_keyfile(tmp_path: Path):
    """``:memory:`` AsyncLore opens without leaving ``~/.lore/key.txt`` behind."""
    from lore import AsyncLore

    key_target = tmp_path / ".lore" / "key.txt"
    assert not key_target.exists()

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        # Sanity: org row was created by the force_for_memory bootstrap.
        async with lore.store._conn.execute(  # type: ignore[attr-defined]
            "SELECT id FROM orgs WHERE id = 'solo'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None

    assert not key_target.exists(), (
        "AsyncLore must not write key.txt for :memory: DBs"
    )


@pytest.mark.asyncio
async def test_remember_then_get_then_forget():
    """Round-trip: insert a memory, fetch it back, delete it, fetch returns None."""
    from lore import AsyncLore

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        stored = await lore.remember(
            "Always retry transient errors with exponential backoff",
            tags=["retry", "policy"],
            project="infra",
            source="test",
        )
        assert stored.id.startswith("mem_")
        assert stored.org_id == "solo"
        assert stored.project == "infra"
        assert tuple(stored.tags) == ("retry", "policy")

        fetched = await lore.get(stored.id)
        assert fetched is not None
        assert fetched.content == stored.content

        deleted = await lore.forget(stored.id)
        assert deleted is True

        gone = await lore.get(stored.id)
        assert gone is None


@pytest.mark.asyncio
async def test_recall_returns_scored_match():
    """``recall`` returns the inserted memory when querying with the same text."""
    from lore import AsyncLore

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        target = await lore.remember(
            "Use exponential backoff for HTTP 429 responses"
        )
        # Insert a couple of distractors to exercise the ordering path.
        await lore.remember("Pasta sauce recipe: tomato + basil")
        await lore.remember("Refactor the auth middleware module")

        hits = await lore.recall(
            "Use exponential backoff for HTTP 429 responses",
            k=5,
            min_score=0.0,
        )
        assert len(hits) >= 1
        ids = {h.id for h in hits}
        assert target.id in ids


@pytest.mark.asyncio
async def test_list_memories_returns_all_inserted():
    """``list_memories`` returns rows inserted via ``remember``."""
    from lore import AsyncLore

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        m1 = await lore.remember("alpha", project="proj-a")
        m2 = await lore.remember("beta", project="proj-a")
        m3 = await lore.remember("gamma", project="proj-b")

        all_rows = await lore.list_memories(limit=100)
        ids = {m.id for m in all_rows}
        assert {m1.id, m2.id, m3.id}.issubset(ids)

        only_a = await lore.list_memories(project="proj-a", limit=100)
        assert {m.id for m in only_a} == {m1.id, m2.id}


@pytest.mark.asyncio
async def test_explicit_embedding_skips_embedder():
    """Passing ``embedding=...`` to ``remember`` short-circuits the embedder."""
    from lore import AsyncLore

    calls: list[str] = []

    def tracking_embed(text: str) -> List[float]:
        calls.append(text)
        return _stub_embed(text)

    async with AsyncLore("sqlite:///:memory:", embed=tracking_embed) as lore:
        # Pre-built embedding -> embedder is NOT called for content.
        await lore.remember("hello", embedding=[0.0] * 384)
        assert calls == []

        # Without ``embedding=`` it IS called.
        await lore.remember("world")
        assert calls == ["world"]


@pytest.mark.asyncio
async def test_async_embedder_supported():
    """An async ``embed`` callable is awaited by AsyncLore."""
    from lore import AsyncLore

    async def async_embed(text: str) -> List[float]:
        return _stub_embed(text)

    async with AsyncLore("sqlite:///:memory:", embed=async_embed) as lore:
        m = await lore.remember("async embed works")
        assert m.id.startswith("mem_")


@pytest.mark.asyncio
async def test_use_after_close_raises():
    """Calling methods on a closed AsyncLore raises ``RuntimeError``."""
    from lore import AsyncLore

    lore = AsyncLore("sqlite:///:memory:", embed=_stub_embed)
    async with lore:
        await lore.remember("inside")
    with pytest.raises(RuntimeError):
        await lore.remember("outside")


@pytest.mark.asyncio
async def test_org_id_validation_raises_for_unknown_org(tmp_path: Path):
    """Explicit ``org_id`` that isn't in the DB raises ``ConfigError``."""
    from lore import AsyncLore
    from lore.persistence import ConfigError

    with pytest.raises(ConfigError):
        async with AsyncLore(
            "sqlite:///:memory:", embed=_stub_embed, org_id="nonexistent"
        ):
            pass  # pragma: no cover


@pytest.mark.asyncio
async def test_filebacked_db_bootstrap_default_path(tmp_path: Path, monkeypatch):
    """File-backed AsyncLore uses the standard Phase 3J bootstrap path.

    Subtle but important: file-backed DBs don't take the
    ``force_for_memory`` route — the SqliteStore.open() bootstrap fires
    in the factory and writes the key file to ``~/.lore/key.txt``.
    AsyncLore should still find the solo org and work normally.
    """
    from lore import AsyncLore

    db_path = tmp_path / "lore.db"
    monkeypatch.setenv("HOME", str(tmp_path))

    async with AsyncLore(
        f"sqlite:///{db_path}", embed=_stub_embed
    ) as lore:
        m = await lore.remember("file-backed roundtrip")
        fetched = await lore.get(m.id)
        assert fetched is not None
        assert fetched.content == "file-backed roundtrip"

    # The default bootstrap path writes ``~/.lore/key.txt``.
    expected = tmp_path / ".lore" / "key.txt"
    assert expected.exists()
    # Sanity: 0600.
    import stat
    mode = stat.S_IMODE(os.stat(expected).st_mode)
    assert mode == 0o600
