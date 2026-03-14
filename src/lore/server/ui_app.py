"""Standalone FastAPI app for the Graph Visualization UI.

This is separate from the cloud server (app.py). It uses a local Store
(typically SQLite) directly, with no auth, no Postgres, no org scoping.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from lore.server.routes.ui import router as ui_router


def create_ui_app(static_dir: str | None = None) -> FastAPI:
    """Create a FastAPI app for the graph visualization UI.

    Args:
        static_dir: Path to the directory containing built frontend assets.
                    Defaults to src/lore/ui/dist/.
    """
    if static_dir is None:
        # Default: look relative to this file
        static_dir = str(Path(__file__).parent.parent / "ui" / "dist")

    app = FastAPI(title="Lore Graph Visualization", version="0.1.0")

    # Include UI API routes
    app.include_router(ui_router)

    # Serve static files if the directory exists
    if os.path.isdir(static_dir):
        # Serve index.html at root
        index_path = os.path.join(static_dir, "index.html")

        @app.get("/")
        async def serve_index():
            if os.path.isfile(index_path):
                return FileResponse(index_path, media_type="text/html")
            return {"error": "index.html not found"}

        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
