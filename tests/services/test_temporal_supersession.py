"""Phase 6F temporal-reasoning tests (memory supersession + at-time).

Covers:

  * Persistence-layer round trips for the new SupersessionOps slice
    (PG + SQLite via the parametrized ``store`` fixture).
  * Service layer (``lore.services.temporal``).
  * Hybrid-recall integration: superseded memories drop in score by 10x.
  * HTTP routes (``/v1/memories/{id}/supersede`` etc.).
  * Prompt fragments in capture + dream.

The heavy SqliteStore-touching paths gate on the ``[solo]`` extras the
same way ``test_dreams.py`` and ``test_sqlite_smoke.py`` do, so the
``python`` CI job (no extras) stays green.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

# Optional [solo] deps for the SQLite branch of the parametrized store fixture.
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")


from lore.persistence import Store, StoredMemory
from lore.persistence.types import NewMemory, StoredSupersession
from lore.services import temporal as temporal_svc
from lore.services.retrieve import (
    _DEFAULT_HYBRID_PROFILE,
    HybridParams,
    _hybrid_recall,
)

# The parametrized ``store`` fixture comes from tests/services/conftest.py
# which re-exports tests/persistence/conftest.py's PG+SQLite parametrization.


# ── helpers ─────────────────────────────────────────────────────────


async def _insert_memory(store: Store, *, org_id: str = "solo", content: str = "x") -> str:
    """Insert a minimal memory and return its id."""
    stored = await store.insert_memory(
        NewMemory(org_id=org_id, content=content, embedding=[0.1] * 384)
    )
    return stored.id


def _make_stored(mid: str, content: str) -> StoredMemory:
    now = datetime.now(timezone.utc)
    return StoredMemory(
        id=mid, org_id="solo", content=content, context=None, tags=(),
        confidence=0.5, source=None, project=None, created_at=now,
        updated_at=now, expires_at=None, upvotes=0, downvotes=0,
        meta={}, importance_score=0.5, access_count=0, last_accessed_at=None,
    )


# ── 1. record_supersession + is_superseded round trip ───────────────


@pytest.mark.asyncio
async def test_record_supersession_basic(store: Store):
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")

    assert await store.is_superseded(a) is False
    await store.record_supersession(a, superseded_by=b, reason="newer")
    assert await store.is_superseded(a) is True
    assert await store.is_superseded(b) is False


@pytest.mark.asyncio
async def test_un_supersede_flips_state(store: Store):
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    await store.record_supersession(a, superseded_by=b, reason="newer")
    assert await store.is_superseded(a) is True

    # Explicit un-supersession: superseded_by=None on a fresh row.
    await store.record_supersession(a, superseded_by=None, reason="rolled back")
    assert await store.is_superseded(a) is False


@pytest.mark.asyncio
async def test_re_supersession_appends(store: Store):
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    c = await _insert_memory(store, content="c")
    await store.record_supersession(a, superseded_by=b, reason="r1")
    await store.record_supersession(a, superseded_by=c, reason="r2")
    chain = await store.get_supersession_chain(a)
    assert len(chain) == 2
    assert chain[0].superseded_by == b
    assert chain[1].superseded_by == c
    # Latest wins.
    assert await store.is_superseded(a) is True


@pytest.mark.asyncio
async def test_is_superseded_at_in_past_ignores_future_events(store: Store):
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    # Record supersession NOW.
    await store.record_supersession(a, superseded_by=b, reason="r")
    # Querying with at = (1 hour ago) ignores the just-recorded event.
    in_past = datetime.now(timezone.utc) - timedelta(hours=1)
    assert await store.is_superseded(a, at=in_past) is False
    # No filter (= now) sees it.
    assert await store.is_superseded(a) is True


# ── 2. are_superseded batch ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_are_superseded_batch(store: Store):
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    c = await _insert_memory(store, content="c")
    d = await _insert_memory(store, content="d")
    # a is superseded by b; c is un-superseded; d is plain.
    await store.record_supersession(a, superseded_by=b, reason="x")
    await store.record_supersession(c, superseded_by=d, reason="y")
    await store.record_supersession(c, superseded_by=None, reason="undo")
    result = await store.are_superseded({a, b, c, d, "missing"})
    assert result == {a}


@pytest.mark.asyncio
async def test_are_superseded_empty_input(store: Store):
    result = await store.are_superseded(set())
    assert result == set()


# ── 3. get_supersession_chain ───────────────────────────────────────


@pytest.mark.asyncio
async def test_get_supersession_chain_orders_oldest_first(store: Store):
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    c = await _insert_memory(store, content="c")
    await store.record_supersession(a, superseded_by=b, reason="r1")
    await store.record_supersession(a, superseded_by=c, reason="r2")
    chain = await store.get_supersession_chain(a)
    reasons = [evt.reason for evt in chain]
    assert reasons == ["r1", "r2"]
    assert all(isinstance(e, StoredSupersession) for e in chain)


@pytest.mark.asyncio
async def test_get_supersession_chain_missing_returns_empty(store: Store):
    chain = await store.get_supersession_chain("nonexistent")
    assert list(chain) == []


# ── 3b. list_supersession_sources (Phase audit-fix: provenance read) ──


@pytest.mark.asyncio
async def test_list_supersession_sources_returns_inverse(store: Store):
    """Sources are events where this memory id appears as superseded_by."""
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    new = await _insert_memory(store, content="merged")
    await store.record_supersession(a, superseded_by=new, reason="merge a")
    await store.record_supersession(b, superseded_by=new, reason="merge b")

    sources = await store.list_supersession_sources(new)
    src_ids = {e.memory_id for e in sources}
    assert src_ids == {a, b}
    reasons = {e.reason for e in sources}
    assert reasons == {"merge a", "merge b"}


@pytest.mark.asyncio
async def test_list_supersession_sources_ignores_un_supersession(store: Store):
    """Rows with superseded_by=NULL aren't sources of anything."""
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    await store.record_supersession(a, superseded_by=None, reason="undo")
    sources = await store.list_supersession_sources(b)
    assert list(sources) == []


@pytest.mark.asyncio
async def test_list_supersession_sources_missing_returns_empty(store: Store):
    sources = await store.list_supersession_sources("nonexistent")
    assert list(sources) == []


# ── 3c. consolidate_memories service helper ────────────────────────


@pytest.mark.asyncio
async def test_service_consolidate_memories_records_each_source(store: Store):
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    new = await _insert_memory(store, content="merged")
    n = await temporal_svc.consolidate_memories(
        store,
        org_id="solo",
        source_ids=[a, b],
        new_memory_id=new,
        reason="merged",
        agent="test",
    )
    assert n == 2
    sources = await store.list_supersession_sources(new)
    assert {e.memory_id for e in sources} == {a, b}
    assert all(e.agent == "test" for e in sources)


@pytest.mark.asyncio
async def test_service_consolidate_memories_skips_blank_ids(store: Store):
    new = await _insert_memory(store, content="merged")
    n = await temporal_svc.consolidate_memories(
        store,
        org_id="solo",
        source_ids=["", None],  # type: ignore[list-item]
        new_memory_id=new,
        reason="x",
    )
    assert n == 0


# ── 4. list_memories_at_time ────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_memories_at_time_excludes_already_superseded(store: Store):
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    await store.record_supersession(a, superseded_by=b, reason="x")
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    rows = await store.list_memories_at_time("solo", at=future)
    ids = {r.id for r in rows}
    assert a not in ids
    assert b in ids


@pytest.mark.asyncio
async def test_list_memories_at_time_far_future_includes_unsuperseded(store: Store):
    """Far-future ``at`` returns all non-superseded memories."""
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    far_future = datetime.now(timezone.utc) + timedelta(days=365)
    rows = await store.list_memories_at_time("solo", at=far_future)
    ids = {r.id for r in rows}
    # Both unsuperseded.
    assert {a, b}.issubset(ids)


@pytest.mark.asyncio
async def test_list_memories_at_time_with_type_filter(store: Store):
    """``type_filter`` matches ``meta->>'type'``."""
    a_id = (await store.insert_memory(NewMemory(
        org_id="solo", content="obs", embedding=[0.1] * 384,
        meta={"type": "observation"},
    ))).id
    b_id = (await store.insert_memory(NewMemory(
        org_id="solo", content="lesson", embedding=[0.1] * 384,
        meta={"type": "lesson"},
    ))).id
    far_future = datetime.now(timezone.utc) + timedelta(days=365)
    rows = await store.list_memories_at_time(
        "solo", at=far_future, type_filter="observation",
    )
    ids = {r.id for r in rows}
    assert a_id in ids
    assert b_id not in ids


# ── 5. service-layer wrappers ───────────────────────────────────────


@pytest.mark.asyncio
async def test_service_supersede_memory_calls_store(store: Store):
    a = await _insert_memory(store, content="a")
    b = await _insert_memory(store, content="b")
    await temporal_svc.supersede_memory(
        store, a, superseded_by=b, reason="r", agent="test",
    )
    chain = await store.get_supersession_chain(a)
    assert len(chain) == 1
    assert chain[0].agent == "test"


@pytest.mark.asyncio
async def test_service_memories_at_time_normalizes_naive_datetime(store: Store):
    a = await _insert_memory(store, content="a")
    naive = (datetime.now(timezone.utc) + timedelta(minutes=1)).replace(tzinfo=None)
    rows = await temporal_svc.memories_at_time(store, "solo", at=naive)
    assert any(r.id == a for r in rows)


# ── 6. Hybrid recall integration: score multiplier ──────────────────


class _FakeStore:
    """Minimal store that returns canned candidate lists for hybrid recall.

    We bypass the real PostgresStore wiring so we can pin every signal —
    only the supersession multiplier path is under test here.
    """

    def __init__(self, memories: list[StoredMemory], superseded_ids: set[str]):
        self._memories = memories
        self._superseded = set(superseded_ids)

    async def recall_by_embedding(self, params):
        from lore.persistence.types import ScoredMemory
        out = []
        for m in self._memories:
            out.append(ScoredMemory(
                id=m.id, org_id=m.org_id, content=m.content, context=m.context,
                tags=m.tags, confidence=m.confidence, source=m.source,
                project=m.project, created_at=m.created_at, updated_at=m.updated_at,
                expires_at=m.expires_at, upvotes=m.upvotes, downvotes=m.downvotes,
                meta=m.meta, importance_score=m.importance_score,
                access_count=m.access_count, last_accessed_at=m.last_accessed_at,
                score=1.0,
            ))
        return out

    async def recall_by_text(self, *args, **kwargs):
        return []

    async def recall_by_entities(self, *args, **kwargs):
        return []

    async def get_entity_by_name(self, *args, **kwargs):
        return None

    async def are_superseded(self, ids, *, at=None):
        return self._superseded & set(ids)


@pytest.mark.asyncio
async def test_hybrid_recall_multiplies_superseded_by_0_1():
    """A superseded candidate's final score must be ≤ 0.1× a non-superseded one."""
    fresh = _make_stored("m-fresh", "fresh")
    stale = _make_stored("m-stale", "stale")
    # Lower min_score so both rows survive thresholding.
    from lore.persistence import ResolvedProfile
    profile = ResolvedProfile(
        name=_DEFAULT_HYBRID_PROFILE.name,
        source=_DEFAULT_HYBRID_PROFILE.source,
        semantic_weight=_DEFAULT_HYBRID_PROFILE.semantic_weight,
        graph_weight=_DEFAULT_HYBRID_PROFILE.graph_weight,
        recency_bias=_DEFAULT_HYBRID_PROFILE.recency_bias,
        min_score=0.0,
        max_results=_DEFAULT_HYBRID_PROFILE.max_results,
        tier_filters=_DEFAULT_HYBRID_PROFILE.tier_filters,
        k=_DEFAULT_HYBRID_PROFILE.k,
        threshold=_DEFAULT_HYBRID_PROFILE.threshold,
        rerank=_DEFAULT_HYBRID_PROFILE.rerank,
        include_graph=_DEFAULT_HYBRID_PROFILE.include_graph,
        fts_weight=_DEFAULT_HYBRID_PROFILE.fts_weight,
    )
    fake = _FakeStore([fresh, stale], superseded_ids={"m-stale"})

    report = await _hybrid_recall(
        fake,
        profile,
        HybridParams(
            org_id="solo",
            query_text="anything",
            query_vec=[0.1] * 384,
            limit=10,
            half_life_days=30,
        ),
    )
    results = report.results
    by_id = {r.memory.id: r for r in results}
    assert "m-fresh" in by_id
    assert "m-stale" in by_id
    fresh_score = by_id["m-fresh"].score
    stale_score = by_id["m-stale"].score
    assert stale_score <= 0.1 * fresh_score + 1e-9
    assert by_id["m-stale"].signals.get("superseded") == 1.0
    assert by_id["m-fresh"].signals.get("superseded") == 0.0


@pytest.mark.asyncio
async def test_hybrid_recall_skips_are_superseded_when_no_candidates():
    """Empty fan-out — no round trip to are_superseded."""
    fake = _FakeStore([], superseded_ids=set())
    fake.are_superseded = AsyncMock(side_effect=AssertionError("should not be called"))
    profile = _DEFAULT_HYBRID_PROFILE
    report = await _hybrid_recall(
        fake,
        profile,
        HybridParams(
            org_id="solo",
            query_text="anything",
            query_vec=[0.1] * 384,
            limit=5,
            half_life_days=30,
        ),
    )
    assert list(report.results) == []
    fake.are_superseded.assert_not_awaited()


# ── 7. HTTP routes ──────────────────────────────────────────────────


@pytest.fixture
def http_client(monkeypatch):
    """Build a FastAPI TestClient with the temporal router + a fake store."""
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lore.server.auth import AuthContext, get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.temporal import router

    auth = AuthContext(
        org_id="solo",
        project=None,
        is_root=True,
        key_id="k1",
        role="admin",
    )

    class _Store:
        def __init__(self):
            self.memories: dict[str, StoredMemory] = {
                "m-old": _make_stored("m-old", "old fact"),
                "m-new": _make_stored("m-new", "new fact"),
            }
            self.events: list = []
            self._next_id = 0

        async def get_memory(self, org_id, mid):
            m = self.memories.get(mid)
            if m and m.org_id == org_id:
                return m
            return None

        async def record_supersession(self, mid, *, superseded_by, reason, agent="auto"):
            self.events.append((mid, superseded_by, reason, agent))

        async def list_memories_at_time(self, org_id, *, at, entity_name=None,
                                        type_filter=None, limit=20):
            return list(self.memories.values())[:limit]

        async def get_supersession_chain(self, mid):
            return [
                StoredSupersession(
                    id=i,
                    memory_id=mid,
                    superseded_by=evt[1],
                    reason=evt[2],
                    ts=datetime.now(timezone.utc),
                    agent=evt[3],
                )
                for i, evt in enumerate(self.events) if evt[0] == mid
            ]

        async def list_supersession_sources(self, mid):
            return [
                StoredSupersession(
                    id=i,
                    memory_id=evt[0],
                    superseded_by=evt[1],
                    reason=evt[2],
                    ts=datetime.now(timezone.utc),
                    agent=evt[3],
                )
                for i, evt in enumerate(self.events) if evt[1] == mid
            ]

        async def insert_memory(self, nm):
            # Mimic just enough of the real Store contract for the
            # consolidate route to work end-to-end.
            self._next_id += 1
            new_id = f"m-merge-{self._next_id}"
            stored = StoredMemory(
                id=new_id, org_id=nm.org_id, content=nm.content,
                context=getattr(nm, "context", None),
                tags=tuple(getattr(nm, "tags", ())),
                confidence=getattr(nm, "confidence", 0.5),
                source=getattr(nm, "source", None),
                project=getattr(nm, "project", None),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                expires_at=getattr(nm, "expires_at", None),
                upvotes=0, downvotes=0,
                meta=dict(getattr(nm, "meta", {}) or {}),
                importance_score=0.5, access_count=0, last_accessed_at=None,
            )
            self.memories[new_id] = stored
            return stored

    fake = _Store()

    # The consolidate route imports the embedder lazily via
    # ``lore.server.routes.retrieve._get_embedder``. Stub it out so tests
    # don't require ONNX and don't pay the model-load cost.
    class _StubEmbedder:
        def embed(self, text):
            return [0.0] * 384

    monkeypatch.setattr(
        "lore.server.routes.retrieve._get_embedder",
        lambda: _StubEmbedder(),
    )

    app = FastAPI()
    app.include_router(router)

    async def _get_store():
        return fake

    app.dependency_overrides[get_store] = _get_store
    app.dependency_overrides[get_auth_context] = lambda: auth

    monkeypatch.setattr(
        "lore.server.routes.temporal.require_role",
        lambda *roles: lambda: auth,
    )

    return TestClient(app), fake


def test_http_supersede_records_event(http_client):
    client, fake = http_client
    resp = client.post(
        "/v1/memories/m-old/supersede",
        json={"by": "m-new", "reason": "switched"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "m-old"
    assert body["superseded_by"] == "m-new"
    assert len(fake.events) == 1
    assert fake.events[0] == ("m-old", "m-new", "switched", "api")


def test_http_supersede_404_on_missing_memory(http_client):
    client, _ = http_client
    resp = client.post(
        "/v1/memories/nope/supersede",
        json={"by": "m-new", "reason": "x"},
    )
    assert resp.status_code == 404


def test_http_at_time_returns_memories(http_client):
    client, _ = http_client
    resp = client.get(
        "/v1/memories/at_time",
        params={"at": "2026-05-07T00:00:00Z", "limit": 10},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert {m["id"] for m in body["memories"]} == {"m-old", "m-new"}


def test_http_supersession_chain(http_client):
    client, fake = http_client
    fake.events.append(("m-old", "m-new", "r", "api"))
    resp = client.get("/v1/memories/m-old/supersession-chain")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["memory_id"] == "m-old"
    assert len(body["events"]) == 1
    assert body["events"][0]["superseded_by"] == "m-new"


# ── Consolidate + provenance routes (audit-fix) ─────────────────────


def test_http_consolidate_creates_memory_and_supersedes_sources(http_client):
    client, fake = http_client
    resp = client.post(
        "/v1/memories/consolidate",
        json={
            "source_ids": ["m-old", "m-new"],
            "content": "merged narrative",
            "type": "lesson",
            "reason": "merged near-duplicates",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    new_id = body["id"]
    assert new_id.startswith("m-merge-")
    assert body["superseded_count"] == 2
    # Both sources have a supersession event pointing at the new memory.
    superseding = {(evt[0], evt[1]) for evt in fake.events}
    assert ("m-old", new_id) in superseding
    assert ("m-new", new_id) in superseding
    # The new memory was actually inserted with type=lesson and a
    # consolidated_from list pointing at both sources.
    new = fake.memories[new_id]
    assert new.meta["type"] == "lesson"
    assert set(new.meta["consolidated_from"]) == {"m-old", "m-new"}


def test_http_consolidate_404_on_missing_source(http_client):
    client, _ = http_client
    resp = client.post(
        "/v1/memories/consolidate",
        json={
            "source_ids": ["m-old", "does-not-exist"],
            "content": "x",
            "type": "lesson",
        },
    )
    assert resp.status_code == 404
    assert "does-not-exist" in resp.json()["detail"]


def test_http_consolidate_dedupes_source_ids(http_client):
    client, fake = http_client
    resp = client.post(
        "/v1/memories/consolidate",
        json={
            "source_ids": ["m-old", "m-old", "m-new"],
            "content": "x",
            "type": "lesson",
        },
    )
    assert resp.status_code == 201, resp.text
    # m-old appears twice in the request but only one supersession row.
    new_id = resp.json()["id"]
    rows = [evt for evt in fake.events if evt[1] == new_id]
    assert len(rows) == 2
    assert {evt[0] for evt in rows} == {"m-old", "m-new"}


def test_http_provenance_returns_sources_chain_and_meta(http_client):
    client, fake = http_client
    # m-merged is consolidated from m-old + m-new, then later superseded.
    fake.memories["m-merged"] = _make_stored("m-merged", "merged")
    fake.memories["m-merged"] = StoredMemory(
        id="m-merged", org_id="solo", content="merged", context=None,
        tags=(), confidence=0.5, source="consolidation", project=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        expires_at=None, upvotes=0, downvotes=0,
        meta={"type": "lesson", "consolidated_from": ["legacy-x"]},
        importance_score=0.5, access_count=0, last_accessed_at=None,
    )
    fake.events.append(("m-old", "m-merged", "merge old", "api"))
    fake.events.append(("m-new", "m-merged", "merge new", "api"))
    fake.events.append(("m-merged", "m-superseder", "later replaced", "api"))

    resp = client.get("/v1/memories/m-merged/provenance")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["memory_id"] == "m-merged"
    src_ids = {e["memory_id"] for e in body["sources"]}
    assert src_ids == {"m-old", "m-new"}
    chain_targets = {e["superseded_by"] for e in body["chain"]}
    assert chain_targets == {"m-superseder"}
    # Legacy meta-based provenance also surfaces.
    assert body["metadata_sources"] == ["legacy-x"]


def test_http_provenance_404_on_missing_memory(http_client):
    client, _ = http_client
    resp = client.get("/v1/memories/nope/provenance")
    assert resp.status_code == 404


# ── 8. Prompt fragments ─────────────────────────────────────────────


def test_capture_prompt_contains_supersede_guidance():
    from lore.cli.commands.capture import _build_prompt

    prompt = _build_prompt(
        buffer_lines=["{}"], transcript_tail="some text",
        recent_titles=["a", "b"],
    )
    assert "mcp__lore__supersede" in prompt
    assert "prefer" in prompt.lower()


def test_dream_prompt_contains_supersede_and_contradiction_guidance():
    from lore.cli.commands.dream import _build_prompt as _build_dream_prompt

    prompt = _build_dream_prompt(
        org_id="solo",
        run_id="run-1",
        phase1_stats={"total_memories": 3},
        phase2_signals=[],
        review_mode=False,
    )
    assert "mcp__lore__supersede" in prompt
    assert "contradict" in prompt.lower()


def test_dream_prompt_uses_consolidate_memories_for_merge_and_promote():
    """Audit fix (Gap 1): both Step 1 (merge) and Step 3 (promote) must
    funnel through consolidate_memories so source provenance is preserved
    via memory_supersessions rows. The pre-fix prompt told the subagent to
    call remember(...) bare for promotion, severing the chain."""
    from lore.cli.commands.dream import _build_prompt as _build_dream_prompt

    prompt = _build_dream_prompt(
        org_id="solo",
        run_id="run-1",
        phase1_stats={},
        phase2_signals=[],
        review_mode=False,
    )
    # Both Step 1 and Step 3 must reference the atomic consolidation tool.
    assert prompt.count("mcp__lore__consolidate_memories") >= 2
    # Step 3 promotion must say it goes through consolidate_memories.
    assert "promoted from observation" in prompt
    # Provenance trail is now traceable via the new MCP tool.
    assert "mcp__lore__provenance" in prompt
