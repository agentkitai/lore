"""Telegram source adapter — HTML/Markdown stripping, token verification."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from ..normalize import normalize_content
from .base import NormalizedMessage, SourceAdapter


class TelegramAdapter(SourceAdapter):
    adapter_name = "telegram"

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.secret_token = hashlib.sha256(bot_token.encode()).hexdigest()[:32]

    def verify(self, request_headers: dict, request_body: bytes) -> bool:
        """Verify Telegram X-Telegram-Bot-Api-Secret-Token header."""
        header_token = request_headers.get("x-telegram-bot-api-secret-token", "")
        return hmac.compare_digest(header_token, self.secret_token)

    def normalize(self, payload: dict) -> NormalizedMessage:
        message = payload.get("message", {})
        chat = message.get("chat", {})
        user = message.get("from", {})

        raw_text = message.get("text", "")
        has_entities = bool(message.get("entities"))
        raw_format = "telegram_html" if has_entities else "plain_text"

        return NormalizedMessage(
            content=normalize_content(raw_text, raw_format),
            user=user.get("username") or str(user.get("id", "")),
            channel=chat.get("title") or str(chat.get("id", "")),
            timestamp=datetime.fromtimestamp(
                message.get("date", 0), tz=timezone.utc
            ).isoformat(),
            source_message_id=str(message.get("message_id", "")),
            raw_format=raw_format,
        )
