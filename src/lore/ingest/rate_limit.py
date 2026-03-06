"""Ingestion-specific rate limiting — wraps existing RateLimitBackend."""

from __future__ import annotations

import time
from typing import Optional, Tuple

from lore.server.rate_limit import MemoryBackend


class IngestRateLimiter:
    """Three-level rate limiting for ingestion endpoints.

    Level 1: Per API key — default 100 req/min
    Level 2: Per source adapter — default 200 req/min
    Level 3: Global — default 1000 req/min
    """

    def __init__(
        self,
        per_key_limit: int = 100,
        per_adapter_limit: int = 200,
        global_limit: int = 1000,
        window_seconds: int = 60,
    ):
        self.per_key_limit = per_key_limit
        self.per_adapter_limit = per_adapter_limit
        self.global_limit = global_limit
        self.window_seconds = window_seconds

        self._key_backends: dict = {}
        self._adapter_backends: dict = {}
        self._global_backend = MemoryBackend(
            max_requests=global_limit, window_seconds=window_seconds
        )

    def check(
        self, key_id: str, adapter_name: str, count: int = 1,
        key_rate_limit: Optional[int] = None,
    ) -> Tuple[bool, dict]:
        """Check all three rate limit levels.

        Returns (allowed, headers_dict).
        """
        key_limit = key_rate_limit or self.per_key_limit

        for _ in range(count):
            # Check per-key
            key_backend = self._key_backends.setdefault(
                key_id,
                MemoryBackend(max_requests=key_limit, window_seconds=self.window_seconds),
            )
            allowed, retry_after, remaining, limit = key_backend.is_allowed(key_id)
            if not allowed:
                return False, self._build_headers(limit, remaining, retry_after)

            # Check per-adapter
            adapter_backend = self._adapter_backends.setdefault(
                adapter_name,
                MemoryBackend(
                    max_requests=self.per_adapter_limit,
                    window_seconds=self.window_seconds,
                ),
            )
            allowed, retry_after, remaining, limit = adapter_backend.is_allowed(adapter_name)
            if not allowed:
                return False, self._build_headers(limit, remaining, retry_after)

            # Check global
            allowed, retry_after, remaining, limit = self._global_backend.is_allowed("global")
            if not allowed:
                return False, self._build_headers(limit, remaining, retry_after)

        return True, self._build_headers(key_limit, remaining, 0)

    def _build_headers(self, limit: int, remaining: int, retry_after: float) -> dict:
        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, remaining)),
            "X-RateLimit-Reset": str(int(time.time()) + int(retry_after)),
        }
        if retry_after > 0:
            headers["Retry-After"] = str(int(retry_after))
        return headers
