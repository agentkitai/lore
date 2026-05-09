"""Phase 6B — observation tier tests.

Coverage:

* Unit: ``NewObservation`` dataclass invariants.
* Service: ``create_observation`` round-trip on both backends; verifies
  the structured ``meta`` payload, the ``meta.type='observation'``
  discriminator location, and the content/context column mapping.
* Routes: ``POST /v1/observations`` + ``GET /v1/observations`` +
  ``GET /v1/observations/{id}`` happy paths and 404 mismatch.
* MCP tool: ``mcp__lore__remember_observation`` calls
  ``POST /v1/observations`` via the HTTP store with the right body.
* Auto-capture prompt: rendered prompt advertises
  ``remember_observation`` as the preferred tool.
* CLI: ``lore observations list`` and ``show`` round-trip via the
  Lore client (in-memory MemoryStore).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the parametrized ``store`` fixture so service-layer tests run on
# both backends. The ``_pg_pool`` re-export is required because pytest
# resolves fixture dependencies via the test module's namespace.
from tests.persistence.conftest import _pg_pool, store  # noqa: F401

# ── Unit: NewObservation dataclass ─────────────────────────────────


class TestNewObservationDataclass:
    def test_required_fields(self):
        from lore.persistence import NewObservation

        obs = NewObservation(
            org_id="solo",
            title="t",
            facts=("a", "b"),
            narrative="n",
        )
        assert obs.org_id == "solo"
        assert obs.title == "t"
        assert obs.facts == ("a", "b")
        assert obs.narrative == "n"
        assert obs.captured_by == "auto"
        assert obs.tags == ()
        assert obs.project is None
        assert obs.source is None
        assert obs.session_id is None

    def test_frozen(self):
        from lore.persistence import NewObservation

        obs = NewObservation(org_id="solo", title="t", facts=(), narrative="n")
        with pytest.raises(Exception):  # FrozenInstanceError
            obs.title = "x"  # type: ignore[misc]


# ── Service: create_observation round-trip ─────────────────────────


@pytest.mark.asyncio
async def test_create_observation_round_trip(store):  # noqa: F811
    """Service-layer round-trip on both backends. Verifies the meta
    payload, content/context placement, and discriminator location."""
    from lore.persistence import NewObservation
    from lore.services.observations import create_observation

    async def fake_embed(text: str):
        # Title + narrative are concatenated in the service.
        assert "phase 6a bootstrap" in text.lower()
        assert "asynclore" in text.lower()
        return [0.0] * 384

    obs = NewObservation(
        org_id="solo",
        title="Phase 6A bootstrap quirk",
        facts=("bootstrap skips :memory: by default", "force_for_memory=True opt-in"),
        narrative=(
            "Investigated why AsyncLore tests failed; found bootstrap_solo_if_empty "
            "had a :memory: skip-clause."
        ),
        tags=("phase-6a", "bootstrap"),
        project="lore-tests",
        source="test",
        session_id="sess-abc",
    )

    stored = await create_observation(store, obs, fake_embed)

    assert stored.id
    assert stored.content == obs.narrative           # content = narrative
    assert stored.context == obs.title               # context = title
    assert stored.project == "lore-tests"
    assert stored.source == "test"
    assert tuple(stored.tags) == ("phase-6a", "bootstrap")

    # Discriminator + structured payload land in meta.
    meta = dict(stored.meta)
    assert meta["type"] == "observation"
    assert meta["title"] == obs.title
    assert meta["narrative"] == obs.narrative
    assert list(meta["facts"]) == list(obs.facts)
    assert meta["captured_by"] == "auto"
    assert meta["session_id"] == "sess-abc"


@pytest.mark.asyncio
async def test_create_observation_default_source(store):  # noqa: F811
    """Source defaults to 'observation' when not supplied."""
    from lore.persistence import NewObservation
    from lore.services.observations import create_observation

    async def fake_embed(text: str):
        return [0.0] * 384

    obs = NewObservation(
        org_id="solo",
        title="t",
        facts=(),
        narrative="n",
    )
    stored = await create_observation(store, obs, fake_embed)
    assert stored.source == "observation"


@pytest.mark.asyncio
async def test_observation_listable_by_type_filter(store):  # noqa: F811
    """list_memories(type='observation') returns observations only."""
    from lore.persistence import NewObservation
    from lore.services.memories import create_memory, list_memories
    from lore.services.observations import create_observation

    async def fake_embed(text: str):
        return [0.0] * 384

    await create_observation(
        store,
        NewObservation(org_id="solo", title="t1", facts=("f",), narrative="n1"),
        fake_embed,
    )
    # A polished memory carrying type='lesson' must NOT match.
    await create_memory(
        store,
        org_id="solo",
        content="polished",
        embedding=[0.0] * 384,
        meta={"type": "lesson"},
    )

    rows = await list_memories(store, org_id="solo", type="observation")
    assert len(rows) == 1
    assert dict(rows[0].meta).get("type") == "observation"


# ── Routes: POST/GET /v1/observations ──────────────────────────────


@pytest.fixture
def routes_client(monkeypatch):
    """FastAPI TestClient wired to the observations router with auth bypassed."""
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lore.server.auth import AuthContext, get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.observations import router

    auth = AuthContext(
        org_id="org-001",
        project=None,
        is_root=True,
        key_id="key-001",
        role="admin",
    )

    fake_store = object()  # never directly accessed; service is mocked.

    async def _fake_get_store():
        return fake_store

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = _fake_get_store
    app.dependency_overrides[get_auth_context] = lambda: auth
    monkeypatch.setattr(
        "lore.server.routes.observations.require_role",
        lambda *roles: lambda: auth,
    )

    # Stub the embedder so tests don't load ONNX.
    class _StubEmbedder:
        def embed(self, text: str):
            return [0.0] * 384

    monkeypatch.setattr(
        "lore.server.routes.retrieve._get_embedder",
        lambda: _StubEmbedder(),
    )

    return TestClient(app)


def _make_stored(memory_id="mem-1", **overrides):
    from lore.persistence.types import StoredMemory

    now = datetime.now(timezone.utc)
    defaults = dict(
        id=memory_id,
        org_id="org-001",
        content="narrative body",
        context="title",
        tags=["a"],
        source="observation",
        project=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={
            "type": "observation",
            "title": "title",
            "facts": ["fact-1"],
            "narrative": "narrative body",
            "captured_by": "auto",
        },
        access_count=0,
        last_accessed_at=None,
    )
    defaults.update(overrides)
    return StoredMemory(**defaults)


def test_route_post_creates_observation(routes_client, monkeypatch):
    stored = _make_stored(memory_id="mem-new")
    create_mock = AsyncMock(return_value=stored)
    monkeypatch.setattr(
        "lore.server.routes.observations._create_observation", create_mock
    )

    resp = routes_client.post(
        "/v1/observations",
        json={
            "title": "Phase 6A bootstrap quirk",
            "facts": ["fact one", "fact two"],
            "narrative": "Long-form prose explaining context.",
            "tags": ["phase-6a"],
            "project": "lore-tests",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"id": "mem-new"}

    # Service was called with a NewObservation carrying the right fields.
    call = create_mock.call_args
    obs = call.args[1]
    assert obs.title == "Phase 6A bootstrap quirk"
    assert tuple(obs.facts) == ("fact one", "fact two")
    assert obs.narrative == "Long-form prose explaining context."
    assert obs.project == "lore-tests"
    assert obs.captured_by == "auto"


def test_route_post_rejects_missing_title(routes_client):
    resp = routes_client.post(
        "/v1/observations",
        json={"facts": [], "narrative": "n"},
    )
    assert resp.status_code == 422


def test_route_post_rejects_invalid_captured_by(routes_client):
    resp = routes_client.post(
        "/v1/observations",
        json={
            "title": "t",
            "facts": [],
            "narrative": "n",
            "captured_by": "bogus",
        },
    )
    assert resp.status_code == 422


def test_route_get_list(routes_client, monkeypatch):
    rows = [_make_stored(memory_id=f"mem-{i}") for i in range(3)]
    monkeypatch.setattr(
        "lore.server.routes.observations._list_memories",
        AsyncMock(return_value=rows),
    )
    resp = routes_client.get("/v1/observations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert len(body["observations"]) == 3
    assert body["observations"][0]["title"] == "title"
    assert body["observations"][0]["facts"] == ["fact-1"]


def test_route_get_show(routes_client, monkeypatch):
    monkeypatch.setattr(
        "lore.server.routes.observations._get_memory",
        AsyncMock(return_value=_make_stored(memory_id="mem-show")),
    )
    resp = routes_client.get("/v1/observations/mem-show")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "mem-show"
    assert body["title"] == "title"
    assert body["facts"] == ["fact-1"]
    assert body["narrative"] == "narrative body"
    assert body["captured_by"] == "auto"


def test_route_get_show_404_when_wrong_type(routes_client, monkeypatch):
    """A polished memory whose meta.type != 'observation' must 404."""
    polished = _make_stored(
        memory_id="mem-lesson",
        meta={"type": "lesson", "narrative": "x", "title": "x", "facts": []},
    )
    monkeypatch.setattr(
        "lore.server.routes.observations._get_memory",
        AsyncMock(return_value=polished),
    )
    resp = routes_client.get("/v1/observations/mem-lesson")
    assert resp.status_code == 404


def test_route_get_show_404_when_missing(routes_client, monkeypatch):
    monkeypatch.setattr(
        "lore.server.routes.observations._get_memory",
        AsyncMock(return_value=None),
    )
    resp = routes_client.get("/v1/observations/missing")
    assert resp.status_code == 404


# ── MCP tool: remember_observation ─────────────────────────────────


def test_mcp_remember_observation_calls_post_v1_observations():
    """The MCP tool must POST /v1/observations via the lore HTTP client."""
    pytest.importorskip("mcp", reason="mcp not installed")
    from lore.mcp.server import remember_observation

    fake_resp = MagicMock()
    fake_resp.content = b'{"id": "obs-123"}'
    fake_resp.json.return_value = {"id": "obs-123"}

    fake_store = MagicMock()
    fake_store._request.return_value = fake_resp

    fake_lore = MagicMock()
    fake_lore._store = fake_store

    with patch("lore.mcp.server._get_lore", return_value=fake_lore):
        result = remember_observation(
            title="A title",
            facts=["f1", "f2"],
            narrative="The narrative.",
            tags=["tag-a"],
            project="p",
        )

    assert "obs-123" in result
    fake_store._request.assert_called_once()
    args, kwargs = fake_store._request.call_args
    assert args[0] == "POST"
    assert args[1] == "/v1/observations"
    body = kwargs["json"]
    assert body["title"] == "A title"
    assert body["facts"] == ["f1", "f2"]
    assert body["narrative"] == "The narrative."
    assert body["tags"] == ["tag-a"]
    assert body["project"] == "p"
    assert body["captured_by"] == "auto"


def test_mcp_remember_observation_omits_project_when_none():
    pytest.importorskip("mcp", reason="mcp not installed")
    from lore.mcp.server import remember_observation

    fake_resp = MagicMock()
    fake_resp.content = b'{"id": "obs-1"}'
    fake_resp.json.return_value = {"id": "obs-1"}
    fake_store = MagicMock()
    fake_store._request.return_value = fake_resp
    fake_lore = MagicMock()
    fake_lore._store = fake_store

    with patch("lore.mcp.server._get_lore", return_value=fake_lore):
        remember_observation(title="t", facts=[], narrative="n")

    body = fake_store._request.call_args.kwargs["json"]
    assert "project" not in body


def test_mcp_remember_observation_appears_in_tool_list():
    """Sanity: the MCP server must expose the tool by name."""
    pytest.importorskip("mcp", reason="mcp not installed")

    import asyncio

    from lore.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert "remember_observation" in names


# ── Auto-capture prompt update ─────────────────────────────────────


def test_capture_prompt_advertises_remember_observation():
    from lore.cli.commands import capture as cap

    prompt = cap._build_prompt(
        buffer_lines=['{"seq":1,"tool":"Edit"}'],
        transcript_tail="(none)",
        recent_titles=[],
    )
    assert "remember_observation" in prompt
    # remember(...) for polished memories still mentioned, but the
    # observation tool comes first as the preferred call.
    assert prompt.index("remember_observation") < prompt.index("mcp__lore__remember(")
    # New "be selective" guidance reflects the observation/memory split.
    assert "0-3 observations" in prompt or "0-3 observations OR" in prompt


# ── CLI: list / show round-trip ────────────────────────────────────


class _StubMemory:
    def __init__(self, memory_id, content, context, metadata, tags=None, project=None):
        self.id = memory_id
        self.content = content
        self.context = context
        self.metadata = metadata
        self.tags = tags or []
        self.project = project
        self.source = "observation"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at


class _StubLore:
    def __init__(self, observations):
        self._memories = observations

    def list_memories(self, type=None, project=None, limit=None):
        out = list(self._memories)
        if type is not None:
            out = [m for m in out if (m.metadata or {}).get("type") == type]
        if project is not None:
            out = [m for m in out if m.project == project]
        if limit:
            out = out[:limit]
        return out

    def get(self, memory_id):
        for m in self._memories:
            if m.id == memory_id:
                return m
        return None

    def close(self):
        pass


def test_cli_observations_list(monkeypatch, capsys):
    from lore.cli.commands import observations as obs_cmd

    obs1 = _StubMemory(
        memory_id="mem-1",
        content="narrative one",
        context="Title One",
        metadata={
            "type": "observation",
            "title": "Title One",
            "facts": ["first fact one"],
            "narrative": "narrative one",
        },
    )
    obs2 = _StubMemory(
        memory_id="mem-2",
        content="narrative two",
        context="Title Two",
        metadata={
            "type": "observation",
            "title": "Title Two",
            "facts": ["first fact two"],
            "narrative": "narrative two",
        },
    )

    monkeypatch.setattr(obs_cmd._helpers, "_get_lore", lambda db=None: _StubLore([obs1, obs2]))

    import argparse

    args = argparse.Namespace(db=None, limit=10, project=None, obs_command="list")
    obs_cmd.cmd_observations(args)
    out = capsys.readouterr().out
    assert "Title One" in out
    assert "Title Two" in out
    assert "first fact one" in out


def test_cli_observations_show(monkeypatch, capsys):
    import argparse

    from lore.cli.commands import observations as obs_cmd

    obs1 = _StubMemory(
        memory_id="mem-1",
        content="narrative one",
        context="Title One",
        metadata={
            "type": "observation",
            "title": "Title One",
            "facts": ["first fact"],
            "narrative": "narrative one",
            "captured_by": "auto",
        },
    )
    monkeypatch.setattr(obs_cmd._helpers, "_get_lore", lambda db=None: _StubLore([obs1]))

    args = argparse.Namespace(db=None, observation_id="mem-1", obs_command="show")
    obs_cmd.cmd_observations(args)
    out = capsys.readouterr().out
    import json as _json
    payload = _json.loads(out)
    assert payload["id"] == "mem-1"
    assert payload["title"] == "Title One"
    assert payload["facts"] == ["first fact"]
    assert payload["narrative"] == "narrative one"


def test_cli_observations_show_rejects_non_observation(monkeypatch, capsys):
    import argparse

    from lore.cli.commands import observations as obs_cmd

    polished = _StubMemory(
        memory_id="mem-x",
        content="x",
        context="x",
        metadata={"type": "lesson"},
    )
    monkeypatch.setattr(obs_cmd._helpers, "_get_lore", lambda db=None: _StubLore([polished]))

    args = argparse.Namespace(db=None, observation_id="mem-x", obs_command="show")
    with pytest.raises(SystemExit) as excinfo:
        obs_cmd.cmd_observations(args)
    assert excinfo.value.code == 1
