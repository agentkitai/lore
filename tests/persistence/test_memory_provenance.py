"""Memory provenance / lineage aggregation (#82).

Pure-builder tests need no store; the integration test uses the parametrized
``store`` fixture (sqlite always, postgres when available).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from lore.persistence import NewMemory
from lore.services.provenance import build_memory_provenance


def _vec(seed: int):
    return [((seed + i * 7) % 100) / 100.0 for i in range(384)]


class _Sup:  # duck-typed StoredSupersession
    def __init__(self, memory_id, superseded_by, reason="r", agent="auto"):
        self.memory_id = memory_id
        self.superseded_by = superseded_by
        self.reason = reason
        self.ts = datetime(2026, 1, 1)
        self.agent = agent


class _Mem:
    id = "m1"
    user_id = "alice"
    tags = ["pii", "topic"]
    source = "capture"
    visibility = "shared"
    created_at = datetime(2026, 1, 1)


# ── pure aggregation ────────────────────────────────────────────────
def test_build_aggregates_owner_trust_redaction_and_lineage():
    p = build_memory_provenance(_Mem(), [_Sup("m1", "m2")], [_Sup("m0", "m1")])
    assert p["owner"] == "alice"
    assert p["trust_signal"] == "owned"
    assert p["redaction_tags"] == ["pii"]  # "topic" is not a redaction tag
    assert p["visibility"] == "shared"
    assert p["supersession_chain"][0]["superseded_by"] == "m2"
    assert p["supersession_sources"][0]["memory_id"] == "m0"


def test_anonymous_memory_trust_signal():
    class _Anon(_Mem):
        user_id = None

    assert build_memory_provenance(_Anon(), [], [])["trust_signal"] == "anonymous"


# ── integration ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_provenance_via_store_chain_and_sources(store):
    a = await store.insert_memory(
        NewMemory(org_id="solo", content="orig", embedding=_vec(1), user_id="alice", tags=["pii"], source="capture")
    )
    b = await store.insert_memory(NewMemory(org_id="solo", content="newer", embedding=_vec(2), user_id="alice"))
    await store.record_supersession(a.id, superseded_by=b.id, reason="consolidated", agent="auto")

    m = await store.get_memory("solo", a.id)
    chain = await store.get_supersession_chain(a.id)
    sources = await store.list_supersession_sources(b.id)  # b consolidated a
    prov = build_memory_provenance(m, chain, sources)

    assert prov["owner"] == "alice"
    assert prov["source"] == "capture"
    assert prov["redaction_tags"] == ["pii"]
    assert any(link["superseded_by"] == b.id for link in prov["supersession_chain"])
    assert any(link["memory_id"] == a.id for link in prov["supersession_sources"])
