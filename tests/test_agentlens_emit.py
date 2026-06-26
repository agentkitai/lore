"""AgentLens memory-event emitter (#78) — best-effort, fire-and-forget.

Uses asyncio.run() wrappers so it needs no pytest-asyncio config; the emitter's
fire-and-forget tasks are drained via the module's _inflight set.
"""

import asyncio

from lore.emit import agentlens


def _drain() -> "asyncio.Future":
    return asyncio.gather(*list(agentlens._inflight)) if agentlens._inflight else asyncio.sleep(0)


def test_build_event_body_shape():
    body = agentlens.build_event_body(
        "memory_created", org_id="org1", agent_id="agt_x", memory_id="m1", data={"type": "note"}
    )
    ev = body["events"][0]
    assert ev["sessionId"] == "lore-memory:org1"
    assert ev["agentId"] == "agt_x"
    assert ev["eventType"] == "custom"
    assert ev["payload"] == {"type": "memory_created", "data": {"type": "note"}}
    assert ev["metadata"]["source"] == "lore"
    assert ev["metadata"]["memoryId"] == "m1"


def test_agent_id_defaults_to_lore_and_omits_memory_id():
    ev = agentlens.build_event_body("x", org_id="o", agent_id=None, memory_id=None, data={})["events"][0]
    assert ev["agentId"] == "lore"
    assert "memoryId" not in ev["metadata"]


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LORE_AGENTLENS_URL", raising=False)
    monkeypatch.delenv("LORE_AGENTLENS_API_KEY", raising=False)
    assert agentlens.agentlens_emit_enabled() is False
    # no-op + never raises, even with no running loop
    agentlens.emit_memory_event("memory_created", org_id="o", data={})


def test_requires_both_url_and_key(monkeypatch):
    monkeypatch.setenv("LORE_AGENTLENS_URL", "http://lens")
    monkeypatch.delenv("LORE_AGENTLENS_API_KEY", raising=False)
    assert agentlens.agentlens_emit_enabled() is False
    monkeypatch.setenv("LORE_AGENTLENS_API_KEY", "k")
    assert agentlens.agentlens_emit_enabled() is True


def test_emit_posts_expected_body(monkeypatch):
    monkeypatch.setenv("LORE_AGENTLENS_URL", "http://lens")
    monkeypatch.setenv("LORE_AGENTLENS_API_KEY", "k")
    captured: dict = {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            captured.update(url=url, json=json, headers=headers)

    monkeypatch.setattr(agentlens.httpx, "AsyncClient", FakeClient)

    async def run():
        agentlens.emit_memory_event(
            "memory_created", org_id="org1", agent_id="agt_x", memory_id="m1", data={"type": "note"}
        )
        await _drain()

    asyncio.run(run())
    assert captured["url"] == "http://lens/api/events"
    assert captured["json"]["events"][0]["payload"]["type"] == "memory_created"
    assert captured["headers"]["Authorization"] == "Bearer k"


def test_emit_never_raises_on_post_failure(monkeypatch):
    monkeypatch.setenv("LORE_AGENTLENS_URL", "http://lens")
    monkeypatch.setenv("LORE_AGENTLENS_API_KEY", "k")

    class BoomClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise RuntimeError("network down")

    monkeypatch.setattr(agentlens.httpx, "AsyncClient", BoomClient)

    async def run():
        agentlens.emit_memory_event("memory_created", org_id="o", data={})
        await _drain()  # must complete without raising

    asyncio.run(run())  # no exception = pass
