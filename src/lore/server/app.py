"""FastAPI application for Lore Cloud Server."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

try:
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
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

from lore.server.auth import AuthContext, AuthError, get_auth_context
from lore.server.config import settings
from lore.server.db import close_pool, get_pool, init_pool, run_migrations
from lore.server.middleware import install_middleware
from lore.server.routes.keys import router as keys_router
from lore.server.routes.lessons import router as lessons_router

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


