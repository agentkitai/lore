"""Server commands — serve, mcp, ui."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys

from lore.persistence.exceptions import InsecureBindError


def _is_loopback_host(host: str) -> bool:
    """Return True if ``host`` resolves to a loopback (127.x / ::1 / localhost).

    A trailing port (``host:port``) is *not* expected here — the CLI splits
    those upstream. Hostnames that don't parse as an IP literal default to
    treating ``localhost`` as loopback and everything else as non-loopback.
    """
    if not host:
        return False
    if host.lower() in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cmd_serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError:
        print(
            "Error: Server dependencies not installed.\n"
            "Install with: pip install lore-sdk[server]",
            file=sys.stderr,
        )
        sys.exit(1)
    port = args.port or int(os.environ.get("LORE_PORT", "8765"))
    host = args.host
    require_auth = bool(getattr(args, "require_auth", False))
    if not _is_loopback_host(host) and not require_auth:
        raise InsecureBindError(
            f"Cannot bind to {host!r} without --require-auth. Solo mode "
            "binds to 127.0.0.1 by default; pass --require-auth to "
            "acknowledge that authentication is enforced before exposing "
            "Lore on a non-loopback address."
        )
    print(f"Starting Lore server on {host}:{port}")
    uvicorn.run("lore.server.app:app", host=host, port=port)


def cmd_mcp(args: argparse.Namespace) -> None:
    try:
        from lore.mcp.server import run_server
    except ImportError:
        print(
            "Error: MCP dependencies not installed.\n"
            "Install with: pip install lore-sdk[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)
    run_server()


def cmd_ui(args: argparse.Namespace) -> None:
    """Open graph visualization UI in the browser."""
    import webbrowser

    host = getattr(args, "host", "localhost")
    port = getattr(args, "port", 8765)
    url = f"http://{host}:{port}/ui"

    print(f"Opening Lore Graph UI: {url}")
    if not getattr(args, "no_open", False):
        webbrowser.open(url)
