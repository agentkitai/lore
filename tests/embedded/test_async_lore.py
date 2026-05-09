"""Phase 4A: ``lore.AsyncLore`` round-trip + lifecycle tests.

These tests exercise the embedded async API end-to-end against an
in-memory SQLite store, covering:

* ``async with AsyncLore("sqlite:///:memory:") as lore:`` opens cleanly
  (Phase 3J bootstrap is force-run for ``:memory:`` so the solo org
  exists for service calls to attach to).
* ``remember`` -> ``get`` -> ``recall`` -> ``forget`` round-trip.
* ``list_memories`` returns inserted memories.
* The bootstrap path leaves the solo org row in place but does NOT write
  ``~/.lore/key.txt`` (in-memory runs must not touch the FS).
* ``__aexit__`` closes the underlying Store.

A lightweight stub embedder (deterministic 384-dim vectors derived from
``hash(text)``) keeps these tests off the onnxruntime download path so
they run in CI without GPU/model-cache setup.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import List

import pytest

# Skip the whole module if the optional [solo] deps aren't installed.
pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")


# ── Fixtures ─────────────────────────────────────────────────────────


def _stub_embed(text: str) -> List[float]:
    """Deterministic 384-dim vector — keeps tests off the ONNX download path.

    Builds a vector by hashing ``text`` to a seed and emitting 384 floats
    in [-1, 1]. Stable for the same input so recall() can match on it.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    # Stretch 32 bytes -> 384 floats by repeating the seed and tweaking
    # each float by its position. Good enough for distinct-vector tests.
    out: List[float] = []
    for i in range(384):
        b = digest[i % len(digest)]
        out.append(((b ^ (i * 7 & 0xFF)) - 128) / 128.0)
    return out


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Redirect ``$HOME`` so any stray bootstrap key write lands in tmp.

    The ``:memory:`` path under test must NOT write a key file; this
    fixture is a belt-and-suspenders to keep the developer's real
    ``~/.lore/key.txt`` untouched if anything regresses.
    """
    monkeypatch.setenv("HOME", str(tmp_path))


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lifecycle_open_and_close():
    """``async with AsyncLore(":memory:")`` opens, sets org_id, closes cleanly."""
    from lore import AsyncLore

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        assert lore.org_id == "solo"
        assert lore.workspace == "solo"
        # The Store should be open and usable.
        assert lore.store is not None

    # After exit, accessing the store should fail.
    with pytest.raises(RuntimeError):
        _ = lore.store


@pytest.mark.asyncio
async def test_memory_db_does_not_write_keyfile(tmp_path: Path):
    """``:memory:`` AsyncLore opens without leaving ``~/.lore/key.txt`` behind."""
    from lore import AsyncLore

    key_target = tmp_path / ".lore" / "key.txt"
    assert not key_target.exists()

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        # Sanity: org row was created by the force_for_memory bootstrap.
        async with lore.store._conn.execute(  # type: ignore[attr-defined]
            "SELECT id FROM orgs WHERE id = 'solo'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None

    assert not key_target.exists(), (
        "AsyncLore must not write key.txt for :memory: DBs"
    )


@pytest.mark.asyncio
async def test_remember_then_get_then_forget():
    """Round-trip: insert a memory, fetch it back, delete it, fetch returns None."""
    from lore import AsyncLore

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        stored = await lore.remember(
            "Always retry transient errors with exponential backoff",
            tags=["retry", "policy"],
            project="infra",
            source="test",
        )
        assert stored.id.startswith("mem_")
        assert stored.org_id == "solo"
        assert stored.project == "infra"
        assert tuple(stored.tags) == ("retry", "policy")

        fetched = await lore.get(stored.id)
        assert fetched is not None
        assert fetched.content == stored.content

        deleted = await lore.forget(stored.id)
        assert deleted is True

        gone = await lore.get(stored.id)
        assert gone is None


@pytest.mark.asyncio
async def test_recall_returns_scored_match():
    """``recall`` returns the inserted memory when querying with the same text.

    Phase 6G: these test memories are stored without a ``project`` and without
    a universal ``meta.type``, so they land at ``scope='project', project=NULL``
    and would be hidden by the default scope filter when the caller has no
    current project. ``scope_mode='all'`` is the documented opt-out.
    """
    from lore import AsyncLore

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        target = await lore.remember(
            "Use exponential backoff for HTTP 429 responses"
        )
        # Insert a couple of distractors to exercise the ordering path.
        await lore.remember("Pasta sauce recipe: tomato + basil")
        await lore.remember("Refactor the auth middleware module")

        hits = await lore.recall(
            "Use exponential backoff for HTTP 429 responses",
            k=5,
            min_score=0.0,
            scope_mode="all",
        )
        assert len(hits) >= 1
        ids = {h.id for h in hits}
        assert target.id in ids


@pytest.mark.asyncio
async def test_list_memories_returns_all_inserted():
    """``list_memories`` returns rows inserted via ``remember``."""
    from lore import AsyncLore

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        m1 = await lore.remember("alpha", project="proj-a")
        m2 = await lore.remember("beta", project="proj-a")
        m3 = await lore.remember("gamma", project="proj-b")

        all_rows = await lore.list_memories(limit=100)
        ids = {m.id for m in all_rows}
        assert {m1.id, m2.id, m3.id}.issubset(ids)

        only_a = await lore.list_memories(project="proj-a", limit=100)
        assert {m.id for m in only_a} == {m1.id, m2.id}


@pytest.mark.asyncio
async def test_explicit_embedding_skips_embedder():
    """Passing ``embedding=...`` to ``remember`` short-circuits the embedder."""
    from lore import AsyncLore

    calls: list[str] = []

    def tracking_embed(text: str) -> List[float]:
        calls.append(text)
        return _stub_embed(text)

    async with AsyncLore("sqlite:///:memory:", embed=tracking_embed) as lore:
        # Pre-built embedding -> embedder is NOT called for content.
        await lore.remember("hello", embedding=[0.0] * 384)
        assert calls == []

        # Without ``embedding=`` it IS called.
        await lore.remember("world")
        assert calls == ["world"]


@pytest.mark.asyncio
async def test_async_embedder_supported():
    """An async ``embed`` callable is awaited by AsyncLore."""
    from lore import AsyncLore

    async def async_embed(text: str) -> List[float]:
        return _stub_embed(text)

    async with AsyncLore("sqlite:///:memory:", embed=async_embed) as lore:
        m = await lore.remember("async embed works")
        assert m.id.startswith("mem_")


@pytest.mark.asyncio
async def test_use_after_close_raises():
    """Calling methods on a closed AsyncLore raises ``RuntimeError``."""
    from lore import AsyncLore

    lore = AsyncLore("sqlite:///:memory:", embed=_stub_embed)
    async with lore:
        await lore.remember("inside")
    with pytest.raises(RuntimeError):
        await lore.remember("outside")


@pytest.mark.asyncio
async def test_org_id_validation_raises_for_unknown_org(tmp_path: Path):
    """Explicit ``org_id`` that isn't in the DB raises ``ConfigError``."""
    from lore import AsyncLore
    from lore.persistence import ConfigError

    with pytest.raises(ConfigError):
        async with AsyncLore(
            "sqlite:///:memory:", embed=_stub_embed, org_id="nonexistent"
        ):
            pass  # pragma: no cover


# ── Phase 4B tests ───────────────────────────────────────────────────


class TestAsyncLoreSnapshots:
    """``save_snapshot`` round-trip + tag/meta wiring."""

    @pytest.mark.asyncio
    async def test_save_snapshot_persists_and_tags(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            snap = await lore.save_snapshot(
                "Decided to retry on 5xx but not 4xx; investigating ratelimit lib next.",
                title="Retry policy",
                session_id="sess-1234",
                tags=["retry"],
                project="infra",
            )
            assert snap.id.startswith("mem_")
            assert "session_snapshot" in snap.tags
            assert "sess-1234" in snap.tags
            assert "retry" in snap.tags
            assert (snap.meta or {}).get("session_id") == "sess-1234"
            assert (snap.meta or {}).get("type") == "session_snapshot"
            assert snap.project == "infra"

    @pytest.mark.asyncio
    async def test_save_snapshot_rejects_empty(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            with pytest.raises(ValueError):
                await lore.save_snapshot("   ")


class TestAsyncLoreTopics:
    """``list_topics`` + ``topic_detail`` over a small graph fixture."""

    @pytest.mark.asyncio
    async def test_list_topics_returns_high_mention_entities(self):
        from lore import AsyncLore
        from lore.persistence import NewEntity

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            store = lore.store
            await store.upsert_entity(
                NewEntity(name="Alpha", entity_type="topic", mention_count=5)
            )
            await store.upsert_entity(
                NewEntity(name="Beta", entity_type="topic", mention_count=1)
            )
            topics = await lore.list_topics(min_mentions=3)
            names = {t.name for t in topics}
            assert "Alpha" in names
            assert "Beta" not in names

    @pytest.mark.asyncio
    async def test_topic_detail_unknown_returns_none(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            assert await lore.topic_detail("does-not-exist") is None

    @pytest.mark.asyncio
    async def test_topic_detail_returns_entity_and_memories(self):
        from lore import AsyncLore
        from lore.persistence import NewEntity, NewMention

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            mem = await lore.remember("Talking about Alpha specifically", project="x")
            store = lore.store
            ent = await store.upsert_entity(
                NewEntity(name="Alpha", entity_type="topic", mention_count=2)
            )
            await store.save_mention(
                NewMention(entity_id=ent.id, memory_id=mem.id)
            )

            detail = await lore.topic_detail("Alpha")
            assert detail is not None
            assert detail.entity.name == "Alpha"
            assert any(m.id == mem.id for m in detail.memories)


class TestAsyncLoreRecentActivity:
    """``recent_activity`` groups by project + clamps args."""

    @pytest.mark.asyncio
    async def test_recent_activity_groups_by_project(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            await lore.remember("a1", project="alpha")
            await lore.remember("a2", project="alpha")
            await lore.remember("b1", project="beta")
            ra = await lore.recent_activity(hours=24)
            assert ra.total_count == 3
            project_counts = {g.project: g.count for g in ra.groups}
            assert project_counts.get("alpha") == 2
            assert project_counts.get("beta") == 1

    @pytest.mark.asyncio
    async def test_recent_activity_clamps_hours(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            ra = await lore.recent_activity(hours=10000)
            assert ra.hours == 168


class TestAsyncLoreReviews:
    """Pending-review listing + bulk approve/reject."""

    @pytest.mark.asyncio
    async def test_get_pending_reviews_empty(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            assert list(await lore.get_pending_reviews()) == []

    @pytest.mark.asyncio
    async def test_review_connection_and_review_all(self):
        from lore import AsyncLore
        from lore.persistence import NewEntity, NewRelationship

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            store = lore.store
            a = await store.upsert_entity(NewEntity(name="ra", entity_type="topic"))
            b = await store.upsert_entity(NewEntity(name="rb", entity_type="topic"))
            r1 = await store.save_relationship(
                NewRelationship(
                    source_entity_id=a.id, target_entity_id=b.id,
                    rel_type="uses", status="pending",
                )
            )
            r2 = await store.save_relationship(
                NewRelationship(
                    source_entity_id=a.id, target_entity_id=b.id,
                    rel_type="depends_on", status="pending",
                )
            )

            pending = await lore.get_pending_reviews()
            assert {p.id for p in pending} >= {r1.id, r2.id}

            res = await lore.review_connection(r1.id, "approve")
            assert res.status == "approved"

            updated = await lore.review_all("reject", reason="cleanup")
            # r2 was the only one still pending after r1's approve.
            assert updated >= 1

    @pytest.mark.asyncio
    async def test_review_connection_rejects_invalid_action(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            with pytest.raises(ValueError):
                await lore.review_connection("rel-x", "archive")


class TestAsyncLoreConversations:
    """Conversation-job queueing + status fetch."""

    @pytest.mark.asyncio
    async def test_add_conversation_creates_queued_job(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            job = await lore.add_conversation(
                [
                    {"role": "user", "content": "What's our retry policy?"},
                    {"role": "assistant", "content": "Exponential backoff."},
                ],
                project="infra",
                session_id="conv-1",
            )
            assert job.org_id == "solo"
            assert job.message_count == 2
            assert job.session_id == "conv-1"
            # Newly-created jobs are queued, not processed.
            assert job.status in ("queued", "pending", "accepted")

    @pytest.mark.asyncio
    async def test_conversation_status_round_trip(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            job = await lore.add_conversation(
                [{"role": "user", "content": "hi"}],
            )
            again = await lore.conversation_status(job.id)
            assert again.id == job.id

    @pytest.mark.asyncio
    async def test_add_conversation_validates_messages(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            with pytest.raises(ValueError):
                await lore.add_conversation([])


class TestAsyncLoreStats:
    """``stats`` aggregates total + by_type + oldest/newest."""

    @pytest.mark.asyncio
    async def test_stats_empty(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            s = await lore.stats()
            assert s.total == 0
            assert s.oldest is None
            assert s.newest is None

    @pytest.mark.asyncio
    async def test_stats_counts_and_extremes(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            await lore.remember("first")
            await lore.remember("second")
            await lore.remember("third")
            s = await lore.stats()
            assert s.total == 3
            assert s.oldest is not None
            assert s.newest is not None
            assert s.newest >= s.oldest
            # All inserted via remember() — no explicit type set, default
            # is sourced from meta.type or "general".
            assert sum(s.by_type.values()) == 3


class TestAsyncLoreOnThisDay:
    """``on_this_day`` filters by month/day of created_at."""

    @pytest.mark.asyncio
    async def test_on_this_day_returns_today_only(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            m = await lore.remember("created today")
            # Default = utc-now; m was just inserted so it should match.
            hits = await lore.on_this_day(limit=10)
            assert any(h.id == m.id for h in hits)

    @pytest.mark.asyncio
    async def test_on_this_day_respects_today_override(self):
        from datetime import datetime, timezone

        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            await lore.remember("just inserted")
            # Pin to Jan 1 of an arbitrary year — the inserted memory's
            # created_at is "now" so it almost certainly won't match.
            hits = await lore.on_this_day(
                today=datetime(2000, 1, 1, tzinfo=timezone.utc),
                limit=10,
            )
            assert hits == [] or all(
                h.created_at.month == 1 and h.created_at.day == 1
                for h in hits
            )


class TestAsyncLoreVoting:
    """``upvote``/``downvote`` increment counters."""

    @pytest.mark.asyncio
    async def test_upvote_increments_count(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            m = await lore.remember("vote me up")
            updated = await lore.upvote(m.id)
            assert updated.upvotes == 1
            again = await lore.upvote(m.id)
            assert again.upvotes == 2

    @pytest.mark.asyncio
    async def test_downvote_increments_count(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            m = await lore.remember("vote me down")
            updated = await lore.downvote(m.id)
            assert updated.downvotes == 1


class TestAsyncLoreConsolidationAndMaintenance:
    """Phase 4B parity stubs and maintenance helpers."""

    @pytest.mark.asyncio
    async def test_consolidate_returns_noop_report(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            report = await lore.consolidate(project="x", dry_run=True)
            assert report.dry_run is True
            assert report.groups_found == 0
            assert "no-op" in report.note

    @pytest.mark.asyncio
    async def test_get_consolidation_log_empty_in_4b(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            log = await lore.get_consolidation_log()
            assert list(log) == []

    @pytest.mark.asyncio
    async def test_cleanup_expired_returns_int(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            count = await lore.cleanup_expired()
            assert count == 0

class TestAsyncLoreEnrichment:
    """``enrich_memories`` short-circuits on already-enriched rows."""

    @pytest.mark.asyncio
    async def test_enrich_memories_skips_already_enriched(self, monkeypatch):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            await lore.remember(
                "already enriched",
                meta={"enrichment": {"topics": ["x"]}},
            )
            # Stub the LLM-touching path — if enrich_memory_async is invoked
            # we'd need network access; this test should never hit it.
            from lore.services import memories as memories_service

            async def boom(*_a, **_kw):  # pragma: no cover - guard
                raise AssertionError(
                    "enrich_memory_async should not be called for already-enriched rows"
                )

            monkeypatch.setattr(memories_service, "enrich_memory_async", boom)
            report = await lore.enrich_memories(limit=10)
            assert report.skipped == 1
            assert report.enriched == 0

    @pytest.mark.asyncio
    async def test_enrich_memories_invokes_pipeline_on_unenriched(self, monkeypatch):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            await lore.remember("plain memory")

            calls: list[str] = []

            async def fake_enrich(_store, *, memory_id, content, context):
                calls.append(memory_id)

            from lore.services import memories as memories_service
            monkeypatch.setattr(
                memories_service, "enrich_memory_async", fake_enrich
            )

            report = await lore.enrich_memories(limit=10)
            assert report.enriched == 1
            assert report.skipped == 0
            assert len(calls) == 1


class TestAsyncLoreClassifyAndPrompt:
    """``classify`` (rule-based) and ``as_prompt`` formatting."""

    @pytest.mark.asyncio
    async def test_classify_returns_classification(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            cls = await lore.classify("Always use exponential backoff for 429s.")
            assert cls.intent
            assert cls.domain
            assert cls.emotion

    @pytest.mark.asyncio
    async def test_as_prompt_empty_when_no_hits(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            out = await lore.as_prompt("anything", limit=3)
            assert out == ""

    @pytest.mark.asyncio
    async def test_as_prompt_renders_recall_hits(self):
        from lore import AsyncLore

        async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
            await lore.remember("Use exponential backoff for HTTP 429 responses")
            # Phase 6G: this no-project / no-type memory lands at
            # scope='project'; opt into cross-project recall to retrieve it
            # without spinning up a project context.
            out = await lore.as_prompt(
                "Use exponential backoff for HTTP 429 responses",
                limit=3,
                scope_mode="all",
            )
            assert isinstance(out, str)
            assert len(out) > 0
            # The default xml format wraps content in tags.
            assert "<" in out


@pytest.mark.asyncio
async def test_filebacked_db_bootstrap_default_path(tmp_path: Path, monkeypatch):
    """File-backed AsyncLore uses the standard Phase 3J bootstrap path.

    Subtle but important: file-backed DBs don't take the
    ``force_for_memory`` route — the SqliteStore.open() bootstrap fires
    in the factory and writes the key file to ``~/.lore/key.txt``.
    AsyncLore should still find the solo org and work normally.
    """
    from lore import AsyncLore

    db_path = tmp_path / "lore.db"
    monkeypatch.setenv("HOME", str(tmp_path))

    async with AsyncLore(
        f"sqlite:///{db_path}", embed=_stub_embed
    ) as lore:
        m = await lore.remember("file-backed roundtrip")
        fetched = await lore.get(m.id)
        assert fetched is not None
        assert fetched.content == "file-backed roundtrip"

    # The default bootstrap path writes ``~/.lore/key.txt``.
    expected = tmp_path / ".lore" / "key.txt"
    assert expected.exists()
    # Sanity: 0600.
    import stat
    mode = stat.S_IMODE(os.stat(expected).st_mode)
    assert mode == 0o600
