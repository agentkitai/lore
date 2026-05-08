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


# ── Wave B: scope filter on the read path ──────────────────────────


@pytest.mark.asyncio
async def test_recall_does_not_return_other_project_memories(sqlite_store):
    """A ``scope='project'`` row is invisible from a different project."""
    from lore.persistence import RecallParams
    from lore.services.memories import create_memory

    # Use the same query embedding for both inserts so similarity is high
    # regardless of content.
    target_vec = _vec(7)
    await create_memory(
        sqlite_store,
        org_id="solo",
        content="alpha-only secret",
        embedding=target_vec,
        project="alpha",
        meta={"type": "note"},
    )

    results = await sqlite_store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target_vec,
            limit=10,
            min_score=0.0,
            project="beta",
        )
    )
    # Querying as project=beta with the default scope filter must hide the
    # alpha-only row.
    assert all(r.project != "alpha" for r in results)


@pytest.mark.asyncio
async def test_recall_returns_global_memories_across_projects(sqlite_store):
    """A ``scope='global'`` row surfaces in any project, including projects
    that didn't author it."""
    from lore.persistence import RecallParams
    from lore.services.memories import create_memory

    target_vec = _vec(8)
    saved = await create_memory(
        sqlite_store,
        org_id="solo",
        content="universal lesson about exit codes",
        embedding=target_vec,
        project="alpha",
        meta={"type": "lesson"},  # auto → global
    )
    assert saved.scope == "global"

    results = await sqlite_store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target_vec,
            limit=10,
            min_score=0.0,
            project="beta",
        )
    )
    ids = {r.id for r in results}
    assert saved.id in ids


@pytest.mark.asyncio
async def test_recall_scope_all_returns_other_project_memories(sqlite_store):
    """``scope_mode='all'`` skips the scope predicate entirely."""
    from lore.persistence import RecallParams
    from lore.services.memories import create_memory

    target_vec = _vec(9)
    saved = await create_memory(
        sqlite_store,
        org_id="solo",
        content="repo-specific memo",
        embedding=target_vec,
        project="alpha",
        meta={"type": "note"},  # → project scope
    )
    assert saved.scope == "project"

    # Without scope_mode='all', alpha would be hidden under project=beta.
    default_results = await sqlite_store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target_vec,
            limit=10,
            min_score=0.0,
            project=None,  # no project: default would only see global rows
        )
    )
    assert saved.id not in {r.id for r in default_results}

    # With scope_mode='all' and no project, scope is fully ignored.
    results = await sqlite_store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target_vec,
            limit=10,
            min_score=0.0,
            project=None,
            scope_mode="all",
        )
    )
    ids = {r.id for r in results}
    assert saved.id in ids


@pytest.mark.asyncio
async def test_recall_with_no_current_project_returns_only_global(sqlite_store):
    """No ``project`` (e.g. unscoped key + no body project) → only
    ``scope='global'`` rows surface; ``scope='project'`` rows are filtered
    out even when their ``project`` column is NULL."""
    from lore.persistence import RecallParams
    from lore.services.memories import create_memory

    target_vec = _vec(10)
    project_row = await create_memory(
        sqlite_store,
        org_id="solo",
        content="project-scoped row with project=NULL",
        embedding=target_vec,
        project=None,
        meta={"type": "note"},
    )
    global_row = await create_memory(
        sqlite_store,
        org_id="solo",
        content="universal lesson",
        embedding=target_vec,
        project=None,
        meta={"type": "lesson"},
    )
    assert project_row.scope == "project"
    assert global_row.scope == "global"

    results = await sqlite_store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target_vec,
            limit=10,
            min_score=0.0,
            project=None,
        )
    )
    ids = {r.id for r in results}
    assert global_row.id in ids
    assert project_row.id not in ids
