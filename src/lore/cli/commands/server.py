"""Server commands — serve, mcp, ui."""

from __future__ import annotations

import argparse
import os
import sys


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
