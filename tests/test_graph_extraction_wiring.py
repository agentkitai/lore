"""PR B route + CLI wiring tests for graph extraction.

  * ``POST /v1/memories`` and ``POST /v1/observations`` fire
    ``graph_extraction.extract_and_persist`` as a fire-and-forget task
    when the feature flag is on.
  * The new ``POST /v1/graph/backfill`` endpoint walks unenriched
    memories, calls extraction on each, and reports per-item counts.
  * ``force=true`` re-extracts memories that already have mentions.
  * Auth: reader gets 403, writer/admin succeeds.
  * Disabled flag returns ``enabled=false`` with empty results, no spawn.
  * The ``lore graph-backfill`` CLI calls the right HTTP endpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


# ── Helpers ────────────────────────────────────────────────────────


def _stored(memory_id="mem-1", content="Pinecone ships Nexus."):
    from lore.persistence.types import StoredMemory
    now = datetime.now(timezone.utc)
    return StoredMemory(
        id=memory_id, org_id="org-001", content=content, context=None,
        tags=(), confidence=0.5, source=None, project=None,
        created_at=now, updated_at=now, expires_at=None,
        upvotes=0, downvotes=0, meta={},
        importance_score=0.5, access_count=0, last_accessed_at=None,
    )


@pytest.fixture
def fake_store():
    s = MagicMock()
    s.insert_memory = AsyncMock(return_value=_stored())
    s.list_memories_without_mentions = AsyncMock(return_value=[])
    s.list = MagicMock()
    return s


@pytest.fixture
def auth_admin():
    from lore.server.auth import AuthContext
    return AuthContext(
        org_id="org-001", project=None, is_root=True,
        key_id="k1", role="admin",
    )


# ── Memory route fires graph extraction task ──────────────────────


def _build_memories_client(fake_store, auth):
    from lore.server.auth import get_auth_context
    from lore.server.routes.memories import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_auth_context] = lambda: auth

    async def fake_get_store():
        return fake_store

    embedder = MagicMock()
    embedder.embed.return_value = [0.0] * 384

    return app, fake_get_store, embedder


class TestMemoryRouteFiresGraphExtraction:
    def test_create_memory_fires_extraction_when_enabled(self, fake_store, auth_admin):
        app, fake_get_store, embedder = _build_memories_client(fake_store, auth_admin)

        with patch("lore.server.routes.memories.get_store", fake_get_store), \
             patch("lore.server.routes.memories.require_role",
                   return_value=lambda: auth_admin), \
             patch("lore.server.routes.retrieve._get_embedder",
                   return_value=embedder), \
             patch("lore.services.graph_extraction.is_enabled",
                   return_value=True), \
             patch("lore.server.routes.memories.asyncio") as mock_asyncio, \
             patch.dict("os.environ", {"LORE_ENRICHMENT_ENABLED": "false"}):
            mock_asyncio.to_thread = AsyncMock(return_value=[0.0] * 384)
            mock_asyncio.create_task = MagicMock()
            client = TestClient(app)
            resp = client.post("/v1/memories", json={"content": "x"})
        assert resp.status_code == 201
        # Exactly one create_task fired (the graph one; enrichment is off).
        assert mock_asyncio.create_task.call_count == 1

    def test_create_memory_skips_extraction_when_disabled(
        self, fake_store, auth_admin,
    ):
        app, fake_get_store, embedder = _build_memories_client(fake_store, auth_admin)

        with patch("lore.server.routes.memories.get_store", fake_get_store), \
             patch("lore.server.routes.memories.require_role",
                   return_value=lambda: auth_admin), \
             patch("lore.server.routes.retrieve._get_embedder",
                   return_value=embedder), \
             patch("lore.services.graph_extraction.is_enabled",
                   return_value=False), \
             patch("lore.server.routes.memories.asyncio") as mock_asyncio, \
             patch.dict("os.environ", {"LORE_ENRICHMENT_ENABLED": "false"}):
            mock_asyncio.to_thread = AsyncMock(return_value=[0.0] * 384)
            mock_asyncio.create_task = MagicMock()
            client = TestClient(app)
            resp = client.post("/v1/memories", json={"content": "x"})
        assert resp.status_code == 201
        # Neither enrichment nor extraction → no tasks fired.
        assert mock_asyncio.create_task.call_count == 0


# ── Observation route fires graph extraction task ─────────────────


class TestObservationRouteFiresGraphExtraction:
    def _build_client(self, fake_store, auth):
        from lore.server.auth import get_auth_context
        from lore.server.routes.observations import router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_auth_context] = lambda: auth

        async def fake_get_store():
            return fake_store

        embedder = MagicMock()
        embedder.embed.return_value = [0.0] * 384

        # services.observations.create_observation calls embed_fn; route
        # handler builds the embed_fn from the embedder. So we need the
        # embedder mocked, plus the store's insert_memory returning a
        # row.
        return app, fake_get_store, embedder

    def _post_observation(self, app, fake_get_store, fake_store, embedder, auth):
        from lore.server.db import get_store
        # Override the FastAPI dependency so the real get_store (which
        # checks an init flag) isn't reached.
        app.dependency_overrides[get_store] = lambda: fake_store
        with patch("lore.server.routes.observations.require_role",
                   return_value=lambda: auth), \
             patch("lore.server.routes.retrieve._get_embedder",
                   return_value=embedder), \
             patch("lore.server.routes.observations.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=[0.0] * 384)
            mock_asyncio.create_task = MagicMock()
            client = TestClient(app)
            resp = client.post("/v1/observations", json={
                "title": "Pinecone ships Nexus",
                "facts": ["Pinecone is a vector DB", "Nexus is the new product"],
                "narrative": "Detailed narrative",
                "tags": [],
                "captured_by": "manual",
            })
            return resp, mock_asyncio.create_task

    def test_observation_fires_extraction_when_enabled(self, fake_store, auth_admin):
        app, fake_get_store, embedder = self._build_client(fake_store, auth_admin)
        with patch("lore.services.graph_extraction.is_enabled",
                   return_value=True):
            resp, create_task = self._post_observation(
                app, fake_get_store, fake_store, embedder, auth_admin,
            )
        assert resp.status_code == 201
        assert create_task.call_count == 1

    def test_observation_skips_when_disabled(self, fake_store, auth_admin):
        app, fake_get_store, embedder = self._build_client(fake_store, auth_admin)
        with patch("lore.services.graph_extraction.is_enabled",
                   return_value=False):
            resp, create_task = self._post_observation(
                app, fake_get_store, fake_store, embedder, auth_admin,
            )
        assert resp.status_code == 201
        assert create_task.call_count == 0


# ── /v1/graph/backfill ─────────────────────────────────────────────


class TestBackfillRoute:
    def _client(self, fake_store, auth):
        from lore.server.auth import get_auth_context
        from lore.server.db import get_store
        from lore.server.routes.graph_backfill import router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_auth_context] = lambda: auth
        app.dependency_overrides[get_store] = lambda: fake_store
        return app

    def test_disabled_short_circuits(self, fake_store, auth_admin):
        app = self._client(fake_store, auth_admin)
        with patch("lore.server.routes.graph_backfill.require_role",
                   return_value=lambda: auth_admin), \
             patch("lore.server.routes.graph_backfill.graph_svc") as svc:
            svc.is_enabled.return_value = False
            client = TestClient(app)
            resp = client.post("/v1/graph/backfill", json={"limit": 10})
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "processed": 0, "failed": 0, "results": [], "enabled": False,
        }
        # No store calls, no extract calls.
        fake_store.list_memories_without_mentions.assert_not_called()
        svc.extract_and_persist.assert_not_called()

    def test_happy_path_processes_unenriched(self, fake_store, auth_admin):
        app = self._client(fake_store, auth_admin)
        mems = [_stored("mem-1"), _stored("mem-2")]
        fake_store.list_memories_without_mentions = AsyncMock(return_value=mems)

        from lore.services.graph_extraction import ExtractionResult

        async def fake_extract(*args, memory_id, **kwargs):
            return ExtractionResult(
                memory_id=memory_id,
                entities_inserted=2, entities_reused=0,
                mentions_inserted=2, relationships_inserted=1,
            )

        with patch("lore.server.routes.graph_backfill.require_role",
                   return_value=lambda: auth_admin), \
             patch("lore.server.routes.graph_backfill.graph_svc") as svc:
            svc.is_enabled.return_value = True
            svc.extract_and_persist = AsyncMock(side_effect=fake_extract)
            client = TestClient(app)
            resp = client.post("/v1/graph/backfill", json={"limit": 10})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["enabled"] is True
        assert body["processed"] == 2
        assert body["failed"] == 0
        assert len(body["results"]) == 2
        assert body["results"][0]["mentions_inserted"] == 2
        assert body["results"][0]["error"] is None

    def test_no_unenriched_memories_returns_empty(self, fake_store, auth_admin):
        app = self._client(fake_store, auth_admin)
        fake_store.list_memories_without_mentions = AsyncMock(return_value=[])

        with patch("lore.server.routes.graph_backfill.require_role",
                   return_value=lambda: auth_admin), \
             patch("lore.server.routes.graph_backfill.graph_svc") as svc:
            svc.is_enabled.return_value = True
            client = TestClient(app)
            resp = client.post("/v1/graph/backfill", json={"limit": 5})
        body = resp.json()
        assert body == {
            "processed": 0, "failed": 0, "results": [], "enabled": True,
        }
        svc.extract_and_persist.assert_not_called()

    def test_force_uses_list_memories_not_without_mentions(
        self, fake_store, auth_admin,
    ):
        """force=true should pull every memory, not just unenriched ones."""
        app = self._client(fake_store, auth_admin)
        mems = [_stored("mem-1")]

        from lore.services.graph_extraction import ExtractionResult

        async def fake_extract(*args, memory_id, **kwargs):
            return ExtractionResult(memory_id=memory_id)

        with patch("lore.server.routes.graph_backfill.require_role",
                   return_value=lambda: auth_admin), \
             patch("lore.server.routes.graph_backfill.graph_svc") as svc, \
             patch("lore.services.memories.list_memories",
                   AsyncMock(return_value=mems)) as mock_list:
            svc.is_enabled.return_value = True
            svc.extract_and_persist = AsyncMock(side_effect=fake_extract)
            client = TestClient(app)
            resp = client.post(
                "/v1/graph/backfill",
                json={"limit": 5, "force": True},
            )
        assert resp.status_code == 200
        # force=true → went through list_memories, not without_mentions.
        mock_list.assert_called_once()
        fake_store.list_memories_without_mentions.assert_not_called()

    def test_extraction_error_counts_as_failed(self, fake_store, auth_admin):
        """An extract result with .error set bumps failed count, not processed."""
        app = self._client(fake_store, auth_admin)
        fake_store.list_memories_without_mentions = AsyncMock(
            return_value=[_stored("mem-1"), _stored("mem-2")],
        )

        from lore.services.graph_extraction import ExtractionResult

        async def fake_extract(*args, memory_id, **kwargs):
            err = "subprocess timeout" if memory_id == "mem-2" else None
            return ExtractionResult(memory_id=memory_id, error=err)

        with patch("lore.server.routes.graph_backfill.require_role",
                   return_value=lambda: auth_admin), \
             patch("lore.server.routes.graph_backfill.graph_svc") as svc:
            svc.is_enabled.return_value = True
            svc.extract_and_persist = AsyncMock(side_effect=fake_extract)
            client = TestClient(app)
            resp = client.post("/v1/graph/backfill", json={"limit": 10})

        body = resp.json()
        assert body["processed"] == 1
        assert body["failed"] == 1
        errored = next(r for r in body["results"] if r["error"])
        assert errored["error"] == "subprocess timeout"


# ── CLI: lore graph-backfill ──────────────────────────────────────


class TestGraphBackfillCli:
    def test_cli_calls_http_endpoint(self, monkeypatch):
        """The CLI must POST /v1/graph/backfill with the limit + project body."""
        from lore.cli.commands.graph import cmd_graph_backfill

        captured = {"calls": []}

        class FakeResp:
            def __init__(self, body):
                self._body = body
                self.status_code = 200
                self.content = b"x"

            def json(self):
                return self._body

        def fake_request(method, path, **kwargs):
            captured["calls"].append((method, path, kwargs))
            # First page: 2 processed; second page: 0 processed → loop stops.
            n = len(captured["calls"])
            if n == 1:
                return FakeResp({"processed": 2, "failed": 0,
                                 "results": [], "enabled": True})
            return FakeResp({"processed": 0, "failed": 0,
                             "results": [], "enabled": True})

        from types import SimpleNamespace

        store = SimpleNamespace(_request=fake_request)
        fake_lore = SimpleNamespace(_store=store, close=lambda: None)

        with patch("lore.Lore", return_value=fake_lore):
            args = SimpleNamespace(project=None, limit=50)
            cmd_graph_backfill(args)

        # First call hits /v1/graph/backfill with limit 50.
        method, path, kwargs = captured["calls"][0]
        assert method == "POST"
        assert path == "/v1/graph/backfill"
        assert kwargs["json"]["limit"] == 50

    def test_cli_drains_pages_until_empty(self, monkeypatch):
        """Repeats the request while processed+failed > 0."""
        from lore.cli.commands.graph import cmd_graph_backfill

        page = {"n": 0}

        class FakeResp:
            def __init__(self, body):
                self._body = body
                self.status_code = 200
                self.content = b"x"

            def json(self):
                return self._body

        def fake_request(method, path, **kwargs):
            page["n"] += 1
            if page["n"] <= 3:
                return FakeResp({"processed": 5, "failed": 0,
                                 "results": [], "enabled": True})
            return FakeResp({"processed": 0, "failed": 0,
                             "results": [], "enabled": True})

        from types import SimpleNamespace

        store = SimpleNamespace(_request=fake_request)
        fake_lore = SimpleNamespace(_store=store, close=lambda: None)

        with patch("lore.Lore", return_value=fake_lore):
            cmd_graph_backfill(SimpleNamespace(project=None, limit=50))

        # 3 productive pages + 1 empty page → 4 calls.
        assert page["n"] == 4

    def test_cli_disabled_message(self, monkeypatch, capsys):
        """When server reports enabled=false, CLI prints a setup hint."""
        from lore.cli.commands.graph import cmd_graph_backfill

        class FakeResp:
            status_code = 200
            content = b"x"

            def json(self):
                return {"processed": 0, "failed": 0, "results": [],
                        "enabled": False}

        def fake_request(method, path, **kwargs):
            return FakeResp()

        from types import SimpleNamespace

        store = SimpleNamespace(_request=fake_request)
        fake_lore = SimpleNamespace(_store=store, close=lambda: None)

        with patch("lore.Lore", return_value=fake_lore):
            cmd_graph_backfill(SimpleNamespace(project=None, limit=50))

        err = capsys.readouterr().err
        assert "extraction is disabled" in err
