"""Export/Import/Snapshot REST API endpoints."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Optional

try:
    from fastapi import APIRouter, Depends, HTTPException, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
except ImportError:
    raise ImportError("FastAPI is required. Install with: pip install lore-sdk[server]")

from lore.server.auth import AuthContext, get_auth_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["export"])


class ExportRequest(BaseModel):
    format: str = "json"
    project: Optional[str] = None
    type: Optional[str] = None
    tier: Optional[str] = None
    since: Optional[str] = None
    include_embeddings: bool = False


class ImportQueryParams(BaseModel):
    overwrite: bool = False
    skip_embeddings: bool = True
    dry_run: bool = False


@router.post("/export")
async def export_data(
    body: ExportRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    """Export all memories and knowledge graph as JSON."""
    from lore import Lore

    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        lore = Lore()
        result = lore.export_data(
            format="json",
            output=tmp_path,
            project=body.project,
            type=body.type,
            tier=body.tier,
            since=body.since,
            include_embeddings=body.include_embeddings,
        )
        lore.close()

        with open(tmp_path, "r") as f:
            export_json = json.load(f)

        os.unlink(tmp_path)

        return JSONResponse(
            content=export_json,
            headers={
                "X-Lore-Export-Memories": str(result.memories),
                "X-Lore-Export-Entities": str(result.entities),
            },
        )
    except Exception as e:
        logger.error("Export failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")


@router.post("/import")
async def import_data(
    request: Request,
    overwrite: bool = False,
    skip_embeddings: bool = True,
    dry_run: bool = False,
    auth: AuthContext = Depends(get_auth_context),
):
    """Import from a JSON export body."""
    from lore import Lore

    try:
        body = await request.body()
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="wb", delete=False
        ) as tmp:
            tmp.write(body)
            tmp_path = tmp.name

        lore = Lore()
        result = lore.import_data(
            file_path=tmp_path,
            overwrite=overwrite,
            skip_embeddings=skip_embeddings,
            dry_run=dry_run,
        )
        lore.close()
        os.unlink(tmp_path)

        return {
            "imported": result.imported,
            "skipped": result.skipped,
            "overwritten": result.overwritten,
            "errors": result.errors,
            "warnings": result.warnings[:20],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Import failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")


@router.post("/snapshots")
async def create_snapshot(
    auth: AuthContext = Depends(get_auth_context),
):
    """Create a server-side snapshot."""
    from lore import Lore
    from lore.export.snapshot import SnapshotManager

    try:
        lore = Lore()
        mgr = SnapshotManager(lore)
        info = mgr.create()
        lore.close()
        return {
            "name": info["name"],
            "memories": info["memories"],
            "size_human": info["size_human"],
        }
    except Exception as e:
        logger.error("Snapshot failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Snapshot failed: {e}")


@router.get("/snapshots")
async def list_snapshots(
    auth: AuthContext = Depends(get_auth_context),
):
    """List available snapshots."""
    from lore import Lore
    from lore.export.snapshot import SnapshotManager

    try:
        lore = Lore()
        mgr = SnapshotManager(lore)
        snapshots = mgr.list()
        lore.close()
        return {"snapshots": snapshots}
    except Exception as e:
        logger.error("List snapshots failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/snapshots/{name}")
async def delete_snapshot(
    name: str,
    auth: AuthContext = Depends(get_auth_context),
):
    """Delete a specific snapshot."""
    from lore import Lore
    from lore.export.snapshot import SnapshotManager

    try:
        lore = Lore()
        mgr = SnapshotManager(lore)
        if mgr.delete(name):
            lore.close()
            return JSONResponse(status_code=204, content=None)
        lore.close()
        raise HTTPException(status_code=404, detail=f"Snapshot {name} not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Delete snapshot failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
