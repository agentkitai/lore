"""Base classes for source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class NormalizedMessage:
    """Common format produced by all source adapters after parsing + normalization."""

    content: str
    user: Optional[str] = None
    channel: Optional[str] = None
    timestamp: Optional[str] = None
    source_message_id: Optional[str] = None
    raw_format: str = "plain_text"
    memory_type: str = "general"
    tags: Optional[List[str]] = None


class SourceAdapter(ABC):
    """Base class for source adapters.

    Each adapter handles:
    1. Webhook verification — validate request authenticity
    2. Payload normalization — parse source format into NormalizedMessage
    """

    adapter_name: str = "raw"

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify webhook signature. Returns True if valid or not applicable."""
        return True

    @abstractmethod
    def normalize(self, payload: dict) -> NormalizedMessage:
        """Parse source-specific payload and return normalized message."""
        ...
