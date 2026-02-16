"""Rate limiting and error handling middleware for Lore Cloud Server."""

from __future__ import annotations

import logging
import re
import time
from typing import Callable

try:
    from fastapi import FastAPI, Request, Response
    from fastapi.responses import JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

logger = logging.getLogger(__name__)

# ── Rate Limiter ───────────────────────────────────────────────────

# Default: 100 requests per 60 seconds
DEFAULT_RATE_LIMIT = 100
DEFAULT_WINDOW_SECONDS = 60


class RateLimiter:
    """In-memory sliding window rate limiter per API key.

    Legacy wrapper — delegates to rate_limit.MemoryBackend.
    Kept for backward compatibility.
    """

    def __init__(
        self,
        max_requests: int = DEFAULT_RATE_LIMIT,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        from lore.server.rate_limit import MemoryBackend
        self._backend = MemoryBackend(max_requests, window_seconds)
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def is_allowed(self, key: str) -> tuple[bool, int]:
        allowed, retry_after, _remaining, _limit = self._backend.is_allowed(key)
        return allowed, retry_after

    def clear(self) -> None:
        self._backend.clear()


def set_rate_limiter(limiter: RateLimiter) -> None:
    """Replace the global rate limiter (for testing/config).

    Installs the underlying backend into the rate_limit module.
    """
    from lore.server.rate_limit import set_backend
    set_backend(limiter._backend)


# ── Path normalization ─────────────────────────────────────────────

# Patterns that look like dynamic path segments (UUIDs, hex IDs, numeric IDs)
_DYNAMIC_SEGMENT_RE = re.compile(
    r"""
    (?:^|/)                        # start or slash
    (?:
        [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}  # UUID
      | [0-9a-f]{24,}             # long hex (MongoDB ObjectId, etc.)
      | [0-9]+                     # numeric ID
    )
    (?=/|$)                        # followed by slash or end
    """,
    re.VERBOSE | re.IGNORECASE,
)


def normalize_path(path: str) -> str:
    """Replace dynamic path segments (UUIDs, hex IDs, numeric IDs) with :id.

    Keeps the leading slash of each segment intact.
    Examples:
        /v1/lessons/abc123def456abc123def456 -> /v1/lessons/:id
        /v1/lessons/550e8400-e29b-41d4-a716-446655440000 -> /v1/lessons/:id
        /v1/orgs/42/lessons -> /v1/orgs/:id/lessons
    """
    # Split on / and replace dynamic segments
    parts = path.split("/")
    normalized = []
    for part in parts:
        if not part:
            normalized.append(part)
            continue
        # UUID
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", part, re.IGNORECASE):
            normalized.append(":id")
        # Long hex (24+ chars, e.g. MongoDB ObjectId)
        elif re.fullmatch(r"[0-9a-f]{24,}", part, re.IGNORECASE):
            normalized.append(":id")
        # Pure numeric
        elif re.fullmatch(r"[0-9]+", part):
            normalized.append(":id")
        else:
            normalized.append(part)
    return "/".join(normalized)


# ── Middleware ─────────────────────────────────────────────────────

# Max request body size: 1MB
MAX_BODY_SIZE = 1_048_576


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Add request ID and structured logging context, collect HTTP metrics."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        import uuid
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = request_id

        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        response.headers["X-Request-Id"] = request_id

        # Collect HTTP metrics (skip /metrics and /health to avoid noise)
        path = request.url.path
        if path not in ("/metrics", "/health"):
            try:
                from lore.server.config import settings as _s
                from lore.server.metrics import http_request_duration, http_requests_total
                if _s.metrics_enabled:
                    normalized = normalize_path(path)
                    http_requests_total.inc(method=request.method, path=normalized, status=str(response.status_code))
                    http_request_duration.observe(duration, method=request.method, path=normalized)
            except Exception:
                pass

        # Structured request logging
        req_logger = logging.getLogger("lore.server.access")
        req_logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status": response.status_code,
                "latency_ms": round(duration * 1000, 2),
                "org_id": getattr(getattr(request, "state", None), "org_id", None),
            },
        )

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply rate limiting based on the API key in the Authorization header."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        from lore.server.rate_limit import get_backend

        # Extract API key for rate limiting
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            key = auth_header[7:]
            backend = get_backend()
            allowed, retry_after, remaining, limit = backend.is_allowed(key)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limit_exceeded",
                        "message": "Too many requests. Please retry later.",
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                    },
                )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(int(time.time()) + 60)
            return response

        return await call_next(request)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with bodies exceeding MAX_BODY_SIZE."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_BODY_SIZE:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": "request_too_large",
                            "message": f"Request body exceeds {MAX_BODY_SIZE} bytes.",
                        },
                    )
            except ValueError:
                pass

        return await call_next(request)


# ── Error Handlers ─────────────────────────────────────────────────


def install_error_handlers(app: FastAPI) -> None:
    """Install global exception handlers for consistent JSON error responses."""

    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # Map status codes to error codes
        error_codes = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            413: "request_too_large",
            422: "validation_error",
            429: "rate_limit_exceeded",
        }
        error_code = error_codes.get(exc.status_code, "error")
        message = str(exc.detail) if exc.detail else f"HTTP {exc.status_code}"

        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error_code, "message": message},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Check if this is a JSON decode error (malformed JSON)
        for error in exc.errors():
            if error.get("type") == "json_invalid":
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "malformed_json",
                        "message": "Request body contains invalid JSON.",
                    },
                )

        # Regular validation errors
        messages = []
        for error in exc.errors():
            loc = " -> ".join(str(l) for l in error["loc"])
            messages.append(f"{loc}: {error['msg']}")

        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "; ".join(messages),
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "An internal server error occurred.",
            },
        )


def install_middleware(app: FastAPI) -> None:
    """Install all middleware on the app."""
    # Order matters: outermost runs first (added last)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
