"""Phase 6C — Hybrid retrieval unit + service + HTTP tests.

Contract tests (``recall_by_text`` / ``recall_by_entities`` / ``fts_weight``
round-trip on both backends) live in ``tests/persistence/test_contract_hybrid.py``.
This module focuses on:

* Unit (no store): RRF math, recency signal, FTS5 query sanitizer.
* Service: ``_hybrid_recall`` with synthetic candidate lists; default-profile
  fallback; graceful degradation when individual signal branches raise.
* HTTP: ``/v1/retrieve`` returns the per-signal breakdown alongside scores.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Sequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient

from lore.persistence import (
    ResolvedProfile,
    StoredMemory,
)
from lore.persistence.types import StoredApiKey
from lore.server.app import app
from lore.server.auth import _key_cache, _last_used_updates
from lore.server.middleware import RateLimiter, set_rate_limiter
from lore.services.retrieve import (
    HybridParams,
    _hybrid_recall,
    _recency_signal,
    _rrf_fuse,
    hybrid_retrieve,
)

# ── helpers ────────────────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)


def _vec(seed: int) -> Sequence[float]:
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


def _make_stored(mid: str, content: str, *, age_days: float = 0.0,
                 importance: float = 0.5) -> StoredMemory:
    created = NOW - timedelta(days=age_days)
    return StoredMemory(
        id=mid,
        org_id="solo",
        content=content,
        context=None,
        tags=(),
        confidence=1.0,
        source=None,
        project=None,
        created_at=created,
        updated_at=created,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta={},
        importance_score=importance,
        access_count=0,
        last_accessed_at=None,
    )


# ── unit tests: RRF math + recency ─────────────────────────────────────────────


def test_rrf_top_rank_normalizes_to_one():
    """Item at rank 0 in every source maps to fused score 1.0."""
    m = _make_stored("m1", "content")
    sources = [
        ([(m, 0.9)], 1.0),
        ([(m, 0.5)], 1.0),
        ([(m, 1.0)], 1.0),
    ]
    fused = _rrf_fuse(sources, k=60)
    assert len(fused) == 1
    _, score, signals = fused[0]
    assert score == pytest.approx(1.0, abs=1e-6)
    # Per-signal raw scores survive into the breakdown
    assert signals["signal_0"] == pytest.approx(0.9)
    assert signals["signal_1"] == pytest.approx(0.5)
    assert signals["signal_2"] == pytest.approx(1.0)


def test_rrf_orders_by_aggregate_rank():
    """Memory with better rank in more signals wins."""
    a = _make_stored("a", "alpha")
    b = _make_stored("b", "beta")
    sources = [
        ([(a, 0.9), (b, 0.6)], 1.0),  # a > b
        ([(a, 0.7), (b, 0.5)], 1.0),  # a > b
    ]
    fused = _rrf_fuse(sources, k=60)
    assert [r[0].id for r in fused] == ["a", "b"]
    assert fused[0][1] > fused[1][1]


def test_rrf_zero_weight_branch_contributes_nothing():
    a = _make_stored("a", "alpha")
    b = _make_stored("b", "beta")
    sources = [
        ([(a, 0.9)], 1.0),
        ([(b, 0.7)], 0.0),  # silenced
    ]
    fused = _rrf_fuse(sources, k=60)
    ids = [r[0].id for r in fused]
    assert "a" in ids
    # b only appears in a zero-weight branch; it still surfaces with a score
    # of 0 (RRF degrades gracefully) but ranks below a.
    if "b" in ids:
        a_score = next(r[1] for r in fused if r[0].id == "a")
        b_score = next(r[1] for r in fused if r[0].id == "b")
        assert a_score > b_score


def test_recency_signal_decays():
    """exp(-age_days / recency_bias) — fresh ≈ 1.0, ancient ≈ 0."""
    now = datetime.now(timezone.utc)
    fresh = _recency_signal(now, 30.0)
    week_old = _recency_signal(now - timedelta(days=7), 30.0)
    year_old = _recency_signal(now - timedelta(days=365), 30.0)
    assert fresh == pytest.approx(1.0, abs=1e-3)
    assert 0.5 < week_old < 1.0
    assert year_old < 0.001


def test_recency_signal_zero_bias_safe():
    """recency_bias <= 0 → 0 (no recency weight) instead of div-by-zero."""
    now = datetime.now(timezone.utc)
    assert _recency_signal(now, 0.0) == 0.0
    assert _recency_signal(now, -5.0) == 0.0
    assert _recency_signal(None, 30.0) == 0.0


def test_sqlite_fts_query_sanitizer_strips_reserved():
    """FTS5 reserved chars get stripped; result wrapped as a phrase."""
    try:
        from lore.persistence.sqlite import SqliteStore
    except ImportError:
        pytest.skip("aiosqlite not installed")
    assert SqliteStore._sanitize_fts_query("hello") == '"hello"'
    # Stars, colons, parens get nuked; remaining tokens collapsed to one phrase
    assert SqliteStore._sanitize_fts_query('hello: "world"*') == '"hello world"'
    assert SqliteStore._sanitize_fts_query("   ") == ""
    assert SqliteStore._sanitize_fts_query(":") == ""


# ── service tests: _hybrid_recall with synthetic data ──────────────────────────


def _profile(**overrides) -> ResolvedProfile:
    base = dict(
        name="t",
        source="default",
        semantic_weight=1.0,
        graph_weight=0.5,
        recency_bias=30.0,
        min_score=0.0,
        max_results=10,
        tier_filters=None,
        k=None,
        threshold=None,
        rerank=False,
        include_graph=True,
        fts_weight=1.0,
    )
    base.update(overrides)
    return ResolvedProfile(**base)


def _fake_store_with(*, vec=None, fts=None, graph=None, fail=()):
    """Build a fake store whose recall_by_* methods return the supplied lists.

    ``fail`` is a tuple of branch names ("vec" / "fts" / "graph") that should
    raise — used to verify graceful degradation.
    """
    store = MagicMock()

    async def _vec(_params):
        if "vec" in fail:
            raise RuntimeError("vec branch boom")
        from lore.persistence.types import ScoredMemory
        out = []
        for m, s in (vec or []):
            out.append(ScoredMemory(
                id=m.id, org_id=m.org_id, content=m.content, context=m.context,
                tags=m.tags, confidence=m.confidence, source=m.source,
                project=m.project, created_at=m.created_at, updated_at=m.updated_at,
                expires_at=m.expires_at, upvotes=m.upvotes, downvotes=m.downvotes,
                meta=m.meta, importance_score=m.importance_score,
                access_count=m.access_count, last_accessed_at=m.last_accessed_at,
                score=s,
            ))
        return out

    async def _fts(*_a, **_kw):
        if "fts" in fail:
            raise RuntimeError("fts branch boom")
        return list(fts or [])

    async def _graph(*_a, **_kw):
        if "graph" in fail:
            raise RuntimeError("graph branch boom")
        return list(graph or [])

    async def _ent_by_name(_n):
        # Return a simple entity for any name so the graph branch fires.
        if not graph:
            return None
        from lore.persistence.types import StoredEntity
        return StoredEntity(
            id="ent-1", name=str(_n), entity_type="topic", aliases=(),
            description=None, metadata={}, mention_count=1,
            first_seen_at=NOW, last_seen_at=NOW, created_at=NOW, updated_at=NOW,
        )

    store.recall_by_embedding = AsyncMock(side_effect=_vec)
    store.recall_by_text = AsyncMock(side_effect=_fts)
    store.recall_by_entities = AsyncMock(side_effect=_graph)
    store.get_entity_by_name = AsyncMock(side_effect=_ent_by_name)
    # Phase 6F: hybrid recall calls are_superseded; default to no-op set.
    store.are_superseded = AsyncMock(return_value=set())
    return store


@pytest.mark.asyncio
async def test_hybrid_recall_combines_signals():
    a = _make_stored("a", "alpha", importance=0.8)
    b = _make_stored("b", "beta", importance=0.5)
    store = _fake_store_with(
        vec=[(a, 0.9), (b, 0.4)],
        fts=[(a, 5.0)],
        graph=[(a, 2)],
    )
    report = await _hybrid_recall(
        store, _profile(min_score=0.0),
        HybridParams(org_id="solo", query_text="alpha", query_vec=_vec(1), limit=5),
    )
    results = report.results
    assert results
    assert results[0].memory.id == "a"
    sigs = results[0].signals
    assert sigs["vector"] == pytest.approx(0.9)
    assert sigs["fts"] == pytest.approx(5.0)
    assert sigs["graph"] == pytest.approx(2.0)
    assert 0.0 < sigs["recency"] <= 1.0
    assert sigs["importance"] == pytest.approx(0.8)
    # Diagnostic plumbing must reflect that all three branches succeeded.
    assert report.attempted == {"vector": "ok", "fts": "ok", "graph": "ok"}
    assert report.best_score >= results[0].score


@pytest.mark.asyncio
async def test_hybrid_recall_fts_failure_degrades_gracefully():
    a = _make_stored("a", "alpha")
    store = _fake_store_with(vec=[(a, 0.9)], fail=("fts",))
    report = await _hybrid_recall(
        store, _profile(min_score=0.0),
        HybridParams(org_id="solo", query_text="alpha", query_vec=_vec(1), limit=5),
    )
    results = report.results
    assert any(r.memory.id == "a" for r in results)
    # FTS signal is 0 — the branch was silenced.
    sigs = next(r.signals for r in results if r.memory.id == "a")
    assert sigs["fts"] == 0.0
    assert sigs["vector"] == pytest.approx(0.9)
    assert report.attempted["fts"] == "error"
    assert report.attempted["vector"] == "ok"


@pytest.mark.asyncio
async def test_hybrid_recall_all_signals_failing_returns_empty():
    store = _fake_store_with(fail=("vec", "fts", "graph"))
    report = await _hybrid_recall(
        store, _profile(min_score=0.0),
        HybridParams(org_id="solo", query_text="alpha", query_vec=_vec(1), limit=5),
    )
    assert list(report.results) == []
    assert report.best_score == 0.0
    # vec + fts errors propagate to gather; graph branch returns empty because
    # the fake's get_entity_by_name returns None for every token (no entities
    # to recall_by_entities — that's a legitimate "empty", not "error").
    assert report.attempted["vector"] == "error"
    assert report.attempted["fts"] == "error"
    assert report.attempted["graph"] == "empty"


@pytest.mark.asyncio
async def test_hybrid_recall_min_score_drops_low_results():
    """Synthetic 30-item vector list — the tail must be culled by min_score."""
    big_vec = [(_make_stored(f"m{i}", f"item {i}"), 0.5) for i in range(30)]
    store = _fake_store_with(vec=big_vec)
    # Single-signal RRF normalises rank 0 → 1.0, rank 29 → ~0.5; with a
    # 0.7 threshold post-multiplicative-annotation, only the top handful
    # should survive.
    report = await _hybrid_recall(
        store, _profile(semantic_weight=1.0, fts_weight=0.0, graph_weight=0.0,
                        min_score=0.9),
        HybridParams(org_id="solo", query_text="alpha", query_vec=_vec(1), limit=20),
    )
    results = report.results
    assert 0 < len(results) < 30
    # Top-ranked stays in
    assert results[0].memory.id == "m0"
    # Tail-ranked dropped
    assert all(r.memory.id != "m29" for r in results)


@pytest.mark.asyncio
async def test_hybrid_recall_best_score_survives_min_score_filter():
    """When min_score drops every candidate, best_score still reflects the
    pre-filter top score so the route can suggest 'try lowering min_score'."""
    a = _make_stored("a", "alpha")
    store = _fake_store_with(vec=[(a, 0.5)])
    report = await _hybrid_recall(
        store,
        _profile(semantic_weight=1.0, fts_weight=0.0, graph_weight=0.0,
                 min_score=10.0),  # impossibly high
        HybridParams(org_id="solo", query_text="alpha", query_vec=_vec(1), limit=5),
    )
    assert list(report.results) == []
    assert report.best_score > 0.0


@pytest.mark.asyncio
async def test_hybrid_retrieve_falls_back_to_default_profile():
    """Passing profile=None still produces results with the default weights."""
    a = _make_stored("a", "alpha")
    store = _fake_store_with(vec=[(a, 0.9)])
    results = await hybrid_retrieve(
        store,
        org_id="solo",
        query_text="alpha",
        query_vec=_vec(1),
        limit=5,
        profile=None,
        min_score_override=0.0,
    )
    assert any(r.memory.id == "a" for r in results)


# ── HTTP: /v1/retrieve carries signals ─────────────────────────────────────────

RAW_KEY = "lore_sk_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
KEY_HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()
ORG_ID = "org-001"
HEADERS = {"Authorization": f"Bearer {RAW_KEY}"}
SAMPLE_EMBEDDING = [0.1] * 384


def _scored_memory(memory_id="m1", content="hello", score=0.85,
                   meta=None, project=None, tags=()):
    from lore.persistence.types import ScoredMemory
    return ScoredMemory(
        id=memory_id,
        org_id=ORG_ID,
        content=content,
        context=None,
        tags=tags,
        confidence=1.0,
        source=None,
        project=project,
        created_at=NOW,
        updated_at=NOW,
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta=meta or {"type": "note", "tier": "long"},
        importance_score=1.0,
        access_count=0,
        last_accessed_at=None,
        score=score,
    )


def _make_auth_store():
    store = AsyncMock()
    store.lookup_api_key_by_hash = AsyncMock(return_value=StoredApiKey(
        id="key-001", org_id=ORG_ID, name="t", key_hash=KEY_HASH,
        key_prefix="lore_sk_xx", project=None, is_root=True,
        workspace_id=None, revoked_at=None, created_at=NOW,
        last_used_at=None, role=None,
    ))
    store.touch_api_key_last_used = AsyncMock(return_value=None)
    return store


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


@pytest.mark.asyncio
async def test_v1_retrieve_returns_signals_breakdown(client):
    """Each memory in the response carries vector/fts/graph/recency/importance signals."""
    sm = _scored_memory("mem-001", "kubernetes ingress troubleshooting", 0.85)
    fake_store = MagicMock()
    fake_store.recall_by_embedding = AsyncMock(return_value=[sm])
    # Hybrid path will probe these — return empty for predictability.
    fake_store.recall_by_text = AsyncMock(return_value=[])
    fake_store.recall_by_entities = AsyncMock(return_value=[])
    fake_store.get_entity_by_name = AsyncMock(return_value=None)
    # Phase 6F: hybrid recall calls are_superseded to score-suppress.
    fake_store.are_superseded = AsyncMock(return_value=set())
    auth_store = _make_auth_store()

    async def _fake_get_store():
        return fake_store

    with patch("lore.server.routes.retrieve.get_store", _fake_get_store), \
         patch("lore.server.auth.get_store", return_value=auth_store):
        resp = await client.get(
            "/v1/retrieve",
            params={"query": "kubernetes ingress", "min_score": 0.0},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    sigs = data["memories"][0]["signals"]
    assert set(sigs.keys()) == {
        "vector", "fts", "graph", "recency", "importance", "superseded",
    }
    assert sigs["vector"] > 0
    assert sigs["fts"] == 0
    assert sigs["graph"] == 0
    assert 0 < sigs["recency"] <= 1.0
    assert sigs["superseded"] == 0
