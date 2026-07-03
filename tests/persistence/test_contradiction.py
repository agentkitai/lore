"""Write-time contradiction detection (#84).

Uses the parametrized ``store`` fixture (sqlite always, postgres when available)
with an injected fake scorer so no LLM is needed. Neighbors must be VISIBLE to
the writer (migration-026): shared (promoted), unowned, or the writer's own.
"""

from __future__ import annotations

import pytest

from lore.persistence import NewMemory
from lore.services.contradiction import _reset_semaphore, detect_and_flag, is_enabled


def _vec(jitter: float):
    # Near-identical vectors so the rows are nearest neighbours of each other.
    return [0.5 + (jitter if i == 0 else 0.0) for i in range(384)]


def _yes(_a, _b):
    return (True, 0.9, "opposite claim")


def _no(_a, _b):
    return (False, 0.0, "")


@pytest.fixture(autouse=True)
def _fresh_semaphore():
    _reset_semaphore()  # rebind the lazy semaphore to each test's event loop
    yield
    _reset_semaphore()


def test_is_enabled_defaults_to_llm_availability(monkeypatch):
    monkeypatch.delenv("LORE_CONTRADICTION_DETECTION", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert is_enabled() is False                    # no key, no override → off
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert is_enabled() is True                     # key present → default on
    monkeypatch.setenv("LORE_CONTRADICTION_DETECTION", "false")
    assert is_enabled() is False                    # explicit override wins over key
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LORE_CONTRADICTION_DETECTION", "true")
    assert is_enabled() is True                     # explicit on without a key


@pytest.mark.asyncio
async def test_supersedes_older_contradicted_memory(store):
    a = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is blue", embedding=_vec(0.0), user_id="y")
    )
    b = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is green", embedding=_vec(0.001), user_id="y")
    )
    await detect_and_flag(
        store, org_id="solo", memory_id=b.id, content=b.content,
        embedding=_vec(0.001), owner_user_id="y", scorer=_yes, min_similarity=0.05,
    )
    # last-write-wins: the older, contradicted memory is soft-superseded by the newer
    assert await store.is_superseded(a.id, "solo") is True
    assert await store.is_superseded(b.id, "solo") is False


@pytest.mark.asyncio
async def test_supersede_can_be_disabled(store, monkeypatch):
    monkeypatch.setenv("LORE_CONTRADICTION_SUPERSEDE", "false")
    a = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is blue", embedding=_vec(0.0), user_id="y")
    )
    b = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is green", embedding=_vec(0.001), user_id="y")
    )
    res = await detect_and_flag(
        store, org_id="solo", memory_id=b.id, content=b.content,
        embedding=_vec(0.001), owner_user_id="y", scorer=_yes, min_similarity=0.05,
    )
    assert res == [a.id]                                  # still flagged
    assert await store.is_superseded(a.id, "solo") is False  # but NOT superseded


@pytest.mark.asyncio
async def test_below_supersede_confidence_flags_but_does_not_supersede(store, monkeypatch):
    # conf 0.7: above the flag bar (0.6) but below the supersede bar (0.75)
    a = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is blue", embedding=_vec(0.0), user_id="y")
    )
    b = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is green", embedding=_vec(0.001), user_id="y")
    )
    res = await detect_and_flag(
        store, org_id="solo", memory_id=b.id, content=b.content, embedding=_vec(0.001),
        owner_user_id="y", scorer=lambda _a, _b: (True, 0.7, "leans opposite"), min_similarity=0.05,
    )
    assert res == [a.id]                                  # flagged (0.7 >= 0.6)
    assert await store.is_superseded(a.id, "solo") is False  # not superseded (0.7 < 0.75)


@pytest.mark.asyncio
async def test_flags_cross_agent_contradiction_against_shared_neighbor(store):
    a = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is blue", embedding=_vec(0.0), user_id="alice")
    )
    await store.promote_memory("solo", a.id, promoted_by="alice")  # SHARED → bob can see it
    b = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is green", embedding=_vec(0.001), user_id="bob")
    )

    conflicts = await detect_and_flag(
        store, org_id="solo", memory_id=b.id, content=b.content,
        embedding=_vec(0.001), owner_user_id="bob", scorer=_yes, min_similarity=0.05,
    )
    assert conflicts == [a.id]
    flagged = await store.get_memory("solo", b.id)
    assert "contradiction" in flagged.tags
    assert flagged.meta.get("contradicts") == [a.id]
    assert flagged.meta.get("cross_agent") is True  # alice != bob, both non-null


@pytest.mark.asyncio
async def test_does_not_disclose_another_users_private_memory(store):
    # alice's PRIVATE (un-promoted) memory must be invisible to bob — no flag,
    # no leak — even though the scorer would call it a contradiction.
    p = await store.insert_memory(
        NewMemory(org_id="solo", content="secret alice fact", embedding=_vec(0.0), user_id="alice")
    )
    b = await store.insert_memory(
        NewMemory(org_id="solo", content="contradicts the secret", embedding=_vec(0.001), user_id="bob")
    )
    res = await detect_and_flag(
        store, org_id="solo", memory_id=b.id, content=b.content,
        embedding=_vec(0.001), owner_user_id="bob", scorer=_yes, min_similarity=0.05,
    )
    assert p.id not in (res or [])
    assert "contradiction" not in (await store.get_memory("solo", b.id)).tags


@pytest.mark.asyncio
async def test_no_contradiction_does_not_flag(store):
    a = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is blue", embedding=_vec(0.0), user_id="alice")
    )
    await store.promote_memory("solo", a.id, promoted_by="alice")
    c = await store.insert_memory(
        NewMemory(org_id="solo", content="The sky is teal", embedding=_vec(0.002), user_id="bob")
    )
    res = await detect_and_flag(
        store, org_id="solo", memory_id=c.id, content=c.content,
        embedding=_vec(0.002), owner_user_id="bob", scorer=_no, min_similarity=0.05,
    )
    assert res is None
    assert "contradiction" not in (await store.get_memory("solo", c.id)).tags


@pytest.mark.asyncio
async def test_below_confidence_threshold_not_flagged(store, monkeypatch):
    monkeypatch.setenv("LORE_CONTRADICTION_MIN_CONFIDENCE", "0.8")
    # same-owner neighbor is visible to the writer without promotion
    await store.insert_memory(
        NewMemory(org_id="solo", content="A", embedding=_vec(0.0), user_id="y")
    )
    b = await store.insert_memory(
        NewMemory(org_id="solo", content="not A", embedding=_vec(0.001), user_id="y")
    )
    res = await detect_and_flag(
        store, org_id="solo", memory_id=b.id, content=b.content, embedding=_vec(0.001),
        owner_user_id="y", scorer=lambda _x, _y: (True, 0.5, "weak"), min_similarity=0.05,
    )
    assert res is None
