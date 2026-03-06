"""In-process async queue for burst ingestion."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    tracking_id: str
    adapter_name: str
    payload: dict
    project: Optional[str] = None
    dedup_mode: Optional[str] = None
    enrich: Optional[bool] = None
    status: str = "queued"
    result: Optional[dict] = None


class IngestionQueue:
    """Async in-process queue for decoupling request acceptance from processing.

    When enabled, POST /ingest returns 202 Accepted immediately with a tracking_id.
    Background workers process items sequentially.

    This is in-process only (asyncio.Queue). Queued items are lost on server restart.
    """

    def __init__(self, max_size: int = 1000, workers: int = 2):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._workers = workers
        self._items: dict = {}
        self._tasks: list = []

    async def start(self, pipeline: object, adapter_secrets: dict) -> None:
        """Start worker tasks."""
        for i in range(self._workers):
            task = asyncio.create_task(self._worker(pipeline, adapter_secrets, i))
            self._tasks.append(task)

    async def stop(self) -> None:
        """Signal workers to stop and wait for them."""
        for _ in self._tasks:
            await self._queue.put(None)
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def enqueue(self, item: QueueItem) -> str:
        """Add item to queue. Raises asyncio.QueueFull if full."""
        self._items[item.tracking_id] = item
        try:
            self._queue.put_nowait(item.tracking_id)
        except asyncio.QueueFull:
            del self._items[item.tracking_id]
            raise
        return item.tracking_id

    def get_status(self, tracking_id: str) -> Optional[QueueItem]:
        return self._items.get(tracking_id)

    async def _worker(self, pipeline: object, adapter_secrets: dict, worker_id: int) -> None:
        """Process queued items one at a time."""
        while True:
            tracking_id = await self._queue.get()
            if tracking_id is None:
                break

            item = self._items.get(tracking_id)
            if not item:
                self._queue.task_done()
                continue

            item.status = "processing"
            try:
                from lore.ingest.adapters import get_adapter

                adapter = get_adapter(
                    item.adapter_name,
                    **adapter_secrets.get(item.adapter_name, {}),
                )
                result = pipeline.ingest(
                    adapter=adapter,
                    payload=item.payload,
                    project=item.project,
                    dedup_mode=item.dedup_mode,
                    enrich=item.enrich,
                )
                item.status = "done"
                item.result = {
                    "status": result.status,
                    "memory_id": result.memory_id,
                    "enriched": result.enriched,
                }
            except Exception as e:
                logger.error("Queue worker %d failed: %s", worker_id, e, exc_info=True)
                item.status = "failed"
                item.result = {"status": "failed", "error": str(e)}
            finally:
                self._queue.task_done()
