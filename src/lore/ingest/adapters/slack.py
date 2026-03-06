"""Slack source adapter — mrkdwn stripping, HMAC-SHA256 verification."""

from __future__ import annotations

import hashlib
import hmac
import time

from .base import NormalizedMessage, SourceAdapter
from ..normalize import normalize_content


class SlackAdapter(SourceAdapter):
    adapter_name = "slack"

    def __init__(self, signing_secret: str):
        self.signing_secret = signing_secret

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify Slack request using HMAC-SHA256 of v0:{timestamp}:{body}."""
        timestamp = request_headers.get("x-slack-request-timestamp", "")
        signature = request_headers.get("x-slack-signature", "")

        # Replay protection
        try:
            if abs(time.time() - float(timestamp)) > 300:
                return False
        except (ValueError, TypeError):
            return False

        sig_basestring = f"v0:{timestamp}:{request_body.decode('utf-8')}"
        expected = "v0=" + hmac.new(
            self.signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    def normalize(self, payload: dict) -> NormalizedMessage:
        event = payload.get("event", {})

        return NormalizedMessage(
            content=normalize_content(event.get("text", ""), "slack_mrkdwn"),
            user=event.get("user"),
            channel=event.get("channel"),
            timestamp=event.get("ts"),
            source_message_id=event.get("ts"),
            raw_format="slack_mrkdwn",
        )

    @staticmethod
    def is_url_verification(payload: dict) -> bool:
        """Check if this is a Slack URL verification challenge."""
        return payload.get("type") == "url_verification"

    @staticmethod
    def is_bot_message(payload: dict) -> bool:
        """Check if this is a bot message (ignore to avoid feedback loops)."""
        event = payload.get("event", {})
        return event.get("subtype") == "bot_message" or event.get("bot_id") is not None
