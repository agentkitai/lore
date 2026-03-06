"""Raw text adapter — passthrough with no verification."""

from __future__ import annotations

from ..normalize import normalize_content
from .base import NormalizedMessage, SourceAdapter


class RawAdapter(SourceAdapter):
    adapter_name = "raw"

    def normalize(self, payload: dict) -> NormalizedMessage:
        return NormalizedMessage(
            content=normalize_content(payload.get("content", ""), "plain_text"),
            user=payload.get("user"),
            channel=payload.get("channel"),
            timestamp=payload.get("timestamp"),
            source_message_id=payload.get("message_id"),
            raw_format="plain_text",
            memory_type=payload.get("type", "general"),
            tags=payload.get("tags"),
        )
