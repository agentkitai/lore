"""Tests for ``GET /v1/timeline`` (Phase 6G T7).

The store layer is mocked so these tests exercise only the route's
auth/scope handling, response shape, and 404/403 surface.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


# ── helpers ──────────────────────────────────────────────────────────


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _make_stored(
    *,
    memory_id: str,
    project: str | None,
    created_at: datetime,
    title: str = "title",
    narrative: str = "Long narrative. Second sentence here.",
    type_: str = "observation",
    session_id: str | None = None,
    org_id: str = "org-001",
):
    from lore.persistence.types import StoredMemory

    meta = {"type": type_, "title": title, "narrative": narrative}
    if session_id is not None:
        meta["session_id"] = session_id
    return StoredMemory(
        id=memory_id,
        org_id=org_id,
        content=narrative,
        context=title,
        tags=(),
        confidence=0.5,
        source=None,
        project=project,
        created_at=_utc(created_at),
        updated_at=_utc(created_at),
        expires_at=None,
        upvotes=0,
        downvotes=0,
        meta=meta,
        importance_score=1.0,
        access_count=0,
        last_accessed_at=None,
        scope="project",
    )


class _FakeStore:
    """Stand-in Store; only ``list_timeline_around`` is exercised."""

    def __init__(self, result):
        self.list_timeline_around = AsyncMock(return_value=result)
        self.calls = self.list_timeline_around

    async def close(self):
        pass


def _make_app(store, *, project_scope: str | None = None):
    from fastapi import FastAPI

    from lore.server.auth import AuthContext, get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.timeline import router

    auth = AuthContext(
        org_id="org-001",
        project=project_scope,
        is_root=True,
        key_id="key-001",
        role="admin",
    )

    async def _get_store():
        return store

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = _get_store
    app.dependency_overrides[get_auth_context] = lambda: auth
    return app


# ── tests ────────────────────────────────────────────────────────────


def test_timeline_returns_adjacent_entries_in_same_project():
    """5 same-session events; anchor is middle; limit=4 direction=both."""
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    events = [
        _make_stored(
            memory_id=f"mem-{i}",
            project="p",
            created_at=base + timedelta(minutes=15 * (i - 2)),
            title=f"event-{i}",
            session_id="sess-A",
        )
        for i in range(5)
    ]
    anchor = events[2]
    adjacent = [events[0], events[1], events[3], events[4]]

    store = _FakeStore(result=(anchor, adjacent))
    client = TestClient(_make_app(store))

    resp = client.get(
        "/v1/timeline",
        params={
            "anchor_id": anchor.id,
            "limit": 4,
            "direction": "both",
            "max_hours": 2.0,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 4
    ids = [e["id"] for e in body["entries"]]
    # The route preserves the order returned by the store.
    assert ids == ["mem-0", "mem-1", "mem-3", "mem-4"]
    # All same session as anchor → same_session=True.
    assert all(e["same_session"] is True for e in body["entries"])
    # Each entry has the expected shape.
    e0 = body["entries"][0]
    assert set(e0.keys()) == {"id", "created_at", "type", "title",
                              "narrative_1l", "same_session"}
    assert e0["title"] == "event-0"
    assert e0["type"] == "observation"
    # narrative_1l is the first sentence of the narrative.
    assert e0["narrative_1l"].endswith("Long narrative.") or \
           e0["narrative_1l"] == "Long narrative."

    # Store was invoked with the right args.
    kw = store.calls.call_args.kwargs
    assert kw["anchor_id"] == anchor.id
    assert kw["org_id"] == "org-001"
    assert kw["direction"] == "both"
    assert kw["limit"] == 4
    assert kw["max_hours"] == 2.0


def test_timeline_excludes_other_project_entries():
    """The store layer enforces same-project filtering; the route trusts it."""
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    anchor = _make_stored(memory_id="mem-anchor", project="p", created_at=base)
    sibling = _make_stored(
        memory_id="mem-sibling", project="p",
        created_at=base + timedelta(minutes=10),
    )
    # The store would never return the cross-project row; here we
    # verify that whatever it returns is what the route exposes.
    store = _FakeStore(result=(anchor, [sibling]))
    client = TestClient(_make_app(store))

    resp = client.get("/v1/timeline", params={"anchor_id": anchor.id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [e["id"] for e in body["entries"]]
    assert ids == ["mem-sibling"]


def test_timeline_respects_max_hours():
    """The route forwards max_hours to the store unchanged."""
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    anchor = _make_stored(memory_id="mem-anchor", project="p", created_at=base)
    # Caller asks for max_hours=2 → the store's filter excludes a 5h-out
    # event, so it returns no adjacent rows.
    store = _FakeStore(result=(anchor, []))
    client = TestClient(_make_app(store))

    resp = client.get(
        "/v1/timeline",
        params={"anchor_id": anchor.id, "max_hours": 2.0},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"entries": [], "count": 0}
    kw = store.calls.call_args.kwargs
    assert kw["max_hours"] == 2.0


def test_timeline_direction_before():
    """``direction=before`` is forwarded; the response surface is the same."""
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    anchor = _make_stored(memory_id="mem-2", project="p", created_at=base)
    earlier = [
        _make_stored(memory_id="mem-0", project="p",
                     created_at=base - timedelta(minutes=30)),
        _make_stored(memory_id="mem-1", project="p",
                     created_at=base - timedelta(minutes=15)),
    ]
    store = _FakeStore(result=(anchor, earlier))
    client = TestClient(_make_app(store))

    resp = client.get(
        "/v1/timeline",
        params={
            "anchor_id": anchor.id,
            "limit": 2,
            "direction": "before",
        },
    )
    assert resp.status_code == 200, resp.text
    ids = [e["id"] for e in resp.json()["entries"]]
    assert ids == ["mem-0", "mem-1"]
    kw = store.calls.call_args.kwargs
    assert kw["direction"] == "before"
    assert kw["limit"] == 2


def test_timeline_404_for_unknown_anchor():
    """Store returns ``(None, [])`` → route returns 404."""
    store = _FakeStore(result=(None, []))
    client = TestClient(_make_app(store))

    resp = client.get("/v1/timeline", params={"anchor_id": "mem-nope"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "anchor_id not found"


def test_timeline_403_for_foreign_project_with_scoped_key():
    """Project-scoped key calling against an anchor in a different project → 403."""
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    anchor = _make_stored(memory_id="mem-x", project="other-project", created_at=base)
    store = _FakeStore(result=(anchor, []))
    # Caller's API key is scoped to project "p" but the anchor is in
    # "other-project" — the route refuses with 403 even though the org
    # matches.
    client = TestClient(_make_app(store, project_scope="p"))

    resp = client.get("/v1/timeline", params={"anchor_id": anchor.id})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "anchor not in scoped project"


def test_timeline_marks_same_session_correctly():
    """``same_session`` is True iff entry session_id == anchor.session_id."""
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    anchor = _make_stored(
        memory_id="mem-anchor", project="p",
        created_at=base, session_id="sess-A",
    )
    same = _make_stored(
        memory_id="mem-same", project="p",
        created_at=base - timedelta(minutes=10),
        session_id="sess-A",
    )
    different = _make_stored(
        memory_id="mem-diff", project="p",
        created_at=base + timedelta(minutes=10),
        session_id="sess-B",
    )
    store = _FakeStore(result=(anchor, [same, different]))
    client = TestClient(_make_app(store))

    resp = client.get("/v1/timeline", params={"anchor_id": anchor.id})
    assert resp.status_code == 200, resp.text
    by_id = {e["id"]: e for e in resp.json()["entries"]}
    assert by_id["mem-same"]["same_session"] is True
    assert by_id["mem-diff"]["same_session"] is False


def test_timeline_validates_query_params():
    """``limit`` out of range and unknown direction return 422."""
    store = _FakeStore(result=(None, []))
    client = TestClient(_make_app(store))

    # limit=0 — below ge=1.
    resp = client.get(
        "/v1/timeline", params={"anchor_id": "x", "limit": 0},
    )
    assert resp.status_code == 422

    # max_hours=0 — must be > 0.
    resp = client.get(
        "/v1/timeline", params={"anchor_id": "x", "max_hours": 0},
    )
    assert resp.status_code == 422

    # Unknown direction.
    resp = client.get(
        "/v1/timeline", params={"anchor_id": "x", "direction": "sideways"},
    )
    assert resp.status_code == 422


def test_timeline_no_session_id_means_same_session_false():
    """When neither anchor nor entry has a session_id, ``same_session=False``."""
    base = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    # Anchor has no session_id at all.
    anchor = _make_stored(memory_id="mem-a", project="p", created_at=base)
    other = _make_stored(
        memory_id="mem-b", project="p",
        created_at=base + timedelta(minutes=10),
    )
    store = _FakeStore(result=(anchor, [other]))
    client = TestClient(_make_app(store))

    resp = client.get("/v1/timeline", params={"anchor_id": anchor.id})
    assert resp.status_code == 200, resp.text
    [entry] = resp.json()["entries"]
    # Anchor session_id is None — never claim "same session".
    assert entry["same_session"] is False
