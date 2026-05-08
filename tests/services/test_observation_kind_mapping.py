"""Phase 6G — meta.kind tag-mapping tests for ``create_observation``.

The classification rule (see ``lore.services.observations._classify_kind``):

    tags contains "intent"          → meta.kind = "intent"
    tags contains "session-summary" → meta.kind = "summary"
    captured_by="auto", no special  → meta.kind = "tool"
    captured_by="manual", no special → meta.kind absent (not auto-classified)
"""

from __future__ import annotations

import pytest

from lore.persistence import NewObservation
from lore.services.observations import _classify_kind, create_observation

# Re-export the parametrized backend matrix so create_observation runs on
# both sqlite and postgres (postgres skips automatically when the local
# pool isn't reachable).
from tests.persistence.conftest import _pg_pool, store  # noqa: F401

# ── Pure helper unit tests ────────────────────────────────────────────


class TestClassifyKind:
    def test_intent_tag_wins(self):
        assert _classify_kind(["intent"], "auto") == "intent"

    def test_intent_tag_wins_over_other_tags(self):
        assert _classify_kind(["foo", "intent", "bar"], "auto") == "intent"

    def test_session_summary_tag_maps_to_summary(self):
        assert _classify_kind(["session-summary"], "auto") == "summary"

    def test_intent_tag_wins_even_for_manual(self):
        # The contract: tags override the auto/manual default. A
        # manually-tagged intent observation still gets meta.kind=intent.
        assert _classify_kind(["intent"], "manual") == "intent"

    def test_auto_no_special_tag_is_tool(self):
        assert _classify_kind([], "auto") == "tool"
        assert _classify_kind(["random"], "auto") == "tool"

    def test_manual_no_special_tag_is_unclassified(self):
        assert _classify_kind([], "manual") is None
        assert _classify_kind(["random"], "manual") is None

    def test_non_string_tags_ignored(self):
        # Defensive: misbehaving callers occasionally pass mixed types.
        assert _classify_kind([None, 42, "intent"], "auto") == "intent"  # type: ignore[list-item]
        assert _classify_kind([None, 42], "manual") is None  # type: ignore[list-item]


# ── End-to-end create_observation round-trip ──────────────────────────


@pytest.mark.asyncio
async def test_create_observation_intent_tag_sets_meta_kind(store):  # noqa: F811
    async def fake_embed(text: str):
        return [0.1] * 8

    obs = NewObservation(
        org_id="solo",
        title="user wanted feature X",
        facts=("they said 'add X'",),
        narrative="During this batch the user asked for X.",
        tags=("intent",),
    )
    stored = await create_observation(store, obs, fake_embed)
    assert stored.meta.get("kind") == "intent"


@pytest.mark.asyncio
async def test_create_observation_session_summary_tag_sets_meta_kind(store):  # noqa: F811
    async def fake_embed(text: str):
        return [0.1] * 8

    obs = NewObservation(
        org_id="solo",
        title="session wrap-up",
        facts=("did the thing",),
        narrative="Did the thing.",
        tags=("session-summary",),
    )
    stored = await create_observation(store, obs, fake_embed)
    assert stored.meta.get("kind") == "summary"


@pytest.mark.asyncio
async def test_create_observation_auto_default_tool_kind(store):  # noqa: F811
    async def fake_embed(text: str):
        return [0.1] * 8

    obs = NewObservation(
        org_id="solo",
        title="ran the linter",
        facts=("eslint passed",),
        narrative="Linter clean.",
        # No special tag, default captured_by='auto'.
    )
    stored = await create_observation(store, obs, fake_embed)
    assert stored.meta.get("kind") == "tool"


@pytest.mark.asyncio
async def test_create_observation_manual_no_kind(store):  # noqa: F811
    async def fake_embed(text: str):
        return [0.1] * 8

    obs = NewObservation(
        org_id="solo",
        title="manual note",
        facts=("hand-written",),
        narrative="Manual entry.",
        captured_by="manual",
    )
    stored = await create_observation(store, obs, fake_embed)
    # Manual observations stay unclassified — no meta.kind key at all.
    assert "kind" not in stored.meta
