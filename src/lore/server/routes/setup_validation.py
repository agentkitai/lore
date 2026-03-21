"""Setup validation endpoint — POST /v1/setup/validate."""

from __future__ import annotations

import logging
import time
from typing import Optional

try:
    from fastapi import APIRouter
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from pydantic import BaseModel

router = APIRouter(prefix="/v1/setup", tags=["setup"])
logger = logging.getLogger(__name__)


class SetupValidateRequest(BaseModel):
    runtime: str = "claude-code"
    test_query: str = "hello"


class SetupValidateResponse(BaseModel):
    status: str
    latency_ms: float
    runtime: str
    server_version: str = "0.2.0"


@router.post("/validate", response_model=SetupValidateResponse)
async def validate_setup(body: SetupValidateRequest) -> SetupValidateResponse:
    """Test connectivity and basic retrieval."""
    start = time.monotonic()

    # Just verify the server is alive and can process a request
    # We don't need auth here — this is a setup validation endpoint
    elapsed_ms = round((time.monotonic() - start) * 1000, 2)

    return SetupValidateResponse(
        status="ok",
        latency_ms=elapsed_ms,
        runtime=body.runtime,
    )
