"""Plugin SDK for custom enrichment — Protocol-based plugin interface.

This module provides the ``EnrichmentPlugin`` protocol and a lightweight
``PluginRegistry`` that complements the full plugin framework in
``lore.plugin``.  The enrichment protocol is intentionally simple so
third-party code can participate without depending on the full SDK.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class EnrichmentPlugin(Protocol):
    """Protocol for custom enrichment plugins."""

    name: str

    def enrich(self, content: str, metadata: dict) -> dict:
        """Enrich memory content.  Returns metadata updates to merge."""
        ...


class PluginRegistry:
    """Registry for enrichment plugins."""

    def __init__(self) -> None:
        self._plugins: Dict[str, EnrichmentPlugin] = {}

    def register(self, plugin: EnrichmentPlugin) -> None:
        """Register a plugin by its ``name`` attribute."""
        self._plugins[plugin.name] = plugin
        logger.info("Registered enrichment plugin: %s", plugin.name)

    def get(self, name: str) -> Optional[EnrichmentPlugin]:
        """Return a registered plugin by name, or ``None``."""
        return self._plugins.get(name)

    def list(self) -> List[str]:
        """Return the names of all registered plugins."""
        return sorted(self._plugins.keys())

    def run_all(self, content: str, metadata: dict) -> dict:
        """Execute every registered plugin and merge metadata updates.

        Each plugin receives the *original* content and a *copy* of the
        current metadata.  Returned dicts are shallow-merged in
        registration order.
        """
        merged: Dict[str, Any] = {}
        for name in sorted(self._plugins):
            plugin = self._plugins[name]
            try:
                updates = plugin.enrich(content, dict(metadata))
                if isinstance(updates, dict):
                    merged.update(updates)
            except Exception:
                logger.warning("Enrichment plugin '%s' failed", name, exc_info=True)
        return merged


# Global registry ---------------------------------------------------------

_registry = PluginRegistry()


def get_plugin_registry() -> PluginRegistry:
    """Return the global enrichment plugin registry."""
    return _registry
