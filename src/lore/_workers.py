"""Background workers for the embedded ``AsyncLore`` lifecycle (Phase 4C).

Spec: ``docs/superpowers/specs/2026-05-05-sqlite-solo-mode-design.md``
("Worker flow" subsection). Four workers are exposed:

* :class:`RetentionWorker` — periodic ``store.expire_memories()`` sweep.
* :class:`SloWorker` — periodic SLO threshold check; on breach, hands off
  to :class:`AlertingWorker`.
* :class:`AlertingWorker` — event-driven dispatch over the alert channels
  configured on a SLO definition; records the alert via the Store.
* :class:`IngestWorker` — drains an ``asyncio.Queue`` of conversation
  job ids, running ``services.conversations.process_job_async`` for each.

All four catch + log per-iteration. A single tick failing never kills
the worker. Three consecutive failures escalate to a structured log +
``worker_consecutive_failures`` metric line; the worker keeps running.
The embedded API propagates uncaught (i.e. non-cancelled, non-handled)
worker exceptions out of ``AsyncLore.__aexit__`` so users see them at
shutdown rather than silently.

Stopping is cooperative via ``asyncio.CancelledError``: callers cancel
the task returned by :meth:`_BackgroundWorker.run_forever` (or invoke
:meth:`_BackgroundWorker.stop`) and ``await`` it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Mapping, Optional

from lore.persistence import NewSloAlert, Store, StoredSloDefinition

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)


# Number of consecutive tick failures before we emit a structured
# escalation log + metric. Per-spec: "Three consecutive failures escalate
# to a structured log + emit a metric (`worker_consecutive_failures{...}`)".
_FAILURE_ESCALATION_THRESHOLD = 3


class _BackgroundWorker:
    """Abstract async tick-based worker.

    Subclasses implement :meth:`_tick`. The :meth:`run_forever` runner
    schedules ticks every ``interval_seconds`` seconds. Per-iteration
    failures are caught and logged; ``_FAILURE_ESCALATION_THRESHOLD``
    consecutive failures emit a structured ``worker_consecutive_failures``
    log/metric line but never crash the worker. Stop is cooperative via
    :class:`asyncio.CancelledError` — call :meth:`stop` and ``await`` the
    task created from :meth:`run_forever`.
    """

    name: str = "worker"
    interval_seconds: float = 60.0

    def __init__(self) -> None:
        self._consecutive_failures = 0
        self._task: Optional[asyncio.Task[None]] = None

    async def _tick(self) -> None:
        """Run one iteration. Subclasses must override."""
        raise NotImplementedError

    async def run_forever(self) -> None:
        """Tick loop. Caught and logged per-iteration; cooperative stop."""
        try:
            while True:
                # Sleep first so we don't run an immediate tick on
                # __aenter__ — gives the rest of the lifecycle a chance
                # to settle (and matches the legacy slo_checker_loop shape).
                try:
                    await asyncio.sleep(self.interval_seconds)
                except asyncio.CancelledError:
                    raise
                try:
                    await self._tick()
                    self._consecutive_failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._consecutive_failures += 1
                    logger.warning(
                        "worker tick failed (worker=%s, consecutive_failures=%d)",
                        self.name,
                        self._consecutive_failures,
                        exc_info=True,
                    )
                    if self._consecutive_failures >= _FAILURE_ESCALATION_THRESHOLD:
                        # Structured "metric" line for ops/alerting pipelines.
                        # Format matches what the metrics layer will scrape;
                        # see _emit_failure_metric for the rationale.
                        self._emit_failure_metric()
        except asyncio.CancelledError:
            # Cooperative shutdown: re-raise so the caller knows we
            # exited via cancellation rather than internal error.
            raise

    def _emit_failure_metric(self) -> None:
        """Emit a structured ``worker_consecutive_failures`` log line.

        We don't have a metrics client wired into the embedded mode (there
        is no Prometheus exporter inside an embedded Lore), so we emit a
        well-formed log record at WARNING. Test code asserts on the log
        record fields (``extra={"worker": ..., "consecutive_failures": ...}``).
        """
        logger.warning(
            "worker_consecutive_failures{worker=%s} %d",
            self.name,
            self._consecutive_failures,
            extra={
                "metric": "worker_consecutive_failures",
                "worker": self.name,
                "consecutive_failures": self._consecutive_failures,
            },
        )

    async def stop(self) -> None:
        """Cancel and await the underlying task, if running."""
        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None


# ── RetentionWorker ─────────────────────────────────────────────────────


class RetentionWorker(_BackgroundWorker):
    """Periodically expire TTL'd memories.

    Phase 4C minimum: just calls ``store.expire_memories()``. Snapshot
    scheduling (``store.snapshot_if_due(policy)`` per the spec) is a
    TODO — the Store protocol doesn't expose that yet, and wiring it
    requires per-policy lookup that we want to defer to a later phase.
    """

    name = "retention"

    def __init__(self, store: Store, *, interval_seconds: float = 60.0) -> None:
        super().__init__()
        self._store = store
        self.interval_seconds = interval_seconds

    async def _tick(self) -> None:
        deleted = await self._store.expire_memories()
        if deleted:
            logger.info("RetentionWorker: expired %d memory rows", deleted)


# ── AlertingWorker ──────────────────────────────────────────────────────


class AlertingWorker:
    """Event-driven alert dispatcher.

    Not a tick-based worker — it's invoked by :class:`SloWorker` (or
    callers who want to dispatch a one-off alert) via :meth:`dispatch`.
    For each channel on the SLO definition we attempt delivery (best
    effort) then call ``store.record_slo_alert`` so the breach is durably
    recorded regardless of dispatch outcome.

    Webhook delivery uses ``httpx`` if installed, else falls back to
    ``urllib`` (mirrors :mod:`lore.server.alerting`). Email delivery
    requires ``smtp_host`` to be configured (either via the channel
    config or the ``SMTP_HOST`` env var); otherwise the email channel is
    a no-op so the embedded mode never blocks on missing SMTP.
    """

    name = "alerting"

    def __init__(self, store: Store) -> None:
        self._store = store

    async def dispatch(
        self,
        slo: StoredSloDefinition,
        value: float,
        *,
        status: str = "firing",
    ) -> None:
        """Dispatch an alert for an SLO breach and record it.

        Each channel attempt is wrapped — channel failures don't prevent
        the alert from being recorded (we just mark them as ``failed``
        in the ``dispatched_to`` metadata).
        """
        dispatched: list[dict[str, Any]] = []
        for channel in slo.alert_channels or ():
            channel_type = (channel.get("type") if isinstance(channel, Mapping) else None) or "unknown"
            try:
                await _dispatch_channel(channel, slo, value, status=status)
                dispatched.append({"channel": channel_type, "status": "sent"})
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "AlertingWorker: channel=%s dispatch failed for slo=%s",
                    channel_type, slo.id, exc_info=True,
                )
                dispatched.append({
                    "channel": channel_type,
                    "status": "failed",
                    "error": str(e),
                })

        try:
            await self._store.record_slo_alert(NewSloAlert(
                org_id=slo.org_id,
                slo_id=slo.id,
                metric_value=value,
                threshold=slo.threshold,
                status=status,
                dispatched_to=dispatched,
            ))
        except Exception:
            logger.warning(
                "AlertingWorker: failed to record alert for slo=%s",
                slo.id, exc_info=True,
            )


async def _dispatch_channel(
    channel: Mapping[str, Any],
    slo: StoredSloDefinition,
    value: float,
    *,
    status: str,
) -> None:
    """Route to the appropriate dispatcher based on ``channel['type']``.

    Unknown types are logged + skipped (not raised) — mirrors the legacy
    slo_checker behaviour so a typo'd channel can't crash dispatch.
    """
    channel_type = channel.get("type", "")
    if channel_type == "webhook":
        await _dispatch_webhook(channel, slo, value, status=status)
    elif channel_type == "email":
        _dispatch_email(channel, slo, value, status=status)
    else:
        logger.warning("AlertingWorker: unknown channel type %r", channel_type)


async def _dispatch_webhook(
    channel: Mapping[str, Any],
    slo: StoredSloDefinition,
    value: float,
    *,
    status: str,
) -> None:
    """POST a JSON payload to ``channel['url']``; httpx -> urllib fallback."""
    url = channel.get("url")
    if not url:
        return
    payload = {
        "slo_name": slo.name,
        "slo_id": slo.id,
        "metric": slo.metric,
        "value": value,
        "threshold": slo.threshold,
        "operator": slo.operator,
        "status": status,
    }
    try:
        import httpx  # type: ignore[import-not-found]

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except ImportError:
        # Fallback path — same shape as server/alerting.py.
        import urllib.request

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # urlopen blocks; defer to a thread so we don't block the loop.
        def _send() -> None:
            with urllib.request.urlopen(req, timeout=10):  # noqa: S310
                pass
        await asyncio.to_thread(_send)


def _dispatch_email(
    channel: Mapping[str, Any],
    slo: StoredSloDefinition,
    value: float,
    *,
    status: str,
) -> None:
    """Send an SMTP email if SMTP_HOST is configured; no-op otherwise."""
    import os

    smtp_host = channel.get("smtp_host") or os.environ.get("SMTP_HOST")
    to_addr = channel.get("email")
    if not smtp_host or not to_addr:
        logger.info("AlertingWorker: email channel skipped (smtp/email unset)")
        return

    smtp_port = int(channel.get("smtp_port") or os.environ.get("SMTP_PORT", "587"))
    smtp_user = channel.get("smtp_user") or os.environ.get("SMTP_USER", "")
    smtp_pass = channel.get("smtp_pass") or os.environ.get("SMTP_PASS", "")
    from_addr = channel.get("from_addr") or os.environ.get("SMTP_FROM", smtp_user)

    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = f"SLO Alert: {slo.name} {status}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(
        f"SLO '{slo.name}' is {status}.\n\n"
        f"Metric: {slo.metric}\n"
        f"Current value: {value}\n"
        f"Threshold: {slo.threshold} ({slo.operator})\n"
    )

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        if smtp_user:
            server.starttls()
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)


# ── SloWorker ───────────────────────────────────────────────────────────


def _check_threshold(value: Optional[float], operator: str, threshold: float) -> bool:
    """Mirror :func:`lore.services.slo._check_threshold` (no data == passing)."""
    if value is None:
        return True
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    return True


class SloWorker(_BackgroundWorker):
    """Periodically evaluate enabled SLO definitions; dispatch alerts on breach."""

    name = "slo"

    def __init__(
        self,
        store: Store,
        alerting: AlertingWorker,
        *,
        interval_seconds: float = 60.0,
    ) -> None:
        super().__init__()
        self._store = store
        self._alerting = alerting
        self.interval_seconds = interval_seconds

    async def _tick(self) -> None:
        # org_id=None == all orgs (matches services.slo.slo_status). For
        # AsyncLore's solo deployment this is just the one org row.
        slos = await self._store.list_slo_definitions(org_id=None)
        for slo in slos:
            if not slo.enabled:
                continue
            try:
                value = await self._store.compute_metric_value(
                    org_id=slo.org_id,
                    metric=slo.metric,
                    window_minutes=slo.window_minutes,
                )
            except Exception:
                logger.warning(
                    "SloWorker: compute_metric_value failed for slo=%s",
                    slo.id, exc_info=True,
                )
                continue
            passing = _check_threshold(value, slo.operator, slo.threshold)
            if not passing and value is not None:
                await self._alerting.dispatch(slo, value)


# ── IngestWorker ────────────────────────────────────────────────────────


class IngestWorker(_BackgroundWorker):
    """Drain an in-memory queue of conversation job ids.

    AsyncLore's ``add_conversation`` enqueues the freshly-created job id
    onto :attr:`queue`; this worker pulls them and runs the extraction
    pipeline via :func:`lore.services.conversations.process_job_async`.

    The queue is unbounded and the tick is a single ``queue.get()`` with
    a short timeout so cancellation stays responsive (the base class's
    cooperative cancel hits during ``asyncio.sleep``).
    """

    name = "ingest"

    def __init__(
        self,
        store: Store,
        queue: "asyncio.Queue[tuple[str, str]]",
        *,
        interval_seconds: float = 2.0,
    ) -> None:
        super().__init__()
        self._store = store
        self.queue = queue
        self.interval_seconds = interval_seconds

    async def _tick(self) -> None:
        # Drain everything currently queued so we don't trickle through
        # at the interval rate when a backlog exists. Returns immediately
        # when the queue is empty.
        from lore.services.conversations import process_job_async

        drained = 0
        while True:
            try:
                job_id, org_id = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await process_job_async(self._store, job_id, org_id)
            finally:
                self.queue.task_done()
                drained += 1
        if drained:
            logger.debug("IngestWorker: drained %d jobs", drained)
