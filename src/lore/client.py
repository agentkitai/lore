"""Hardened async SDK client for Lore server.

Enterprise-grade client with retry, graceful degradation, connection pooling,
and optional batched saves.

Usage::

    async with LoreClient() as client:
        lesson_id = await client.save(problem="...", resolution="...")
        results = await client.recall("how to handle rate limits")
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    raise ImportError(
        "httpx is required for LoreClient. "
        "Install with: pip install lore-sdk[remote]"
    )

logger = logging.getLogger("lore.client")

_DEFAULT_URL = "http://localhost:8765"
_DEFAULT_TIMEOUT = 5.0
_RETRY_BACKOFFS = [0.5, 1.0, 2.0]  # 3 retries
_RETRYABLE_STATUS = {500, 502, 503, 504}
_BATCH_FLUSH_INTERVAL = 5.0  # seconds
_BATCH_FLUSH_SIZE = 10  # items


class LoreClient:
    """Hardened async client for Lore server.

    Parameters:
        url: Lore server URL. Defaults to ``LORE_URL`` env var or ``http://localhost:8765``.
        api_key: API key. Defaults to ``LORE_API_KEY`` env var.
        org_id: Organization ID. Defaults to ``LORE_ORG_ID`` env var.
        timeout: Request timeout in seconds. Defaults to ``LORE_TIMEOUT`` env var or 5.
        batch: If True, buffer ``save()`` calls and flush periodically.
        batch_size: Flush after this many buffered items (default 10).
        batch_interval: Flush every N seconds (default 5.0).
    """

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        org_id: Optional[str] = None,
        timeout: Optional[float] = None,
        batch: bool = False,
        batch_size: int = _BATCH_FLUSH_SIZE,
        batch_interval: float = _BATCH_FLUSH_INTERVAL,
    ) -> None:
        self._url = (url or os.environ.get("LORE_URL", _DEFAULT_URL)).rstrip("/")
        self._api_key = api_key or os.environ.get("LORE_API_KEY", "")
        self._org_id = org_id or os.environ.get("LORE_ORG_ID", "")

        _timeout_raw = timeout if timeout is not None else os.environ.get("LORE_TIMEOUT")
        self._timeout = float(_timeout_raw) if _timeout_raw is not None else _DEFAULT_TIMEOUT

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._org_id:
            headers["X-Org-Id"] = self._org_id

        # Connection-pooled async client (reused across calls)
        self._http = httpx.AsyncClient(
            base_url=self._url,
            headers=headers,
            timeout=self._timeout,
        )

        # Batching
        self._batch_enabled = batch
        self._batch_size = batch_size
        self._batch_interval = batch_interval
        self._batch_buffer: List[Dict[str, Any]] = []
        self._batch_lock: Optional[asyncio.Lock] = None
        self._batch_task: Optional[asyncio.Task[None]] = None

    async def __aenter__(self) -> "LoreClient":
        if self._batch_enabled:
            self._batch_lock = asyncio.Lock()
            self._batch_task = asyncio.create_task(self._batch_flusher())
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Flush pending batches and close the HTTP client."""
        if self._batch_task is not None:
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
        if self._batch_buffer:
            await self._flush_batch()
        await self._http.aclose()

    # ── Core API ──────────────────────────────────────────────────────

    async def save(
        self,
        problem: str,
        resolution: str,
        context: Optional[str] = None,
        tags: Optional[List[str]] = None,
        confidence: float = 0.5,
        source: Optional[str] = None,
        project: Optional[str] = None,
    ) -> Optional[str]:
        """Save a lesson. Returns lesson ID, or None if server is unreachable.

        Never raises on connection/server errors — logs a warning and returns None.
        """
        payload: Dict[str, Any] = {
            "problem": problem,
            "resolution": resolution,
            "context": context,
            "tags": tags or [],
            "confidence": confidence,
            "source": source,
            "project": project,
        }

        if self._batch_enabled:
            assert self._batch_lock is not None
            async with self._batch_lock:
                self._batch_buffer.append(payload)
                if len(self._batch_buffer) >= self._batch_size:
                    await self._flush_batch()
            return None  # Batched saves don't return IDs immediately

        return await self._do_save(payload)

    async def recall(
        self,
        query: str,
        tags: Optional[List[str]] = None,
        limit: int = 5,
        min_confidence: float = 0.0,
        project: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recall lessons by semantic query. Returns list of results, or empty list on failure.

        Never raises on connection/server errors — logs a warning and returns [].
        """
        payload: Dict[str, Any] = {
            "query": query,
            "limit": limit,
            "min_confidence": min_confidence,
        }
        if tags:
            payload["tags"] = tags
        if project:
            payload["project"] = project

        try:
            resp = await self._request_with_retry("POST", "/v1/lessons/recall", json_data=payload)
            data = resp.json()
            return data.get("lessons", [])
        except Exception:
            logger.warning("Lore recall failed — returning empty list", exc_info=True)
            return []

    # ── Internals ─────────────────────────────────────────────────────

    async def _do_save(self, payload: Dict[str, Any]) -> Optional[str]:
        """Execute a single save request with graceful degradation."""
        try:
            resp = await self._request_with_retry("POST", "/v1/lessons", json_data=payload)
            data = resp.json()
            return data.get("id")
        except Exception:
            logger.warning("Lore save failed — returning None", exc_info=True)
            return None

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        json_data: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """Make HTTP request with exponential backoff retry on 5xx / connection errors."""
        last_exc: Optional[Exception] = None

        for attempt in range(1 + len(_RETRY_BACKOFFS)):
            try:
                resp = await self._http.request(
                    method, path, json=json_data, params=params
                )
                if resp.status_code in _RETRYABLE_STATUS and attempt < len(_RETRY_BACKOFFS):
                    logger.warning(
                        "Lore server returned %d on attempt %d, retrying in %.1fs",
                        resp.status_code, attempt + 1, _RETRY_BACKOFFS[attempt],
                    )
                    await asyncio.sleep(_RETRY_BACKOFFS[attempt])
                    continue
                resp.raise_for_status()
                return resp
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ConnectTimeout) as exc:
                last_exc = exc
                if attempt < len(_RETRY_BACKOFFS):
                    logger.warning(
                        "Lore connection error on attempt %d: %s, retrying in %.1fs",
                        attempt + 1, exc, _RETRY_BACKOFFS[attempt],
                    )
                    await asyncio.sleep(_RETRY_BACKOFFS[attempt])
                    continue
                raise
            except httpx.HTTPStatusError:
                raise

        # Should not reach here, but just in case
        if last_exc:
            raise last_exc
        raise RuntimeError("Retry loop exhausted unexpectedly")

    async def _flush_batch(self) -> None:
        """Flush the batch buffer by saving each item."""
        if not self._batch_buffer:
            return
        items = list(self._batch_buffer)
        self._batch_buffer.clear()
        for payload in items:
            await self._do_save(payload)

    async def _batch_flusher(self) -> None:
        """Background task that periodically flushes the batch buffer."""
        try:
            while True:
                await asyncio.sleep(self._batch_interval)
                if self._batch_lock and self._batch_buffer:
                    async with self._batch_lock:
                        await self._flush_batch()
        except asyncio.CancelledError:
            return
