"""Ingestion REST endpoints — /ingest, /ingest/batch, /ingest/webhook/*"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])


# -- Request/Response Models --------------------------------------------------


class IngestRequest(BaseModel):
    source: str = "raw"
    payload: Optional[Dict[str, Any]] = None
    content: Optional[str] = None
    user: Optional[str] = None
    channel: Optional[str] = None
    type: Optional[str] = None
    tags: Optional[List[str]] = None
    project: Optional[str] = None
    enrich: Optional[bool] = None
    dedup_mode: Optional[str] = None


class IngestResponse(BaseModel):
    status: str
    memory_id: Optional[str] = None
    source: Optional[str] = None
    enriched: bool = False
    dedup_check: str = "unique"
    duplicate_of: Optional[str] = None
    similarity: float = 0.0
    tracking_id: Optional[str] = None


class BatchIngestRequest(BaseModel):
    items: List[Dict[str, Any]]
    source: str = "raw"
    project: Optional[str] = None
    enrich: Optional[bool] = None
    dedup_mode: Optional[str] = None


class BatchItemResult(BaseModel):
    index: int
    status: str
    memory_id: Optional[str] = None
    duplicate_of: Optional[str] = None
    error: Optional[str] = None


class BatchIngestResponse(BaseModel):
    status: str = "batch_complete"
    total: int
    ingested: int
    duplicates_skipped: int = 0
    failed: int = 0
    results: List[BatchItemResult]


# -- Auth helpers -------------------------------------------------------------


def _extract_api_key(request: Request) -> str:
    """Extract API key from query param or Authorization header."""
    key = request.query_params.get("key", "")
    if not key:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            key = auth_header[7:]
    return key


def _get_ingest_state(request: Request):
    """Get ingestion pipeline and config from app state."""
    state = request.app.state
    if not getattr(state, "ingest_enabled", False):
        raise HTTPException(503, "Ingestion is not enabled")
    return state


def _check_auth(request: Request, source: str) -> dict:
    """Validate API key for ingest. Returns auth context dict."""
    state = _get_ingest_state(request)
    api_key = _extract_api_key(request)
    if not api_key:
        raise HTTPException(401, "API key required")

    auth_keys = getattr(state, "ingest_api_keys", {})
    key_data = auth_keys.get(api_key)
    if key_data is None:
        raise HTTPException(401, "Invalid API key")

    # Check ingest scope
    scopes = key_data.get("scopes", [])
    if scopes and "ingest" not in scopes:
        raise HTTPException(403, "Key does not have ingest scope")

    # Check source restriction
    allowed_sources = key_data.get("allowed_sources")
    if allowed_sources and source not in allowed_sources:
        raise HTTPException(403, f"Key not authorized for source: {source}")

    return key_data


def _check_rate_limit(request: Request, key_id: str, source: str, count: int = 1) -> dict:
    """Check rate limits, return headers."""
    state = request.app.state
    rate_limiter = getattr(state, "ingest_rate_limiter", None)
    if rate_limiter is None:
        return {}
    allowed, headers = rate_limiter.check(key_id, source, count=count)
    if not allowed:
        raise HTTPException(429, "Rate limit exceeded", headers=headers)
    return headers


def _get_adapter(name: str, request: Request):
    """Get adapter instance with secrets from app config."""
    from lore.ingest.adapters import get_adapter

    state = request.app.state
    secrets = getattr(state, "adapter_secrets", {}).get(name, {})
    try:
        return get_adapter(name, **secrets)
    except ValueError:
        raise HTTPException(400, f"Unknown source adapter: {name}")


def _result_status_code(status: str) -> int:
    return {
        "ingested": 201,
        "duplicate_rejected": 409,
        "duplicate_skipped": 200,
        "duplicate_merged": 200,
        "failed": 400,
        "queued": 202,
    }.get(status, 200)


# -- Endpoints ----------------------------------------------------------------


@router.post("")
async def ingest_single(req: IngestRequest, request: Request):
    """Ingest a single item from any source."""
    state = _get_ingest_state(request)

    # Determine source
    source = req.source
    if req.content is not None and req.payload is None:
        source = "raw"

    # Auth
    key_data = _check_auth(request, source)
    key_id = key_data.get("key_id", "unknown")

    # Rate limit
    rl_headers = _check_rate_limit(request, key_id, source)

    # Queue mode?
    queue = getattr(state, "ingest_queue", None)
    if queue is not None:
        from ulid import ULID

        from lore.ingest.queue import QueueItem

        # Build payload for queue
        if req.content is not None and req.payload is None:
            payload = {
                "content": req.content,
                "user": req.user,
                "channel": req.channel,
                "type": req.type or "general",
                "tags": req.tags,
            }
        else:
            payload = req.payload or {}

        item = QueueItem(
            tracking_id=str(ULID()),
            adapter_name=source,
            payload=payload,
            project=req.project or key_data.get("project"),
            dedup_mode=req.dedup_mode,
            enrich=req.enrich,
        )
        try:
            await queue.enqueue(item)
        except Exception:
            raise HTTPException(503, "Ingestion queue is full")

        return JSONResponse(
            status_code=202,
            content={"status": "queued", "tracking_id": item.tracking_id},
            headers=rl_headers,
        )

    # Build payload
    if req.content is not None and req.payload is None:
        payload = {
            "content": req.content,
            "user": req.user,
            "channel": req.channel,
            "type": req.type or "general",
            "tags": req.tags,
        }
    else:
        payload = req.payload or {}

    adapter = _get_adapter(source, request)
    project = req.project or key_data.get("project")
    pipeline = state.ingest_pipeline

    result = pipeline.ingest(
        adapter=adapter,
        payload=payload,
        project=project,
        dedup_mode=req.dedup_mode,
        enrich=req.enrich,
        extra_tags=req.tags,
    )

    status_code = _result_status_code(result.status)
    response = IngestResponse(
        status=result.status,
        memory_id=result.memory_id,
        source=source,
        enriched=result.enriched,
        dedup_check="unique" if result.memory_id else result.status,
        duplicate_of=result.duplicate_of,
        similarity=result.similarity,
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(),
        headers=rl_headers,
    )


@router.post("/batch")
async def ingest_batch(req: BatchIngestRequest, request: Request):
    """Batch ingestion — up to 100 items per request."""
    state = _get_ingest_state(request)
    batch_max = getattr(state, "ingest_batch_max", 100)

    if len(req.items) > batch_max:
        raise HTTPException(400, f"Batch size exceeds maximum of {batch_max}")
    if not req.items:
        raise HTTPException(400, "Batch items list is empty")

    key_data = _check_auth(request, req.source)
    key_id = key_data.get("key_id", "unknown")
    rl_headers = _check_rate_limit(request, key_id, req.source, count=len(req.items))

    adapter = _get_adapter(req.source, request)
    project = req.project or key_data.get("project")
    pipeline = state.ingest_pipeline

    results = []
    ingested = 0
    skipped = 0
    failed = 0

    for i, item in enumerate(req.items):
        result = pipeline.ingest(
            adapter=adapter,
            payload=item,
            project=project,
            dedup_mode=req.dedup_mode,
            enrich=req.enrich,
        )
        item_result = BatchItemResult(
            index=i,
            status=result.status,
            memory_id=result.memory_id,
            duplicate_of=result.duplicate_of,
            error=result.error,
        )
        results.append(item_result)

        if result.status == "ingested":
            ingested += 1
        elif result.status in ("duplicate_skipped", "duplicate_merged"):
            skipped += 1
        elif result.status in ("failed", "duplicate_rejected"):
            failed += 1

    # 207 if mixed, 200 if all same
    status_code = 207 if (failed > 0 and ingested > 0) else 200
    resp = BatchIngestResponse(
        total=len(req.items),
        ingested=ingested,
        duplicates_skipped=skipped,
        failed=failed,
        results=results,
    )
    return JSONResponse(
        status_code=status_code,
        content=resp.model_dump(),
        headers=rl_headers,
    )


@router.post("/webhook/{adapter_name}")
async def ingest_webhook(adapter_name: str, request: Request):
    """Adapter-specific webhook endpoint with platform signature verification."""
    state = _get_ingest_state(request)
    body = await request.body()
    headers = dict(request.headers)

    # Step 1: Get adapter + verify signature
    adapter = _get_adapter(adapter_name, request)
    if not adapter.verify(headers, body):
        raise HTTPException(401, "Webhook signature verification failed")

    # Step 2: Auth
    key_data = _check_auth(request, adapter_name)
    key_id = key_data.get("key_id", "unknown")
    _check_rate_limit(request, key_id, adapter_name)

    # Step 3: Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload")

    # Handle Slack-specific behaviors
    if adapter_name == "slack":
        from lore.ingest.adapters.slack import SlackAdapter

        if SlackAdapter.is_url_verification(payload):
            return JSONResponse(
                status_code=200,
                content={"challenge": payload.get("challenge", "")},
            )
        if SlackAdapter.is_bot_message(payload):
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "bot_message"},
            )

    # Run pipeline
    pipeline = state.ingest_pipeline
    project = key_data.get("project")

    result = pipeline.ingest(
        adapter=adapter,
        payload=payload,
        project=project,
    )

    status_code = _result_status_code(result.status)
    response = IngestResponse(
        status=result.status,
        memory_id=result.memory_id,
        source=adapter_name,
        enriched=result.enriched,
        dedup_check="unique" if result.memory_id else result.status,
        duplicate_of=result.duplicate_of,
        similarity=result.similarity,
    )
    return JSONResponse(status_code=status_code, content=response.model_dump())


@router.get("/status/{tracking_id}")
async def ingest_status(tracking_id: str, request: Request):
    """Check status of a queued ingestion item (async mode only)."""
    state = _get_ingest_state(request)
    queue = getattr(state, "ingest_queue", None)
    if queue is None:
        raise HTTPException(404, "Queue mode is not enabled")

    item = queue.get_status(tracking_id)
    if item is None:
        raise HTTPException(404, f"Tracking ID not found: {tracking_id}")

    return {
        "tracking_id": tracking_id,
        "status": item.status,
        "result": item.result,
    }
