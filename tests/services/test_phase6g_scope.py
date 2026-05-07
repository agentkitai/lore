"""Phase 6G (claude-mem parity) sqlite-only service-layer scope tests.

The parametrized ``store`` fixture in ``tests/persistence/conftest.py``
co-mounts a Postgres connection pool, so it short-circuits to ``SKIPPED``
on every parameter (including ``sqlite``) when Postgres isn't reachable —
which is the local-dev case. This module spins up a fresh
``SqliteStore`` directly so the scope plumbing is exercised without
Postgres. The same assertions run on Postgres too via the parametrized
file in ``tests/integration/test_phase6g_e2e.py`` once CI brings up the
container.

Coverage (Wave A — write-side):

* ``create_observation`` honours an explicit ``NewObservation.scope`` and
  defaults to ``'project'`` when one isn't set.
* ``create_memory`` derives a default scope from ``meta.type`` —
  universal types (lesson/preference/pattern/convention) become
  ``'global'``, everything else stays ``'project'``.
* An explicit ``scope=`` argument always wins over the type-based default
  in either direction.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Sequence

import pytest
import pytest_asyncio


def _vec(seed: int) -> Sequence[float]:
    """Deterministic 384-dim vector matching the contract-test helper."""
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


@pytest_asyncio.fixture(loop_scope="function")
async def sqlite_store(tmp_path: Path) -> AsyncIterator:
    """A fresh on-disk SqliteStore with the canonical 'solo' org seeded."""
    pytest.importorskip("aiosqlite")
    pytest.importorskip("sqlite_vec")

    from lore.persistence.factory import make_store

    db_path = tmp_path / "phase6g.db"
    store = await make_store(f"sqlite:///{db_path}")
    try:
        await store._conn.execute(
            "INSERT OR IGNORE INTO orgs (id, name) VALUES (?, ?)",
            ("solo", "Solo Test"),
        )
        await store._conn.commit()
        yield store
    finally:
        await store.close()


# ── Wave A: scope on the write path ────────────────────────────────


@pytest.mark.asyncio
async def test_observation_with_explicit_scope_persists(sqlite_store):
    from lore.persistence import NewObservation
    from lore.services.observations import create_observation

    async def fake_embed(text: str):
        return [0.0] * 384

    obs = NewObservation(
        org_id="solo",
        title="Universal lesson",
        facts=("always quote env vars",),
        narrative="Universal shell-quoting lesson.",
        scope="global",
    )
    stored = await create_observation(sqlite_store, obs, fake_embed)
    assert stored.scope == "global"

    fetched = await sqlite_store.get_memory("solo", stored.id)
    assert fetched is not None
    assert fetched.scope == "global"


@pytest.mark.asyncio
async def test_observation_default_scope_is_project(sqlite_store):
    from lore.persistence import NewObservation
    from lore.services.observations import create_observation

    async def fake_embed(text: str):
        return [0.0] * 384

    obs = NewObservation(
        org_id="solo",
        title="Repo-specific quirk",
        facts=("uses sqlite under the hood",),
        narrative="Lore stores observations in the memories table.",
    )
    stored = await create_observation(sqlite_store, obs, fake_embed)
    assert stored.scope == "project"


@pytest.mark.parametrize(
    "memory_type,expected_scope",
    [
        ("lesson", "global"),
        ("preference", "global"),
        ("pattern", "global"),
        ("convention", "global"),
        ("note", "project"),
        ("fact", "project"),
    ],
)
@pytest.mark.asyncio
async def test_remember_default_scope_by_type(
    sqlite_store, memory_type, expected_scope
):
    from lore.services.memories import create_memory

    stored = await create_memory(
        sqlite_store,
        org_id="solo",
        content=f"a {memory_type}",
        embedding=_vec(hash(memory_type) & 0xFF),
        meta={"type": memory_type},
    )
    assert stored.scope == expected_scope

    fetched = await sqlite_store.get_memory("solo", stored.id)
    assert fetched is not None
    assert fetched.scope == expected_scope


@pytest.mark.asyncio
async def test_remember_explicit_scope_overrides_type_default(sqlite_store):
    from lore.services.memories import create_memory

    stored = await create_memory(
        sqlite_store,
        org_id="solo",
        content="lesson but pinned to this repo",
        embedding=_vec(42),
        meta={"type": "lesson"},
        scope="project",
    )
    assert stored.scope == "project"


@pytest.mark.asyncio
async def test_remember_explicit_global_for_non_universal_type(sqlite_store):
    from lore.services.memories import create_memory

    stored = await create_memory(
        sqlite_store,
        org_id="solo",
        content="a fact promoted to global",
        embedding=_vec(43),
        meta={"type": "fact"},
        scope="global",
    )
    assert stored.scope == "global"


def test_default_scope_for_type_helper():
    from lore.services.memories import GLOBAL_TYPES, default_scope_for_type

    assert default_scope_for_type("lesson") == "global"
    assert default_scope_for_type("preference") == "global"
    assert default_scope_for_type("pattern") == "global"
    assert default_scope_for_type("convention") == "global"
    assert default_scope_for_type("note") == "project"
    assert default_scope_for_type("fact") == "project"
    assert default_scope_for_type("observation") == "project"
    assert default_scope_for_type(None) == "project"
    assert default_scope_for_type("") == "project"
    assert "lesson" in GLOBAL_TYPES
    assert "note" not in GLOBAL_TYPES
