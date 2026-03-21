"""Plugin discovery, registration, and lifecycle management."""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from typing import Dict, List, Optional

from lore.plugin.base import LorePlugin, PluginMeta

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "lore.plugins"


class PluginRegistry:
    """Discovers, loads, and manages Lore plugins."""

    def __init__(self) -> None:
        self._plugins: Dict[str, LorePlugin] = {}
        self._disabled: set = set()

    def discover(self) -> List[PluginMeta]:
        """Discover plugins via entry_points."""
        discovered: List[PluginMeta] = []
        try:
            eps = importlib.metadata.entry_points()
            if hasattr(eps, "select"):
                group = eps.select(group=ENTRY_POINT_GROUP)
            else:
                group = eps.get(ENTRY_POINT_GROUP, [])

            for ep in group:
                try:
                    plugin_cls = ep.load()
                    if hasattr(plugin_cls, "meta"):
                        discovered.append(plugin_cls.meta)
                except Exception:
                    logger.warning("Failed to load plugin: %s", ep.name, exc_info=True)
        except Exception:
            logger.warning("Plugin discovery failed", exc_info=True)
        return discovered

    def load_all(self) -> int:
        """Load all discovered plugins. Returns count loaded."""
        count = 0
        try:
            eps = importlib.metadata.entry_points()
            if hasattr(eps, "select"):
                group = eps.select(group=ENTRY_POINT_GROUP)
            else:
                group = eps.get(ENTRY_POINT_GROUP, [])

            for ep in group:
                try:
                    plugin_cls = ep.load()
                    plugin = plugin_cls()
                    if hasattr(plugin, "meta") and isinstance(plugin, LorePlugin):
                        self._plugins[plugin.meta.name] = plugin
                        count += 1
                        logger.info("Loaded plugin: %s v%s", plugin.meta.name, plugin.meta.version)
                except Exception:
                    logger.warning("Failed to load plugin: %s", ep.name, exc_info=True)
        except Exception:
            logger.warning("Plugin loading failed", exc_info=True)
        return count

    def get(self, name: str) -> Optional[LorePlugin]:
        """Get a loaded plugin by name."""
        return self._plugins.get(name)

    def list_plugins(self) -> List[Dict]:
        """List all loaded plugins with status."""
        return [
            {
                "name": p.meta.name,
                "version": p.meta.version,
                "description": p.meta.description,
                "priority": p.meta.priority,
                "enabled": p.meta.name not in self._disabled,
            }
            for p in sorted(self._plugins.values(), key=lambda p: p.meta.priority)
        ]

    def enable(self, name: str) -> bool:
        if name in self._plugins:
            self._disabled.discard(name)
            return True
        return False

    def disable(self, name: str) -> bool:
        if name in self._plugins:
            self._disabled.add(name)
            return True
        return False

    def reload(self, name: str) -> bool:
        """Hot-reload a plugin by re-importing its module."""
        plugin = self._plugins.get(name)
        if not plugin:
            return False
        try:
            module = importlib.import_module(type(plugin).__module__)
            importlib.reload(module)
            plugin_cls = getattr(module, type(plugin).__name__)
            new_plugin = plugin_cls()
            self._plugins[name] = new_plugin
            logger.info("Reloaded plugin: %s", name)
            return True
        except Exception:
            logger.warning("Failed to reload plugin: %s", name, exc_info=True)
            return False

    def get_active(self) -> List[LorePlugin]:
        """Return active (enabled) plugins sorted by priority."""
        return [
            p for p in sorted(self._plugins.values(), key=lambda p: p.meta.priority)
            if p.meta.name not in self._disabled
        ]

    def cleanup_all(self) -> None:
        """Call cleanup on all plugins."""
        for plugin in self._plugins.values():
            try:
                plugin.cleanup()
            except Exception:
                logger.warning("Plugin cleanup failed: %s", plugin.meta.name, exc_info=True)
