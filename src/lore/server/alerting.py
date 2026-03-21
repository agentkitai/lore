"""Alert channel abstraction for SLO alerts."""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict

logger = logging.getLogger(__name__)


class AlertChannel(ABC):
    """Abstract base class for alert delivery channels."""

    @abstractmethod
    async def send(self, alert: Dict[str, Any]) -> bool:
        """Send an alert. Returns True on success."""


class WebhookChannel(AlertChannel):
    """Deliver alerts via HTTP webhook."""

    def __init__(self, url: str, headers: Dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = headers or {}

    async def send(self, alert: Dict[str, Any]) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self.url,
                    json=alert,
                    headers=self.headers,
                )
                return 200 <= resp.status_code < 300
        except ImportError:
            import urllib.request
            req = urllib.request.Request(
                self.url,
                data=json.dumps(alert).encode(),
                headers={
                    "Content-Type": "application/json",
                    **self.headers,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 300
        except Exception:
            logger.warning("Webhook alert failed for %s", self.url, exc_info=True)
            return False


class EmailChannel(AlertChannel):
    """Deliver alerts via SMTP email."""

    def __init__(
        self,
        to_addr: str,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_pass: str | None = None,
        from_addr: str | None = None,
    ) -> None:
        self.to_addr = to_addr
        self.smtp_host = smtp_host or os.environ.get("SMTP_HOST", "")
        self.smtp_port = smtp_port or int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user = smtp_user or os.environ.get("SMTP_USER", "")
        self.smtp_pass = smtp_pass or os.environ.get("SMTP_PASS", "")
        self.from_addr = from_addr or os.environ.get("SMTP_FROM", self.smtp_user)

    async def send(self, alert: Dict[str, Any]) -> bool:
        if not self.smtp_host:
            logger.warning("Email alert skipped — SMTP not configured")
            return False

        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = f"SLO Alert: {alert.get('slo_name', 'Unknown')}"
            msg["From"] = self.from_addr
            msg["To"] = self.to_addr
            msg.set_content(
                f"SLO Alert\n\n"
                f"Name: {alert.get('slo_name')}\n"
                f"Metric: {alert.get('metric')}\n"
                f"Value: {alert.get('value')}\n"
                f"Threshold: {alert.get('threshold')}\n"
                f"Status: {alert.get('status')}\n"
            )

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.smtp_user:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)
            return True
        except Exception:
            logger.warning("Email alert failed", exc_info=True)
            return False
