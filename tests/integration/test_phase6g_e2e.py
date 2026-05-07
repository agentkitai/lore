"""Phase 6G (claude-mem parity) end-to-end tests.

Coverage (Wave A — write side; runs on both backends via the parametrized
``store`` fixture):

* ``create_observation`` carries ``NewObservation.scope`` through to the
  persisted memories row.
* ``create_memory`` derives a default scope from ``meta.type`` —
  universal types (lesson/preference/pattern/convention) become
  ``'global'``, everything else stays ``'project'``. An explicit
  ``scope=`` argument always wins.

Wave B (read-side scope filter on ``recall_by_embedding``) is covered in
the same module's later commit.

The parametrized ``store`` fixture skips on every parameter when Postgres
isn't reachable; the SQLite-only mirror in
``tests/services/test_phase6g_scope.py`` covers the same surface area
without needing the docker container.
"""

from __future__ import annotations

from typing import Sequence

import pytest

# Re-export the parametrized ``store`` fixture so this module's tests run on
# both backends. The ``_pg_pool`` re-export is required because pytest
# resolves fixture dependencies via the test module's namespace.
from tests.persistence.conftest import _pg_pool, store  # noqa: F401


def _vec(seed: int) -> Sequence[float]:
    """Deterministic 384-dim vector matching the contract-test helper."""
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


# ── Wave A: scope on the write path ────────────────────────────────


@pytest.mark.asyncio
async def test_observation_with_explicit_scope_persists(store):  # noqa: F811
    """``create_observation`` honours ``NewObservation.scope='global'``."""
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
    stored = await create_observation(store, obs, fake_embed)
    assert stored.scope == "global"

    fetched = await store.get_memory("solo", stored.id)
    assert fetched is not None
    assert fetched.scope == "global"


@pytest.mark.asyncio
async def test_observation_default_scope_is_project(store):  # noqa: F811
    """``NewObservation`` without an explicit scope defaults to ``'project'``."""
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
    stored = await create_observation(store, obs, fake_embed)
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
async def test_remember_default_scope_by_type(store, memory_type, expected_scope):  # noqa: F811
    """``create_memory`` derives default scope from ``meta.type``."""
    from lore.services.memories import create_memory

    stored = await create_memory(
        store,
        org_id="solo",
        content=f"a {memory_type}",
        embedding=_vec(hash(memory_type) & 0xFF),
        meta={"type": memory_type},
    )
    assert stored.scope == expected_scope

    fetched = await store.get_memory("solo", stored.id)
    assert fetched is not None
    assert fetched.scope == expected_scope


@pytest.mark.asyncio
async def test_remember_explicit_scope_overrides_type_default(store):  # noqa: F811
    """An explicit ``scope='project'`` wins over the type-based default."""
    from lore.services.memories import create_memory

    stored = await create_memory(
        store,
        org_id="solo",
        content="lesson but pinned to this repo",
        embedding=_vec(42),
        meta={"type": "lesson"},
        scope="project",
    )
    assert stored.scope == "project"

    fetched = await store.get_memory("solo", stored.id)
    assert fetched is not None
    assert fetched.scope == "project"


# ── Wave B: scope filter on the read path ──────────────────────────


@pytest.mark.asyncio
async def test_recall_does_not_return_other_project_memories(store):  # noqa: F811
    """A ``scope='project'`` row is invisible from a different project."""
    from lore.persistence import RecallParams
    from lore.services.memories import create_memory

    target_vec = _vec(7)
    await create_memory(
        store,
        org_id="solo",
        content="alpha-only secret",
        embedding=target_vec,
        project="alpha",
        meta={"type": "note"},
    )

    results = await store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target_vec,
            limit=10,
            min_score=0.0,
            project="beta",
        )
    )
    assert all(r.project != "alpha" for r in results)


@pytest.mark.asyncio
async def test_recall_returns_global_memories_across_projects(store):  # noqa: F811
    """A ``scope='global'`` row surfaces in any project."""
    from lore.persistence import RecallParams
    from lore.services.memories import create_memory

    target_vec = _vec(8)
    saved = await create_memory(
        store,
        org_id="solo",
        content="universal lesson about exit codes",
        embedding=target_vec,
        project="alpha",
        meta={"type": "lesson"},
    )
    assert saved.scope == "global"

    results = await store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target_vec,
            limit=10,
            min_score=0.0,
            project="beta",
        )
    )
    assert saved.id in {r.id for r in results}


@pytest.mark.asyncio
async def test_recall_scope_all_returns_other_project_memories(store):  # noqa: F811
    """``scope_mode='all'`` skips the scope predicate entirely."""
    from lore.persistence import RecallParams
    from lore.services.memories import create_memory

    target_vec = _vec(9)
    saved = await create_memory(
        store,
        org_id="solo",
        content="repo-specific memo",
        embedding=target_vec,
        project="alpha",
        meta={"type": "note"},
    )
    assert saved.scope == "project"

    results = await store.recall_by_embedding(
        RecallParams(
            org_id="solo",
            query_vec=target_vec,
            limit=10,
            min_score=0.0,
            project=None,
            scope_mode="all",
        )
    )
    assert saved.id in {r.id for r in results}


@pytest.mark.asyncio
async def test_recall_with_no_current_project_returns_only_global(store):  # noqa: F811
    """No ``project`` → only ``scope='global'`` rows surface."""
    from lore.persistence import RecallParams
    from lore.services.memories import create_memory

    target_vec = _vec(10)
    project_row = await create_memory(
        store,
        org_id="solo",
        content="project-scoped row with project=NULL",
        embedding=target_vec,
        project=None,
        meta={"type": "note"},
    )
    global_row = await create_memory(
        store,
        org_id="solo",
        content="universal lesson",
        embedding=target_vec,
        project=None,
        meta={"type": "lesson"},
    )
    assert project_row.scope == "project"
    assert global_row.scope == "global"

    results = await store.recall_by_embedding(
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
