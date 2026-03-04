"""Lore SDK exceptions."""


class MemoryNotFoundError(Exception):
    """Raised when an operation targets a memory ID that does not exist."""

    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        super().__init__(f"Memory not found: {memory_id}")


class LoreConnectionError(Exception):
    """Raised when the SDK cannot connect to the Lore server."""


class LoreAuthError(Exception):
    """Raised when the server rejects the API key (401/403)."""


class SecretBlockedError(Exception):
    """Raised when content contains a detected secret and storage is blocked."""

    def __init__(self, finding_type: str) -> None:
        self.finding_type = finding_type
        super().__init__(
            f"Blocked: content contains a secret ({finding_type} detected). "
            "Remove the secret and retry."
        )


# Deprecated alias
LessonNotFoundError = MemoryNotFoundError
