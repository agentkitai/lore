"""Plugin management API — /v1/plugins."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

try:
    from fastapi import APIRouter, HTTPException
except ImportError:
    raise ImportError("FastAPI is required.")

from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])

# Module-level registry (initialized in app lifespan)
_registry = None


def set_registry(registry) -> None:
    global _registry
    _registry = registry


def _get_registry():
    if _registry is None:
        from lore.plugin.registry import PluginRegistry
        return PluginRegistry()
    return _registry


class PluginInfo(BaseModel):
    name: str
    version: str
    description: str = ""
    priority: int = 100
    enabled: bool = True


@router.get("", response_model=List[PluginInfo])
async def list_plugins() -> List[PluginInfo]:
    registry = _get_registry()
    return [PluginInfo(**p) for p in registry.list_plugins()]


@router.post("/{name}/enable")
async def enable_plugin(name: str) -> Dict[str, Any]:
    registry = _get_registry()
    if registry.enable(name):
        return {"status": "enabled", "name": name}
    raise HTTPException(404, f"Plugin '{name}' not found")


@router.post("/{name}/disable")
async def disable_plugin(name: str) -> Dict[str, Any]:
    registry = _get_registry()
    if registry.disable(name):
        return {"status": "disabled", "name": name}
    raise HTTPException(404, f"Plugin '{name}' not found")


@router.post("/{name}/reload")
async def reload_plugin(name: str) -> Dict[str, Any]:
    registry = _get_registry()
    if registry.reload(name):
        return {"status": "reloaded", "name": name}
    raise HTTPException(404, f"Plugin '{name}' not found or reload failed")


@router.get("/{name}", response_model=PluginInfo)
async def get_plugin(name: str) -> PluginInfo:
    registry = _get_registry()
    plugin = registry.get(name)
    if not plugin:
        raise HTTPException(404, f"Plugin '{name}' not found")
    return PluginInfo(
        name=plugin.meta.name,
        version=plugin.meta.version,
        description=plugin.meta.description,
        priority=plugin.meta.priority,
        enabled=plugin.meta.name not in registry._disabled,
    )
