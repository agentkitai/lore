"""Background SLO checker — evaluates SLO definitions periodically."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def slo_checker_loop(interval_seconds: int = 60) -> None:
    """Background task that evaluates SLOs and creates alerts.

    Runs indefinitely, checking all enabled SLO definitions every
    ``interval_seconds``. When a threshold is breached, inserts a
    ``slo_alerts`` row and dispatches to configured alert channels.
    """
    from lore.server.db import get_pool

    while True:
        try:
            await _check_all_slos()
        except Exception:
            logger.warning("SLO check iteration failed", exc_info=True)

        await asyncio.sleep(interval_seconds)


async def _check_all_slos() -> None:
    """Evaluate all enabled SLOs and fire alerts for breaches."""
    from lore.server.db import get_pool
    from lore.server.routes.slo import _compute_metric, _check_threshold

    pool = await get_pool()
    async with pool.acquire() as conn:
        slos = await conn.fetch(
            """SELECT id, org_id, name, metric, operator, threshold,
                      window_minutes, alert_channels
               FROM slo_definitions
               WHERE enabled = TRUE"""
        )

        for slo in slos:
            try:
                value = await _compute_metric(
                    conn, slo["org_id"], slo["metric"], slo["window_minutes"],
                )
                passing = _check_threshold(
                    value, slo["operator"], float(slo["threshold"]),
                )

                if not passing and value is not None:
                    # Check if there's already a recent firing alert (debounce)
                    recent = await conn.fetchval(
                        """SELECT id FROM slo_alerts
                           WHERE slo_id = $1 AND status = 'firing'
                             AND created_at > now() - interval '5 minutes'""",
                        slo["id"],
                    )
                    if recent:
                        continue

                    # Create alert
                    channels = slo["alert_channels"] or []
                    dispatched: List[Dict[str, Any]] = []

                    for channel in channels:
                        try:
                            await _dispatch_alert(channel, slo, value)
                            dispatched.append({
                                "channel": channel.get("type", "unknown"),
                                "status": "sent",
                            })
                        except Exception as e:
                            dispatched.append({
                                "channel": channel.get("type", "unknown"),
                                "status": "failed",
                                "error": str(e),
                            })

                    await conn.execute(
                        """INSERT INTO slo_alerts
                           (org_id, slo_id, metric_value, threshold, status, dispatched_to)
                           VALUES ($1, $2, $3, $4, 'firing', $5::jsonb)""",
                        slo["org_id"], slo["id"], value,
                        float(slo["threshold"]),
                        json.dumps(dispatched),
                    )
                    logger.info(
                        "SLO breach: %s (value=%.4f, threshold=%.4f)",
                        slo["name"], value, float(slo["threshold"]),
                    )

            except Exception:
                logger.warning(
                    "Failed to check SLO %s", slo["id"], exc_info=True,
                )


async def _dispatch_alert(
    channel: Dict[str, Any],
    slo: Any,
    value: float,
) -> None:
    """Dispatch an alert to a configured channel."""
    channel_type = channel.get("type", "")

    if channel_type == "webhook":
        await _dispatch_webhook(channel, slo, value)
    elif channel_type == "email":
        _dispatch_email(channel, slo, value)
    else:
        logger.warning("Unknown alert channel type: %s", channel_type)


async def _dispatch_webhook(
    channel: Dict[str, Any],
    slo: Any,
    value: float,
) -> None:
    """Send webhook alert via httpx."""
    url = channel.get("url")
    if not url:
        return

    payload = {
        "slo_name": slo["name"],
        "metric": slo["metric"],
        "value": value,
        "threshold": float(slo["threshold"]),
        "operator": slo["operator"],
        "status": "firing",
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except ImportError:
        # Fallback to urllib
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)


def _dispatch_email(
    channel: Dict[str, Any],
    slo: Any,
    value: float,
) -> None:
    """Send email alert via smtplib."""
    import os

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_addr = os.environ.get("SMTP_FROM", smtp_user)
    to_addr = channel.get("email")

    if not smtp_host or not to_addr:
        logger.warning("Email alert skipped — SMTP not configured")
        return

    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = f"SLO Alert: {slo['name']} breached"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(
        f"SLO '{slo['name']}' is breaching.\n\n"
        f"Metric: {slo['metric']}\n"
        f"Current value: {value}\n"
        f"Threshold: {slo['threshold']} ({slo['operator']})\n"
    )

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        if smtp_user:
            server.starttls()
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)
