"""FastAPI application for Lore Cloud Server."""

from __future__ import annotations

import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from typing import AsyncIterator

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, Response
    from pydantic import BaseModel
except ImportError:
    raise ImportError(
        "FastAPI dependencies are required for the Lore server. "
        "Install them with: pip install lore-sdk[server]"
    )

try:
    from ulid import ULID
except ImportError:
    raise ImportError(
        "python-ulid is required. Install with: pip install python-ulid"
    )

from lore.server.auth import AuthError
from lore.server.config import settings
from lore.server.db import close_pool, get_pool, init_pool, run_migrations
from lore.server.logging_config import setup_logging
from lore.server.middleware import install_middleware
from lore.server.routes.keys import router as keys_router
from lore.server.routes.lessons import router as lessons_router
from lore.server.routes.sharing import rate_router
from lore.server.routes.sharing import router as sharing_router
from openbrain.server.routes.memories import router as memories_router
from openbrain.server.routes.memories import stats_router

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage DB pool lifecycle."""
    db_url = settings.database_url
    if not db_url:
        logger.warning("DATABASE_URL not set — running without database")
        yield
        return

    pool = await init_pool(db_url)
    await run_migrations(pool, settings.migrations_dir)

    # Warm up the embedding model for server-side embedding
    try:
        from openbrain.server.embed import ServerEmbedder
        embedder = ServerEmbedder.get_instance()
        embedder.load()
    except Exception:
        logger.warning("Failed to pre-load embedding model — will load on first request", exc_info=True)

    yield
    await close_pool()


app = FastAPI(
    title="Open Brain",
    version="0.3.0",
    lifespan=lifespan,
)


app.include_router(keys_router)
app.include_router(lessons_router)
app.include_router(sharing_router)
app.include_router(rate_router)

# Open Brain memory endpoints
app.include_router(memories_router)
app.include_router(stats_router)

# Install middleware and error handlers
install_middleware(app)


@app.exception_handler(AuthError)
async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error_code, "message": exc.error_code},
    )


# ── Health ─────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "openbrain"}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe: checks DB pool and pgvector extension."""
    checks: dict = {"db": False, "pgvector": False}
    try:
        from lore.server.db import _pool

        if _pool is None:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "checks": checks},
            )

        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            checks["db"] = True

            result = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            checks["pgvector"] = bool(result)
    except Exception:
        logger.exception("Readiness check failed")

    all_ok = all(checks.values())
    status_code = 200 if all_ok else 503
    status = "ok" if all_ok else "not_ready"
    return JSONResponse(
        status_code=status_code,
        content={"status": status, "checks": checks},
    )


# ── Metrics ────────────────────────────────────────────────────────


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    if not settings.metrics_enabled:
        return JSONResponse(status_code=404, content={"error": "metrics_disabled"})
    from lore.server.metrics import collect_all

    return Response(content=collect_all(), media_type="text/plain; version=0.0.4; charset=utf-8")


# ── Org Init ───────────────────────────────────────────────────────


class OrgInitRequest(BaseModel):
    name: str


class OrgInitResponse(BaseModel):
    org_id: str
    api_key: str
    key_prefix: str


@app.post("/v1/org/init", response_model=OrgInitResponse, status_code=201)
async def org_init(body: OrgInitRequest) -> OrgInitResponse:
    """Create a new org and return a root API key.

    The raw API key is returned once and never stored.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Check if any org exists already
            existing = await conn.fetchval("SELECT id FROM orgs LIMIT 1")
            if existing is not None:
                raise HTTPException(status_code=409, detail="Org already exists")

            org_id = str(ULID())
            await conn.execute(
                "INSERT INTO orgs (id, name) VALUES ($1, $2)",
                org_id,
                body.name,
            )

            # Generate API key with ob_sk_ prefix (Open Brain)
            raw_key = "ob_sk_" + secrets.token_hex(16)
            key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
            key_prefix = raw_key[:12]
            key_id = str(ULID())

            await conn.execute(
                """INSERT INTO api_keys (id, org_id, name, key_hash, key_prefix, is_root)
                   VALUES ($1, $2, $3, $4, $5, TRUE)""",
                key_id,
                org_id,
                "root",
                key_hash,
                key_prefix,
            )

    return OrgInitResponse(
        org_id=org_id,
        api_key=raw_key,
        key_prefix=key_prefix,
    )


