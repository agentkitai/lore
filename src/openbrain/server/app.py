"""Open Brain server application.

Re-exports the FastAPI app from lore.server.app so the server can be
started as `uvicorn openbrain.server.app:app`.
"""

from lore.server.app import app  # noqa: F401
