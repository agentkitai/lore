"""Idle-timeout watcher for ``lore serve --idle-timeout`` (lazy mode).

When ``LORE_IDLE_TIMEOUT`` is set to a positive integer, the FastAPI app
installs a ``LastRequestTracker`` middleware that updates a global
``_last_request_at`` (monotonic-clock seconds) on every request — and a
background task spawned in ``lifespan()`` ticks every ``CHECK_INTERVAL``
seconds and calls ``os._exit(0)`` once the idle window is exceeded.

This is the server-side half of the lazy-server design: hooks spawn
``lore serve --idle-timeout 3600`` on demand, the server self-exits an
hour after the last request, and the next hook fire respawns it. Default
behavior (no env var, no flag) is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time_module  # patched as idle._monotonic in tests
from typing import Callable

# Indirection so tests can replace ``idle._monotonic`` without poking the
# real ``time.monotonic`` (which would also slow down asyncio's own
# scheduler). Production code goes through this indirection too.
_monotonic = _time_module.monotonic
time = _time_module  # back-compat alias for any external readers

try:
    from fastapi import Request, Response
    from starlette.middleware.base import BaseHTTPMiddleware
except ImportError:
    raise ImportError(
        "FastAPI is required. Install with: pip install lore-sdk[server]"
    )

logger = logging.getLogger(__name__)

# How often the watcher wakes up to check the idle window. 60s is plenty
# given the timeout itself is in minutes/hours.
CHECK_INTERVAL = 60.0

# Sentinel updated by LastRequestTracker; read by the watcher loop. Using
# time.monotonic() so wall-clock skew (NTP, DST) can't double-count.
_last_request_at: float = 0.0


def _touch() -> None:
    """Mark "now" as the most recent request timestamp."""
    global _last_request_at
    _last_request_at = _monotonic()


def _seconds_since_last_request() -> float:
    """Return the elapsed seconds since the last request was observed."""
    return _monotonic() - _last_request_at


def reset_for_tests() -> None:
    """Test-only: snap the sentinel back to "now" so tests start fresh."""
    _touch()


class LastRequestTracker(BaseHTTPMiddleware):
    """Update the idle sentinel on every request, including ``/health``.

    We deliberately count health checks: the hook-side ensure-server
    helper polls /health every time the user is active, so treating that
    poll as a keep-alive correctly maps "user is working in Claude Code"
    to "don't auto-exit." Otherwise an active user with no retrieval hits
    would still get their server killed underneath them.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        _touch()
        return await call_next(request)


async def idle_watcher_loop(
    idle_timeout: float,
    *,
    check_interval: float = CHECK_INTERVAL,
    exit_fn: Callable[[int], None] | None = None,
) -> None:
    """Background task: sleep ``check_interval`` seconds, then check.

    Once ``time.monotonic() - _last_request_at >= idle_timeout``, log and
    invoke ``exit_fn(0)`` (defaults to ``os._exit``). We use ``os._exit``
    over ``sys.exit`` because uvicorn's signal handling can swallow
    ``SystemExit`` raised from a background task; ``_exit`` bypasses that
    and tears down the worker cleanly.

    ``exit_fn`` is injected so tests can assert the call without the
    process actually exiting.
    """
    if exit_fn is None:
        exit_fn = os._exit

    # Initialize the sentinel at startup so a server with zero traffic
    # also exits after idle_timeout (instead of staying up forever
    # because _last_request_at == 0 means "never seen a request, so the
    # delta is huge" — we want the delta to be sane from t=0). Only
    # touch when the sentinel is still at the cold default; if a test
    # or a request has already advanced it, leave it alone so the
    # caller's "this server is already stale" intent is preserved.
    global _last_request_at
    if _last_request_at == 0.0:
        _touch()
    logger.info("idle-timeout watcher started: %ds", int(idle_timeout))

    while True:
        try:
            await asyncio.sleep(check_interval)
        except asyncio.CancelledError:
            return

        elapsed = _seconds_since_last_request()
        if elapsed >= idle_timeout:
            logger.warning(
                "idle-timeout exit: %ds idle (limit %ds)",
                int(elapsed),
                int(idle_timeout),
            )
            exit_fn(0)
            # exit_fn is os._exit in production (no return); in tests it's
            # a mock — break so we don't busy-loop forever after the call.
            return


def get_configured_timeout() -> int:
    """Return the configured idle-timeout in seconds.

    Read order: ``LORE_IDLE_TIMEOUT`` env var, then 0 (disabled). The CLI
    layer is responsible for passing ``--idle-timeout`` through as an env
    var before ``uvicorn.run`` so this helper sees a single source of
    truth from inside the FastAPI lifespan.
    """
    raw = os.environ.get("LORE_IDLE_TIMEOUT", "0")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0
