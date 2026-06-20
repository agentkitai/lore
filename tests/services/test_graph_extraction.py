"""Service-layer tests for ``services.graph_extraction``.

Covers:
  * Prompt builder shape — content + optional context block.
  * Stream-json response parsing — finds the final assistant text and
    extracts the JSON object, even with mid-stream system / tool_use
    events sprinkled in.
  * Spawn-args sanity — guards against the dream/capture flag-saga
    repeating: ``--output-format stream-json --verbose
    --permission-mode default`` must all be present.
  * `extract_and_persist` happy path with stub spawn_fn.
  * Entity dedup: case-insensitive name match, alias match.
  * Idempotent re-extraction: second run replaces, doesn't double.
  * Failure modes: subprocess timeout, parse failure, missing claude.
  * Concurrency cap: bursts honor `LORE_GRAPH_EXTRACTION_CONCURRENCY`.

The persistence-layer parametrized ``store`` fixture is reused here so
the dedup / idempotency assertions exercise the real SQLite + PG store
implementations of the new ops (``find_entity_by_name_or_alias``,
``replace_memory_mentions``, ``replace_memory_relationships``).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any, List, Optional
from unittest.mock import patch

import pytest

# Optional [solo] deps for the SQLite branch of the parametrized fixture.
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")


from lore.persistence import NewEntity, NewMemory, Store  # noqa: E402
from lore.services import graph_extraction as gx  # noqa: E402

# ── Prompt builder ─────────────────────────────────────────────────


class TestBuildExtractionPrompt:
    def test_includes_content(self):
        out = gx._build_extraction_prompt(content="Pinecone ships Nexus.", context=None)
        assert "Pinecone ships Nexus." in out
        assert "JSON" in out
        assert "Memory context:" not in out

    def test_includes_context_when_present(self):
        out = gx._build_extraction_prompt(
            content="They ship a knowledge engine.",
            context="Vector DB market leader",
        )
        assert "Vector DB market leader" in out
        assert "Memory context:" in out

    def test_schema_lists_entity_types(self):
        out = gx._build_extraction_prompt(content="x", context=None)
        for t in ("person", "project", "technology", "concept",
                  "organization", "location", "other"):
            assert t in out


# ── Response parsing ───────────────────────────────────────────────


def _stream_event(role: str = "assistant", text: str = "") -> str:
    """Render one stream-json line in Claude Code's shape."""
    return json.dumps({
        "type": role,
        "message": {
            "role": role,
            "content": [{"type": "text", "text": text}],
        },
    })


class TestParseExtractionResponse:
    def test_picks_last_assistant_text(self):
        stdout = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            _stream_event(text='{"entities": [], "relationships": []}'),
            json.dumps({"type": "result", "subtype": "success"}),
        ])
        out = gx._parse_extraction_response(stdout)
        assert out == {"entities": [], "relationships": []}

    def test_handles_json_fence(self):
        text = "Here you go:\n```json\n{\"entities\": [{\"name\": \"X\"}]}\n```"
        stdout = _stream_event(text=text)
        out = gx._parse_extraction_response(stdout)
        assert out == {"entities": [{"name": "X"}]}

    def test_returns_none_on_no_json(self):
        stdout = _stream_event(text="I refuse to comply.")
        assert gx._parse_extraction_response(stdout) is None

    def test_returns_none_on_empty(self):
        assert gx._parse_extraction_response("") is None

    def test_skips_non_assistant_events(self):
        # Mid-stream tool-use event must not interfere.
        stdout = "\n".join([
            json.dumps({
                "type": "assistant",
                "message": {"role": "assistant",
                            "content": [{"type": "tool_use", "id": "x"}]},
            }),
            _stream_event(text='{"entities": [{"name": "Y"}], "relationships": []}'),
        ])
        out = gx._parse_extraction_response(stdout)
        assert out == {"entities": [{"name": "Y"}], "relationships": []}


# ── _spawn_claude default args ────────────────────────────────────


class TestSpawnClaudeArgs:
    def test_passes_required_flags(self, monkeypatch, tmp_path):
        captured = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs

        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        monkeypatch.setenv("LORE_HOME", str(tmp_path))
        gx._spawn_claude("hello prompt")
        cmd = captured["cmd"]
        assert cmd[0] == "claude"
        assert cmd[1] == "-p"
        assert cmd[2] == "hello prompt"
        flags = cmd[3:]
        # Regression guard for PRs #48 and #49: stream-json + verbose +
        # permission-mode default. If Claude Code adds another required
        # flag this test will catch it sooner than the silent-empty-graph
        # mode it would otherwise produce.
        assert "--output-format" in flags
        assert "stream-json" in flags
        assert "--verbose" in flags
        assert "--permission-mode" in flags
        assert "default" in flags  # not bypassPermissions
        # Cheap-subagent flags (lore.subagent_config) — guards against
        # silently regressing back to inheriting the user's full Claude
        # Code stack on every spawn.
        assert "--model" in flags
        assert "--strict-mcp-config" in flags
        assert "--mcp-config" in flags
        assert "--settings" in flags
        # Stdin/stdout hygiene.
        assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
        # Recursion-guard env vars must be set (LORE_AUTO_SAVE=false,
        # LORE_DREAM_AUTO=false). Without these, the subagent's own
        # tool uses fire the user's lore-capture-* hooks and spawn
        # nested capture-extracts.
        env = captured["kwargs"]["env"]
        assert env["LORE_AUTO_SAVE"] == "false"
        assert env["LORE_DREAM_AUTO"] == "false"


# ── extract_and_persist with stub spawn_fn ─────────────────────────


def _make_fake_proc(*, stdout: bytes = b"", returncode: int = 0,
                   raise_communicate: Optional[Exception] = None):
    """Build a minimal Popen-like object the service can drive."""

    class FakeProc:
        def __init__(self):
            self.returncode = returncode

        def communicate(self):
            if raise_communicate:
                raise raise_communicate
            return (stdout, b"")

        def kill(self):
            pass

    return FakeProc()


def _spawn_returning(payload: dict, *, returncode: int = 0):
    """Build a spawn_fn that returns a fake proc whose stdout is one
    stream-json line containing ``payload`` as the assistant text."""
    line = _stream_event(text=json.dumps(payload))
    stdout = (line + "\n").encode("utf-8")

    def spawn(_prompt: str):
        return _make_fake_proc(stdout=stdout, returncode=returncode)

    return spawn


async def _insert_memory(store: Store, *, content: str = "x") -> str:
    stored = await store.insert_memory(
        NewMemory(org_id="solo", content=content, embedding=[0.1] * 384)
    )
    return stored.id


@pytest.mark.asyncio
async def test_extract_happy_path(store: Store, monkeypatch):
    """Two entities + one relationship → both persist; counts reported."""
    gx._reset_semaphore()
    mem_id = await _insert_memory(store, content="Pinecone ships Nexus.")
    payload = {
        "entities": [
            {"name": "Pinecone", "type": "organization",
             "description": "Vector DB", "aliases": ["PC"], "confidence": 0.9},
            {"name": "Nexus", "type": "project",
             "description": "Knowledge engine", "aliases": [], "confidence": 0.8},
        ],
        "relationships": [
            {"subject": "Pinecone", "predicate": "ships",
             "object": "Nexus", "confidence": 0.95},
        ],
    }
    result = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="Pinecone ships Nexus.", context=None,
        spawn_fn=_spawn_returning(payload),
    )
    assert result.error is None
    assert result.entities_inserted == 2
    assert result.entities_reused == 0
    assert result.mentions_inserted == 2
    assert result.relationships_inserted == 1
    # Verify the round trip via the store.
    pin = await store.find_entity_by_name_or_alias("pinecone")
    assert pin is not None
    nexus = await store.find_entity_by_name_or_alias("Nexus")
    assert nexus is not None
    mentions = await store.get_mentions_for_memory(mem_id)
    assert {m.entity_id for m in mentions} == {pin.id, nexus.id}


@pytest.mark.asyncio
async def test_extract_dedupes_by_case_insensitive_name(store: Store):
    """Pre-seeded ``Pinecone`` is reused when extraction emits ``pinecone``."""
    gx._reset_semaphore()
    mem_id = await _insert_memory(store, content="pinecone is great")
    seeded = await store.upsert_entity(
        NewEntity(name="Pinecone", entity_type="organization", description="seed")
    )
    payload = {
        "entities": [
            {"name": "pinecone", "type": "organization",
             "description": "lowercased", "aliases": [], "confidence": 0.7},
        ],
        "relationships": [],
    }
    result = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="pinecone is great", context=None,
        spawn_fn=_spawn_returning(payload),
    )
    assert result.entities_inserted == 0
    assert result.entities_reused == 1
    mentions = await store.get_mentions_for_memory(mem_id)
    assert len(mentions) == 1
    assert mentions[0].entity_id == seeded.id


@pytest.mark.asyncio
async def test_extract_dedupes_by_alias(store: Store):
    """Pre-seeded entity with alias ``PC`` matches when extraction emits ``PC``."""
    gx._reset_semaphore()
    mem_id = await _insert_memory(store, content="PC is the abbreviation")
    seeded = await store.upsert_entity(
        NewEntity(
            name="Pinecone",
            entity_type="organization",
            aliases=("PC", "Pine Cone"),
        ),
    )
    payload = {
        "entities": [
            {"name": "PC", "type": "organization",
             "description": "alias hit", "aliases": [], "confidence": 0.6},
        ],
        "relationships": [],
    }
    result = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="PC is the abbreviation", context=None,
        spawn_fn=_spawn_returning(payload),
    )
    assert result.entities_reused == 1
    assert result.entities_inserted == 0
    mentions = await store.get_mentions_for_memory(mem_id)
    assert mentions[0].entity_id == seeded.id


@pytest.mark.asyncio
async def test_extract_idempotent_replay(store: Store):
    """Running extraction twice on the same memory rewrites, doesn't double."""
    gx._reset_semaphore()
    mem_id = await _insert_memory(store, content="alpha mentions Beta")
    payload = {
        "entities": [
            {"name": "Alpha", "type": "concept", "description": "",
             "aliases": [], "confidence": 0.5},
            {"name": "Beta", "type": "concept", "description": "",
             "aliases": [], "confidence": 0.5},
        ],
        "relationships": [
            {"subject": "Alpha", "predicate": "mentions",
             "object": "Beta", "confidence": 0.6},
        ],
    }
    spawn = _spawn_returning(payload)
    r1 = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="alpha mentions Beta", context=None, spawn_fn=spawn,
    )
    r2 = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="alpha mentions Beta", context=None, spawn_fn=spawn,
    )
    assert r1.error is None
    assert r2.error is None
    # First run inserts both entities, second run reuses them.
    assert r2.entities_reused == 2
    assert r2.entities_inserted == 0
    # Mentions: still exactly 2 after the replay (replace, not double).
    mentions = await store.get_mentions_for_memory(mem_id)
    assert len(mentions) == 2


@pytest.mark.asyncio
async def test_extract_skips_relationship_with_unknown_subject(store: Store):
    """LLM occasionally references an entity it didn't declare; skip those rels."""
    gx._reset_semaphore()
    mem_id = await _insert_memory(store, content="x")
    payload = {
        "entities": [
            {"name": "Alpha", "type": "concept", "description": "",
             "aliases": [], "confidence": 0.5},
        ],
        "relationships": [
            {"subject": "Alpha", "predicate": "uses",
             "object": "UndeclaredEntity", "confidence": 0.5},
        ],
    }
    result = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="x", context=None, spawn_fn=_spawn_returning(payload),
    )
    assert result.entities_inserted == 1
    # Relationship is dropped because object isn't in name_to_id map.
    assert result.relationships_inserted == 0


# ── Failure modes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_subprocess_timeout(store: Store):
    """A communicate() that never returns is killed and reported."""
    gx._reset_semaphore()
    mem_id = await _insert_memory(store)

    class HangingProc:
        returncode = None

        def communicate(self):
            # Block forever; asyncio.wait_for will cancel the to_thread
            # call after the deadline.
            import time
            time.sleep(60)
            return b"", b""

        def kill(self):
            pass

    def spawn(_p):
        return HangingProc()

    result = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="x", context=None,
        spawn_fn=spawn, timeout=0.1,
    )
    assert result.error is not None
    assert "timeout" in result.error.lower()
    assert result.entities_inserted == 0


@pytest.mark.asyncio
async def test_extract_parse_failure(store: Store):
    """Stdout with no JSON in it returns an error, no rows persisted."""
    gx._reset_semaphore()
    mem_id = await _insert_memory(store)

    def spawn(_p):
        return _make_fake_proc(stdout=b"random non-json text")

    result = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="x", context=None, spawn_fn=spawn,
    )
    assert result.error is not None
    assert "parse" in result.error.lower()


@pytest.mark.asyncio
async def test_extract_subprocess_nonzero_exit(store: Store):
    """Non-2xx exit code is reported as the error."""
    gx._reset_semaphore()
    mem_id = await _insert_memory(store)
    spawn = _spawn_returning({"entities": [], "relationships": []}, returncode=2)
    result = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="x", context=None, spawn_fn=spawn,
    )
    assert result.error is not None
    assert "exit 2" in result.error


@pytest.mark.asyncio
async def test_extract_no_claude_on_path(store: Store, monkeypatch):
    """When `claude` isn't on PATH (and no spawn_fn given), no-op clean."""
    gx._reset_semaphore()
    mem_id = await _insert_memory(store)
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="x", context=None,
        # No spawn_fn → falls through to claude PATH check
    )
    assert result.error == "claude CLI not on PATH"


@pytest.mark.asyncio
async def test_extract_empty_extraction_persists_nothing(store: Store):
    gx._reset_semaphore()
    mem_id = await _insert_memory(store)
    result = await gx.extract_and_persist(
        store, org_id="solo", memory_id=mem_id,
        content="nothing to extract", context=None,
        spawn_fn=_spawn_returning({"entities": [], "relationships": []}),
    )
    assert result.error is None
    assert result.entities_inserted == 0
    assert result.mentions_inserted == 0
    assert result.relationships_inserted == 0


# ── Feature flag ──────────────────────────────────────────────────


class TestIsEnabled:
    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv("LORE_GRAPH_EXTRACTION_ENABLED", "true")
        assert gx.is_enabled() is True

    def test_explicit_false(self, monkeypatch):
        monkeypatch.setenv("LORE_GRAPH_EXTRACTION_ENABLED", "false")
        assert gx.is_enabled() is False

    def test_default_follows_claude_on_path(self, monkeypatch):
        monkeypatch.delenv("LORE_GRAPH_EXTRACTION_ENABLED", raising=False)
        with patch("lore.services.graph_extraction.shutil.which",
                   return_value="/fake/claude"):
            assert gx.is_enabled() is True
        with patch("lore.services.graph_extraction.shutil.which",
                   return_value=None):
            assert gx.is_enabled() is False


# ── Concurrency cap ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrency_cap_holds_under_burst(store: Store, monkeypatch):
    """20 concurrent calls with cap=2 → at most 2 spawn_fn invocations
    in flight at any moment."""
    gx._reset_semaphore()
    monkeypatch.setenv("LORE_GRAPH_EXTRACTION_CONCURRENCY", "2")

    in_flight = 0
    high_water = 0
    lock = asyncio.Lock()

    payload = {"entities": [], "relationships": []}
    line = _stream_event(text=json.dumps(payload))
    stdout = (line + "\n").encode("utf-8")

    class SlowProc:
        returncode = 0

        def communicate(self):
            nonlocal in_flight, high_water
            return stdout, b""

        def kill(self):
            pass

    async def watched_spawn_async(_p: str):
        nonlocal in_flight, high_water
        async with lock:
            in_flight += 1
            high_water = max(high_water, in_flight)
        # Yield to let other tasks pile up before we return the proc.
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return SlowProc()

    # The service expects a sync spawn_fn, so wrap our async accountant
    # in a sync shim by running it on the event loop via a pre-built
    # task. Simpler: just count sync-side and let asyncio.gather drive
    # the natural ordering — the semaphore acquire happens before spawn.
    spawned: List[Any] = []
    cap_observations: List[int] = []

    def spawn(_p: str):
        # Snapshot semaphore state at the moment spawn is called: how
        # many extract_and_persist coroutines are inside the semaphore?
        cap_observations.append(gx._sem._value if gx._sem else -1)
        spawned.append(1)
        return SlowProc()

    mem_ids = [await _insert_memory(store, content=f"m{i}") for i in range(10)]

    async def one(mid):
        return await gx.extract_and_persist(
            store, org_id="solo", memory_id=mid,
            content="x", context=None, spawn_fn=spawn,
        )

    await asyncio.gather(*(one(mid) for mid in mem_ids))

    # All 10 ran. The semaphore's `_value` (slots remaining) should
    # never go below zero — asyncio.Semaphore blocks at zero. With
    # cap=2, the only valid observed values during spawn are 0 or 1
    # (not 2: spawn always happens after one acquire).
    assert len(spawned) == 10
    for obs in cap_observations:
        assert 0 <= obs <= 1, f"cap=2 violated, observed _value={obs}"


@pytest.mark.asyncio
async def test_concurrency_default_is_two(monkeypatch):
    """When no env var is set, the default cap is 2."""
    gx._reset_semaphore()
    monkeypatch.delenv("LORE_GRAPH_EXTRACTION_CONCURRENCY", raising=False)
    sem = gx._get_semaphore()
    assert sem._value == 2


# ── Hygiene: env var validators ──────────────────────────────────


class TestEnvKnobs:
    def test_concurrency_min_one(self, monkeypatch):
        monkeypatch.setenv("LORE_GRAPH_EXTRACTION_CONCURRENCY", "0")
        assert gx._concurrency() == 1
        monkeypatch.setenv("LORE_GRAPH_EXTRACTION_CONCURRENCY", "-3")
        assert gx._concurrency() == 1

    def test_concurrency_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("LORE_GRAPH_EXTRACTION_CONCURRENCY", "abc")
        assert gx._concurrency() == 2

    def test_timeout_min_one(self, monkeypatch):
        monkeypatch.setenv("LORE_GRAPH_EXTRACTION_TIMEOUT", "0")
        assert gx._timeout_s() == 1.0

    def test_timeout_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("LORE_GRAPH_EXTRACTION_TIMEOUT", "xyz")
        assert gx._timeout_s() == 30.0
