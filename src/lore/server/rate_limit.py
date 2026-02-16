"""Pluggable rate-limit backends: memory (default) and Redis (LO-E7)."""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


class RateLimitBackend(Protocol):
    """Interface for rate-limit backends."""

    def is_allowed(self, key: str) -> Tuple[bool, int, int, int]:
        """Check if request is allowed.

        Returns (allowed, retry_after, remaining, limit).
        """
        ...

    def clear(self) -> None: ...


class MemoryBackend:
    """In-memory sliding window rate limiter (single-process)."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> Tuple[bool, int, int, int]:
        now = time.monotonic()
        window_start = now - self.window_seconds
        timestamps = self._requests.setdefault(key, [])

        # Prune old entries
        while timestamps and timestamps[0] < window_start:
            timestamps.pop(0)

        if len(timestamps) >= self.max_requests:
            retry_after = max(1, int(timestamps[0] - window_start) + 1)
            return False, retry_after, 0, self.max_requests

        timestamps.append(now)
        remaining = self.max_requests - len(timestamps)
        return True, 0, remaining, self.max_requests

    def clear(self) -> None:
        self._requests.clear()


class RedisBackend:
    """Redis sliding-window rate limiter using sorted sets."""

    def __init__(self, redis_url: str, max_requests: int = 100, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._redis_url = redis_url
        self._redis = None
        self._fallback = MemoryBackend(max_requests, window_seconds)

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis as redis_lib  # type: ignore[import-untyped]
                self._redis = redis_lib.Redis.from_url(self._redis_url, socket_connect_timeout=2, socket_timeout=2)
                self._redis.ping()
            except Exception as exc:
                logger.warning("Redis unavailable (%s), falling back to memory backend", exc)
                self._redis = None
        return self._redis

    def is_allowed(self, key: str) -> Tuple[bool, int, int, int]:
        r = self._get_redis()
        if r is None:
            # Fail-open: allow the request
            return True, 0, self.max_requests - 1, self.max_requests

        try:
            return self._check_redis(r, key)
        except Exception as exc:
            logger.warning("Redis error during rate check (%s), allowing request", exc)
            self._redis = None  # Reset connection for next attempt
            return True, 0, self.max_requests - 1, self.max_requests

    def _check_redis(self, r, key: str) -> Tuple[bool, int, int, int]:
        import time as _time

        now_ms = int(_time.time() * 1000)
        window_ms = self.window_seconds * 1000
        window_start = now_ms - window_ms
        rkey = f"rl:{key}"

        pipe = r.pipeline(True)
        pipe.zremrangebyscore(rkey, 0, window_start)
        pipe.zcard(rkey)
        pipe.execute()

        count = r.zcard(rkey)

        if count >= self.max_requests:
            # Get oldest entry to calculate retry-after
            oldest = r.zrange(rkey, 0, 0, withscores=True)
            if oldest:
                oldest_ms = int(oldest[0][1])
                retry_after = max(1, int((oldest_ms + window_ms - now_ms) / 1000) + 1)
            else:
                retry_after = 1
            return False, retry_after, 0, self.max_requests

        # Add current request
        r.zadd(rkey, {f"{now_ms}:{os.urandom(4).hex()}": now_ms})
        r.expire(rkey, self.window_seconds + 1)

        remaining = self.max_requests - count - 1
        return True, 0, max(0, remaining), self.max_requests

    def clear(self) -> None:
        r = self._get_redis()
        if r:
            try:
                for key in r.scan_iter("rl:*"):
                    r.delete(key)
            except Exception:
                pass


_backend: Optional[RateLimitBackend] = None


def get_backend() -> RateLimitBackend:
    global _backend
    if _backend is None:
        backend_type = os.environ.get("RATE_LIMIT_BACKEND", "memory").lower()
        max_req = int(os.environ.get("RATE_LIMIT_MAX", "100"))
        window = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))

        if backend_type == "redis":
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
            _backend = RedisBackend(redis_url, max_req, window)
            logger.info("Rate limiting: Redis backend (%s)", redis_url)
        else:
            _backend = MemoryBackend(max_req, window)
            logger.info("Rate limiting: memory backend")
    return _backend


def set_backend(backend: RateLimitBackend) -> None:
    global _backend
    _backend = backend
