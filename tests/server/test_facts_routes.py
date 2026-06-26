"""HTTP route tests for the bi-temporal facts endpoints (#67).

Mirrors the memory temporal-route tests (tests/services/test_temporal_supersession.py
section 7): a FastAPI TestClient with the facts router + a fake store +
get_auth_context override. Covers the handler logic the persistence/service
tests can't reach — auth gating, 400/404 branches, Literal validation, and the
response shapes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from lore.persistence.types import StoredRelationship, StoredRelationshipSupersession  # noqa: E402

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _rel(rid: str, src: str, tgt: str, rel_type: str) -> StoredRelationship:
    return StoredRelationship(
        id=rid, org_id="solo", source_entity_id=src, target_entity_id=tgt, rel_type=rel_type,
        weight=0.9, properties={}, source_fact_id=None, source_memory_id="mem1",
        valid_from=_NOW, valid_until=None, superseded_by=None, status="approved",
        created_at=_NOW, updated_at=_NOW,
    )


class _FakeStore:
    def __init__(self):
        self.entities = {
            "e_pg": SimpleNamespace(id="e_pg", name="Postgres"),
            "e_pgv": SimpleNamespace(id="e_pgv", name="pgvector"),
            "e_my": SimpleNamespace(id="e_my", name="MySQL"),
        }
        self._by_name = {e.name: e for e in self.entities.values()}
        self.rels = {
            "rel_1": _rel("rel_1", "e_pg", "e_pgv", "depends_on"),
            "rel_2": _rel("rel_2", "e_pg", "e_my", "depends_on"),
        }
        self.supersede_calls: list = []

    async def get_entity_by_name(self, name, org_id):
        return self._by_name.get(name)

    async def get_entity(self, eid, org_id):
        return self.entities.get(eid)

    async def query_relationships(self, entity_ids, org_id, *, direction="both",
                                  active_only=True, at_time=None, rel_types=None):
        out = [r for r in self.rels.values()
               if r.source_entity_id in entity_ids or r.target_entity_id in entity_ids]
        if rel_types:
            out = [r for r in out if r.rel_type in rel_types]
        return out

    async def get_relationship(self, rid, org_id):
        return self.rels.get(rid)

    async def supersede_relationship(self, rid, org_id, *, superseded_by, reason=None, agent="auto"):
        self.supersede_calls.append((rid, superseded_by, reason, agent))

    async def get_relationship_supersession_chain(self, rid, org_id):
        if rid not in self.rels:
            return []
        return [StoredRelationshipSupersession(
            id=1, relationship_id=rid, superseded_by="rel_2",
            reason="corrected", ts=_NOW, agent="api",
        )]


def _client(role: str = "admin"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lore.server.auth import AuthContext, get_auth_context
    from lore.server.db import get_store
    from lore.server.routes.facts import router

    auth = AuthContext(org_id="solo", project=None, is_root=True, key_id="k1", role=role)
    fake = _FakeStore()

    app = FastAPI()
    app.include_router(router)

    async def _get_store():
        return fake

    app.dependency_overrides[get_store] = _get_store
    app.dependency_overrides[get_auth_context] = lambda: auth
    return TestClient(app), fake


# ── GET /v1/facts/at_time ───────────────────────────────────────────


def test_at_time_returns_spo_facts():
    client, _ = _client()
    resp = client.get("/v1/facts/at_time", params={"at": _NOW.isoformat(), "entity": "Postgres"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    spo = {(f["subject"], f["predicate"], f["object"]) for f in body["facts"]}
    assert ("Postgres", "depends_on", "pgvector") in spo
    assert body["total"] == len(body["facts"])
    # SPO view resolves entity names + carries the relationship id.
    assert all(f["relationship_id"] for f in body["facts"])


def test_at_time_unknown_entity_returns_empty():
    client, _ = _client()
    resp = client.get("/v1/facts/at_time", params={"at": _NOW.isoformat(), "entity": "Nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["facts"] == [] and body["total"] == 0


def test_at_time_predicate_filter():
    client, _ = _client()
    resp = client.get("/v1/facts/at_time", params={
        "at": _NOW.isoformat(), "entity": "Postgres", "predicate": "uses"})
    assert resp.status_code == 200
    assert resp.json()["facts"] == []  # no "uses" edge in the fake graph


def test_at_time_invalid_direction_422():
    client, _ = _client()
    resp = client.get("/v1/facts/at_time", params={
        "at": _NOW.isoformat(), "entity": "Postgres", "direction": "sideways"})
    assert resp.status_code == 422


def test_at_time_requires_entity_and_at_422():
    client, _ = _client()
    assert client.get("/v1/facts/at_time", params={"at": _NOW.isoformat()}).status_code == 422
    assert client.get("/v1/facts/at_time", params={"entity": "Postgres"}).status_code == 422


# ── POST /v1/facts/{id}/supersede ───────────────────────────────────


def test_supersede_happy_path():
    client, fake = _client()
    resp = client.post("/v1/facts/rel_1/supersede", json={"by": "rel_2", "reason": "switched"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"relationship_id": "rel_1", "superseded_by": "rel_2", "reason": "switched"}
    assert fake.supersede_calls == [("rel_1", "rel_2", "switched", "api")]


def test_supersede_self_400():
    client, fake = _client()
    resp = client.post("/v1/facts/rel_1/supersede", json={"by": "rel_1"})
    assert resp.status_code == 400
    assert fake.supersede_calls == []


def test_supersede_missing_target_404():
    client, _ = _client()
    resp = client.post("/v1/facts/nope/supersede", json={"by": "rel_2"})
    assert resp.status_code == 404


def test_supersede_missing_replacement_404():
    client, fake = _client()
    resp = client.post("/v1/facts/rel_1/supersede", json={"by": "rel_missing"})
    assert resp.status_code == 404
    assert fake.supersede_calls == []


def test_supersede_requires_writer_role():
    client, fake = _client(role="reader")
    resp = client.post("/v1/facts/rel_1/supersede", json={"by": "rel_2"})
    assert resp.status_code == 403
    assert fake.supersede_calls == []


# ── GET /v1/facts/{id}/supersession-chain ───────────────────────────


def test_supersession_chain_happy():
    client, _ = _client()
    resp = client.get("/v1/facts/rel_1/supersession-chain")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["relationship_id"] == "rel_1"
    assert len(body["events"]) == 1
    assert body["events"][0]["superseded_by"] == "rel_2"
    assert body["events"][0]["agent"] == "api"


def test_supersession_chain_404_on_missing():
    client, _ = _client()
    resp = client.get("/v1/facts/nope/supersession-chain")
    assert resp.status_code == 404
