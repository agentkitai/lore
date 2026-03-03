"""Remote HTTP store implementation for Lore memories."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from lore.memory_store.base import Store
from lore.types import Memory, SearchResult, StoreStats

try:
    import httpx
except ImportError:
    raise ImportError(
        "httpx is required for RemoteStore. "
        "Install with: pip install lore-sdk[remote]"
    )


def _response_to_memory(data: Dict[str, Any]) -> Memory:
    """Deserialize an API response dict to a Memory."""
    created_at = data.get("created_at", "")
    updated_at = data.get("updated_at", "")
    expires_at = data.get("expires_at")
    if created_at and not isinstance(created_at, str):
        created_at = str(created_at)
    if updated_at and not isinstance(updated_at, str):
        updated_at = str(updated_at)
    if expires_at and not isinstance(expires_at, str):
        expires_at = str(expires_at)

    return Memory(
        id=data["id"],
        content=data["content"],
        type=data.get("type", "note"),
        source=data.get("source"),
        project=data.get("project"),
        tags=data.get("tags", []),
        metadata=data.get("metadata", {}),
        embedding=None,
        created_at=created_at,
        updated_at=updated_at,
        expires_at=expires_at,
    )


class RemoteStore(Store):
    """HTTP-backed memory store that delegates to the Lore API server."""

    def __init__(self, api_url: str, api_key: str, timeout: float = 30.0) -> None:
        self._api_url = api_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        try:
            resp = self._client.request(
                method, path, json=json_data, params=params
            )
        except httpx.ConnectError as exc:
            raise ConnectionError(f"Cannot connect to {self._api_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise ConnectionError(f"Request timed out: {exc}") from exc

        if resp.status_code in (401, 403):
            raise PermissionError(
                f"Authentication failed ({resp.status_code}): {resp.text}"
            )
        resp.raise_for_status()
        return resp

    def save(self, memory: Memory) -> None:
        payload: Dict[str, Any] = {
            "content": memory.content,
            "type": memory.type,
            "tags": memory.tags,
            "metadata": memory.metadata,
        }
        if memory.source:
            payload["source"] = memory.source
        if memory.project:
            payload["project"] = memory.project
        if memory.expires_at:
            payload["expires_at"] = memory.expires_at

        resp = self._request("POST", "/v1/memories", json_data=payload)
        data = resp.json()
        memory.id = data["id"]

    def get(self, memory_id: str) -> Optional[Memory]:
        try:
            resp = self._request("GET", f"/v1/memories/{memory_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return _response_to_memory(resp.json())

    def search(
        self,
        embedding: List[float],
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 5,
    ) -> List[SearchResult]:
        raise NotImplementedError(
            "RemoteStore.search() with embeddings is not supported. "
            "Use search_text() instead."
        )

    def search_text(
        self,
        query: str,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 5,
    ) -> List[SearchResult]:
        """Search via the server's text-based search endpoint."""
        params: Dict[str, Any] = {"q": query, "limit": limit}
        if type:
            params["type"] = type
        if tags:
            params["tags"] = ",".join(tags)
        if project:
            params["project"] = project

        resp = self._request("GET", "/v1/memories/search", params=params)
        data = resp.json()
        results = []
        for item in data["memories"]:
            memory = _response_to_memory(item)
            results.append(SearchResult(memory=memory, score=item.get("score", 0.0)))
        return results

    def list(
        self,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        include_expired: bool = False,
    ) -> Tuple[List[Memory], int]:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if type:
            params["type"] = type
        if tags:
            params["tags"] = ",".join(tags)
        if project:
            params["project"] = project
        if include_expired:
            params["include_expired"] = "true"

        resp = self._request("GET", "/v1/memories", params=params)
        data = resp.json()
        memories = [_response_to_memory(item) for item in data["memories"]]
        return memories, data.get("total", len(memories))

    def delete(self, memory_id: str) -> bool:
        try:
            self._request("DELETE", f"/v1/memories/{memory_id}")
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return False
            raise

    def delete_by_filter(
        self,
        type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        project: Optional[str] = None,
    ) -> int:
        params: Dict[str, Any] = {"confirm": "true"}
        if type:
            params["type"] = type
        if tags:
            params["tags"] = ",".join(tags)
        if project:
            params["project"] = project

        resp = self._request("DELETE", "/v1/memories", params=params)
        return resp.json().get("deleted", 0)

    def delete_expired(self) -> int:
        resp = self._request("DELETE", "/v1/memories/expired")
        return resp.json().get("deleted", 0)

    def stats(self, project: Optional[str] = None) -> StoreStats:
        params: Dict[str, Any] = {}
        if project:
            params["project"] = project
        resp = self._request("GET", "/v1/stats", params=params)
        data = resp.json()
        return StoreStats(
            total_count=data.get("total_count", 0),
            count_by_type=data.get("count_by_type", {}),
            count_by_project=data.get("count_by_project", {}),
            oldest_memory=data.get("oldest_memory"),
            newest_memory=data.get("newest_memory"),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RemoteStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
