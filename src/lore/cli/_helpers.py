"""Shared helpers for the Lore CLI — _get_lore, API config, HTTP requests."""

from __future__ import annotations

import json
import sys
from typing import Optional


def _get_lore(db: Optional[str] = None) -> "Lore":  # noqa: F821
    import os

    from lore import Lore

    # Auto-enable enrichment if API key is available
    enrichment = bool(os.environ.get("OPENAI_API_KEY"))
    enrichment_model = os.environ.get("LORE_ENRICHMENT_MODEL", "gpt-4o-mini")

    return Lore(
        enrichment=enrichment,
        enrichment_model=enrichment_model,
        knowledge_graph=True,
    )


def _get_api_config(args) -> tuple:
    """Get API URL and key from args or env vars."""
    import os

    api_url = getattr(args, "api_url", None) or os.environ.get("LORE_API_URL")
    api_key = getattr(args, "api_key", None) or os.environ.get("LORE_API_KEY")
    if not api_url:
        print("Error: --api-url or LORE_API_URL required", file=sys.stderr)
        sys.exit(1)
    if not api_key:
        print("Error: --api-key or LORE_API_KEY required", file=sys.stderr)
        sys.exit(1)
    return api_url.rstrip("/"), api_key


def _api_request(
    method: str, url: str, api_key: str, json_data: Optional[dict] = None
) -> dict:
    """Make an HTTP request to the Lore API."""
    import urllib.error
    import urllib.request

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = None
    if json_data is not None:
        data = json.dumps(json_data).encode()

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 204:
                return {}
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            err = json.loads(body)
            detail = err.get("detail", err.get("error", body))
        except (json.JSONDecodeError, ValueError):
            detail = body
        print(f"Error {e.code}: {detail}", file=sys.stderr)
        sys.exit(1)
