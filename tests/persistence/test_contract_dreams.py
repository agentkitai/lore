"""Contract tests for the DreamOps slice of Store (Phase 6E).

Covers ``start_dream`` / ``complete_dream`` / ``fail_dream`` /
``get_last_dream_run`` / ``count_distinct_sessions_since`` round-trips
across both backends. Eligibility math + status snapshots are also
exercised here since they read straight off the Store and we want
parametrized PG + SQLite coverage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lore.persistence import Store
from lore.persistence.types import DreamRun, NewDreamRun, NewMemory
from lore.services import dreams as dream_svc

# ── Lifecycle round-trip ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_dream_returns_running_row(store: Store):
    run = await store.start_dream(NewDreamRun(org_id="solo"))
    assert isinstance(run, DreamRun)
    assert run.org_id == "solo"
    assert run.status == "running"
    assert run.completed_at is None
    assert run.summary is None
    assert run.error is None
    assert run.id  # ULID assigned


@pytest.mark.asyncio
async def test_complete_dream_round_trip(store: Store):
    run = await store.start_dream(NewDreamRun(org_id="solo"))
    summary = {
        "phase_3_merged": 4,
        "phase_3_promoted": 1,
        "phase_4_pruned": 7,
    }
    await store.complete_dream(run.id, summary)
    last = await store.get_last_dream_run("solo")
    assert last is not None
    assert last.id == run.id
    assert last.status == "completed"
    assert last.completed_at is not None
    assert dict(last.summary or {}) == summary
    assert last.error is None


@pytest.mark.asyncio
async def test_fail_dream_round_trip(store: Store):
    run = await store.start_dream(NewDreamRun(org_id="solo"))
    await store.fail_dream(run.id, "claude binary missing")
    last = await store.get_last_dream_run("solo")
    assert last is not None
    assert last.status == "failed"
    assert last.error == "claude binary missing"
    assert last.completed_at is not None


@pytest.mark.asyncio
async def test_get_last_dream_run_missing_returns_none(store: Store):
    result = await store.get_last_dream_run("solo")
    assert result is None


@pytest.mark.asyncio
async def test_get_last_dream_run_orders_by_started_desc(store: Store):
    first = await store.start_dream(NewDreamRun(org_id="solo"))
    await store.complete_dream(first.id, {"n": 1})
    second = await store.start_dream(NewDreamRun(org_id="solo"))
    last = await store.get_last_dream_run("solo")
    assert last is not None
    assert last.id == second.id


@pytest.mark.asyncio
async def test_get_last_dream_run_org_isolation(store: Store):
    run = await store.start_dream(NewDreamRun(org_id="org_a"))
    await store.complete_dream(run.id, {"n": 1})
    # 'org_b' has no runs.
    assert await store.get_last_dream_run("org_b") is None
    # 'org_a' sees its own.
    last = await store.get_last_dream_run("org_a")
    assert last is not None and last.id == run.id


# ── Session counting ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_distinct_sessions_empty(store: Store):
    since = datetime.now(timezone.utc) - timedelta(days=1)
    n = await store.count_distinct_sessions_since("solo", since)
    assert n == 0


@pytest.mark.asyncio
async def test_count_distinct_sessions_dedups_by_session_id(store: Store):
    """Insert 5 memories: 3 unique sessions + 2 duplicates → count = 3."""
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    for sid in ("s1", "s1", "s2", "s2", "s3"):
        await store.insert_memory(NewMemory(
            org_id="solo",
            content=f"memory in {sid}",
            embedding=[0.1] * 384,
            meta={"session_id": sid, "type": "observation"},
        ))
    n = await store.count_distinct_sessions_since("solo", since)
    assert n == 3


@pytest.mark.asyncio
async def test_count_distinct_sessions_ignores_no_session_id(store: Store):
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    # One memory with session_id, two without.
    await store.insert_memory(NewMemory(
        org_id="solo", content="with sid", embedding=[0.1] * 384,
        meta={"session_id": "s1"},
    ))
    await store.insert_memory(NewMemory(
        org_id="solo", content="no sid", embedding=[0.1] * 384,
        meta={},
    ))
    await store.insert_memory(NewMemory(
        org_id="solo", content="no sid 2", embedding=[0.1] * 384,
        meta={"type": "observation"},
    ))
    n = await store.count_distinct_sessions_since("solo", since)
    assert n == 1


@pytest.mark.asyncio
async def test_count_distinct_sessions_org_isolation(store: Store):
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    await store.insert_memory(NewMemory(
        org_id="org_a", content="a1", embedding=[0.1] * 384,
        meta={"session_id": "sA"},
    ))
    await store.insert_memory(NewMemory(
        org_id="org_b", content="b1", embedding=[0.1] * 384,
        meta={"session_id": "sB"},
    ))
    assert await store.count_distinct_sessions_since("org_a", since) == 1
    assert await store.count_distinct_sessions_since("org_b", since) == 1


# ── Service-layer eligibility (parametrized over backends) ─────────


@pytest.mark.asyncio
async def test_eligibility_no_prior_run(store: Store):
    assert await dream_svc.is_dream_eligible(store, "solo") is True


@pytest.mark.asyncio
async def test_eligibility_running_blocks(store: Store):
    await store.start_dream(NewDreamRun(org_id="solo"))
    assert await dream_svc.is_dream_eligible(store, "solo") is False


@pytest.mark.asyncio
async def test_eligibility_recent_completion_blocks(store: Store):
    run = await store.start_dream(NewDreamRun(org_id="solo"))
    await store.complete_dream(run.id, {})
    assert await dream_svc.is_dream_eligible(store, "solo") is False


@pytest.mark.asyncio
async def test_eligibility_24h_elapsed_no_sessions_blocks(store: Store):
    run = await store.start_dream(NewDreamRun(org_id="solo"))
    await store.complete_dream(run.id, {})
    future = datetime.now(timezone.utc) + timedelta(hours=25)
    assert await dream_svc.is_dream_eligible(
        store, "solo", now=future,
    ) is False


@pytest.mark.asyncio
async def test_eligibility_24h_elapsed_with_5_sessions_passes(store: Store):
    """24h elapsed AND 5 distinct sessions captured → eligible."""
    run = await store.start_dream(NewDreamRun(org_id="solo"))
    await store.complete_dream(run.id, {})
    # 5 memories across 5 distinct session_ids (created NOW > started_at).
    for i in range(5):
        await store.insert_memory(NewMemory(
            org_id="solo",
            content=f"memory {i}",
            embedding=[0.1] * 384,
            meta={"session_id": f"s{i}"},
        ))
    future = datetime.now(timezone.utc) + timedelta(hours=25)
    assert await dream_svc.is_dream_eligible(
        store, "solo", now=future,
    ) is True


# ── Status snapshot shape ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_cold_db(store: Store):
    s = await dream_svc.get_status(store, "solo")
    assert s["last_run_at"] is None
    assert s["last_run_status"] is None
    assert s["sessions_required"] == 5
    assert s["interval_hours"] == 24
    assert s["eligible_now"] is True


@pytest.mark.asyncio
async def test_status_after_completed_run(store: Store):
    run = await store.start_dream(NewDreamRun(org_id="solo"))
    await store.complete_dream(run.id, {"phase_3_merged": 2})
    s = await dream_svc.get_status(store, "solo")
    assert s["last_run_at"] is not None
    assert s["last_run_status"] == "completed"
    assert s["last_summary"] == {"phase_3_merged": 2}
    assert s["eligible_now"] is False


@pytest.mark.asyncio
async def test_status_after_failed_run_surfaces_error(store: Store):
    run = await store.start_dream(NewDreamRun(org_id="solo"))
    await store.fail_dream(run.id, "boom")
    s = await dream_svc.get_status(store, "solo")
    assert s["last_run_status"] == "failed"
    assert s["last_error"] == "boom"
