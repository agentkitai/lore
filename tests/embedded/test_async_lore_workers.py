"""Phase 4C: ``AsyncLore`` background worker lifecycle + behaviour tests.

Covers the four workers wired in by ``AsyncLore.__aenter__``:

* :class:`RetentionWorker` — periodic ``store.expire_memories()``.
* :class:`SloWorker` — checks SLO definitions; dispatches alerts on breach.
* :class:`AlertingWorker` — dispatches to channels, records the alert.
* :class:`IngestWorker` — drains the in-memory conversation-job queue.

Tests call ``_tick()`` directly (rather than waiting on the 60s loop) to
keep the suite fast. The lifecycle test exercises the actual ``run_forever``
path to ensure tasks start + cancel cleanly.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("sqlite_vec")


# ── Fixtures ─────────────────────────────────────────────────────────


def _stub_embed(text: str) -> List[float]:
    """Deterministic 384-dim vector — keeps tests off the ONNX path."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    out: List[float] = []
    for i in range(384):
        b = digest[i % len(digest)]
        out.append(((b ^ (i * 7 & 0xFF)) - 128) / 128.0)
    return out


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


# ── Lifecycle ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workers_start_and_stop_cleanly():
    """All three loop workers start as named tasks and exit on __aexit__."""
    from lore import AsyncLore

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        names = {t.get_name() for t in asyncio.all_tasks()}
        assert "lore-retention" in names
        assert "lore-slo" in names
        assert "lore-ingest" in names
        assert lore._alerting_worker is not None
        # Worker task references are stashed on the workers too.
        assert lore._retention_worker._task is not None
        assert not lore._retention_worker._task.done()

    # After exit, no lore-* tasks should remain.
    leftover = [t for t in asyncio.all_tasks() if t.get_name().startswith("lore-")]
    assert leftover == []


@pytest.mark.asyncio
async def test_auto_workers_false_skips_lifecycle():
    """``auto_workers=False`` opts out of background tasks entirely."""
    from lore import AsyncLore

    async with AsyncLore(
        "sqlite:///:memory:", embed=_stub_embed, auto_workers=False,
    ) as lore:
        names = {t.get_name() for t in asyncio.all_tasks()}
        assert not any(n.startswith("lore-") for n in names)
        assert lore._retention_worker is None
        assert lore._alerting_worker is None
        assert lore._ingest_queue is None


# ── RetentionWorker ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retention_worker_expires_memories():
    """A memory with expires_at < now is removed by one RetentionWorker tick."""
    from lore import AsyncLore

    async with AsyncLore(
        "sqlite:///:memory:", embed=_stub_embed, auto_workers=False,
    ) as lore:
        store = lore.store
        mem = await lore.remember("expiring soon", project="p1")
        # Patch its expires_at to the past via direct SQL — list_memories
        # filters expired rows out by default, so we go around the service.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn = store._conn  # type: ignore[attr-defined]
        await conn.execute(
            "UPDATE memories SET expires_at = ? WHERE id = ?",
            (past, mem.id),
        )
        await conn.commit()

        # Sanity: the raw row still exists.
        async with conn.execute(
            "SELECT id FROM memories WHERE id = ?", (mem.id,),
        ) as cur:
            assert await cur.fetchone() is not None

        from lore._workers import RetentionWorker
        worker = RetentionWorker(store)
        await worker._tick()

        async with conn.execute(
            "SELECT id FROM memories WHERE id = ?", (mem.id,),
        ) as cur:
            assert await cur.fetchone() is None


# ── AlertingWorker ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alerting_worker_records_alert_on_dispatch(monkeypatch):
    """``dispatch`` records an slo_alerts row and reports per-channel status."""
    from lore import AsyncLore
    from lore import _workers as workers_module
    from lore._workers import AlertingWorker
    from lore.services import slo as slo_service

    async with AsyncLore(
        "sqlite:///:memory:", embed=_stub_embed, auto_workers=False,
    ) as lore:
        store = lore.store
        slo = await slo_service.create_slo(
            store,
            org_id=lore.org_id,
            name="latency",
            metric="p95_latency",
            operator="lt",
            threshold=200.0,
            alert_channels=[{"type": "webhook", "url": "https://example.com/x"}],
        )

        # Mock channel dispatch so we don't actually hit the network.
        async def _fake_webhook(channel, slo, value, *, status):  # noqa: ARG001
            return None
        monkeypatch.setattr(workers_module, "_dispatch_webhook", _fake_webhook)

        alerting = AlertingWorker(store)
        await alerting.dispatch(slo, value=420.0)

        alerts = await store.list_slo_alerts(slo_id=slo.id, limit=10)
        assert len(alerts) == 1
        assert alerts[0].metric_value == 420.0
        assert alerts[0].status == "firing"
        assert any(d.get("channel") == "webhook" for d in alerts[0].dispatched_to)


# ── SloWorker ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slo_worker_dispatches_alert_on_breach(monkeypatch):
    """A breaching SLO triggers ``alerting.dispatch`` with the right args."""
    from lore import AsyncLore
    from lore._workers import AlertingWorker, SloWorker
    from lore.services import slo as slo_service

    async with AsyncLore(
        "sqlite:///:memory:", embed=_stub_embed, auto_workers=False,
    ) as lore:
        store = lore.store
        slo = await slo_service.create_slo(
            store,
            org_id=lore.org_id,
            name="latency",
            metric="p95_latency",
            operator="lt",
            threshold=100.0,
            alert_channels=[],
        )

        # Force compute_metric_value to return a breach value.
        async def _fake_metric(*, org_id, metric, window_minutes):  # noqa: ARG001
            return 999.0
        monkeypatch.setattr(store, "compute_metric_value", _fake_metric)

        alerting = AlertingWorker(store)
        alerting.dispatch = AsyncMock()  # type: ignore[method-assign]
        worker = SloWorker(store, alerting)

        await worker._tick()

        alerting.dispatch.assert_awaited_once()
        called_slo, called_value = alerting.dispatch.await_args.args
        assert called_slo.id == slo.id
        assert called_value == 999.0


@pytest.mark.asyncio
async def test_slo_worker_no_breach_no_dispatch(monkeypatch):
    """A passing SLO must not trigger dispatch."""
    from lore import AsyncLore
    from lore._workers import AlertingWorker, SloWorker
    from lore.services import slo as slo_service

    async with AsyncLore(
        "sqlite:///:memory:", embed=_stub_embed, auto_workers=False,
    ) as lore:
        store = lore.store
        await slo_service.create_slo(
            store,
            org_id=lore.org_id,
            name="latency",
            metric="p95_latency",
            operator="lt",
            threshold=1000.0,
            alert_channels=[],
        )

        async def _fake_metric(*, org_id, metric, window_minutes):  # noqa: ARG001
            return 50.0
        monkeypatch.setattr(store, "compute_metric_value", _fake_metric)

        alerting = AlertingWorker(store)
        alerting.dispatch = AsyncMock()  # type: ignore[method-assign]
        worker = SloWorker(store, alerting)
        await worker._tick()

        alerting.dispatch.assert_not_awaited()


# ── IngestWorker ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_worker_drains_queue(monkeypatch):
    """An enqueued job id is drained and ``process_job_async`` runs."""
    from lore import AsyncLore
    from lore._workers import IngestWorker

    captured: List[tuple[str, str]] = []

    async def _fake_process(store, job_id, org_id):  # noqa: ARG001
        captured.append((job_id, org_id))

    async with AsyncLore(
        "sqlite:///:memory:", embed=_stub_embed, auto_workers=False,
    ) as lore:
        store = lore.store
        # Patch the conversations module lookup that IngestWorker uses.
        import lore.services.conversations as conv_mod
        monkeypatch.setattr(conv_mod, "process_job_async", _fake_process)

        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        await queue.put(("job-a", lore.org_id))
        await queue.put(("job-b", lore.org_id))

        worker = IngestWorker(store, queue)
        await worker._tick()

        assert sorted(captured) == [("job-a", lore.org_id), ("job-b", lore.org_id)]
        assert queue.empty()


@pytest.mark.asyncio
async def test_add_conversation_enqueues_for_ingest_worker(monkeypatch):
    """``add_conversation`` posts the new job id to the IngestWorker queue."""
    from lore import AsyncLore

    # Don't actually run extraction (no LLM in CI). We just want to see
    # that the worker pulled the job id off the queue.
    seen: List[str] = []

    async def _fake_process(store, job_id, org_id):  # noqa: ARG001
        seen.append(job_id)

    import lore.services.conversations as conv_mod
    monkeypatch.setattr(conv_mod, "process_job_async", _fake_process)

    async with AsyncLore("sqlite:///:memory:", embed=_stub_embed) as lore:
        job = await lore.add_conversation([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ])
        # Drain via direct tick (faster than waiting 2s).
        await lore._ingest_worker._tick()
        assert seen == [job.id]


# ── Failure resilience ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workers_continue_after_tick_failure(caplog):
    """A raising tick is caught + logged; 3 in a row hits the metric line."""
    from lore._workers import _BackgroundWorker

    class _Boom(_BackgroundWorker):
        name = "boom"
        interval_seconds = 0.01

        def __init__(self):
            super().__init__()
            self.calls = 0

        async def _tick(self):
            self.calls += 1
            raise RuntimeError(f"boom #{self.calls}")

    worker = _Boom()
    caplog.set_level(logging.WARNING, logger="lore._workers")

    task = asyncio.create_task(worker.run_forever())
    # Wait long enough for ~5 ticks @ 10ms each so the threshold definitely fires.
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # The worker survived and ran multiple ticks.
    assert worker.calls >= 3
    # The escalation log fired at least once.
    metric_records = [
        r for r in caplog.records
        if getattr(r, "metric", None) == "worker_consecutive_failures"
    ]
    assert metric_records, "expected at least one worker_consecutive_failures metric line"
    assert metric_records[0].worker == "boom"
    assert metric_records[0].consecutive_failures >= 3


@pytest.mark.asyncio
async def test_worker_failure_counter_resets_on_success():
    """A successful tick resets the consecutive-failure counter."""
    from lore._workers import _BackgroundWorker

    class _Flaky(_BackgroundWorker):
        name = "flaky"
        interval_seconds = 0.01

        def __init__(self):
            super().__init__()
            self.calls = 0

        async def _tick(self):
            self.calls += 1
            if self.calls in (1, 2):
                raise RuntimeError("flaky")
            # Third call succeeds.

    worker = _Flaky()
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.08)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # After the third success the counter should have been reset.
    assert worker.calls >= 3
    assert worker._consecutive_failures == 0
