"""Graph visualization endpoints package.

Re-exports the router so that ``from lore.server.routes.graph import router``
continues to work after the module-to-package conversion.
"""

from .router import router

__all__ = ["router"]
