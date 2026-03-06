"""HTTP store implementation — bridges SDK to Postgres-backed REST API."""

from __future__ import annotations

import os
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from lore.exceptions import LoreAuthError, LoreConnectionError
from lore.store.base import Store
from lore.types import Memory, RecallResult


class HttpStore(Store):
    """Store backend that delegates to a Lore REST API server."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: int = 2,
        verify_ssl: bool = True,
    ) -> None:
        self._api_url = (api_url or os.environ.get("LORE_API_URL", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("LORE_API_KEY", "")

        if timeout is not None:
            self._timeout = timeout
        else:
            env_timeout = os.environ.get("LORE_HTTP_TIMEOUT", "")
            self._timeout = float(env_timeout) if env_timeout else 30.0

        if not self._api_url:
            raise ValueError("api_url is required (or set LORE_API_URL)")
        if not self._api_key:
            raise ValueError("api_key is required (or set LORE_API_KEY)")

        self._max_retries = max_retries
        self._closed = False

        self._client = httpx.Client(
            base_url=self._api_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(self._timeout),
            verify=verify_ssl,
        )

        self._check_health()

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def _check_health(self) -> None:
        try:
            resp = self._client.get("/health", timeout=5.0)
            resp.raise_for_status()
        except httpx.ConnectError:
            raise LoreConnectionError(
                f"Cannot connect to Lore server at {self._api_url}. "
                "Is the server running?"
            )
        except httpx.TimeoutException:
            raise LoreConnectionError(
                f"Lore server at {self._api_url} did not respond within 5s."
            )
        except httpx.HTTPStatusError as e:
            raise LoreConnectionError(
                f"Lore server at {self._api_url} returned {e.response.status_code}."
            )

    # ------------------------------------------------------------------
    # Central HTTP dispatch with retry
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, path, **kwargs)

                if response.status_code in (401, 403):
                    msg = (
                        "Invalid API key"
                        if response.status_code == 401
                        else "Insufficient permissions"
                    )
                    raise LoreAuthError(msg)

                if response.status_code == 404:
                    return response

                if response.status_code == 422:
                    detail = response.json().get("detail", "Validation error")
                    raise ValueError(f"Server validation error: {detail}")

                if response.status_code == 429 or response.status_code >= 500:
                    last_exc = LoreConnectionError(
                        f"Server error {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                    if attempt < self._max_retries:
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                    raise last_exc

                response.raise_for_status()
                return response

            except httpx.ConnectError as e:
                last_exc = LoreConnectionError(
                    f"Cannot connect to {self._api_url}: {e}"
                )
                if attempt < self._max_retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise last_exc from e

            except httpx.TimeoutException as e:
                last_exc = LoreConnectionError(
                    f"Request timed out after {self._timeout}s"
                )
                if attempt < self._max_retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise last_exc from e

        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Field mapping helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _memory_to_lesson(memory: Memory) -> Dict[str, Any]:
        meta = dict(memory.metadata) if memory.metadata else {}
        meta["type"] = memory.type
        meta["tier"] = memory.tier

        # Deserialize embedding bytes -> List[float]
        embedding: Optional[List[float]] = None
        if memory.embedding:
            count = len(memory.embedding) // 4
            embedding = list(struct.unpack(f"{count}f", memory.embedding))

        # Compute expires_at from ttl if not already set
        expires_at = memory.expires_at
        if expires_at is None and memory.ttl is not None:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=memory.ttl)
            ).isoformat()

        payload: Dict[str, Any] = {
            "problem": memory.content,
            "resolution": memory.content,
            "context": memory.context,
            "tags": memory.tags,
            "confidence": memory.confidence,
            "source": memory.source,
            "project": memory.project,
            "meta": meta,
        }
        if embedding is not None:
            payload["embedding"] = embedding
        if expires_at is not None:
            payload["expires_at"] = expires_at

        return payload

    @staticmethod
    def _lesson_to_memory(data: Dict[str, Any]) -> Memory:
        meta = data.get("meta") or {}
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)
        meta = dict(meta)

        # Extract type and tier from meta
        mem_type = meta.pop("type", "general")
        mem_tier = meta.pop("tier", "long")

        # Store resolution in metadata if different from problem
        problem = data.get("problem", "")
        resolution = data.get("resolution", "")
        if resolution and resolution != problem:
            meta["_resolution"] = resolution

        metadata = meta if meta else None

        # Handle datetime fields — may be str or datetime objects
        def _to_iso(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, datetime):
                return val.isoformat()
            return str(val)

        return Memory(
            id=data.get("id", ""),
            content=problem,
            type=mem_type,
            tier=mem_tier,
            context=data.get("context"),
            tags=data.get("tags") or [],
            metadata=metadata,
            source=data.get("source"),
            project=data.get("project"),
            embedding=None,
            created_at=_to_iso(data.get("created_at")),
            updated_at=_to_iso(data.get("updated_at")),
            ttl=None,
            expires_at=_to_iso(data.get("expires_at")) or None,
            confidence=data.get("confidence", 1.0),
            upvotes=data.get("upvotes", 0),
            downvotes=data.get("downvotes", 0),
            importance_score=data.get("importance_score", 1.0),
            access_count=data.get("access_count", 0),
            last_accessed_at=data.get("last_accessed_at"),
        )

    # ------------------------------------------------------------------
    # Store ABC methods
    # ------------------------------------------------------------------

    def save(self, memory: Memory) -> None:
        payload = self._memory_to_lesson(memory)
        resp = self._request("POST", "/v1/lessons", json=payload)
        data = resp.json()
        memory.id = data.get("id", memory.id)

    def get(self, memory_id: str) -> Optional[Memory]:
        resp = self._request("GET", f"/v1/lessons/{memory_id}")
        if resp.status_code == 404:
            return None
        return self._lesson_to_memory(resp.json())

    def list(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Memory]:
        params: Dict[str, Any] = {}
        if project is not None:
            params["project"] = project
        if limit is not None:
            params["limit"] = limit

        resp = self._request("GET", "/v1/lessons", params=params)
        data = resp.json()
        lessons = data.get("lessons", [])
        memories = [self._lesson_to_memory(l) for l in lessons]

        # Client-side post-filter by type and tier (stored in meta)
        if type is not None:
            memories = [m for m in memories if m.type == type]
        if tier is not None:
            memories = [m for m in memories if m.tier == tier]

        return memories

    def update(self, memory: Memory) -> bool:
        payload: Dict[str, Any] = {}
        if memory.confidence is not None:
            payload["confidence"] = memory.confidence
        if memory.tags:
            payload["tags"] = memory.tags
        meta = dict(memory.metadata) if memory.metadata else {}
        meta["type"] = memory.type
        meta["tier"] = memory.tier
        payload["meta"] = meta

        resp = self._request("PATCH", f"/v1/lessons/{memory.id}", json=payload)
        return resp.status_code != 404

    def delete(self, memory_id: str) -> bool:
        resp = self._request("DELETE", f"/v1/lessons/{memory_id}")
        return resp.status_code != 404

    def count(
        self,
        project: Optional[str] = None,
        type: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> int:
        params: Dict[str, Any] = {"limit": 1}
        if project is not None:
            params["project"] = project
        resp = self._request("GET", "/v1/lessons", params=params)
        data = resp.json()
        return data.get("total", 0)

    def cleanup_expired(self) -> int:
        return 0

    # ------------------------------------------------------------------
    # Atomic vote helpers
    # ------------------------------------------------------------------

    def upvote(self, memory_id: str) -> None:
        resp = self._request(
            "PATCH", f"/v1/lessons/{memory_id}", json={"upvotes": "+1"}
        )
        if resp.status_code == 404:
            from lore.exceptions import MemoryNotFoundError
            raise MemoryNotFoundError(memory_id)

    def downvote(self, memory_id: str) -> None:
        resp = self._request(
            "PATCH", f"/v1/lessons/{memory_id}", json={"downvotes": "+1"}
        )
        if resp.status_code == 404:
            from lore.exceptions import MemoryNotFoundError
            raise MemoryNotFoundError(memory_id)

    # ------------------------------------------------------------------
    # Search (not part of Store ABC — used by Lore.recall())
    # ------------------------------------------------------------------

    def search(
        self,
        embedding: List[float],
        *,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        tier: Optional[str] = None,
        limit: int = 5,
        min_confidence: float = 0.0,
    ) -> List[RecallResult]:
        payload: Dict[str, Any] = {
            "embedding": embedding,
            "limit": limit,
            "min_confidence": min_confidence,
        }
        if tags:
            payload["tags"] = tags
        if project:
            payload["project"] = project
        if tier is not None:
            payload["tier"] = tier

        resp = self._request("POST", "/v1/lessons/search", json=payload)
        data = resp.json()
        results = []
        for item in data.get("lessons", []):
            memory = self._lesson_to_memory(item)
            results.append(RecallResult(memory=memory, score=item.get("score", 0.0)))

        # Record access for returned results (fire-and-forget, best effort)
        for r in results:
            try:
                self._request("POST", f"/v1/lessons/{r.memory.id}/access")
            except Exception:
                pass

        return results

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        if not self._closed:
            self._client.close()
            self._closed = True

    def __del__(self) -> None:
        if hasattr(self, "_closed"):
            self.close()

    def __repr__(self) -> str:
        masked = "***"
        if self._api_key and len(self._api_key) > 8:
            masked = self._api_key[:8] + "***"
        return f"HttpStore(api_url={self._api_url!r}, api_key={masked!r})"
