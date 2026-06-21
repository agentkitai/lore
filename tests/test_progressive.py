"""Tests for Phase 6D progressive disclosure.

Covers:
- ``GET /v1/search`` — compact-index endpoint shape, signals, auth.
- ``GET /v1/memories/details`` — full payload, errors array, 404 policy.
- ``memory_title()`` — deterministic title generation.
- MCP ``search()`` + ``get_memories()`` round-trip via fake HTTP store.
- Hook ``LORE_PROGRESSIVE=true`` branch — invokes search + details.
- Token budget — compact index ≥ 5× smaller than full payload.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from lore.persistence.types import StoredApiKey, StoredMemory  # noqa: E402
from lore.server._titles import memory_title  # noqa: E402
from lore.server.app import app  # noqa: E402
from lore.server.auth import _key_cache, _last_used_updates  # noqa: E402
from lore.server.middleware import RateLimiter, set_rate_limiter  # noqa: E402

# ── Test fixtures ──────────────────────────────────────────────────

RAW_KEY = "lore_sk_aaaabbbbccccddddeeeeffff00001111"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()
ORG_ID = "org-progressive"
HEADERS = {"Authorization": f"Bearer {RAW_KEY}"}

PROJECT_KEY = "lore_sk_project_aaaabbbbccccdddd99887766"
PROJECT_KEY_HASH = hashlib.sha256(PROJECT_KEY.encode()).hexdigest()
PROJECT_HEADERS = {"Authorization": f"Bearer {PROJECT_KEY}"}

NOW = datetime.now(timezone.utc)
SAMPLE_EMBEDDING = [0.1] * 384


def _stored_memory(
    memory_id: str = "mem-001",
    content: str = "User prefers dark mode",
    project: str | None = None,
    meta: dict | None = None,
    tags=("ui",),
):
    return StoredMemory(
        id=memory_id,
        org_id=ORG_ID,
        content=content,
        context=None,
        tags=tuple(tags),
        source="conversation",
        project=project,
        created_at=NOW,
        updated_at=NOW,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta=meta or {"type": "preference", "tier": "long"},
        access_count=0,
        last_accessed_at=None,
    )


def _stored_key(key_hash: str = KEY_HASH, project: str | None = None) -> StoredApiKey:
    return StoredApiKey(
        id="key-progressive",
        org_id=ORG_ID,
        name="test",
        key_hash=key_hash,
        key_prefix="lore_sk_xx",
        project=project,
        is_root=project is None,
        workspace_id=None,
        revoked_at=None,
        created_at=NOW,
        last_used_at=None,
        role=None,
    )


def _auth_store(key_row: StoredApiKey | None = None):
    s = AsyncMock()
    s.lookup_api_key_by_hash = AsyncMock(return_value=key_row or _stored_key())
    s.touch_api_key_last_used = AsyncMock(return_value=None)
    return s


@pytest_asyncio.fixture
async def client():
    _key_cache.clear()
    _last_used_updates.clear()
    set_rate_limiter(RateLimiter())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    _key_cache.clear()
    _last_used_updates.clear()


@pytest.fixture(autouse=True)
def mock_embedder():
    with patch("lore.server.routes.retrieve._get_embedder") as mock:
        embedder = MagicMock()
        embedder.embed.return_value = SAMPLE_EMBEDDING
        mock.return_value = embedder
        yield embedder


# ── memory_title() unit tests ──────────────────────────────────────


class TestMemoryTitle:
    def test_uses_meta_title(self):
        m = _stored_memory(content="long full content here", meta={"title": "Bug fix for 503s"})
        assert memory_title(m) == "Bug fix for 503s"

    def test_meta_title_truncates_at_80(self):
        long = "x" * 200
        m = _stored_memory(content="ignored", meta={"title": long})
        assert memory_title(m) == "x" * 80

    def test_falls_back_to_first_line(self):
        m = _stored_memory(content="First line as title\nSecond line ignored", meta={})
        assert memory_title(m) == "First line as title"

    def test_truncates_long_first_line_with_ellipsis(self):
        line = "y" * 200
        m = _stored_memory(content=line, meta={})
        title = memory_title(m)
        assert title == "y" * 80 + "…"
        assert len(title) == 81  # 80 chars + ellipsis

    def test_skips_blank_lines(self):
        m = _stored_memory(content="\n\n   \nReal title here", meta={})
        assert memory_title(m) == "Real title here"

    def test_empty_content_returns_untitled(self):
        m = _stored_memory(content="", meta={})
        assert memory_title(m) == "(untitled)"

    def test_whitespace_content_returns_untitled(self):
        m = _stored_memory(content="   \n  \t", meta={})
        assert memory_title(m) == "(untitled)"

    def test_deterministic(self):
        # Same input twice → same output.
        m = _stored_memory(content="The quick brown fox", meta={"title": "Fox fact"})
        assert memory_title(m) == memory_title(m) == "Fox fact"

    def test_meta_title_stripped(self):
        m = _stored_memory(content="ignored", meta={"title": "  spaced  "})
        assert memory_title(m) == "spaced"

    def test_blank_meta_title_falls_through(self):
        m = _stored_memory(content="real first line", meta={"title": "   "})
        assert memory_title(m) == "real first line"


# ── /v1/search HTTP tests ─────────────────────────────────────────


def _make_hybrid_results(memories_with_score):
    """Build HybridResult dataclasses for a fake hybrid_retrieve patch."""
    from lore.services.retrieve import HybridResult
    out = []
    for mem, score in memories_with_score:
        out.append(
            HybridResult(
                memory=mem,
                score=score,
                signals={
                    "vector": 0.9,
                    "fts": 0.5,
                    "graph": 0.0,
                    "recency": 1.0,
                },
            )
        )
    return out


@pytest.mark.asyncio
async def test_search_compact_shape(client):
    """`/v1/search` returns id/title/score/signals only — no content/tags/meta."""
    mems = [
        _stored_memory("mem-001", "User prefers dark mode", meta={"title": "dark mode pref"}),
        _stored_memory("mem-002", "Long\ncontent\nstarts here", meta={}),
    ]
    auth = _auth_store()

    async def _fake_get_store():
        return MagicMock()

    fake_results = _make_hybrid_results([(mems[0], 0.85), (mems[1], 0.65)])
    with patch("lore.server.routes.search.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth), \
         patch("lore.server.routes.search._hybrid_retrieve_service",
               new=AsyncMock(return_value=fake_results)):
        resp = await client.get("/v1/search", params={"query": "preferences"}, headers=HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    hits = data["hits"]
    # Compact shape: only id, title, score, signals.
    assert set(hits[0].keys()) == {"id", "title", "score", "signals"}
    assert hits[0]["title"] == "dark mode pref"
    assert hits[1]["title"] == "Long"  # first non-blank line
    assert all("vector" in h["signals"] for h in hits)
    assert all("fts" in h["signals"] for h in hits)


@pytest.mark.asyncio
async def test_search_signals_carry_through(client):
    """Per-signal breakdown round-trips into the response."""
    mems = [_stored_memory("mem-1")]
    fake_results = _make_hybrid_results([(mems[0], 0.5)])
    auth = _auth_store()

    async def _fake_get_store():
        return MagicMock()

    with patch("lore.server.routes.search.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth), \
         patch("lore.server.routes.search._hybrid_retrieve_service",
               new=AsyncMock(return_value=fake_results)):
        resp = await client.get("/v1/search", params={"query": "x"}, headers=HEADERS)

    assert resp.status_code == 200
    sigs = resp.json()["hits"][0]["signals"]
    for k in ("vector", "fts", "graph", "recency"):
        assert k in sigs


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    """No auth header → 401."""
    resp = await client.get("/v1/search", params={"query": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_missing_query(client):
    """Missing query is 422."""
    auth = _auth_store()
    with patch("lore.server.auth.get_store", return_value=auth):
        resp = await client.get("/v1/search", headers=HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_empty_results(client):
    """No matches → empty hits list, count=0."""
    auth = _auth_store()

    async def _fake_get_store():
        return MagicMock()

    with patch("lore.server.routes.search.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth), \
         patch("lore.server.routes.search._hybrid_retrieve_service",
               new=AsyncMock(return_value=[])):
        resp = await client.get("/v1/search", params={"query": "nothing"}, headers=HEADERS)

    assert resp.status_code == 200
    assert resp.json() == {"hits": [], "count": 0}


# ── /v1/memories/details HTTP tests ───────────────────────────────


def _fake_store_with_memories(mems_by_id: dict):
    """Build a store mock keyed by memory id (returns None for misses)."""
    s = MagicMock()

    async def _get_memory(org_id, memory_id, *, requesting_user_id=None):
        return mems_by_id.get(memory_id)

    s.get_memory = AsyncMock(side_effect=_get_memory)
    return s


@pytest.mark.asyncio
async def test_details_full_payload(client):
    """Full StoredMemory fields come back."""
    mems = {
        "mem-001": _stored_memory("mem-001", "Full content one", tags=("a", "b")),
        "mem-002": _stored_memory("mem-002", "Full content two"),
    }
    fake = _fake_store_with_memories(mems)
    auth = _auth_store()

    async def _fake_get_store():
        return fake

    with patch("lore.server.routes.memories.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth):
        resp = await client.get(
            "/v1/memories/details",
            params={"ids": "mem-001,mem-002"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["errors"] == []
    contents = {m["content"] for m in data["memories"]}
    assert contents == {"Full content one", "Full content two"}
    # Verify full-payload fields present.
    first = data["memories"][0]
    assert "tags" in first
    assert "meta" in first
    assert "created_at" in first


@pytest.mark.asyncio
async def test_details_partial_missing_returns_errors(client):
    """Some IDs missing → 200 with errors[] listing them."""
    mems = {"mem-001": _stored_memory("mem-001", "exists")}
    fake = _fake_store_with_memories(mems)
    auth = _auth_store()

    async def _fake_get_store():
        return fake

    with patch("lore.server.routes.memories.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth):
        resp = await client.get(
            "/v1/memories/details",
            params={"ids": "mem-001,mem-missing,mem-also-missing"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert set(data["errors"]) == {"mem-missing", "mem-also-missing"}


@pytest.mark.asyncio
async def test_details_all_missing_404(client):
    """If every requested ID is missing, return 404 (don't leak emptiness)."""
    fake = _fake_store_with_memories({})
    auth = _auth_store()

    async def _fake_get_store():
        return fake

    with patch("lore.server.routes.memories.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth):
        resp = await client.get(
            "/v1/memories/details",
            params={"ids": "mem-1,mem-2,mem-3"},
            headers=HEADERS,
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_details_unauthorized_project_treated_as_missing(client):
    """Project-scoped key mustn't disclose memories from another project."""
    # Auth: scoped to project "frontend".
    project_key = _stored_key(key_hash=PROJECT_KEY_HASH, project="frontend")
    mems = {
        "mem-frontend": _stored_memory("mem-frontend", "frontend stuff", project="frontend"),
        "mem-backend": _stored_memory("mem-backend", "backend stuff", project="backend"),
    }
    fake = _fake_store_with_memories(mems)
    auth = _auth_store(project_key)

    async def _fake_get_store():
        return fake

    with patch("lore.server.routes.memories.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth):
        resp = await client.get(
            "/v1/memories/details",
            params={"ids": "mem-frontend,mem-backend"},
            headers=PROJECT_HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["memories"][0]["id"] == "mem-frontend"
    assert "mem-backend" in data["errors"]


@pytest.mark.asyncio
async def test_details_too_many_ids(client):
    """>10 ids → 422 (cap to keep round-trip cheap)."""
    auth = _auth_store()

    async def _fake_get_store():
        return _fake_store_with_memories({})

    ids = ",".join(f"mem-{i}" for i in range(11))
    with patch("lore.server.routes.memories.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth):
        resp = await client.get(
            "/v1/memories/details",
            params={"ids": ids},
            headers=HEADERS,
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_details_requires_auth(client):
    """No auth → 401."""
    resp = await client.get("/v1/memories/details", params={"ids": "mem-1"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_details_blank_ids_422(client):
    """All-blank ids string → 422."""
    auth = _auth_store()

    async def _fake_get_store():
        return _fake_store_with_memories({})

    with patch("lore.server.routes.memories.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth):
        resp = await client.get(
            "/v1/memories/details",
            params={"ids": " , , "},
            headers=HEADERS,
        )
    assert resp.status_code == 422


# ── MCP search() / get_memories() round-trip ──────────────────────


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.content = json.dumps(payload).encode()
        self._payload = payload

    def json(self):
        return self._payload


def _patch_lore_with_fake_request(request_fn):
    """Patch _get_lore() to return an object whose _store._request is request_fn."""
    fake_store = SimpleNamespace(_request=request_fn)
    fake_lore = SimpleNamespace(_store=fake_store)
    return patch("lore.mcp.server._get_lore", return_value=fake_lore)


def test_mcp_search_returns_compact_json():
    from lore.mcp import server as mcp_server

    captured = {}

    def fake_request(method, path, params=None, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params
        return _FakeResp(200, {
            "hits": [
                {"id": "mem-1", "title": "First", "score": 0.9, "signals": {"vector": 0.9}},
                {"id": "mem-2", "title": "Second", "score": 0.8, "signals": {"vector": 0.7}},
            ],
            "count": 2,
        })

    fn = mcp_server.search  # @mcp.tool() leaves the function callable
    with _patch_lore_with_fake_request(fake_request):
        out = fn(query="dark mode", limit=20, min_score=0.3)

    assert captured["path"] == "/v1/search"
    assert captured["method"] == "GET"
    assert captured["params"]["query"] == "dark mode"
    parsed = json.loads(out)
    assert parsed["count"] == 2
    assert parsed["hits"][0]["id"] == "mem-1"


def test_mcp_get_memories_returns_full_payload():
    from lore.mcp import server as mcp_server

    captured = {}

    def fake_request(method, path, params=None, **kwargs):
        captured["path"] = path
        captured["params"] = params
        return _FakeResp(200, {
            "memories": [{"id": "mem-1", "content": "full body", "tags": ["x"]}],
            "total": 1,
            "errors": [],
            "limit": 1,
            "offset": 0,
        })

    fn = mcp_server.get_memories
    with _patch_lore_with_fake_request(fake_request):
        out = fn(ids=["mem-1"])

    assert captured["path"] == "/v1/memories/details"
    assert captured["params"]["ids"] == "mem-1"
    parsed = json.loads(out)
    assert parsed["total"] == 1
    assert parsed["memories"][0]["content"] == "full body"


def test_mcp_round_trip_search_then_details():
    """search() result feeds into get_memories() — proves the disclosure flow."""
    from lore.mcp import server as mcp_server

    calls: list = []

    def fake_request(method, path, params=None, **kwargs):
        calls.append((path, params))
        if path == "/v1/search":
            return _FakeResp(200, {
                "hits": [
                    {"id": "mem-a", "title": "A", "score": 0.9, "signals": {}},
                    {"id": "mem-b", "title": "B", "score": 0.8, "signals": {}},
                ],
                "count": 2,
            })
        if path == "/v1/memories/details":
            return _FakeResp(200, {
                "memories": [
                    {"id": "mem-a", "content": "alpha"},
                    {"id": "mem-b", "content": "beta"},
                ],
                "total": 2,
                "errors": [],
                "limit": 2,
                "offset": 0,
            })
        return _FakeResp(404, {})

    with _patch_lore_with_fake_request(fake_request):
        index = json.loads(mcp_server.search(query="x"))
        ids = [h["id"] for h in index["hits"]]
        full = json.loads(mcp_server.get_memories(ids=ids))

    assert [c[0] for c in calls] == ["/v1/search", "/v1/memories/details"]
    assert {m["id"] for m in full["memories"]} == {"mem-a", "mem-b"}


def test_mcp_get_memories_404_handled():
    from lore.mcp import server as mcp_server

    def fake_request(method, path, params=None, **kwargs):
        return _FakeResp(404, {"detail": "not found"})

    with _patch_lore_with_fake_request(fake_request):
        out = mcp_server.get_memories(ids=["bogus"])

    assert "No memories" in out


# ── Hook progressive branch tests ─────────────────────────────────


class _FakeUrlResponse:
    def __init__(self, payload: dict):
        self._buf = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._buf


def test_hook_progressive_renders_two_phase_calls(tmp_path):
    """LORE_PROGRESSIVE=true makes the hook call /v1/search then /v1/memories/details."""
    import os
    import sys
    import types

    from lore.setup import CLAUDE_CODE_HOOK_SCRIPT

    rendered = CLAUDE_CODE_HOOK_SCRIPT.format(
        server_url="http://test-server",
        api_key="lore_sk_test",
    )

    hook_path = tmp_path / "hook_module.py"
    hook_path.write_text(rendered)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lore_hook_under_test", str(hook_path)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lore_hook_under_test"] = mod
    spec.loader.exec_module(mod)

    paths_called: list = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        paths_called.append(url)
        if "/v1/search" in url:
            return _FakeUrlResponse({
                "hits": [
                    {"id": "mem-x", "title": "X", "score": 0.9, "signals": {}},
                    {"id": "mem-y", "title": "Y", "score": 0.8, "signals": {}},
                ],
                "count": 2,
            })
        if "/v1/memories/details" in url:
            return _FakeUrlResponse({
                "memories": [
                    {"id": "mem-x", "content": "xxx", "score": 0.9},
                    {"id": "mem-y", "content": "yyy", "score": 0.8},
                ],
                "total": 2,
                "errors": [],
                "limit": 2,
                "offset": 0,
            })
        return _FakeUrlResponse({})

    inp = {"prompt": "this is a long enough prompt for the hook to fire"}

    captured_stdout = []

    class _CapStdout:
        def write(self, s):
            captured_stdout.append(s)

        def flush(self):
            pass

    old_environ = dict(os.environ)
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    try:
        os.environ["LORE_PROGRESSIVE"] = "true"
        os.environ["LORE_MIN_SCORE"] = "0.0"
        sys.stdin = types.SimpleNamespace(read=lambda: json.dumps(inp))
        # _read_input does json.load(sys.stdin), which expects a file-like.
        import io
        sys.stdin = io.StringIO(json.dumps(inp))
        sys.stdout = _CapStdout()
        with patch.object(mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            mod.main()
    finally:
        os.environ.clear()
        os.environ.update(old_environ)
        sys.stdin = old_stdin
        sys.stdout = old_stdout

    # Both endpoints must have been hit, in order.
    assert any("/v1/search" in u for u in paths_called)
    assert any("/v1/memories/details" in u for u in paths_called)
    search_idx = next(i for i, u in enumerate(paths_called) if "/v1/search" in u)
    detail_idx = next(i for i, u in enumerate(paths_called) if "/v1/memories/details" in u)
    assert search_idx < detail_idx


def test_hook_default_uses_classic_retrieve(tmp_path):
    """Without LORE_PROGRESSIVE, the hook still calls /v1/retrieve."""
    import os
    import sys

    from lore.setup import CLAUDE_CODE_HOOK_SCRIPT

    rendered = CLAUDE_CODE_HOOK_SCRIPT.format(
        server_url="http://test-server",
        api_key="lore_sk_test",
    )
    hook_path = tmp_path / "hook_classic.py"
    hook_path.write_text(rendered)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lore_hook_classic", str(hook_path)
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lore_hook_classic"] = mod
    spec.loader.exec_module(mod)

    paths_called: list = []

    def fake_urlopen(req, timeout=None):
        paths_called.append(req.full_url)
        return _FakeUrlResponse({
            "memories": [{"id": "m1", "content": "x", "score": 0.9}],
            "formatted": "## Relevant Memories\n- **[0.90]** x",
            "count": 1,
            "query_time_ms": 1,
        })

    inp = {"prompt": "this is a long enough prompt for the hook to fire"}

    old_environ = dict(os.environ)
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    try:
        os.environ.pop("LORE_PROGRESSIVE", None)
        os.environ["LORE_MIN_SCORE"] = "0.0"
        import io
        sys.stdin = io.StringIO(json.dumps(inp))

        class _CapStdout:
            def write(self, s):
                pass

            def flush(self):
                pass

        sys.stdout = _CapStdout()
        with patch.object(mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            mod.main()
    finally:
        os.environ.clear()
        os.environ.update(old_environ)
        sys.stdin = old_stdin
        sys.stdout = old_stdout

    assert any("/v1/retrieve" in u for u in paths_called)
    assert not any("/v1/search" in u for u in paths_called)


# ── Token-budget reality check ────────────────────────────────────


def test_token_budget_compact_index_is_5x_smaller_than_full_payload():
    """Compact index payload size ≥ 5× smaller than full payload on a fixture.

    Phase 6D's stated win: ~50 tokens/result for the compact index, ~300 for
    the full payload (~6× ratio). This test verifies the byte-size ratio
    holds on a representative fixture; bytes are a fine proxy for tokens
    since both encodings are JSON-as-UTF-8.
    """
    # Realistic-sized memories: 5 entries with non-trivial content / tags / meta.
    # Real Lore memories average 1–2k chars (debug logs, observations,
    # conversation extracts); the spec budget assumes ~300 tokens / ~1.2 KB
    # per full payload, ~50 tokens / 200 bytes per index row.
    body = (
        "Detailed note about a debugging session investigating intermittent "
        "503 errors during peak traffic on the API gateway. Root cause was "
        "connection-pool exhaustion when a downstream auth service slowed "
        "to ~800ms p99 under load. Fix: bumped HTTPX pool size from 20 to "
        "100, introduced a circuit breaker with a 5s open window, and "
        "added a saturation alert at 80% pool utilization. Verified with "
        "a 60-rps load test holding p99 under 250ms across 10 minutes. "
        "Followups: revisit pool size after the auth team rolls out their "
        "caching layer; consider migrating to httpx async client pooling "
        "with HTTP/2 multiplexing to reduce per-call overhead further."
    )
    enrichment = {
        "topics": ["infrastructure", "debugging", "performance"],
        "entities": [
            {"name": "API gateway", "type": "service"},
            {"name": "auth service", "type": "service"},
            {"name": "HTTPX", "type": "library"},
        ],
        "sentiment": {"label": "neutral", "score": 0.1},
        "categories": ["incident-response", "scaling"],
    }
    memories = [
        _stored_memory(
            f"mem-{i:03d}",
            content=body,
            tags=("debugging", "infra", "lesson", "p99", f"tag-{i}"),
            meta={
                "title": f"Debug session {i}: 503s on API gateway",
                "type": "lesson",
                "tier": "long",
                "enrichment": enrichment,
            },
        )
        for i in range(5)
    ]

    # Compact-index payload — what /v1/search emits.
    compact = {
        "hits": [
            {
                "id": m.id,
                "title": memory_title(m),
                "score": 0.85,
                "signals": {"vector": 0.9, "fts": 0.5, "graph": 0.0,
                            "recency": 1.0},
            }
            for m in memories
        ],
        "count": len(memories),
    }

    # Full-payload — what /v1/memories/details (or /v1/retrieve) emits.
    full = {
        "memories": [
            {
                "id": m.id,
                "content": m.content,
                "context": m.context,
                "tags": list(m.tags),
                "source": m.source,
                "project": m.project,
                "created_at": m.created_at.isoformat(),
                "updated_at": m.updated_at.isoformat(),
                "expires_at": None,
                "upvotes": m.upvotes,
                "downvotes": m.downvotes,
                "meta": dict(m.meta),
            }
            for m in memories
        ],
        "total": len(memories),
        "errors": [],
        "limit": len(memories),
        "offset": 0,
    }

    compact_bytes = len(json.dumps(compact))
    full_bytes = len(json.dumps(full))
    ratio = full_bytes / compact_bytes
    assert ratio >= 5.0, (
        f"Expected full/compact ratio >= 5x, got {ratio:.2f} "
        f"(compact={compact_bytes}b, full={full_bytes}b)"
    )
