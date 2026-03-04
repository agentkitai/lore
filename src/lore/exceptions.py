"""Lore SDK exceptions."""


class LoreConnectionError(Exception):
    """Raised when the SDK cannot connect to the Lore server."""


class LoreAuthError(Exception):
    """Raised when the server rejects the API key (401/403)."""
