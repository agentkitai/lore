"""FastAPI application for Lore Cloud Server."""

from __future__ import annotations

import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from typing import AsyncIterator

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
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
from lore.server.routes.analytics import router as analytics_router
from lore.server.routes.conversations import router as conversations_router
from lore.server.routes.export import router as export_router
from lore.server.routes.graph import router as graph_router
from lore.server.routes.ingest import router as ingest_router
from lore.server.routes.keys import router as keys_router
from lore.server.routes.lessons import router as lessons_router
from lore.server.routes.memories import router as memories_router
from lore.server.routes.recent import router as recent_router
from lore.server.routes.retrieve import router as retrieve_router
from lore.server.routes.review import router as review_router
from lore.server.routes.sharing import rate_router
from lore.server.routes.sharing import router as sharing_router
from lore.server.routes.topics import router as topics_router

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
    yield
    await close_pool()


app = FastAPI(
    title="Lore Cloud",
    version="0.2.0",
    lifespan=lifespan,
)


app.include_router(keys_router)
app.include_router(lessons_router)
app.include_router(memories_router)
app.include_router(sharing_router)
app.include_router(rate_router)
app.include_router(ingest_router)
app.include_router(retrieve_router)
app.include_router(analytics_router)
app.include_router(conversations_router)
app.include_router(recent_router)
app.include_router(export_router)
app.include_router(graph_router)
app.include_router(review_router)
app.include_router(topics_router)

# ── UI static files ────────────────────────────────────────────────
import importlib.resources as _pkg_resources  # noqa: E402
import pathlib as _pathlib  # noqa: E402

_ui_dist = None
try:
    _ui_pkg = _pkg_resources.files("lore") / "ui" / "dist"
    _ui_path = _pathlib.Path(str(_ui_pkg))
    if _ui_path.is_dir():
        _ui_dist = _ui_path
except Exception:
    pass

if _ui_dist:
    _index_html = (_ui_dist / "index.html").read_text()

    @app.get("/ui/", response_class=HTMLResponse)
    @app.get("/ui/{path:path}", response_class=HTMLResponse)
    async def serve_ui(path: str = "") -> HTMLResponse:
        return HTMLResponse(_index_html)

    app.mount("/static", StaticFiles(directory=str(_ui_dist)), name="static")

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
    return {"status": "ok"}


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

            # Generate API key
            raw_key = "lore_sk_" + secrets.token_hex(16)
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


